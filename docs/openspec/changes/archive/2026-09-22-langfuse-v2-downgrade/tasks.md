## 1. 前置调研与确认

- [x] 1.1 **已完成（实测）**：`langfuse/langfuse:2.95.12` **没有 Docker tag**（404，官方只发 GitHub release）；浮动 `:2` 解析为 **`2.95.11`**；`2.95.11` 是独立可锁 tag → **锁定 `langfuse/langfuse:2.95.11`**。注意 `docker manifest inspect` 在本机**不可用**（CLI 不读 daemon.json 加速器、直连 registry-1.docker.io 超时）；须走加速器自带鉴权端点（见 design D10#2）。需完全可复现时用 amd64 摘要 `sha256:e7aafd3ccf721821b40f8b2251220b4bb8af5e4877b5c5a8846af5b3318aaf1d`
- [x] 1.2 **v2 的监听地址**：已确认 v2 的 `HOSTNAME` **默认 `localhost`**（Next.js standalone 默认绑回环；官方示例须设 `HOSTNAME="0.0.0.0"`）→ **跨容器不可达**。因此这不是"待验证"，而是**必须在组 2/3 显式设 `HOSTNAME=0.0.0.0`**（对应 design D9#2，已由待验证升级为必设）
- [x] 1.3 `NEXTAUTH_URL`：已确认 `.env:56` 为 `http://localhost:3000`，即回环绑定下的"外部可见 URL"，符合 v2 要求（v2 要求该值是可被浏览器解析的对外地址，**不是**容器名）→ **无需改动**（对应 design D9#7，已解决）
- [x] 1.4 明确库重建的操作边界：先停 v3 栈再 drop；只 `DROP DATABASE`，**禁止** `docker compose down -v` / `docker volume prune`（会毁应用库与 `postgres_data` 卷）。**例外**：组 5 中显式 `docker volume rm` 已核实的孤儿卷是允许的（那是定向删除，不是 prune）
- [x] 1.5 收口 `architecture-review` 闸门结论（共四轮：round 1 Request changes → 7 处已修；round 2 Request changes → NF1 顺序/NF2 连接上下文/一致性问题；round 3 Request changes → NF-A design↔tasks 顺序矛盾、NF2 在 D4 的残留、NF-B 容器不存在、一致性问题；round 4 **Approve**）
- [x] 1.6 **前置动作：停 v3 栈（必须先于组 2/3 的 compose 改动）** —— `docker stop corporate-rag-langfuse-web corporate-rag-langfuse-worker corporate-rag-clickhouse || true`（**按容器名**，因为组 2/3 会把后两个服务名从 compose 删除，之后再按服务名 stop 会报未知服务）。**本机实测这三个容器根本不存在**（该 profile 从未启用），故实际为空操作——`|| true` 即为此准备。保留 `postgres` 运行

## 2. dev compose 改造（`docker-compose.yml`）

