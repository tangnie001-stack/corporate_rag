# Langfuse「只用 PostgreSQL」自托管可行性 — 一手来源研究

生成日期：2026-09-18 ｜ 研究范围：Langfuse 服务端自托管存储依赖、v2/v3/v4 分界、SDK 兼容、托管 Postgres、升级路径、替代方案，以及本仓库实际用到的 Langfuse 能力。
来源约定：**一手确认** = Langfuse/LangChain/Arize/MLflow 官方文档、官方 GitHub release/源码、或本仓 `文件:行号`；**推断** = 由一手事实经推理得出；**未确认** = 仅有二手来源或无法核实。

---

## 0. 结论摘要（先给答案）

**问题：是否存在「只用 PostgreSQL 且可用」的 Langfuse 服务端版本？**

**是，存在：Langfuse v2（最后一个版本 `v2.95.12`，发布 2025-11-18）。** v2 自托管架构只有 Web 容器 + PostgreSQL（可选 LLM API），不需要 ClickHouse / Redis / S3【一手，见 §1】。

**但代价是三重的：**

1. **v2 已被官方标记为 End of life**。官方版本兼容矩阵里 OSS v2 状态为 `End of life`，OSS v3 为 `Deprecated`，OSS v4 为 `GA`【一手，§2】。官方 v2 文档写的是「安全更新至 2025 年 Q1 末」，但 v2 分支在 2025-11 仍发过补丁（含安全升级）——官方自身表述与发布记录不一致，见 §2，此处如实标注。
2. **SDK 被锁死在 Python SDK v2**。官方矩阵明确：Python SDK v3 / v4 **不支持** OSS v2 服务端【一手，§4】。本仓已 pin 在 Python SDK v2（`langfuse>=2.60.0,<3.0.0`），所以当前能无缝切到 v2 服务端；但以后想用新 SDK 就必须离开 v2 服务端。
3. **未来升级必然引入 ClickHouse + Redis + S3**。v2→v3 升级是官方一等路径，但步骤第一步就是「备齐 ClickHouse / Redis / S3」；且当前最新是 v4，等于要跨两个大版本迁移【一手，§6】。

**对本项目的关键发现（决定 v2 够不够用）：**

- 本仓对 Langfuse 的**真实、在用**能力只有一项：**prompt 管理**（3 个 prompt，通过 HTTP 直连 `/api/public/v2/prompts/{name}` 拉取，不依赖 SDK 版本）【一手，§8】。
- **tracing 代码已实例化但当前没有调用点**：`LangfuseTracer` 在 `agent_service.py:759` 被创建后无任何调用；`@traced` 装饰器（`langfuse_tracing.py:34`）在本仓除定义与 docstring 示例外**无应用点**，而 `current_tracer` 只在该装饰器内置值（`langfuse_tracing.py:66`），因此 `rag/stream.py:45` 取到的恒为 `None`【一手（静态代码判断），未做运行时验证】。
- 未发现任何 score / dataset / evaluation 的 Langfuse 用法（`datasets` 是 HuggingFace 的，用于 RAGAS，与 Langfuse 无关）【一手，§8】。

**因此：v2 的功能集对本项目完全够用——v2/v3 的功能差异不是阻碍；真正的阻碍是 v2 的 EOL 状态与 SDK/升级锁定。**

**如果不想接受 EOL，最接近的可行路线是保留 Langfuse v3/v4 但把 ClickHouse 改为托管**（阿里云有云数据库 ClickHouse），代价是「少一个自建组件、仍多一个服务」；若目标只是彻底消掉 ClickHouse，另一条更省的路是**换用只依赖 PG 的可观测方案**（Arize Phoenix / MLflow 3）或**干脆去掉 Langfuse**（本仓 prompt 已自带本地兜底）。详见 §7、§9。

---

## 1. 版本分界：哪个版本开始强制 ClickHouse

### 1.1 分界点

**Langfuse v3 是分界版本，发布于 2024-12-06。** 官方 v2→v3 升级指南原文（中文页）：「Langfuse v3（于 2024 年 12 月 6 日发布）引入了全新的后端架构……为了实现这一规模，我们引入了第二个 Langfuse 容器以及 S3/Blob 存储、Clickhouse 和 Redis 等额外的存储服务，**这些服务比我们之前的基于 Postgres 的设置更适合所需的负载**。」
来源：https://langfuse.com.cn/self-hosting/upgrade/upgrade-guides/upgrade-v2-to-v3 （一手，官方文档）

即：**v2 是「基于 Postgres 的设置」；v3 起引入 ClickHouse/Redis/S3。** 这与你的怀疑（v2 系列）一致。

### 1.2 v2 自托管依赖清单

官方 v2 自托管架构图（https://langfuse.com/self-hosting/v2 ，一手）：

```
Web Server (langfuse/langfuse) ──► Postgres Database
                                 ┄┄► LLM API（可选，仅 playground 用）
```

v2 分支的官方 `docker-compose.yml`（https://raw.githubusercontent.com/langfuse/langfuse/v2/docker-compose.yml ，一手）只有两个 service：

- `langfuse-server`（镜像 `langfuse/langfuse:2`）
- `db`（镜像 `postgres:17`）
- 环境变量只有 `DATABASE_URL` / `NEXTAUTH_SECRET` / `SALT` / `ENCRYPTION_KEY` / `NEXTAUTH_URL` / `TELEMETRY_ENABLED` / `LANGFUSE_INIT_*`

**没有 redis、没有 clickhouse、没有 minio。**

### 1.3 v3/v4 自托管依赖清单

