# ---- Build stage ----
# 基镜像取自阿里云 ACR 公开仓库（免登录拉取）。tag 带 patch 号（3.12.14）——
# 升级基镜像必须改这一行、走代码评审，避免"同 tag 覆盖"造成的静默换基
FROM crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com/deploy_demo/deploy_base:python-3.12.14 AS builder

# 依赖源 = 云效制品仓库（repo-okxha，代理公共 PyPI，缓存已预热到位）。
# 凭据由流水线的全局变量经 --build-arg 传入 —— 不写进仓库、只在构建期存在。
# ⚠ 已知代价：BuildKit 会把 ARG 的值记进镜像 history（`docker history --no-trunc` 可见），
#   并随镜像进 ACR / ECS。故该账号必须是「只读、仓库级」，且首次部署后要轮换。
ARG PIP_REPO_USER
ARG PIP_REPO_PASS

WORKDIR /build
# 先只 COPY pyproject.toml — 依赖只跟它有关
COPY pyproject.toml .
# pip install 需要 README.md（pyproject.toml 声明了 readme = "README.md"），
# 放一个临时的占位，避免 README.md 改动触发重新安装全部依赖
# 只装主依赖（不带 [dev]）：ragas/datasets/pytest/pyright 等仅开发与**离线评估**用，
# 不进生产镜像（实测省下 pyarrow 156M + pandas 72M + pyright 38M 等）
# 注意：alembic 已归入主依赖，否则容器内没有 alembic CLI、迁移跑不了
# 云效代理仓是**懒加载缓存**：冷包/元数据的回源可能 504 或读超时，故放宽 timeout/retries。
# 单个 RUN 内 set → install → 清理：凭据不留在任何镜像层里（只留在 history 元数据，见上）。
# 未传 ARG 时立刻失败 —— 否则会静默退回公网源，慢到 70 分钟且多半以超时告终。
RUN set -eux; \
    [ -n "${PIP_REPO_USER:-}" ] && [ -n "${PIP_REPO_PASS:-}" ] || { echo "FATAL: 未传入 PIP_REPO_USER / PIP_REPO_PASS（检查流水线的 --build-arg）" >&2; exit 1; }; \
    pip config set global.index-url "https://${PIP_REPO_USER}:${PIP_REPO_PASS}@deploytest-cn-shanghai.devops.aliyuncs.com/packages/api/protocol/pypi/repo-okxha"; \
    pip config set global.trusted-host deploytest-cn-shanghai.devops.aliyuncs.com; \
    echo "# placeholder" > README.md; \
    mkdir -p src; \
    pip install --timeout 300 --retries 5 .; \
    pip config unset global.index-url || true; \
    pip config unset global.trusted-host || true; \
    rm -f /root/.config/pip/pip.conf /root/.pip/pip.conf /etc/pip.conf
# 再 COPY 真正的 README.md（此后的变动不影响上层的 pip 缓存）
COPY README.md .

# ---- Runtime stage ----
FROM crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com/deploy_demo/deploy_base:python-3.12.14-slim

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
