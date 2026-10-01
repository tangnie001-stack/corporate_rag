# ---- Build stage ----
# 基镜像取自阿里云 ACR 公开仓库（免登录拉取）。tag 带 patch 号（3.12.14）——
# 升级基镜像必须改这一行、走代码评审，避免"同 tag 覆盖"造成的静默换基
FROM crpi-u3ezxc1o5hirfddw.cn-shanghai.personal.cr.aliyuncs.com/deploy_demo/deploy_base:python-3.12.14 AS builder

# 依赖源 = 云效制品仓库（repo-okxha，代理公共 PyPI，缓存已预热到位）。
# 凭据由流水线的全局变量经 --build-arg 传入 —— 不写进仓库、只在构建期存在。
# ⚠ 凭据泄漏面（已核对）：
#   ✅ 不进最终镜像 —— ARG 只在 builder 阶段声明/使用，推送到 ACR 的是 runtime 阶段，
#      `docker history --no-trunc <最终镜像>` 里看不到这两个 ARG 的值。
#   ❌ **会进构建日志**（本地实测确认）—— BuildKit 在 --progress=plain 下回显 RUN 命令时会把
#      ARG 值代入：日志里直接出现 `#11 [builder 4/5] RUN ... -z "<PIP_REPO_PASS 明文>"`。这与 set -x **无关**
#      （去掉 -x 照样回显，-x 只是额外多几行 trace）。云效步骤用 --progress=plain，故日志必然带明文。
#   ❓ 会进 buildx 命令行 —— 云效步骤会回显 `docker buildx build ... --build-arg PIP_REPO_PASS=...`，
#      是否打码待第 3 次运行日志确认；若为明文，改用 --secret 方案。
#   综上：该账号仍必须是「只读、仓库级」，并定期轮换。
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
# 单个 RUN 内 set → install → 清理 + 残留自检：凭据不留在任何镜像层，也不进最终镜像。
# 未传 ARG 时立刻失败 —— 否则会静默退回公网源，慢到 70 分钟且多半以超时告终。
# ⚠ 用 `set -eu`（不带 -x）：-x 的 trace 会额外把展开后的密码写进日志（命令回显那处无法避免）。
RUN set -eu; \
    if [ -z "${PIP_REPO_USER:-}" ] || [ -z "${PIP_REPO_PASS:-}" ]; then \
      echo "FATAL: 未传入 PIP_REPO_USER / PIP_REPO_PASS（检查流水线 step 的 variables / --build-arg）" >&2; \
      exit 1; \
    fi; \
    echo ">>> [1/4] 配置 pip 源 -> 云效制品仓库 repo-okxha（凭据不回显）"; \
    pip config set global.index-url "https://${PIP_REPO_USER}:${PIP_REPO_PASS}@deploytest-cn-shanghai.devops.aliyuncs.com/packages/api/protocol/pypi/repo-okxha"; \
    pip config set global.trusted-host deploytest-cn-shanghai.devops.aliyuncs.com; \
    echo ">>> [2/4] 准备构建上下文占位（README.md / src）"; \
    echo "# placeholder" > README.md; \
    mkdir -p src; \
    echo ">>> [3/4] pip install .（--timeout 300 --retries 5）"; \
    pip install --timeout 300 --retries 5 .; \
    echo ">>> [4/4] 清理 pip 配置，确保凭据不留在任何镜像层"; \
    pip config unset global.index-url || true; \
    pip config unset global.trusted-host || true; \
    rm -f /root/.config/pip/pip.conf /root/.pip/pip.conf /etc/pip.conf; \
    if grep -rIlF -- "${PIP_REPO_PASS}" /root/.config /root/.pip /etc/pip.conf 2>/dev/null; then \
      echo "FATAL: 清理后仍检测到凭据残留" >&2; exit 1; \
    fi; \
    echo ">>> builder 阶段完成，凭据已清理"
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