官方架构（https://langfuse.com/self-hosting ，一手）要求：

- 应用容器：Langfuse **Web** + Langfuse **Worker**（v3 新增）
- 存储：**Postgres**（事务数据）+ **ClickHouse**（OLAP，存 traces/observations/scores）+ **Redis/Valkey**（队列与缓存）+ **S3/Blob**（原始事件、多模态附件）

ClickHouse 官方 FAQ（中文页，一手）对「是否必需」给了明确答复：

> 「**是的，ClickHouse 目前是自托管 Langfuse 的必需组件。目前不支持其他 OLAP 数据库。** 如果没有将 ClickHouse 作为跟踪、观察和评分的主要存储解决方案，Langfuse 无法进行自托管。**所有自托管部署都必须包含一个 ClickHouse 实例。**」

来源：https://langfuse.com.cn/self-hosting/deployment/infrastructure/clickhouse

### 1.4 依赖对照表

| 组件 | v2 | v3 | v4 | 依据 |
|---|---|---|---|---|
| Postgres | **必需** | 必需 | 必需 | 官方架构图（v2 / 当前） |
| ClickHouse | **不需要** | **必需** | **必需** | v2 架构图无；v3+ FAQ 明确「必需」 |
| Redis/Valkey | **不需要** | **必需** | **必需** | v2 compose 无；v3 升级指南列入必需 env |
| S3/Blob | **不需要** | **必需** | **必需** | 同上（`LANGFUSE_S3_EVENT_UPLOAD_BUCKET` 缺失则部署失败） |
| 第二应用容器（worker） | 不需要 | 必需 | 必需 | v3 升级指南 |
| 可选 LLM API | 可选（playground） | 可选（eval） | 可选 | 两版架构图 |

**结论（一手确认）：v2 是最后一个「只用 PostgreSQL 即可自托管」的大版本；v3 起 ClickHouse 为硬依赖。**

> 注：v2 迁移目录里也有一条 `20241106122605_add_media_tables`（v2.95.12 的 prisma 迁移，见 §3），但官方功能矩阵把「Media（multimodal）」标为「Requires OSS v3」——即 v2 有表结构、无 v3 的多模态能力。不影响「v2 只需 PG」的结论。

---

## 2. v2 是否还可用：最后版本、EOL、安全支持

### 2.1 最后的 v2 版本

**`v2.95.12`，published_at = 2025-11-18T16:14:42Z（GitHub API 一手）。**
来源：https://api.github.com/repos/langfuse/langfuse/releases/tags/v2.95.12

- `target_commitish` 显示该次发布的基线是 `main`（不是 `v2` 分支），body 为 `fix: use nextauth fallback for checks (#10464)`。
- v2 分支 `package.json` 的 `version` 字段为 `2.95.12`，与之一致（https://raw.githubusercontent.com/langfuse/langfuse/v2/package.json ，一手）。
- GitHub releases 按 `q=v2.95` 过滤，列表最高项即 v2.95.12（一手）。

v2 分支后期发布记录（一手，release 页/API）：

| 版本 | 日期 | 内容性质 |
|---|---|---|
| v2.95.7 | 2025-03-27 | `security: upgrade next` |
| v2.95.8 | 2025-04-06 | `security: upgrade nextjs 14.2.26` |
| v2.95.9 | 2025-06-13 | `security: upgrade nextjs` |
| v2.95.10 / v2.95.11 | 2025-11-07 | 修复路由 / 测试 |
| **v2.95.12** | **2025-11-18** | nextauth fallback 修复（**最后一个 v2**） |

### 2.2 官方支持状态

| 来源 | 原文 | 判定 |
|---|---|---|
| 官方版本兼容矩阵 https://langfuse.com/self-hosting/upgrade/versioning | `OSS v2 (End of life)` / `OSS v3 (Deprecated)` / `OSS v4 (GA)` | **一手：v2 = EOL** |
| v2 自托管文档 https://langfuse.com/self-hosting/v2 | 「Langfuse v2 received security updates **until end of Q1 2025**.」 | 一手：声明的安全支持已于 2025Q1 结束 |
| v2 部署指南 https://langfuse.com/self-hosting/v2/deployment-guide | 同上语句 | 一手 |
| GitHub releases | v2.95.7~v2.95.12 在 2025-03 ~ 2025-11 之间仍有发布 | 一手：实际有零星 backport |

**官方表述含糊/自相矛盾之处（如实标注，不替官方下结论）：**
官方文档声明「安全更新至 2025Q1 末」，但 v2 分支在 2025-04、06、11 仍发布了包含 `security:` 前缀的补丁（v2.95.8/9，及 11 月的 v2.95.10~12）。这两条一手事实互相矛盾：可能是文档未更新，也可能是「非承诺性的零星修复」。**无法从一手源确定 2025-11 之后的 v2 是否还有安全补丁承诺；按官方矩阵，v2 已被明确标为 End of life，不应假设继续供货。**

### 2.3 自托管文档是否保留 v2 章节

**保留。** 官方仍提供 v2 专区：https://langfuse.com/self-hosting/v2 、`/self-hosting/v2/deployment-guide`、`/self-hosting/v2/docker-compose`（一手，页面可访问）。顶部有升级提示，指向 v3 升级指南。v3 也保留专区（`self-hosting` 当前主文档已到 v4）。

---

## 3. 功能差异：v2 vs v3（prompt / trace / eval）

### 3.1 Prompt 管理（项目最关心）

