# postgres-storage-consolidation —— 执行档索引

> **本文件不再承载任务清单。** 执行档按阶段拆分为实施计划，落在 `docs/superpowers/plans/`：
>
> | 阶段 | 计划文件 | 范围 | 状态 |
> |---|---|---|---|
> | **P1** | `docs/superpowers/plans/2026-09-19-postgres-storage-p1-relational-base.md` | PostgreSQL 关系型底座：配置 → compose PG 服务 → alembic baseline（8 表 + pgvector 扩展）→ 合并 ORM → 统一 `ChunkData` → 引擎切 asyncpg → repo 幂等写入 → 退役 MySQL → 文档收尾 | **已完成**（收口 commit 待回填） |
> | P2 | （待 P1 落地后编写） | `vector_store/` 换 pgvector、`ChunkResult.metadata` 回填契约、删除 `similarity_search_all`、Chroma→PG 数据搬迁、dense 迁移等价性验收 | 未编写 |
> | P3 | （待 P2 落地后编写） | jieba 分词入口 + 查询串构造与转义 + `tsv` 生成列 + 词项命中探针选型 + `rrf_fusion` 迁移 + 删除 `bm25_index.py` | 未编写 |
> | P4 | （待 P3 落地后编写） | 入库/删除两条路径同事务 + 故障注入验收 + 依赖与卷清理 + prod compose 与 dev 同构（**不指向 RDS、不安装**）+ 文档与 ADR + 归档 | 未编写 |
>
> 分阶段的原因与各 capability 的覆盖对照见 P1 计划文件的「阶段定位」与「覆盖的 spec requirement」两节。
>
> **执行范围（用户 2026-09-19 决定）：本轮只做本地。远程 RDS 不处理，prod 不进行安装。** prod 的 `docker-compose.prod.yml` 只做与 dev 同构的**文件**调整（继续用本地 PG 实例），不部署、不验证；RDS 托管化与 RDS 侧扩展权限是**遗留项**。
>
> **为什么不再保留一份任务清单**：`dev-flow.md` 的 ⑤ 环节规定 `writing-plans` 与 openspec tasks **二选一，不可都写**，否则一事两档。本 change 选择 `writing-plans` 作为执行档，故任务清单已迁出。设计决策仍由 `design.md` 承载，需求仍由 `specs/` 承载。

## §1 前置核查结果（2026-09-19）

完整实测记录见 `docs/tmp/postgres-probe-2026-09-19.md`。

| 项 | 状态 | 结论 |
|---|---|---|
| 1.1 `vector` 能否 `CREATE EXTENSION`、需什么权限 | **本地已实测；RDS 部分移出范围** | **本地（2026-09-19 实测）**：`vector.control` **无 `trusted = true`** → 应用账号（非超级用户）被拒：`ERROR: permission denied to create extension "vector" / HINT: Must be superuser`；`langfuse`（compose 的 `POSTGRES_USER`）实测 `rolsuper = t`，可建；建后应用账号能正常建含 `vector(1024)` 列的表、插入与 `<=>` 排序。`<=>` 返回余弦距离 0~2，与 Chroma 语义一致 → `score = 1 - distance` 契约无需修改。**RDS 侧同项按用户 2026-09-19 决定移出本轮范围（登记为遗留）** |
| 1.2 Chroma 能否原样读出全部 embeddings | **通过** | 691 collection（686 空 / 5 含分块）、共 176 分块，`documents`/`metadatas`/`embeddings` **全部可读**，维度 1024，无 `None` 行，0 失败 → 数据搬迁与 dense 等价性验收成立 |
| 1.3 扩展清单与 pgvector 版本 | **本地已得；RDS 部分移出范围** | 本地 `pgvector/pgvector:pg15` = PG 15.19 + pgvector **0.8.6**；`pg_available_extensions` 只有 `vector` 与 `pg_trgm` 1.6，**无** `zhparser`/`pg_jieba`/`pg_bigm`/`pg_search` → 本地只有 `pg_trgm` 可作探针对照。RDS 侧移出本轮范围 |
| 1.4 prod 与 dev 是否不同机 | **结论已硬化；本轮不安装 prod** | 两份 compose 的 project name（`corporate_rag`）、postgres 容器名（`corporate-rag-postgres`）与**全部卷名**完全相同 → 同机不是"数据互污"而是**容器名冲突、第二个 `up` 直接失败**（`--force-recreate` 会拆掉先起的那套）。本轮不安装 prod，故只把这条写进 compose 注释与部署文档 |

