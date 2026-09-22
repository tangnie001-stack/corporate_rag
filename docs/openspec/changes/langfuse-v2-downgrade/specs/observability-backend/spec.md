## ADDED Requirements

### Requirement: 服务端版本与必需组件集

系统 SHALL 以 **`langfuse/langfuse:2.95.11`** 作为自托管可观测后端（v2 线的最后可拉取版本；`2.95.12` 只有 GitHub release、**没有 Docker tag**，浮动 `:2` 现亦解析到 `2.95.11`）。部署 SHALL NOT 包含 ClickHouse、Langfuse worker，以及供 Langfuse 使用的 S3/MinIO 事件存储；后端 SHALL 仅由 Langfuse web 容器与本项目既有 PostgreSQL 构成。

#### Scenario: compose 中不存在 v3 专属组件

- **WHEN** 检查 `docker-compose.yml` 与 `docker-compose.prod.yml` 的服务集合
- **THEN** 不存在 `clickhouse` 与 `langfuse-worker` 服务
- **AND** `langfuse-web` 的镜像 tag 为 `2.95.11`
- **AND** 该镜像引用在既定拉取通道内**可解析**（实测可拉取）
- **AND** `langfuse-web` 的环境变量不含 `CLICKHOUSE_*`、`LANGFUSE_S3_*`、`LANGFUSE_ENABLE_BACKGROUND_MIGRATIONS`

#### Scenario: v2 专属 env 齐备

- **WHEN** 检查 `langfuse-web` 的环境变量
- **THEN** 含 `DATABASE_URL`、`NEXTAUTH_URL`、`NEXTAUTH_SECRET`、`SALT`、`ENCRYPTION_KEY`
- **AND** 含 `HOSTNAME=0.0.0.0`（v2 镜像不设该值且 Next.js standalone 默认绑回环）
- **AND** 保留了 `LANGFUSE_INIT_*` 以便库重建后重新初始化组织/项目/账号，其中**含** `LANGFUSE_INIT_PROJECT_ID`、`LANGFUSE_INIT_PROJECT_PUBLIC_KEY` 与 `LANGFUSE_INIT_PROJECT_SECRET_KEY`

### Requirement: 存储复用既有 PostgreSQL

Langfuse SHALL 复用应用所在的同一 PostgreSQL 实例的独立 database（承接 ADR-0004「一个实例、两个 database」），SHALL NOT 引入 ClickHouse、独立 Redis 或对象存储作为其后端依赖。

#### Scenario: 只依赖 postgres

- **WHEN** 启动 `langfuse-web`
- **THEN** 其 `depends_on` 仅包含 `postgres`（`service_healthy`）
- **AND** 其 `DATABASE_URL` 指向既有 `postgres` 服务上的 Langfuse database

#### Scenario: 数据库 schema 与 v2 一致

- **WHEN** `langfuse-web` 首次启动并执行自动迁移
- **THEN** 迁移在空的 Langfuse database 上从零完成
- **AND** 日志中不出现迁移历史不匹配或 `permission denied` 类错误

### Requirement: 单元资源上限

`langfuse-web` SHALL 声明显式 `mem_limit`：dev 为 `512m`（并带 `mem_reservation: 256m`），prod 为 `2g`。dev 取值须相对**实测空载占用**留有可测余量（v2 首次启动后实测约 199 MiB，即 `256m` 上限下的 78%，余量不足）。

> **实施后修订（记例外，非静默删除）**：本 requirement 获批时末尾还有一条 `- **AND** 两者的取值不因移除 ClickHouse 而上调`。实施中实测 `256m` 下空载即占 78%、余量不足，故把 dev 上调为 `512m` —— 这是对那条禁令的**显式例外**，而非把它删掉不提。例外成立的依据：本次上调的净效应仍是宿主内存占用**下降**（同时移除 clickhouse 与 worker 两个容器，其 dev 上限合计 512m），且 `mem_limit` 是硬上限、不预占。

#### Scenario: 内存上限为确定值

