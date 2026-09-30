# ---- Build stage ----
# 基镜像取自阿里云 ACR 公开仓库（免登录拉取）。tag 带 patch 号（3.12.14）——
# 升级基镜像必须改这一行、走代码评审，避免"同 tag 覆盖"造成的静默换基
FROM crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com/deploy_demo/deploy_base:python-3.12.14 AS builder

# Use Aliyun mirror for faster downloads in China
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

WORKDIR /build
# 先只 COPY pyproject.toml — 依赖只跟它有关
COPY pyproject.toml .
# pip install 需要 README.md（pyproject.toml 声明了 readme = "README.md"），
# 放一个临时的占位，避免 README.md 改动触发重新安装全部依赖
# 只装主依赖（不带 [dev]）：ragas/datasets/pytest/pyright 等仅开发与**离线评估**用，
# 不进生产镜像（实测省下 pyarrow 156M + pandas 72M + pyright 38M 等）
# 注意：alembic 已归入主依赖，否则容器内没有 alembic CLI、迁移跑不了
RUN echo "# placeholder" > README.md && \
    mkdir -p src && pip install .
# 再 COPY 真正的 README.md（此后的变动不影响上层的 pip 缓存）
COPY README.md .

# ---- Runtime stage ----
FROM crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com/deploy_demo/deploy_base:python-3.12.14-slim

# Use Aliyun mirror for faster downloads in China
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# Copy installed packages from builder (preserves compiled native extensions)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 代码与随版本发布的资源 —— 必须与镜像同源，否则宿主版本与镜像版本会静默不一致：
#   src/ scripts/          应用代码
#   skills/ agents/        技能与智能体预设（无运行时自定义入口，只能随代码发布）
#   alembic/ alembic.ini   迁移脚本（必须与代码版本对应；alembic CLI 已在镜像内）
# 不拷 deploy/：其内容（nginx.conf / html / postgres init）归 nginx / postgres 容器消费，
# 由 compose 从宿主挂载；app 容器内无任何消费方（实测 src/ 与 scripts/ 零引用）
COPY src/ src/
COPY scripts/ scripts/
COPY skills/ skills/
COPY agents/ agents/
COPY alembic/ alembic/
COPY alembic.ini .

# Volume mount points
VOLUME ["/data/logs"]

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "30"]