- [x] 2.1 `langfuse-web` 镜像 `langfuse/langfuse:3` → **`langfuse/langfuse:2.95.11`**（`:155`）
- [x] 2.2 **锚点取消（改为内联）**：worker 删除后 `langfuse-web` 成为**唯一**消费者；同一 mapping 内既定义 `&anchor` 又写 `<<: *anchor` 属**自引用、非法**（实测 `compose config` 报 `exceeds maximum node visit limit`）→ 把 `&langfuse-env` / `&langfuse-depends` 的内容**直接内联**进 `langfuse-web`。**注**：原计划写"迁到 web，否则 compose 校验失败"**立论过强** —— 顶层 `x-langfuse-env: &langfuse-env` + 服务内 `<<: *langfuse-env` 实测解析正常；单消费者下内联更简（少一层间接、语义不变）。该偏离已在 design D3 记明
- [x] 2.3 删除 `langfuse-worker` 服务（`:110-152`）
- [x] 2.4 `langfuse-web` 环境变量精简：删除 `CLICKHOUSE_*`（`:129-132`）、`REDIS_*`（`:133-135`）、`LANGFUSE_S3_*`（`:136-149`）、`LANGFUSE_ENABLE_BACKGROUND_MIGRATIONS`（`:167`）；保留 `DATABASE_URL`/`NEXTAUTH_URL`/`SALT`/`ENCRYPTION_KEY`/`LANGFUSE_INIT_*`
- [x] 2.5 `langfuse-web.depends_on` 只保留 `postgres: { condition: service_healthy }`
- [x] 2.6 删除 `clickhouse` 服务（`:58-84`）
- [x] 2.7 删除 `clickhouse_data` 卷定义（`:248-249`）与 keeper 配置挂载（`:73`）
- [x] 2.8 `langfuse-web` 端口 `3000:3000` → `127.0.0.1:3000:3000`
- [x] 2.9 确认保留不动：`profiles: ["langfuse"]`、`minio`、`redis`、`postgres`
- [x] 2.10 minio 启动命令去掉 `mkdir -p /data/langfuse`（v2 不再用 S3 事件存储；minio 本身与应用的 `documents` bucket 保留）
- [x] 2.11 `langfuse-web` 环境**新增** `HOSTNAME: "0.0.0.0"`（v2 默认绑回环，不设则 `app` 无法经 `langfuse-web:3000` 访问）
- [x] 2.12 `langfuse-web` 环境**新增** `LANGFUSE_INIT_PROJECT_ID` / `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`（值取自 `.env`）——使库重建后**现有 API Key 继续有效**；**注意官方 gotcha：这些值不要加双引号**
- [x] 2.13 **无法按 tag 锁定 → 改按 index 摘要锁定**（用户 2026-09-22 复议后决定「B：dev+prod 同锁」）：实测该 registry 的非签名 tag **只有 `latest` / `latest-dev`**（1000 个 tag 中其余全是 cosign 的 `.sig`/`.att`），没有任何版本 tag；`latest` 已在漂移（index 摘要 `sha256:a3c85091…` ≠ 本机早先拉取的 `sha256:a74b2956…`）。故锁 `cgr.dev/chainguard/minio@sha256:a3c85091…`（**index 摘要**，含 amd64/arm64；**不用平台摘要**，否则另一架构拉取失败）。代价：不再自动获得 Chainguard 的 CVE 重建，须人工 bump（步骤见 ADR-0011 复查条件⑥）

## 3. prod compose 改造（`docker-compose.prod.yml`）

- [x] 3.1 `langfuse-web` 镜像 `langfuse/langfuse:3` → **`:2.95.11`**（`:154`）
- [x] 3.2 同 2.2：锚点取消，`&langfuse-env` / `&langfuse-depends` 的内容内联进 `langfuse-web`
- [x] 3.3 删除 `langfuse-worker` 服务（`:112-152`）
- [x] 3.4 `langfuse-web` 环境变量精简（同 2.4 的 v3 专属项）
- [x] 3.5 `langfuse-web.depends_on` 只保留 `postgres(service_healthy)`
- [x] 3.6 删除 `clickhouse` 服务（`:63-86`）、`clickhouse_data` 卷定义与 keeper 配置挂载
- [x] 3.7 确认 `langfuse-web` 端口已是 `127.0.0.1:3000:3000`
- [x] 3.8 确认未向 prod 引入任何 `profiles:`（保持 prod 全服务常开，与 dev 的默认启用行为对齐）
- [x] 3.9 minio 启动命令去掉 `mkdir -p /data/langfuse`（与 dev 同步）
- [x] 3.10 `langfuse-web` 环境**新增** `HOSTNAME: "0.0.0.0"`（同 2.11）
- [x] 3.11 `langfuse-web` 环境**新增** `LANGFUSE_INIT_PROJECT_ID` / `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`（同 2.12，值不要加双引号）
- [x] 3.12 **既有缺陷顺带修复**：`docker-compose.prod.yml:88` 的 `minio/minio:latest` **已被上游删除镜像**（实测 404，MinIO 2026.09 删镜像、仓库已存档）→ 改为 `cgr.dev/chainguard/minio`（与 dev 一致）并**按同一 index 摘要锁定**，见 2.13