**v2 服务端已具备版本 + label 的 prompt 管理**，有一手证据（v2.95.12 的 Prisma 迁移目录，https://api.github.com/repos/langfuse/langfuse/contents/packages/shared/prisma/migrations?ref=v2.95.12 ）：

| 迁移 | 含义 |
|---|---|
| `20231230151856_add_prompt_table` | v2 已有 prompt 表 |
| `20240219162415_add_prompt_config` | prompt 配置 |
| `20240405124810_prompt_to_json` | prompt 结构升级 |
| `20240408134328_prompt_table_add_tags` | tags |
| `20240429124411_add_prompt_version_labels` | **版本 label** |
| `20240429194411_add_latest_prompt_tag` | latest 标记 |

且项目使用的 HTTP 端点**在 v2.95.12 就存在**：`web/src/pages/api/public/v2/prompts/[promptName].ts` 与 `index.ts`（https://api.github.com/repos/langfuse/langfuse/contents/web/src/pages/api/public/v2/prompts?ref=v2.95.12 ，一手）。

Prompt 管理首次对外发布时间约 2024-09（官方 changelog `2024-09-04-prompt-management-zero-latency.mdx`，一手），处于 v2 服务端周期内。

→ **推断（可靠）：v2 服务端支持 prompt 管理、版本、label、按 label/version 拉取。本仓只取 latest，v2 完全覆盖。**

### 3.2 Trace / Span / Generation

| 能力 | OSS v2 | OSS v3 | 依据 |
|---|---|---|---|
| legacy batch ingestion（`/api/public/ingestion`，Python SDK v2 用的路径） | 支持 | 支持 | 官方矩阵（一手） |
| trace/observation/score 查询 | 支持 | 支持 | 官方矩阵 |
| 多模态 media | **不支持** | 支持 | 官方矩阵「Media (multimodal) Requires OSS v3」 |
| OpenTelemetry 摄入 | 不支持 | 支持（≥3.22.0） | 官方矩阵 |

### 3.3 评估（scores / datasets）

| 能力 | OSS v2 | OSS v3 | 依据 |
|---|---|---|---|
| Datasets | 支持 | 支持 | 官方矩阵（experiments 需 SDK v4） |
| 直接 scores 摄入 `/api/public/scores` | 支持 | 支持 | 官方矩阵 |
| 服务端 LLM-as-a-Judge evaluator / 实验（experiments） | 弱/无 | v3 起逐步提供，experiments 需 Python SDK v4 | 官方矩阵 |
| Scores API v3（`/api/public/v3/scores`） | 不支持 | 支持 | 官方矩阵 |

### 3.4 v3/v4 独有、而 v2 没有的关键能力（与本项目的相关性）

- 多模态 traces、OTel 实时摄入、Scores API v3、服务端 evaluators/experiments、ClickHouse 上的高性能分析。
- **本仓一项都没用到**（§8）：无 score、无 dataset、无多模态、无 OTel、无 evaluator。

---

## 4. SDK 兼容矩阵

### 4.1 官方规则

官方 https://langfuse.com/self-hosting/upgrade/versioning （一手）原文规则：

> 「each server major version aims to support the current and the previous SDK major version of each language, and each SDK major requires a minimum server version.」

官方兼容矩阵（一手，同页）：

| Python SDK | OSS v2 | OSS v3 | OSS v4 | SDK 状态 |
|---|---|---|---|---|
| Python SDK v4 | **Unsupported** | ≥ 3.63.0 | Full | GA |
| Python SDK v3 | **Unsupported** | ≥ 3.63.0 | Deprecated | Deprecated |
| **Python SDK v2** | **Full** | **Full** | **Unsupported** | Deprecated |
| Python SDK v1 | Full | Unsupported | Unsupported | End of life |

（JS/TS 同理：v5 不支持 v2；v3/v2 支持 v2。）

**结论（一手）：**
- **v2 服务端只能配 Python SDK v2**；SDK v3 / v4 在 v2 服务端上是 Unsupported。
- **反向**：Python SDK v2 在 OSS v3 上仍为 Full（所以本仓当前「SDK v2 + 服务端 v3」是受支持的组合）；但在 **OSS v4 上 Unsupported**。

### 4.2 Python SDK v2 的最后版本

- 最后版本 **`v2.60.9`，发布 2025-06-29**（GitHub releases 一手，https://github.com/langfuse/langfuse-python/releases?q=v2.）。
- 之后 SDK 线进入 v3：`v3.0.0-alpha.1` 发布 2025-05-20（`feat(core): move to OTEL backbone`）。
- 当前最新为 `v4.15.3`（2026-09-15，一手），其 changelog 含 `docs: strengthen agent-facing README so v2 APIs are not treated as current`——官方已明示 v2 SDK 不是现行 API。

本仓 pin：`pyproject.toml:22` → `"langfuse>=2.60.0,<3.0.0"`（一手）。即已锁在 Python SDK v2。

### 4.3 对本项目的含义

- 切到 v2 服务端：**零 SDK 改动**（当前就是 SDK v2）。
- 但一旦将来要升服务端到 v4，**必须同步把 SDK 升到 v4**（v2 SDK 在 v4 是 Unsupported，且 v4 移除 legacy batch ingestion）。本仓 tracing 代码用的是 SDK v2 的 `client.trace()` / `client.generation()`（`langfuse_tracing.py:147/167/196/241`），升 SDK v4 需要重写这部分（不过它当前并未接线，见 §8）。

---

## 5. 能不能把 `DATABASE_URL` 指向云托管 RDS PostgreSQL

### 5.1 官方支持托管 Postgres

官方 Postgres 文档 https://langfuse.com/self-hosting/deployment/infrastructure/postgres （一手）原文：