- **WHEN** 检查 `docker-compose.yml` 与 `docker-compose.prod.yml` 中 `langfuse-web` 的服务定义
- **THEN** dev 的 `mem_limit` 为 `512m` 且带 `mem_reservation`
- **AND** prod 的 `mem_limit` 为 `2g`

### Requirement: EOL 版本的暴露面收敛

因 Langfuse v2 已 End of life、不再有安全补丁承诺，其 web 端口 SHALL 仅绑定回环地址（`127.0.0.1`），SHALL NOT 对外网可达。

#### Scenario: 端口仅回环

- **WHEN** 检查 dev 与 prod compose 中 `langfuse-web` 的 `ports` 映射
- **THEN** 两处均形如 `127.0.0.1:<port>:<port>`
- **AND** 不存在形如 `<port>:<port>` 的全网卡绑定

### Requirement: 跨容器可达

Langfuse web SHALL 在网络内可被其他容器经服务别名访问（`http://langfuse-web:3000`），SHALL NOT 仅绑定容器内回环。

#### Scenario: app 可达后端

- **WHEN** 从 `app` 容器请求 `http://langfuse-web:3000`
- **THEN** 得到 HTTP 响应（而非连接失败）

### Requirement: 凭据连续性

Langfuse database 重建后，`.env` 中的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` SHALL 仍然有效。系统 SHALL 经 `LANGFUSE_INIT_PROJECT_ID` 与 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY` 播种既有 key，SHALL NOT 要求重新签发。

播种 SHALL 依赖三者齐备：`LANGFUSE_INIT_PROJECT_ID` 是载体，缺它时 project 与 key 均不创建（且不报错），凭据连续性即不成立。

#### Scenario: 重建后 key 不变

- **WHEN** 库重建并首次启动 v2，且 `LANGFUSE_INIT_PROJECT_ID` 与 keys 对均已配置
- **THEN** UI 中的 project 持有与 `.env` 相同的 key（secret 在 UI 中为掩码显示，以前缀/后缀核对）
- **AND** 用该 key 可成功完成一次写入

#### Scenario: 缺 PROJECT_ID 则凭据失效（负例）

> **前提**：**空库首次启动**（既有 project 时，缺 `PROJECT_ID` 不会使已存在的 key 失效）。
> **依据**：上游 `initialize.ts` 的嵌套判断；**未做运行期复现** —— 复现需移除播种键并重启，与正例互斥。

- **WHEN** 在空库上首次启动，仅配置 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY` 而缺 `LANGFUSE_INIT_PROJECT_ID`
- **THEN** project 与 key 均不被创建，`.env` 中的 key 失效

### Requirement: 退役资源不遗留

组件退役后 SHALL NOT 留下其运行期资源（命名卷、对象存储桶）；同时 SHALL NOT 删除仍在使用的资源。

#### Scenario: 退役资源已清而在用资源完好

- **WHEN** 迁移完成后检查命名卷与 MinIO 桶
- **THEN** 不存在 `corporate_rag_clickhouse_data`、`corporate_rag_chroma_data`、`corporate_rag_chroma_onnx_cache`、`financial_qa_app_logs`，也不存在 MinIO 的 `langfuse` 桶
- **AND** 仍存在 `corporate_rag_app_logs`、`corporate_rag_postgres_data`、`corporate_rag_redis_data`、`corporate_rag_minio_data` 与 `documents` 桶

### Requirement: dev 默认启用且可关闭

dev 环境 SHALL 默认启用 langfuse profile（无需显式传入 `--profile`）；同时 SHALL 保留通过 profile 关闭该后端的能力，关闭后应用其余部分仍可用。

#### Scenario: 默认启用

- **WHEN** 在 dev 执行 `docker compose up -d` 且未显式指定 profile
- **THEN** `langfuse-web` 被启动

#### Scenario: 可关闭且不影响应用

- **WHEN** 覆盖 profile 使其不激活（如将 `COMPOSE_PROFILES` 置空）
- **THEN** `langfuse-web` 不启动
- **AND** 应用仍可正常完成对话（prompt 走本地兜底）
