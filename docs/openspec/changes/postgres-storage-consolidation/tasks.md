# postgres-storage-consolidation —— 执行档索引

> **本文件不再承载任务清单。** 执行档按阶段拆分为实施计划，落在 `docs/superpowers/plans/`：
>
> | 阶段 | 计划文件 | 范围 | 状态 |
> |---|---|---|---|
> | **P1** | `docs/superpowers/plans/2026-09-19-postgres-storage-p1-relational-base.md` | PostgreSQL 关系型底座：配置 → compose PG 服务 → alembic baseline（8 表 + pgvector 扩展）→ 合并 ORM → 统一 `ChunkData` → 引擎切 asyncpg → repo 幂等写入 → 退役 MySQL → 文档收尾 | **待执行** |
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
| 2026-09-19 | change §2.3 / design D3（`CREATE EXTENSION` 放在迁移里） | `vector` 的 `vector.control` **没有 `trusted = true`** → 迁移使用的应用账号（业务库属主、非超级用户）执行 `CREATE EXTENSION vector` 得到 `ERROR: permission denied ... Must be superuser`。**初稿"放进迁移，幂等且两条路径都生效"不成立** | 扩展改由**超级用户一次性创建**（全新卷走初始化脚本、既有卷走一次性命令）；迁移**不建扩展**，改为在建 `chunks` 前**断言存在**并抛可操作的错误 | 是（已改 design D1/D3、`database-migrations` delta、proposal、Migration Plan） |
| 2026-09-19 | change §3.2（幂等写入改 `ON CONFLICT`） | 实读代码后只有 `chat_repo.create_session` 是纯幂等插入；`kb_repo.get_or_create_kb` 是三态语义（新建 / 复活软删 / 已存在活跃），`ON CONFLICT DO UPDATE` 表达不了「已存在活跃 → 返回 False」，硬改会改掉返回值；document / eval / user 三个 repo 无 `IntegrityError` 捕获。故实际改动为 **1 处**，非 5 处 | 收窄为只改 `chat_repo`，`kb_repo` 保持异常兜底并补 docstring 说明三态契约 | 是（已并入 `design.md` D3 的 upsert 说明） |
| 2026-09-19 | 范围（tasks §1 的 1.1/1.3、§9.6、design D1/Migration Plan 第 8 步） | 用户决定：**本轮只做本地，远程 RDS 不处理、prod 不进行安装** | 1.1/1.3 的 RDS 部分移出本轮；prod compose 只做与 dev 同构的文件调整、不指向 RDS、不部署验证；RDS 托管化 / prod 安装 / 连接预算登记为遗留项 L1–L3 | 是（已改 design D1 范围边界、Goals、Migration Plan、Open Questions、proposal） |