> 「Langfuse requires a persistent Postgres database to store its state. **You can use a managed service on AWS, Azure, or GCP**, or host it yourself.」
> 「Managed services that run PostgreSQL, such as **Amazon RDS**, Azure Database for PostgreSQL, and GCP Cloud SQL, are covered by the **official support level**.」
> 「Langfuse supports Postgres versions **>= 12** and uses the `public` schema in the selected database. **For Langfuse v4, PostgreSQL 16 is recommended and the minimum PostgreSQL version is 15.**」

→ **一手确认：可以把 `DATABASE_URL` 指向阿里云 RDS PostgreSQL（同属 managed PostgreSQL，官方对 managed PostgreSQL 为 official support level）。**
版本要求：对 v3 用 PG >= 12 即可（v3 时代要求）；v4 要求 >= 15（建议 16）。本仓当前 compose 用 `postgres:15-alpine`（`docker-compose.yml:49`），满足两个版本。
阿里云 RDS PostgreSQL 兼容社区版，属于官方所列「managed service」范畴；**但 Langfuse 文档列举的云厂商是 AWS/Azure/GCP，未点名阿里云** —— 「阿里云 RDS 可用」是**推断**（同协议 + 官方对 managed PG 的通用承诺），不是文档点名确认。

### 5.2 迁移与权限（`prisma migrate deploy`）

- **迁移在容器启动时自动执行**：官方升级文档 https://langfuse.com/self-hosting/upgrade （一手）：「On start of the application, **all migrations are automatically applied to the databases**.」
- **权限问题有官方 FAQ**（一手）：https://langfuse.com/faq/all/self-hosting-postgresql-table-ownership-migration-failures
  - 症状：`permission denied for table projects`（`SqlState(E42501)`）
  - 根因：跑迁移的数据库用户变了，新用户不拥有既有表，无法修改
  - **官方解法 1**：`ALTER TABLE ... OWNER TO new_user`（转移表所有权）
  - **官方解法 2（关键）**：设 `DIRECT_URL=postgresql://migration_user:password@host:port/database`，让 Prisma **用超级用户/表 owner 专门跑迁移**，而应用日常用 `DATABASE_URL`。
- 另有 `LANGFUSE_ENABLE_BACKGROUND_MIGRATIONS`（v3 上可关后台迁移）——本仓 compose 已设 `"false"`（`docker-compose.yml:179`）。

→ **结论（一手）：指向托管 RDS 可行；唯一需注意的是迁移用户权限——用 `DIRECT_URL` 指定具备 DDL/表 owner 权限的账号即可，不必让应用账号长期持有高权限。** 这是官方给出的标准做法。

### 5.3 扩展 / 其他限制

- **未在一手源找到 Langfuse 要求 `pgvector` 或特定扩展的说明。** Postgres 文档只提版本、`public` schema、UTC 三项要求；v2.95.12 的迁移目录中也没有 `CREATE EXTENSION` 相关命名的迁移。→ **判定：无已知必需扩展；但不等于零扩展（未逐条读 SQL），标注为「未确认扩展方面为零要求」。**（若担心，阿里云 RDS 支持 `pg_trgm`/`pgcrypto` 等常见 contrib 扩展，通常够用。）
- **时区**：官方要求 Postgres / ClickHouse 默认 UTC，非 UTC 会返回错误或空结果（一手，Postgres 与 ClickHouse 文档均要求）。阿里云 RDS 默认时区若被设为 CST，需要改回 UTC。
- **多实例/负载均衡**：官方有 FAQ「Can I deploy multiple instances of Langfuse behind a load balancer?」（一手，见 troubleshooting 列表）。生产部署模型可查该条。
- 本仓 CLAUDE.md 记录的生产形态是「单 worker」（项目规则），与 Langfuse 的多实例能力无关，此处不展开。

---

## 6. 升级路径与锁定风险

### 6.1 v2 → v3 是官方一等路径

官方升级指南 https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v2-to-v3 （一手）给出完整步骤，摘要：