## §2 实施期修正记录

执行阶段若发现与设计不符的事实，逐条登记于此（格式：日期 / 落点 / 事实 / 处置 / 是否回改 `design.md`）。**不要直接改写 `design.md` 的既有决策叙述** —— 决策的修订走正常流程并在此留索引。

| 日期 | 落点 | 事实 | 处置 | 回改 design |
|---|---|---|---|---|
| 2026-09-19 | change §2.1/§2.5（ORM 模型与迁移同步） | **ORM metadata 里没有任何 `Index`**（全仓只有 `kb.py:18` 的 `uk_user_kb` 唯一约束），而旧 MySQL schema 有 5 个查询路径索引（`idx_user_kb`/`idx_session`/`idx_user`/`idx_updated_at`/`idx_kb_date`）。因迁移改为 autogenerate 从 metadata 生成 → **未声明的索引不会被建立**，且下次 autogenerate 会把它们生成为 **drop**。受影响的是热路径：`get_messages`、`get_sessions`、`get_all_kb` | 5 个索引补进 ORM `__table_args__`（沿用旧索引名；PG 能反向扫 ASC btree，故 `idx_updated_at` 不需要 DESC）；`database-orm` delta 增两条要求与两个 scenario；baseline 测试加「索引真的被建出来」+「tsv 索引是 GIN」 | 是（已并入 `design.md` D3 与 `database-orm` delta） |
| 2026-09-19 | change §2.5（去 MySQL 方言类型） | `models/chat.py:43-45` 的 `conversation_history.process` 用 `sqlalchemy.dialects.mysql.MEDIUMTEXT`。实测 PG 方言渲染它**直接抛 `CompileError: can't render element of type MEDIUMTEXT`** → 若先跑 autogenerate，生成的 baseline 一执行就崩。**原先把它排在 autogenerate 之后是顺序错误** | 去 `MEDIUMTEXT` 与补索引一起**前移为 autogenerate 的前置步骤**（P1 Task 2）；并在 Task 5 生成后加 `grep -n "mysql\." ` 与索引齐全性两道核对 | 是（已并入 `database-orm` delta：模型 SHALL NOT 依赖 MySQL 方言类型） |
| 2026-09-19 | change §2.3 / design D3（`CREATE EXTENSION` 放在迁移里） | `vector` 的 `vector.control` **没有 `trusted = true`** → 迁移使用的应用账号（业务库属主、非超级用户）执行 `CREATE EXTENSION vector` 得到 `ERROR: permission denied ... Must be superuser`。**初稿"放进迁移，幂等且两条路径都生效"不成立** | 扩展改由**超级用户一次性创建**（全新卷走初始化脚本、既有卷走一次性命令）；迁移**不建扩展**，改为在建 `chunks` 前**断言存在**并抛可操作的错误 | 是（已改 design D1/D3、`database-migrations` delta、proposal、Migration Plan） |
| 2026-09-19 | change §2.7（统一 `ChunkData`） | 生产代码两侧都用关键字传参（不用改），但 `tests/chunking/test_chunking.py` 有 **5 处位置参数**构造，import 的是 `chunking.validator`（旧字段序 `(content, metadata, tokens)`）→ 统一为 `(content, metadata, chunk_id, tokens)` 后第 3 个位置参数从 `tokens` 改落 `chunk_id`。已核实 `src/chunking/router.py:15` 只读 `metadata["block_type"]`/`content`，故那 3 个测试**不会挂**，属**静默语义漂移** | 5 处改关键字；新增一条测试把位置参数顺序钉成契约 | 否（属测试侧实现细节；契约写在 P1 Task 1） |
| 2026-09-19 | change §8 的 reset 脚手架（design 未单列，属 P1 执行面） | ① `tests/reset_data.py` 的 `reset_all()` 是**三合一重置**（MySQL + 删 Chroma 目录 + Redis FLUSHALL），P1 阶段 Chroma 与 Redis 仍在用；② 它的 TRUNCATE 只清 3 张表、**刻意不碰 `users`**（清了没法登录）；③ 它的 `__main__` 块用 `docker exec financial-qa-mysql`，**容器名早已过时**（实际 `corporate-rag-mysql`），那段本来走不通 | 只替换 MySQL 段、保留 Chroma/Redis；TRUNCATE 范围不变（仅补新增的 `chunks`）；`__main__` 块一并改 PG | 否（属测试脚手架；已写进 design Risks 与 P1 Task 8） |
| 2026-09-19 | change §9.5（退役 MySQL 卷） | `mysql_data` 真实卷名 `corporate_rag_mysql_data`；退役只是删 compose **声明**，Docker **不会**因此删卷（实测卷与容器都还在）→ 卷是回滚依据。但 MySQL 卷在退役后处于"未被容器引用"状态，**正是 `volume prune` 的目标** | Migration Plan 的回滚段补：锚点 commit、卷会存活、两条禁令（`down -v` / `prune`）；compose 顶部加注释 | 是（已改 design 的「回滚」段） |
| 2026-09-19 | change §3.1 / §10.x（配置与依赖） | ① `engine.py` 在**模块级** `DSN = build_postgres_dsn()`，缺 `POSTGRES_PASSWORD` 会表现为**"导入即崩"**，而该模块在 4 个 CLI、repos、services、存储侧测试链上；② 仓库**没有 `.env.example`**，而本变更新增了强制必填的 `POSTGRES_PASSWORD` | ① `.env` 补变量必须先于 Task 4/6，并把该事实写进 plan 与 design Risks（不改惰性初始化，那超出本变更）；② 新建 `.env.example` 只列键名、**不含真值** | 是（已并入 design Risks） |
| 2026-09-19 | change §2.1 / §8.2（`chunks` 验收） | P1 期间**没有任何业务代码读写 `chunks`** → 只断言"表与列存在"的话，生成列写错或维度写成 1023 都会 **P1 全绿、P2 才炸**，且会归因到 P2 | 加一条 `chunks` 可用性冒烟测试（插入 1024 维向量 + 分段文本 → 断言生成列自动填充、词法可命中、`<=>` 可排序、外键拒绝不存在的 kb）；`database-migrations` delta 增一个 scenario | 是（已加 scenario） |
| 2026-09-19 | change 的验收方式（§8 之外） | **"`pytest` 全绿"不等于"整条链路在 PG 上通了"** —— 11 个存储侧测试只覆盖 repo 层；登录 / 建库 / 上传 / 检索 / 引用渲染是跨层路径，而换引擎的残余风险恰好是"某个没被测到的查询依赖了 MySQL 的语义差异"（`is_deleted` 整数比较、PG 微秒 vs MySQL 秒级时间戳、`ORDER BY` 空值位置、`LIKE` 大小写、`onupdate` 由 ORM 而非数据库触发器提供） | 实施计划增**第 10 个 Task：在 PG 上跑一次真实 E2E 冒烟**（登录→建库→上传到 ready→提问→看到引用），并逐条核对上述嫌疑点；design Migration Plan 增第 8b 步；实施计划顶部增「P1 完成标准（DoD）」表，D8 即此条 | 是（已加 Migration Plan 8b 与 Risks） |
| 2026-09-19 | change §9 之后的 dev 环境状态 | ① `docker-compose.override.yml` 把 `./src` 与 `./tests` **挂进容器**（实测），所以 **Task 2–8 的代码改动 `docker compose restart app` 即生效、无需 rebuild**；只有 Task 9 改了依赖才需要 `build`，且 `CLAUDE.md` 推荐 `--no-cache`。② P1 后 PG 是新建库，但**并非空库** —— Task 7/8/9 的测试运行留下 **67 文档 / 56 知识库**残留（PG 曾是新建空库，故无用户原有数据）；Task 11 Step 2c 用 `reset_pg` 清掉后才空。**登录会自动注册**（`src/api/auth.py:38-41`），**不需要 seed 账号**。③ `TEST_ACCOUNT`/`TEST_PASSWORD` **全仓 `.py` 零引用**（只在 `docs/agents/cookbook.md:92` 作手工 API 调试凭据）—— 我此前写的"测试依赖它"是错的 | 实施计划增「运行中的 dev 栈（改代码怎么生效）」与「P1 结束时你会看到什么」两节；Task 9 的 `build app` 改 `build --no-cache app`；4 处 `TEST_ACCOUNT` 错述已更正；Task 11 增 Step 2c 清理残留 | 是（已并入 design Risks） |
| 2026-09-19 | change §3.2（幂等写入改 `ON CONFLICT`） | 实读代码后只有 `chat_repo.create_session` 是纯幂等插入；`kb_repo.get_or_create_kb` 是三态语义（新建 / 复活软删 / 已存在活跃），`ON CONFLICT DO UPDATE` 表达不了「已存在活跃 → 返回 False」，硬改会改掉返回值；document / eval / user 三个 repo 无 `IntegrityError` 捕获。故实际改动为 **1 处**，非 5 处 | 收窄为只改 `chat_repo`，`kb_repo` 保持异常兜底并补 docstring 说明三态契约 | 是（已并入 `design.md` D3 的 upsert 说明） |
| 2026-09-19 | 范围（tasks §1 的 1.1/1.3、§9.6、design D1/Migration Plan 第 8 步） | 用户决定：**本轮只做本地，远程 RDS 不处理、prod 不进行安装** | 1.1/1.3 的 RDS 部分移出本轮；prod compose 只做与 dev 同构的文件调整、不指向 RDS、不部署验证；RDS 托管化 / prod 安装 / 连接预算登记为遗留项 L1–L3 | 是（已改 design D1 范围边界、Goals、Migration Plan、Open Questions、proposal） |
| 2026-09-19 | P1 Task 6–7（`ChatRepo.get_sessions`） | 原查询在 `SELECT` 中带了非分组列，在 MySQL 宽松分组下可跑，切到 PG 后触发**分组违规**（`GROUP BY` 校验）—— 属**生产查询缺陷**，不是测试问题。Task 6 切引擎后暴露，Task 7 修复 | 重写为符合 PG 语义的查询（补全 `GROUP BY`）并加回归测试；属实现缺陷修复，不改 spec | 否（实现缺陷） |
| 2026-09-19 | P1 Global Constraints（宿主侧连库） | dev 的 `postgres` 服务**不发布宿主端口**，而 `.env` 的 `POSTGRES_HOST=postgres` 是 compose 服务名、宿主上解析不了 → 宿主用 `.venv` 跑 alembic / pytest 连不上库（Task 5 临时用 `socat` 转发） | dev `postgres` 服务发布**仅回环**端口 `127.0.0.1:5432:5432`；宿主侧跑 alembic / pytest 一律加 `POSTGRES_HOST=localhost` 前缀（`python-dotenv` `override=False`，已验证有效）；容器侧继续用服务名 | 否（已写入 plan Global Constraints、code-map.md、glossary.md） |
| 2026-09-19 | plan 的端点表（Task 10 实测） | plan 里写的端点路径与 HTTP 方法**几乎全错**：实际全是 **POST**（`/api/kbs`、`/api/kbs/list`、`/api/kbs/documents/upload` 等），且 chat 请求体字段是 `query` 而非 `message` | 按实机修正端点表（见 plan Task 10 的「端点路径以本节为准」），Task 10 的 E2E 改用正确端点重跑 | 否（属 plan 事实修正） |
| 2026-09-19 | plan Task 6 与 Task 7 的步骤描述 | ① Task 6 的 `git add` 清单**漏了 `docker-compose.yml`**（该任务实际改了它）；② Task 7 写"5 个特性化测试"，**实为 4 个**（计数笔误） | 执行时已按实况处理（补 add 清单、以实际测试数为准）；此处登记，避免后续按旧数字核对 | 否（计数/清单笔误） |
| 2026-09-19 | P1 Task 9（退役 MySQL）的 offender 清单 | `scripts/clean_all_data.py` 也是 MySQL offender，plan 未点名；按 Task 9 Step 4 的 grep 指令（扫 `scripts/` 与 `src/` 的 `mysql`/驱动名）一并发现 | 已随 Task 9 改为 PostgreSQL（删应用库 `public` schema 下全部表、保留 `vector` 扩展） | 否（属 Task 9 执行面） |
