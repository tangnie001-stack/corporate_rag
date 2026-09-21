## Why

自托管 Langfuse v3 把 ClickHouse、独立 worker、S3 事件存储都变成了**启动硬依赖**（官方 FAQ：所有自托管部署都必须包含 ClickHouse 实例）。对一个初期量级的部署来说，这是在为用不到的能力维护一整套有状态服务，而收益从未兑现——v3 profile 长期没启，**tracing 也一直没接线，当前根本不产出 trace**（规模判断见 design D1）。

v2 是最后一个「只需 PostgreSQL 即可自托管」的大版本。项目的 PostgreSQL 已经在跑，且按 ADR-0004 是「一个实例、两个 database（应用 + Langfuse）」，所以 v2 不需要任何新存储。降级的可行性取决于一个前提：**本仓对 Langfuse 的真实在用面只有 prompt 管理**——ADR-0010 已**决策**将其出列，但代码落地归属在途 change `prompt-layering-and-domain-binding`；当前运行安全靠 `.env` 的 `LANGFUSE_ENABLE=false` 走本地兜底。加上 SDK 早已 pin 在 Python SDK v2（`pyproject.toml:21`），v2 与本仓的能力面完全对齐，且 `src/` 代码零改动。

代价是显式的：**v2 线已被官方标注 End of life**（最后一个可拉取版本是 `2.95.11`）。本变更以「收敛暴露面 + 保留升级通道」来对冲，而不是否认它。

## What Changes

- **服务端版本**：`langfuse/langfuse:3` → **`langfuse/langfuse:2.95.11`**（dev 与 prod 两份 compose 同构改动）。**注**：最终版 `2.95.12` **只有 GitHub release、没有 Docker tag**（实测 404）；浮动 `:2` 现解析为 `2.95.11`，故锁 `2.95.11` 更可复现。
- **删除 v3 独有组件**：
  - `langfuse-worker` 服务（v2 无 worker）——其承载的 compose 锚点 `&langfuse-env` / `&langfuse-depends` 迁到 `langfuse-web`。
  - `clickhouse` 服务 + `clickhouse_data` 卷 + `deploy/clickhouse/keeper_and_cluster.xml`。
  - `langfuse-web` 的 v3 专属 env：`CLICKHOUSE_*` / `REDIS_*` / `LANGFUSE_S3_*` / `LANGFUSE_ENABLE_BACKGROUND_MIGRATIONS`。
- **保留不变**（它们不是 Langfuse 专属）：`minio`（同时是应用文档存储，`src/infra/db/file_store.py`）、`redis`（应用会话锁/流式状态）、`postgres`（应用库）。
- **BREAKING（数据）**：现有 Langfuse database 装的是 v3 的 Prisma schema，而 Prisma 迁移单向、无 downgrade 路径，v2 无法在其上迁移 → **直接 drop 重建空库**。Langfuse 侧数据不保留。已确认无在用数据：prompt 远端读取被 `.env` 的 `LANGFUSE_ENABLE=false` 关闭（ADR-0010 已决策出列，代码落地在在途 change）、tracing 未接线（`@traced` 无应用点，且唯一消费点 `rag/stream.py:stream_answer` 无任何调用方）、无 score/dataset。**prod 从未部署过 v3**，故丢失范围仅限 dev。
- **暴露面收敛**：`langfuse-web` 端口改为仅绑回环（dev `3000:3000` → `127.0.0.1:3000:3000`；prod 已是回环）——EOL 版本不应对外可达。
- **跨容器可达**：v2 镜像不设 `HOSTNAME`、Next.js standalone 默认绑回环 → 必须显式设 `HOSTNAME=0.0.0.0`，否则 `app` 无法经 `langfuse-web:3000` 访问（且失败会静默走本地兜底）。
- **凭据连续性**：库重建会清掉旧 Langfuse 数据，但 v2 支持经 `LANGFUSE_INIT_PROJECT_ID`（载体）+ `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `SECRET_KEY` 直接播种 key → 现有 `.env` 那对 key **继续有效**，无需重新签发。**缺 `PROJECT_ID` 时 project 与 key 都不创建且不报错**（见 design D9#2b）。
- **退役资源清理**：删除四个已核实为孤儿的命名卷（`corporate_rag_clickhouse_data` 439MB、`corporate_rag_chroma_data`、`corporate_rag_chroma_onnx_cache`、`financial_qa_app_logs`）与 MinIO 的 `langfuse` 桶；**不动**在用的 `corporate_rag_app_logs` 与 `postgres_data`/`redis_data`/`minio_data`。（用户 2026-09-21 决定直接删、不留回退）
- **顺带修复的既有缺陷：prod 的 MinIO 镜像引用已失效**。`docker-compose.prod.yml:88` 用的 `minio/minio:latest` **已被上游删除镜像（实测 404）**——MinIO 于 2025.06 删 Web 管理界面、2025.12 进维护模式、2026.02 仓库不再维护、**2026.09 删除 Docker 镜像**，GitHub 仓库已存档（最后版本 `RELEASE.2025-04-22T22-12-26Z`）。本变更把 prod 改为与 dev 一致的 `cgr.dev/chainguard/minio`（Chainguard 自建的免费加固版，实测匿名可拉）。**须写明：这是既有缺陷被顺带修掉，非本变更引入**。
- **默认可用**：保留 `profiles: ["langfuse"]` 不删（仍可切换关闭），在 `.env` 设 `COMPOSE_PROFILES=langfuse`，使 dev `docker compose up -d` 默认启用；prod 本无 profile 门，天然默认启用。
- **明确不做**：v2 → v3/v4 的升级（另案，走独立分支；升级时需经 `v3.29.0` 中转并搬运 trace）。
- **未变**：`src/` 无代码改动。SDK 已是 v2；prompt 走 HTTP `/api/public/v2/prompts/{name}`（v2 线具备该端点）；tracing 封装使用 SDK v2 低层 API（legacy ingestion，v2/v3 均支持）；`LANGFUSE_HOST=http://langfuse-web:3000`（`.env:48`）不变。