## 4. env 与 deploy 件

- [x] 4.1 `.env` 新增 `COMPOSE_PROFILES=langfuse`（实现"保留 profile + 默认开启"）
- [x] 4.2 `.env.template` 新增 `COMPOSE_PROFILES=langfuse`，删除 `CLICKHOUSE_PASSWORD`（`:90`），并**补齐漂移的键**：`LANGFUSE_INIT_*`、`LANGFUSE_ENCRYPTION_KEY`、`NEXTAUTH_URL`（对照 `.env.example` 齐平）；同时把 `LANGFUSE_HOST`（`:79`）由 `http://langfuse:3000`（**无此服务**）改为 `http://langfuse-web:3000`，并把 `LANGFUSE_ENABLE`（`:80`）由 `true` 改为 **`false`**（与 `.env:49`、本地兜底前提及 ADR-0010 出列一致）
- [x] 4.3 `.env.example` 补 `COMPOSE_PROFILES` 键名，并核对无残留 ClickHouse 键（注：其中只有 `LANGFUSE_INIT_ORG_*` / `PROJECT_NAME` / `USER_*`，**缺** `PROJECT_ID` / `PUBLIC_KEY` / `SECRET_KEY`，由 4.7 补）
- [x] 4.4 删除 `deploy/clickhouse/keeper_and_cluster.xml`（目录随之移除）
- [x] 4.5 `docker compose config` 校验 dev 与 prod 两份配置：无锚点悬空、无语法错误
- [x] 4.6 `.env` 新增 `LANGFUSE_INIT_PROJECT_ID` / `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY`，取值 = 现有 `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` 那对（`.env:46-47`）与一个固定的 project id —— 目的是让重建后凭据不变
- [x] 4.7 `.env.template` / `.env.example` 同步登记上述三个键名（占位值）

## 5. Langfuse 库重建与退役资源清理

- [x] 5.1 复核 Langfuse 侧确无在用数据（prompt 远端被 `LANGFUSE_ENABLE=false` 关闭、tracing 未接线且唯一消费点 `rag/stream.py:stream_answer` 无调用方、无 score/dataset）；**本机实测 langfuse 库 14MB、活跃连接数 0**；确认 prod 从未部署过 v3，故范围仅限 dev
- [x] 5.2 重建库（连**维护库**执行，避开"cannot drop the currently open database"——`langfuse` 角色的默认库正是待删库）：`docker compose exec postgres psql -U langfuse -d postgres -c "DROP DATABASE langfuse WITH (FORCE);"` 再 `-c "CREATE DATABASE langfuse OWNER langfuse;"`。**不经** `down -v` / `volume prune`
- [x] 5.3 清退役容器：`docker rm corporate-rag-langfuse-worker corporate-rag-clickhouse || true`（本机本就不存在，故为空操作）。若改用 `docker compose --profile langfuse down --remove-orphans`，须知 `down` 会停掉本项目**全部**容器（postgres/minio/redis/app/nginx；命名卷保留），需随后重新 `up -d`
- [x] 5.4 确认应用库 `corporate_rag` 与 `postgres_data` 卷未受影响（应用表可正常读写）
- [x] 5.5 **删除退役命名卷**（4 个，均已实测 `LINKS=0` 孤儿；用户 2026-09-21 决定直接删、不留回退）：`docker volume rm corporate_rag_clickhouse_data`（439MB）、`corporate_rag_chroma_data`（815kB）、`corporate_rag_chroma_onnx_cache`（0B）、`financial_qa_app_logs`（0B）。**严禁触碰** `corporate_rag_app_logs`（正被 `corporate-rag-app` 挂载）、`corporate_rag_postgres_data`、`corporate_rag_redis_data`、`corporate_rag_minio_data`
- [x] 5.6 删除 minio 的 `langfuse` 桶/目录（v2 不再用 S3 事件存储；`documents` 保留）。**注意**：本机实测该条目时间戳为 `0001-01-01`，疑似 entrypoint 的 `mkdir -p /data/langfuse` 产生的**普通目录**而非真实 bucket（v3 从未启用）→ 用 `mc rb --force`，或直接删目录并在验证时确认其不存在；`mc rb` 对不存在的 bucket 报错属预期