1. **配置新基础设施**：ClickHouse、Redis、S3/Blob 全部备齐；可复用现有 Postgres。新容器必需 env（缺则部署失败）：`CLICKHOUSE_URL`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_MIGRATION_URL`、`REDIS_CONNECTION_STRING`、`LANGFUSE_S3_EVENT_UPLOAD_BUCKET`。
2. 启动 v3 的 web + worker（此时 UI 无历史数据，因为读的是 ClickHouse）。
3. 切流量（DNS/LB）→ 新事件写入 ClickHouse。
4. **等后台迁移完成**：4 个后台任务（成本回填、traces/observations/scores 分批 Postgres → ClickHouse），每个必须等上一个完成，按数据量可能数小时。
5. 停旧 v2 容器。

### 6.2 已知窗口限制（重要）

指南一手原文：

> 「已知该升级在 **v2.92.0 到 v3.29.0 之间工作良好**。较新的 v3 版本会**移除 v2 仍依赖的数据库实体**。因此，我们建议**使用 v3.29.0 进行并行操作，并在迁移完成后升级到最新的 v3 版本**。」
> 另：「确保你运行的是较新的 Langfuse 版本，**理想情况下是 v2.92.0 之后**」。

→ **一手：v2.95.12（最后 v2）升到「最新 v3」不能一步到位，中间要过 v3.29.0。** 现在最新已是 v4（v3 GA 3.225.x，v4.38.0 as of 2026-09-17，一手 tag 列表），跨两个大版本。

### 6.3 v3 → v4 也需要 ClickHouse

官方升级页 https://langfuse.com/self-hosting/upgrade （一手）列有 v3→v4 迁移指南，并提到 Helm chart 需先升 v1→v2「because the v1 chart cannot bring its bundled ClickHouse to a v4-compatible version」——即 **v4 仍然围绕 ClickHouse**。

### 6.4 锁定风险总结

| 风险 | 说明 | 依据 |
|---|---|---|
| v2 是 EOL | 官方矩阵标注 | 一手 §2 |
| SDK 锁死 | v2 服务端只支持 Python SDK v2 | 一手 §4 |
| 升级必经三件套 | v2→v3 必须 ClickHouse+Redis+S3 | 一手 §6.1 |
| 升级需中转版本 | v2.95.x 不能直连最新 v3，需 v3.29.0 | 一手 §6.2 |
| 跨两代 | 现在最新是 v4，v3 已 Deprecated | 一手 §2/§6.3 |
| 数据迁移是后台任务 | 数小时到更久，且是一次性「PG→CH」单向搬运 | 一手 §6.1 |

---

## 7. 替代选项（目标：不引入 ClickHouse）

### 7.1 选项 ①：阿里云托管 ClickHouse（消掉自建，但仍多一个服务）

- **一手**：Langfuse 官方支持托管 ClickHouse，文档列出 **ClickHouse Cloud**（可经 AWS / GCP / Azure Marketplace 开通，提供 Private Link）；K8s 场景推荐 Bitnami ClickHouse Helm chart。要求：**ClickHouse 版本 >= 24.3**、**时区必须 UTC**、**单分片**（`CLICKHOUSE_CLUSTER_ENABLED` 默认 true，ClickHouse Cloud Azure 上官方建议设 false）、用户需 `INSERT/SELECT/ALTER UPDATE/ALTER DELETE/ALTER DROP INDEX/CREATE/DROP TABLE` 权限。来源：https://langfuse.com.cn/self-hosting/deployment/infrastructure/clickhouse
- **阿里云有云数据库 ClickHouse**（社区兼容版与企业版两类）。来源：https://www.aliyun.com/product/clickhouse 、https://help.aliyun.com/zh/clickhouse/
- **推断（非一手确认）**：阿里云 ClickHouse 社区兼容版属同一 ClickHouse 软件，理论可作 Langfuse 的 ClickHouse；但 **Langfuse 官方未点名阿里云**，且文档提到「ON CLUSTER / Replicated 在部分云托管上需关 cluster、否则迁移报错」，托管服务需满足单分片与迁移权限，需实测。
- **代价**：消掉了「自建 ClickHouse 运维」，但没消掉 ClickHouse 本身——仍是第二个存储服务、第二套备份/告警/权限体系。

### 7.2 选项 ②：换用只依赖 PG 的可观测方案

已核实存储依赖的候选（均为官方一手来源）：

| 方案 | 存储依赖 | 覆盖能力 | 是否需 ClickHouse | 一手来源 |
|---|---|---|---|---|
| **Arize Phoenix** | **SQLite（默认）/ PostgreSQL >= 14**（`PHOENIX_SQL_DATABASE_URL`）；CPU：单容器 | Tracing、Evals、Prompt 管理（含版本）、无功能限制自托管 | **否** | https://arize.com/docs/phoenix/self-hosting/deployment-options/docker （「We do only officially support Postgres versions >= 14」）；prompt：https://arize.com/docs/phoenix/prompt-engineering/overview-prompts/prompt-management |
| **MLflow 3** | **Backend Store = 关系库（支持 PostgreSQL）** + **Artifact Store = S3/GCS/Azure/本地**（3.7 起默认 SQLite） | LLM Tracing、Prompt Registry、Eval | **否** | https://mlflow.org/docs/latest/self-hosting/ （官方：backend store 可切 PostgreSQL cluster，artifact store 指 S3/GCS/Azure） |
| **OpenTelemetry + Grafana Tempo / Jaeger** | 后端各自的选择（Tempo 用对象存储；Jaeger 用 Cassandra/ES/Badger） | 仅 tracing；无 prompt 管理 / dataset | 否 | 属通用事实，未逐条抓官方页（**未确认**具体版本要求） |

需要排除的（它们同样引入 ClickHouse，不能解决目标）：

| 方案 | 存储依赖 | 一手来源 |
|---|---|---|
| **LangSmith self-hosted** | **ClickHouse + PostgreSQL + Redis（+ 可选 blob）**，且是企业版付费 add-on | https://docs.langchain.com/langsmith/self-hosted （一手，「self-hosted LangSmith is an add-on to the Enterprise plan」） |
| **Comet Opik** | **ClickHouse（分析）+ MySQL（事务）** | https://www.comet.com/docs/opik/self-host/architecture （一手） |
| 新 Langfuse v3/v4 | ClickHouse + Redis + S3 + PG | §1 |

→ **若愿意换产品，Phoenix 与 MLflow 3 是「只用 PG（+ 对象存储）」且同时具备 tracing + prompt 管理的一手候选。** Phoenix 更接近 Langfuse 的定位（observability + eval + prompt 管理）；MLflow 3 的 prompt registry 与 tracing 也齐备，且天然复用 S3（与团队已规划的对象存储一致）。

### 7.3 选项 ③：只保留 prompt 管理、放弃 tracing

- **在 v3/v4 上不可行**：ClickHouse 是启动硬依赖（FAQ 明确「所有自托管部署都必须包含一个 ClickHouse 实例」），即使不发 trace，也必须部署 ClickHouse/Redis/S3 才能把服务跑起来。→ **放弃 tracing 省不掉 ClickHouse。**
- **在 v2 上可行**：v2 本就只需 PG，prompt 数据在 Postgres（prompt 表迁移见 §3.1）；ClickHouse 文档也明确 ClickHouse 只存 trace/observation/score，prompt 不在其中。
- **更彻底的做法（本仓适用）**：本仓 prompt 已有本地兜底（`prompt_manager.py:31-32,162-165`，`LANGFUSE_ENABLE=false` 时完全跳过远端）。因此「只保留 prompt 管理」这个诉求，在本仓可以直接退化为「**去掉 Langfuse，prompt 回归代码/文件**」——不引入任何新服务。若仍需要非工程师改 prompt 的后台体验，可先上 Phoenix 的 prompt 管理（PG-only）。

---

## 8. 本仓实际用到了 Langfuse 的什么（决定 v2 够不够用）

### 8.1 清单（`文件:行号`）

| # | 用途 | 位置 | 说明 |
|---|---|---|---|
| 1 | SDK 依赖 | `pyproject.toml:22` | `langfuse>=2.60.0,<3.0.0` → **Python SDK v2** |
| 2 | 配置 | `src/config/settings.py:264-276` | `LANGFUSE_SECRET_KEY/PUBLIC_KEY/HOST/ENABLE`，默认 `LANGFUSE_HOST=http://langfuse:3000`、`LANGFUSE_ENABLE=true` |
| 3 | **Prompt 管理（在用）** | `src/infra/llm/prompt_manager.py:117` | `GET {host}/api/public/v2/prompts/{name}`，HTTP Basic Auth，**不经过 SDK** |
| 4 | prompt 名称（3 个） | `prompt_manager.py:69-73` | `financial-system-prompt` / `user-prompt-template` / `classifier-prompt` |
| 5 | 只取 latest，不用 label | `prompt_manager.py:123,126` | 取 `data["prompt"]`；`version` 只写日志，不参与取用 |
| 6 | 本地兜底 | `prompt_manager.py:31-32,162-165` | Langfuse 不可用落到 `src/config/prompts.py` 常量 |
| 7 | prompt 消费点 | `src/rag/prompt.py:52,114`、`src/infra/search/query_router.py:343`、`src/agents/graph/agent_node.py:110` | system / user / classifier 三条 |
| 8 | Tracing 封装（已实现） | `src/infra/llm/langfuse_tracing.py:84-246` | `LangfuseTracer`：`start_trace/end_trace/start_generation/end_generation`，SDK v2 低层 API（`client.trace()` / `client.generation()`） |
| 9 | Tracing 实例化 | `src/services/agent_service.py:759` | `self._tracer = LangfuseTracer()` |
| 10 | Tracing 取值点 | `src/rag/stream.py:45-57,98-104,140-142` | 从 `current_tracer` ContextVar 取 tracer 记录 generation |
| 11 | trace_id 贯通（日志用，与 Langfuse 解耦） | `src/middleware/trace_id.py:12,27`、`src/agents/graph/state.py:9,22`、`src/cli/eval_ragas.py:138` | `trace_<uuid>` 用于日志与 Langfuse trace id 对齐 |
| 12 | **无 score** | —— | 全仓无 `langfuse.*.score` / `create_score` 调用 |
| 13 | **无 dataset / 实验** | —— | `datasets` 导入在 `src/cli/eval_ragas.py:221`，是 HuggingFace `datasets`，用于 RAGAS，**非 Langfuse** |