## Capabilities

### New Capabilities

- `observability-backend`: 自托管可观测后端的部署形态与运维约束——服务端版本与组件集（v2 / 仅 web + 复用 PG）、单元资源上限、EOL 版本的暴露面收敛、跨容器可达、凭据连续性、退役资源不遗留、dev 默认启用与可关闭性。**trace 保留/清理不在本能力内**（见 design D7：本次不做）。

### Modified Capabilities

<!-- 无。既有两个提及 Langfuse 的能力其 requirement 均与版本无关：
     - agent-loop-observability:21 「无 Langfuse 也能从日志还原」——讲日志自足性
     - prompt-composition:22,77 「Langfuse 拉取或本地兜底」「不因远端改动而消失」——讲 prompt 层级与兜底
     均不随服务端版本变化。 -->

## Impact

- **部署件**：`docker-compose.yml`、`docker-compose.prod.yml`、`.env`、`.env.template`、`.env.example`（加 `COMPOSE_PROFILES` 与 `LANGFUSE_INIT_PROJECT_*`、补齐 `LANGFUSE_*` 漂移键；`CLICKHOUSE_PASSWORD` 仅存在于 `.env.template:90`，删它）；删除 `deploy/clickhouse/`。
- **镜像引用**：langfuse 由 `:3` 改为 `:2.95.11`；prod 的 MinIO 由失效的 `minio/minio:latest` 改为 `cgr.dev/chainguard/minio`。**拉取前提**：本项目依赖 `/etc/docker/daemon.json` 中的 3 个国内加速器（`auth.docker.io` 与 `registry-1.docker.io` 直连不通），Hub 镜像须经加速器拉取——该前提应写进部署文档。
- **数据**：drop + recreate Langfuse database（Langfuse 侧数据丢失，应用库不受影响）。**prod 从未部署过 v3**，丢失范围仅限 dev。
- **既有资源的清理（范围外扩）**：本变更顺带删除 4 个孤儿命名卷 + 1 个 MinIO 桶（其中 `chroma_*` / `financial_qa_app_logs` 来自更早的 Chroma/旧项目退役，与 Langfuse 无关），另修复 prod 的失效 MinIO 引用。这几项是超出"Langfuse 退役"本身的动作，均**属既有缺陷的顺带修复**。
- **文档**：新增 ADR（ADR-0004「trace 在 ClickHouse」将失真，按规则只追加不改旧）；`docs/agents/code-map.md:16,26` 服务清单与 deploy 树；`docs/agents/glossary.md` 登记「可观测后端」「凭据播种」等新术语；`docs/langfuse-v3-vs-v2-and-clickhouse-memory.md` 与 `docs/tmp/deep-research-langfuse-postgres-only.md` 标注被取代（冻结分析，不回写）。
- **残留风险**：`2.95.11` 为 EOL 线，不再有安全补丁承诺；未来升级需经 `v3.29.0` 中转、跨两个大版本，且 v2 期间累积的 trace 不会自动上行（升级时应显式选择丢弃）；`clickhouse_data` 卷删除后，回退 v3 的数据面依据一并消失（用户已接受）；**MinIO 上游已存档，`cgr.dev/chainguard/minio` 是第三方加固重建，长期需另立议题换 S3 实现**。
- **未验证项**：dev 内存余量、prod 侧无运行验证、prod 机器能否直连 `cgr.dev`（该 registry 不被国内加速器代理）。
- **不动**：`src/`、`tests/`、`pyproject.toml`。