## 6. 验证

- [x] 6.1 启用 langfuse profile 起服务（**必须在组 2–5 全部完成之后执行；勿在"compose 已指向 v2 而库仍是 v3 schema"的中间态 `up -d`**），确认 v2 自动迁移在空库上从零完成，日志无 schema 不匹配或 `permission denied`
- [x] 6.2 确认 UI 本机可访问（`127.0.0.1:3000`），且非本机不可达
- [x] 6.3 确认容器集合只含 `langfuse-web`，不存在 `clickhouse` / `langfuse-worker`
- [x] 6.4 应用侧回归：发起一次对话，确认正常（`LANGFUSE_ENABLE=false` 走本地兜底），后端变更不影响应用。**实测在 `langfuse-web` 已停止的状态下完成** —— SSE 事件 `status → 33×token → model_info → agent_used → done`，**error 数 0**；这是"Langfuse 不在请求路径上"最强的实测证据
- [x] 6.5 质量门禁（**已收窄**）：`ruff check .` 无错误、`pyright src/` 无新增 error，且**全量测试结果与改动前逐项一致**（先取基线再对比）。**不要求绝对全绿**——本 change 不改 `src/`/`tests/`，且测试机 DB 用例历史上报 `gaierror`（环境问题，与本次无关）。
  **实测与定性的三个数字**：① 首轮 `33 failed / 65 errors` 中，**65 个 error 全是我调用漏了 `POSTGRES_HOST=localhost`**（宿主侧必需，见 glossary「DSN 单一来源」；漏设后 DSN 指向 compose 服务名 `postgres`，宿主解析不了 → `socket.gaierror`）；② 另有 **11 个 parser 用例失败是 worktree 缺夹具** —— 它们用相对路径读 `data/test_docs/*`，而本 worktree 的 `data/` 只 symlink 了 `ragas`（补 `test_docs` / `reports` 后 `tests/parsers/` → 46 passed）；③ 余下 **9 failed + 2 errors 是 dev-wsl 既有失败**。**判据**：同一用例集在 worktree 与主工作区各跑一次，结果**逐项一致（9 failed / 11 passed / 2 errors）**；`src/`+`tests/` 与 dev-wsl 亦逐字节一致 → **零回归**