### 8.2 关键发现：tracing 当前未接线

按静态代码判断（一手，未做运行时验证）：

- `@traced(...)` 装饰器定义在 `langfuse_tracing.py:34`；**全仓（排除 docs）除该定义与 docstring 示例外，没有任何应用点**。
- `current_tracer`（`trace_context.py:17`）**只在 `traced` 装饰器内部被 set**（`langfuse_tracing.py:66`）。
- `self._tracer`（`agent_service.py:759`）除赋值外**无任何调用**。
- 结论：`rag/stream.py:45` 的 `tracer = current_tracer.get()` 恒为 `None`，tracing 分支不执行；**当前部署实际不向 Langfuse 产出 trace/generation**。
- 佐证（测试层已失配）：`tests/infra/llm/test_langfuse.py` 仍假设旧接口（`tracer._initialized`、`start_trace` 返回 None、传 `trace_id` 参数），与现行实现（无 `_initialized`、用 ContextVar）已不一致。

**因此，本仓对 Langfuse 的「真实在用面」= prompt 管理（且为 HTTP 直连，跨服务端版本稳定）。**

### 8.3 对 v2 路线判定的直接结论

- v2 服务端**具备**本仓唯一在用的能力（prompt 管理 + `/api/public/v2/prompts`，§3.1）。
- v2 服务端**具备**本仓 tracing 封装所用的 legacy 写入路径（Python SDK v2 + `/api/public/ingestion`，v2/v3 均 Full，§3.2/§4）。
- 本仓**未使用**任何 v3/v4 独有功能（多模态、OTel、Scores API v3、服务端 evaluator、experiments）。
- → **「功能差异」不构成本项目的阻碍。** v2 路线的阻碍只在 EOL、SDK 锁定、升级锁定（§2/§4/§6）。

---

## 9. 明确结论

1. **是否存在「只用 PostgreSQL 且可用」的 Langfuse 版本？**
   **存在，且只有 v2 系列。最后一个版本 `v2.95.12`（2025-11-18，一手）。** v2 自托管只需 Web + PostgreSQL。v3 起 ClickHouse 为官方明确硬依赖。

2. **如果走 v2，代价是什么？**
   - **EOL**：官方矩阵明确 `OSS v2 (End of life)`；文档声明安全更新止于 2025Q1（实际有零星 backport 至 2025-11，官方表述与发布记录不一致）。
   - **SDK 锁死**：只能配 Python SDK v2（本仓已符合），不能升 SDK v3/v4。
   - **升级锁定**：未来升 v3/v4 必须引入 ClickHouse + Redis + S3，且 v2.95.x 需经 v3.29.0 中转，跨两个大版本，数据靠数小时级后台迁移。
   - **功能缺失（对本项目不构成阻碍）**：无多模态、无 OTel、无 Scores API v3、无服务端 evaluator；本仓均未使用。

3. **如果不存在可接受的 PG-only 版本呢（即不接受 EOL）？最接近的替代是什么？**
   - **保留 Langfuse、只消掉自建 ClickHouse**：把 ClickHouse 换成托管（阿里云云数据库 ClickHouse 是**推断可行**、Langfuse 官方点名的托管选项是 ClickHouse Cloud/AWS/GCP/Azure）。**仍是「多一个服务」，只是不自建。**
   - **换产品、真正只依赖 PG**：**Arize Phoenix**（PG>=14 或 SQLite，tracing + prompt 管理 + evals，自托管无功能限制）或 **MLflow 3**（PG backend + S3 artifact，tracing + prompt registry）为一手候选。
   - **本仓最省的一条**：本仓 prompt 已有本地兜底、tracing 实际未接线——**直接去掉 Langfuse**，prompt 回归代码/文件，即可完全回到「无 ClickHouse、无额外服务」。这也意味着「把 Langfuse 收敛进 RDS」这个需求本身，在本仓当前实现下并不强。

4. **给决策者的最短判断：**
   - 若**必须先保 tracing 能力并保留 Langfuse** → 不要退到 EOL 的 v2，选「v3/v4 + 托管 ClickHouse」更稳。
   - 若**可以放弃/暂不需要 tracing**（本仓现状即如此） → 优先考虑「去掉 Langfuse」或换 Phoenix（PG-only），而不是把 v2 重新拉起来。
   - **不建议**为了「只用 PG」而新上 v2.95.12：它把一个 EOL 服务重新纳入关键路径，换来的只是省掉 ClickHouse，而升级窗口会在未来一次性反噬。

---

## 来源清单

### 一手（官方文档 / 官方发布 / 官方源码）

| # | 来源 | 类型 | 用于 |
|---|---|---|---|
| S1 | https://langfuse.com/self-hosting | 官方文档（v4） | v3+ 架构与必需组件（§1.3） |
| S2 | https://langfuse.com/self-hosting/v2 | 官方文档（v2） | v2 架构仅 PG；安全更新至 2025Q1（§1.2, §2.2） |
| S3 | https://raw.githubusercontent.com/langfuse/langfuse/v2/docker-compose.yml | 官方源码 | v2 compose 仅 postgres（§1.2） |
| S4 | https://raw.githubusercontent.com/langfuse/langfuse/v2/package.json | 官方源码 | v2 分支版本 = 2.95.12（§2.1） |
| S5 | https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v2-to-v3 | 官方文档 | v3 发布 2024-12-06；架构变更；迁移步骤；v2.92.0→v3.29.0 窗口（§1.1, §6） |
| S6 | https://langfuse.com/self-hosting/upgrade/versioning | 官方文档 | 兼容矩阵：OSS v2 EOL / v3 Deprecated / v4 GA；SDK↔服务端矩阵（§2.2, §4.1） |
| S7 | https://langfuse.com/self-hosting/deployment/infrastructure/postgres | 官方文档 | 托管 PG（含 Amazon RDS）官方支持；PG>=12；v4 需 >=15；UTC/public schema（§5.1） |
| S8 | https://langfuse.com.cn/self-hosting/deployment/infrastructure/clickhouse | 官方文档 | ClickHouse 必需 FAQ；版本>=24.3；UTC；单分片；权限；托管选项（§1.3, §7.1） |
| S9 | https://langfuse.com/self-hosting/upgrade | 官方文档 | 启动自动迁移；v3→v4 指南（§5.2, §6.3） |
| S10 | https://langfuse.com/faq/all/self-hosting-postgresql-table-ownership-migration-failures | 官方 FAQ | 迁移权限失败；`DIRECT_URL` 专用迁移账号（§5.2） |
| S11 | https://langfuse.com/faq/all/compatibility-langfuse-ui-and-python-sdk | 官方 FAQ | SDK/服务端兼容规则（§4.1） |
| S12 | https://langfuse.com/docs/prompts/get-started | 官方文档 | prompt 管理：版本/label/缓存/fallback/非关键路径（§3.1） |
| S13 | https://api.github.com/repos/langfuse/langfuse/releases/tags/v2.95.12 | 官方 API | v2.95.12 published 2025-11-18（§2.1） |
| S14 | https://github.com/langfuse/langfuse/releases?q=v2.95&expanded=true | 官方 releases | v2.95.7~v2.95.12 日期（§2.1） |
| S15 | https://api.github.com/repos/langfuse/langfuse/contents/packages/shared/prisma/migrations?ref=v2.95.12 | 官方源码 | v2 prompt 表/version/label 迁移；无 extension 迁移（§3.1, §5.3） |
| S16 | https://api.github.com/repos/langfuse/langfuse/contents/web/src/pages/api/public/v2/prompts?ref=v2.95.12 | 官方源码 | v2 服务端存在 `/api/public/v2/prompts/{name}`（§3.1, §8.3） |
| S17 | https://github.com/langfuse/langfuse-python/releases?q=v2. | 官方 releases | Python SDK v2 最后版本 2.60.9（2025-06-29）；v3.0.0-alpha.1（§4.2） |
| S18 | https://api.github.com/repos/langfuse/langfuse/tags?per_page=100 | 官方 API | 当前最新 v4.38.0（§6.2） |
| S19 | https://langfuse.com/changelog | 官方 changelog | v4 live 2026-08-17；prompt 管理 2024-09 发布（§3.1, §6.3） |
| S20 | https://arize.com/docs/phoenix/self-hosting/deployment-options/docker | 官方文档 | Phoenix：SQLite 默认 / PostgreSQL>=14 官方支持（§7.2） |
| S21 | https://arize.com/docs/phoenix/prompt-engineering/overview-prompts/prompt-management | 官方文档 | Phoenix prompt 管理 + 版本（§7.2） |
| S22 | https://mlflow.org/docs/latest/self-hosting/ | 官方文档 | MLflow backend store（Postgres 可）+ artifact store；tracing/prompt registry（§7.2） |
| S23 | https://docs.langchain.com/langsmith/self-hosted | 官方文档 | LangSmith 自托管 = 企业版，ClickHouse+PG+Redis（§7.2） |
| S24 | https://www.comet.com/docs/opik/self-host/architecture | 官方文档 | Opik = ClickHouse + MySQL（§7.2） |
| S25 | https://www.aliyun.com/product/clickhouse 、https://help.aliyun.com/zh/clickhouse/ | 阿里云官方 | 云数据库 ClickHouse 存在（§7.1） |