- [x] 6.6 内存实测：测试机 3.8 GB 下运行该后端的余量可接受。**实测 `langfuse-web` 空载约 199 MiB，在 `256m` 上限下已达 78%、余量不足** → 已把 dev 上调为 `mem_limit: 512m` / `mem_reservation: 256m`（复测 149 MiB / 512 MiB = 29.1%，`OOMKilled=false`），并同步修正 spec 的「单元资源上限」断言 —— 原先写死的 `256m` 是未经测量的取值
- [x] 6.7 可关闭性验证：置空 `COMPOSE_PROFILES` 后 `langfuse-web` 不启动、应用仍可完成对话
- [x] 6.8 跨容器可达性验证：从 `app` 容器确认能访问 `http://langfuse-web:3000`（验证 2.11/3.10 的 `HOSTNAME=0.0.0.0` 确实生效）
- [x] 6.9 **端到端写入冒烟**（证明后端真的"可用"而非只是进程起来了）：用现有 `LangfuseTracer` **裸发一条 trace**（不经应用路径、不改 `src/`），在 UI 的 Traces 里确认可见，随后丢弃。**脚本要点**：容器内 `python -c` 显式 `current_trace_id.set(<uuid>)`——`start_trace`/`end_trace` 都读该 ContextVar，不设会各读 `None` 而产出**两条** trace；结束前 `flush`，否则批次事件可能暂不可见。**实测已写入 `trace_smoke_1790014782`（`name=v2_smoke_verify`、`output="smoke ok"`），在 langfuse 库 `traces` 表中可查** —— 证明 v2 后端的写入链路端到端可用，而不只是"进程起来了"
- [x] 6.10 **凭据连续性验证**：确认 UI 里的 project 仍持有 `.env` 中那对 `pk-lf-…`/`sk-lf-…`（即 `LANGFUSE_INIT_PROJECT_*` 生效，无需重签 key）
- [x] 6.11 **prod 静态验收**（prod 从未部署，无运行环境）：`docker compose -f docker-compose.prod.yml config` 通过，且与 dev 逐项同构对照（镜像 tag、env 差异项、端口、无 `profiles:`）。**前置**：prod compose 用 `${MINIO_ROOT_USER:?}` 等必填插值，而 `.env` 只有 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`（实测当前直接跑 `config` 即 exit 1）→ 须用独立 `--env-file` 或临时环境提供缺失的 prod-only 变量，再执行校验
- [x] 6.12 **镜像可拉取性验证**：`docker pull langfuse/langfuse:2.95.11` 成功（实测 41s）；`cgr.dev/chainguard/minio` 可拉 —— 该 registry **无版本 tag**，故按 **index 摘要**锁定（见 2.13），拉取时须验证**目标架构**存在。`cgr.dev` 不被加速器代理、是直连，**prod 机器可达性待真部署前实测**。
  **实测**：dev 已重建到该摘要 —— 容器 `ImageID` / `Config.Image` 均为 `sha256:a3c85091…`（容器内 minio 由 `RELEASE.2026-06-04` 变为 `RELEASE.2026-09-21`）；重建前后 `documents` 桶均为 **`8.0MiB / 34 objects`** 且应用健康正常 → **数据面无损**，"dev 验过的 = prod 跑的"成立

## 7. 文档与 ADR

- [x] 7.1 新增 ADR：可观测后端由 v3 切至 v2 的决策、EOL 的显式接受与升级通道的保留（不改旧 ADR；ADR-0004 中"trace 在 ClickHouse"已失真，按"只追加"处理）。**须记明**：prod 侧未运行验证；凭据经 `LANGFUSE_INIT_PROJECT_*` 保持连续
- [x] 7.2 更新 `docs/agents/code-map.md:16,26` 的服务清单与 `deploy/` 树（移除 clickhouse）
- [x] 7.3 在 `docs/langfuse-v3-vs-v2-and-clickhouse-memory.md` 标注其已被本决策取代（冻结分析，不回写数字）
- [x] 7.4 在 `docs/tmp/deep-research-langfuse-postgres-only.md` 标注其"v2 路线"结论已被采纳（同时记录其"不建议"意见已被显式权衡）
- [x] 7.5 在新 ADR 的复查条件里登记触发点：① trace 量级超过既定阈值时评估 v3/v4 迁移；② Langfuse 首次被真正接入请求路径（prompt 远端读取开启或 tracing 接线）时重新评估本次降级决策；③ **tracing 接线的那次变更必须一并落地 trace 保留/清理机制**（承接 design D7 的残留）；④ `src/config/settings.py:305` 的 `LANGFUSE_HOST` 陈旧默认值另行清理；⑤ **MinIO 上游已存档、镜像已删** → 需另立议题替换 S3 实现（Garage / RustFS / VersityGW / SeaweedFS 等），`cgr.dev/chainguard/minio` 只是权宜
- [x] 7.6 在 `docs/agents/glossary.md` 登记本变更引入的术语：「可观测后端」（自托管 Langfuse 后端及其部署形态）、「凭据播种」（`LANGFUSE_INIT_PROJECT_*` 保持 key 连续）；「trace 保留窗口」标注为另案