### 一手（本仓库，HEAD `1f4dec9`）

| # | 位置 | 用于 |
|---|---|---|
| R1 | `pyproject.toml:22` | SDK pin `>=2.60.0,<3.0.0`（§8.1） |
| R2 | `src/config/settings.py:264-276` | Langfuse 配置与开关（§8.1） |
| R3 | `src/infra/llm/prompt_manager.py:31-32,69-73,117,123-126,162-165` | prompt 管理：HTTP 端点、3 个 prompt、只取 latest、本地兜底（§8.1） |
| R4 | `src/infra/llm/langfuse_tracing.py:34,45,66,84-246` | tracing 封装；`traced` 装饰器仅定义（§8.1, §8.2） |
| R5 | `src/services/agent_service.py:759` | `LangfuseTracer` 实例化后无调用（§8.2） |
| R6 | `src/rag/stream.py:45-57,98-104,140-142` | `current_tracer` 取值点（§8.1） |
| R7 | `src/infra/llm/trace_context.py:14-19` | `current_tracer` 定义（§8.2） |
| R8 | `src/rag/prompt.py:52,114`、`src/infra/search/query_router.py:343`、`src/agents/graph/agent_node.py:110` | prompt 消费点（§8.1） |
| R9 | `docker-compose.yml:49,122-184`、`docker-compose.prod.yml:55-154` | 现网 v3 依赖（PG+CH+MinIO+Redis+worker）——**仅作现状记录，不作依赖依据** |
| R10 | `tests/infra/llm/test_langfuse.py:33-35` | 测试假设旧接口，与实现失配（§8.2） |

### 未确认 / 推断（显式标注）

| 项 | 性质 | 说明 |
|---|---|---|
| 阿里云 RDS PostgreSQL 可直接作为 Langfuse 的 PG | **推断** | 官方对 managed PostgreSQL 明确支持（点名 AWS/Azure/GCP）；未点名阿里云。PG>=12（v3）、UTC、public schema 需满足 |
| 阿里云云数据库 ClickHouse 可用于 Langfuse | **推断** | Langfuse 官方托管选项点名为 ClickHouse Cloud / AWS / GCP / Azure；阿里云属同软件、未在官方文档点名，且需满足单分片/UTC/迁移权限 |
| Langfuse 对 Postgres 扩展（pgvector/pg_trgm 等）无强制要求 | **未确认** | 官方文档未提扩展；v2 迁移名中无 extension 相关项；未逐条读 SQL |
| v2 在 2025-11 之后是否仍有安全补丁 | **官方表述含糊** | 文档说安全更新止于 2025Q1，但实际 2025-04/06/11 有 `security:` 补丁；无法从一手源确定后续承诺 |
| tracing 当前完全未产出数据 | **一手（静态代码）+ 未运行时验证** | 依据「`@traced` 无应用点 + `current_tracer` 仅在该装饰器内置值 + `self._tracer` 无调用」；建议运行时确认一次 |
| OTel + Tempo/Jaeger 的具体版本要求 | **未确认** | 未抓官方版本矩阵 |

---

## 附：一句话回答了团队的原始问题

> 「Langfuse 能只用 PostgreSQL（不用 ClickHouse）吗？」
>
> **能，但只在 v2：官方最后 v2 版本为 `v2.95.12`，而 v2 已被官方标注 End of life。** v3 起 ClickHouse 是官方明确的必需组件（含 FAQ 原文「所有自托管部署都必须包含一个 ClickHouse 实例」）。因此「v2 路线可行、但不建议」：功能上本仓完全够用（本仓实际只用 prompt 管理，且 tracing 当前未接线），风险全在 EOL、SDK 锁定与未来必跨两代的升级。若一定要消掉 ClickHouse，更推荐「换 PG-only 方案（Phoenix/MLflow）」或「本仓直接去掉 Langfuse」，而不是重新引入一个 EOL 的 v2。
