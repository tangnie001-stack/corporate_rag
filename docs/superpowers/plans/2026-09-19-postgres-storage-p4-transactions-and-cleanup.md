# PostgreSQL 事务化、依赖与卷清理、归档（postgres-storage-consolidation / P4）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成存储收敛的最后一段 —— 让「分块」与「文档/知识库状态」在**同一事务**内提交（孤儿类缺陷结构性消失）、把两条删除路径收编为一条、退役 Chroma 与 BM25 的**依赖/配置/卷/数据目录**、让 prod 与 dev 同构、写 ADR 并归档本 change。

**Architecture:** P1–P3 已把三套存储收敛到一个 PostgreSQL 实例（关系型 + dense + 词法），但**跨表原子性**从未建立：每个 repo 方法都 `async with self._sf() as session: … await session.commit()` 自开自提，所以「写 chunks」与「更新 document.status」天然是两个事务（`design.md` D7 的靶子）。P4 用一个**可选的会话参数 + 一个 `session_scope()` 上下文管理器**建立事务边界：不传 `session` 时行为与今天完全一致；传了就在**外层唯一一次提交**，参与方法只执行语句。在这之上：入库、删文档、删知识库三条路径各成为一个事务；删除路径从「API 里的两条中途半死」收编为「service 里的一条」。随后退役 Chroma/BM25 的依赖、配置、脚本、卷与数据目录（**数据目录最后删，它是 dense 的复现与回滚依据**），最后写 ADR 并把 14 个 delta 同步进在效规格、归档本 change。

**Tech Stack:** Python 3.11+ / SQLAlchemy 2.x async（asyncpg）+ `async_sessionmaker` / PostgreSQL 15.19 + pgvector / Docker Compose（dev + prod 两份）/ pytest（存储侧打真实 PG）/ openspec CLI 1.5.0

**Spec:** `docs/openspec/changes/postgres-storage-consolidation/`（`proposal.md` / `design.md` / `specs/`）。本 plan 实现的 delta：`hybrid-retrieval`（「分块与文档状态同事务」「删除路径同样同事务」两条 scenario）、`database-migrations`（8 张表的 baseline 已完成于 P1）、`typed-data-layer`（「关系型实体类型」的落地裁决）、`architecture-tidy`，以及 `proposal.md` 的「依赖增减」与 Migration Plan 的第 7/8/8b/9 步。设计决策以 `design.md` 的 **D1（共享实例的四条运维后果）/ D7（事务边界）/ D5（融合位置）** 为准。

## 阶段定位（重要，先读）

本 change 跨 4 个可独立交付的子系统，每个子系统一份 plan。本文件是 **P4（最后一个阶段）**：

| 阶段 | 范围 | 状态 |
|---|---|---|
| P1 | PostgreSQL 关系型底座（配置 / compose / alembic baseline / ORM 合并 / `ChunkData` 统一 / engine 切 asyncpg / repo 幂等 / 退役 MySQL / 文档） | **已完成**（HEAD `8fb581e`） |
| P2 | dense 检索换 pgvector、`ChunkResult` 分路字段与 `metadata` 回填契约、删全局检索入口、Chroma→PG 数据搬迁、dense 迁移等价性验收 | **已完成**（HEAD `43ab19a`） |
| P3 | jieba 分词入口 + 查询串构造与转义 + `content_seg` 换分词输出与存量重写 + 词项命中探针 + `rrf_fusion` 迁移 + 两路同源取数 + 删 `bm25_index.py` | **已完成**（HEAD `d049373`；`1058 passed / 14 skipped`、`ruff` 全过、`pyright src/` 0 error） |
| **P4（本文件）** | 入库/删除两条路径同事务 + 故障注入验收 + 依赖与卷清理（含 Chroma）+ prod 与 dev 同构 + ADR + delta 对账与归档 | 本次 |

**P4 的边界（明确不做）**：远程 RDS 与 prod 安装（`proposal.md` 的「明确不在本变更范围」，遗留项 F-16/F-17）；prod `--workers 4` 与单 worker 规则的冲突（F-17，独立变更）；`alembic/env.py` 的 `compare_server_default`（F-22，独立变更）；`multi-query-retrieval` requirement 的结构性重写（F-29，与 `retrieval-fetch-and-dedup` 的重定基一并）；词法路质量判据的重建（F-30，需 k ≪ 池的新判据或端到端 RAGAS）；`reqwest`/HNSW 之类的性能项。**MySQL 卷 `corporate_rag_mysql_data` 与孤儿容器 `corporate-rag-mysql` 不动** —— 它们是关系型侧的回滚依据。

## ⚠ 已知事实与陷阱（先读这张表）

每一条都已在本仓实读/实测得出，编号在正文里被引用。

| # | 事实 / 陷阱 | 依据 | 怎么避 |
|---|---|---|---|
| **F1** | **每个 repo 方法都自开会话并提交**（`async with self._sf() as session: … await session.commit()`）→ 跨表原子性**不可能** | `document_repo.py:16-29`、`kb_repo.py`、`chunk_repo.py` 全部同形 | Task 1 引入 `session_scope(session_factory, session=None)`：不传 `session` 时逐字保持今天的行为；传了就只执行语句、由外层提交 |
| **F2** | **API 的删除端点走的是旧序且越层**：`src/api/documents.py:244-246` 先 `soft_delete_document` → 再 `vector_store.delete_document`，且用 `svc.document._doc_repo` 私有直取 + `svc.vector_store` 直达（违反「api/ 不得直接调用 infra/」）；而语义正确的 `DocumentService.delete_document` **全仓无路由调用** | 实读 `api/documents.py` 删除端点；需求池 F-23/F-25 | Task 3 把端点改为调 service；service 内部同事务 |
| **F3** | `AppService.delete_knowledge_base` 三步各开事务，且**吞掉**分块删除异常（`except Exception: logger.warning`）→ 分块删失败时 KB 照样软删，留下永久孤儿分块 | 实读 `app_service.py` 该方法 | Task 3 改为同事务 + 不吞异常 |
| **F4** | `/api/kbs` 前缀**必须登录**且中间件已设 `request.state.user_id`；上传路径把该值写进 `doc.user_id`（`api/documents.py:110,122`）→ 删除走 service 的属主校验与上传**同源**，不会误 403 | 实读 `src/middleware/auth.py:30-53`、`api/documents.py:110` | Task 3 在删除端点加 `request: Request` 并读 `request.state.user_id` |
| **F5** | 入库的孤儿窗口：`document_service` 先 `add_chunks`（自提交）再 `update_document_status`（自提交）；而 `update_document_meta_info`（eval/实体）在它们**之前**，属元数据，**不进**本事务 | 实读 `document_service.py` 流水线尾部 | Task 2 只把「chunks + 文档状态」这一对包进事务 |
| **F6** | **prod 的 app 没有 `LOG_DIR`**（dev 有 `LOG_DIR=/data/logs`），而 `src/core/logging.py` 取 `os.getenv("LOG_DIR", "logs")` → prod 日志落到容器可写层，`app_logs` 卷形同虚设。与 P1 发现的「prod 的 Chroma 无挂载」是同一类既有缺陷 | 实读两份 compose 与 `logging.py` | Task 7 给 prod 补 `LOG_DIR: /data/logs`，并在 ADR/tasks §2 写明是**既有缺陷被顺带修掉** |
| **F7** | `chroma_onnx_cache` 卷与其 app 挂载（`/root/.cache/chroma`）在 dev/prod 仍声明；dev 的 app 还挂 `./data/chroma_persist:/app/data/chroma_persist` | 盘点两份 compose | Task 7 一并删 |
| **F8** | 根 `Dockerfile:36` 仍有 `VOLUME ["/data/chroma", "/data/logs"]` | 盘点 | Task 7 去掉 `/data/chroma` |
| **F9** | `deploy/chroma/Dockerfile`（含 `pip install chromadb==1.5.9`）**未被任何 compose/Dockerfile/脚本引用** | 全仓 grep `deploy/chroma` 只命中 SDD 记录 | Task 6 删除 |
| **F10** | `CHROMA_HOST` / `CHROMA_PORT` 全仓**零消费者**；`CHROMA_COLLECTION_PREFIX` / `CHROMA_PERSIST_DIR` 只被 3 个脚本消费（`migrate_chroma_to_pg.py` / `dense_equivalence_check.py` / `clean_all_data.py`） | 盘点（逐键 grep） | Task 6 先删脚本的 chroma 分支与两个一次性脚本，再删 4 个常量 |
| **F11** | `CHROMA_CLIENT_READY`（`log_events.py:89` + `log_event_specs.py:153-154`）**无生产者**；`_validate_registry` 在 import 期断言枚举与 spec 键集相等 → **必须成对删除** | 盘点 + `log_events.py:223-243` | Task 6 两处同删，并立即 `python -c "import src.core.log_events"` 验证 |
| **F12** | `pyproject.toml:14` `chromadb==1.5.9`、`:34` `rank_bm25>=0.2.2`；`rank_bm25` 的**唯一**消费者是探针脚本（`scripts/lexical_probe.py:33`，被 `tests/config/test_no_bm25_leftovers.py` 的 ALLOWED 表放行） | 盘点 | Task 6 删两个依赖 + 删探针脚本 + 同步守卫测试的 ALLOWED 表；**删依赖后必须 `docker compose build --no-cache app`** |
| **F13** | `data/chroma_persist` 是 dense 的**唯一复现与回滚依据**（P3 收尾的语料复位就靠它 + `migrate_chroma_to_pg.py`） | P2 Ruling 4 / P3 收尾两次实跑 | Task 11 是**最后一个动作**，且在它之前完成全部验收；删除后「回滚到 Chroma」不再可能（`design.md` 已预告「第 9 步必须放在验收全部通过之后」） |
| **F14** | **`src/infra/db/entities/` 已被一次刻意清理删除**（commit `ce3a6c9`），而 `typed-data-layer` delta 的 ADDED「关系型实体类型」点名 6 个类型（`KbListItem` / `DocEntity` / `SessionEntity` / `SessionListItem` / `MessageEntity` / `UserEntity`）**全部不存在**；实际返回的是 ORM 模型实例（`KbModel` / `DocModel` / `SessionModel` / `MessageModel` / `UserModel`） | 全仓 grep 这些类名零命中；`ls src/infra/db/entities/` 不存在 | Task 10 按 Ruling R4 把 delta 改成如实描述（见下） |
| **F15** | `ChatRepo.get_sessions(self, user_id="") -> list` **未加类型注解**，实际返回 SQLAlchemy `Row` 列表（docstring 自述「返回 Row 对象（支持 .id 属性访问）」） | 实读 `chat_repo.py:64-105` | Task 10 补注解为 `list[Row]` + 一条断言属性访问的测试 |
| **F16** | `openspec` CLI **1.5.0 可用**（`/usr/bin/openspec`）；在效规格 `docs/openspec/specs/` **没有** `hybrid-retrieval`（本变更新建的 capability，归档时创建） | 盘点 | Task 12 用 `openspec` 做 sync + archive |
| **F17** | `bm25-index-durability` 已被 `proposal.md` 明示作废，但目录仍在 `changes/` 下 | `proposal.md` D9 | Task 12 删除该目录并在 ADR/§2 记录作废理由 |
| **F18** | `tests/infra/db/test_db.py` 以 `user_id="test-user"` 直写真实 PG 且**无 teardown**（真实 PG 测试里唯一一个没有清理的）→ 每跑一次全量 pytest 就累积约 5 KB / 6 doc | P3 Task 11 实测（`90|102|176|1`）与需求池 F-31 | Task 5 先修它（补 teardown），使 P4 后续的全量门禁**不再污染**语料；否则 Task 11 之后再也无法用 Chroma 复位 |
| **F19** | prod 的 `app` 用 `--workers 4`，与 `CLAUDE.md`「生产单 worker」冲突（流式状态在进程内） | 实读 `docker-compose.prod.yml` | **本阶段只登记不处置**（F-17）；Task 7 明确写出「不动 worker 数」 |
| **F20** | `data/chroma` 是**未被任何 compose/代码引用**的游离目录；`data/bm25_index` 是词法路的回滚依据 | 盘点 | Task 11 一并删（`data/chroma` 无争议；`data/bm25_index` 随 P3 的 F-28 退役） |
| **F21** | `api/documents.py:110` 用 `getattr(request.state, "user_id", "")`，违反 CLAUDE.md「显式类型检查：不用 `getattr(x,"attr",default)` 隐式兜底」 | 实读 | Task 3 顺带改为显式判断（该文件已在改动范围内） |

## P4 完成标准（DoD）

**P4 完成的判据是下面全部成立，而不是「Task 0–12 的 checkbox 都打了勾」。**

| # | 判据 | 怎么验 |
|---|---|---|
| **D1** | **入库同事务**：在 `add_chunks` 与 `update_document_status` 之间注入异常时，**chunks 与文档状态都不落库** | Task 2 的故障注入测试（真实 PG） |
| **D2** | **删除同事务**：删文档时「删分块 + 软删文档」原子；删知识库时「软删文档 + 删分块 + 软删 KB」原子；分块删除失败时**不得**软删 | Task 3 的两条故障注入测试 |
| **D3** | **删除路径唯一**：`src/api/documents.py` 不再出现 `_doc_repo` / `vector_store` 直取（越层消失），端点只转调 service | Task 3 的 grep + 断言 |
| **D4** | **KB 删除不吞异常**：`delete_knowledge_base` 无 `except Exception: logger.warning` 式的吞异常 | Task 3 的源码断言 + 故障注入 |
| **D5** | **Chroma/BM25 的代码、配置、依赖、脚本全清**：`src/` 内 `chroma` 零命中；`pyproject.toml` 无 `chromadb` / `rank_bm25`；`deploy/chroma/` 不存在 | Task 6 的守卫测试 |
| **D6** | **数据目录已删**（`data/chroma_persist` / `data/chroma` / `data/bm25_index`），且**在全部验收之后**发生 | Task 11 |
| **D7** | **prod 与 dev 同构**：两份 compose 都无 `chroma_onnx_cache` 卷与其挂载；prod 的 app 补上 `LOG_DIR: /data/logs`（既有缺陷）；「prod 与 dev 不同机」的前置注释仍在 | Task 7 的 `docker compose config` 与实跑 |
| **D8** | **ADR 已写**：存储收敛（含共享实例的四条运维后果）、融合留在应用层；`bm25-index-durability` 的作废在册 | Task 8 |
| **D9** | **delta 与代码对账完成**：`typed-data-layer` 的实体类型要求按 R4 落地；`hybrid-retrieval` 的两条同事务 scenario 现在为真；`openspec validate --strict --all` 通过 | Task 10、Task 12 |
| **D10** | 门禁全绿（`pytest tests/` / `ruff check .` / `pyright src/`）+ **删文档与删知识库两条真实 E2E 路径各跑一次**，语料回到 `5|0|176|1` | Task 4、Task 12 |

> **D1/D2 为什么不能只靠"读代码确认"**：`session_scope` 的正确性全在"参与者不提交、外层提交一次、异常时回滚"这三件事上，而它们**只在失败路径上体现**。看不出问题的测试（只跑成功路径）证明不了原子性。

## 本 plan 的裁决（需要执行者知道）

1. **Ruling 1（事务边界的形态）：`session_scope(session_factory, session=None)` + 参与方法接受 `session=`.**
   新建 `src/infra/db/transaction.py`：

   ```python
   @asynccontextmanager
   async def session_scope(session_factory, session: AsyncSession | None = None):
       """提供一个会话：外部传入则复用它且**不提交**（由外层事务决定），否则自开自提交。"""
   ```
   参与方法（8 个 repo 方法 + 3 个 `PgVectorStore` 方法）把 `async with self._sf() as session:` 换成 `async with session_scope(self._sf, session) as session:` 即可 —— 这是**每处一行的改动**，不复制方法体。
   代价（若判断错）：11 个方法签名多一个可选参数；若将来有人误在事务内传 `session` 给一个"总想自己提交"的方法，回滚语义会由外层决定（这正是我们想要的，但要在 docstring 写明）。
2. **Ruling 2（删除路径收编为一条）：API 端点改调 `svc.document.delete_document(kb_id, doc_id, user_id=request.state.user_id)`。**
   这会给删除端点加上**属主校验**（403）与**状态校验**（仅 `ready`/`failed` 可删，409）—— 语义正确，且顺带堵住「任何登录用户可删他人文档」的既有越权（F2）。
   代价（若判断错）：前端若对非 ready 文档调用删除，会从 200 变成 409。
3. **Ruling 3（不吞异常）：`delete_knowledge_base` 的分块删除失败直接向上抛、整体回滚。**
   与 P2 对文档删除的处理（不再吞异常）一致。
   代价（若判断错）：KB 删除失败会变成 500，而不是"看起来成功但留下孤儿"。
4. **Ruling 4（`关系型实体类型` 如实化）：把 `typed-data-layer` delta 的 ADDED「关系型实体类型」改为描述**实际契约** —— repos 的查询结果 SHALL 返回对应的 **ORM 模型实例**（`KbModel` / `DocModel` / `SessionModel` / `MessageModel` / `UserModel` / `EvalReportModel`）或带具名属性的 `Row`，SHALL NOT 返回 raw dict；scenario 的类型名同步改为实际名；并给 `ChatRepo.get_sessions` 补 `-> list[Row]`。
   理由：dataclass Entity 层曾被一次**刻意的清理**删除（`ce3a6c9`，"remove old queries.py, entities/, pool.py"），本变更的靶子是存储收敛、不是重建该层；要求的实质（repos 边界不返回 raw dict）已满足。
   代价（若判断错）：delta/在效规格里的类型名与历史命名不再一致（但那些命名本就无人实现）。
5. **Ruling 5（一次性验收产物随 P4 退役）**：删 `scripts/migrate_chroma_to_pg.py`、`scripts/dense_equivalence_check.py`、`scripts/lexical_probe.py`、`scripts/lexical_probe_report.py` 及其测试与 `tests/fixtures/dense_equivalence_queries.json`；**保留** `scripts/rewrite_content_seg.py`（分词器变更的操作协议，已登记进 `cookbook.md`）与 `docs/tmp/` 下的报告（测量记录，冻结）。
   理由：这些脚本存在的唯一理由是"迁移期可比对/可挑选"；Chroma 一旦退役，它们既跑不了也不该跑（P2 Ruling 7 已预告「P4 归档时一并处置」）。
   代价（若判断错）：Chroma 语料与探针结论不再可复现（报告仍可读）；这正是「关闭回滚路径」的一部分（见 F13）。
6. **Ruling 6（顺序不可换）**：数据目录删除（Task 11）是**最后一个动作**；它之前必须已完成 Task 1–10 的全部验收与清理。
   代价（若判断错）：删早了就永久失去 dense 的复现依据与 Chroma 回滚路径。
7. **Ruling 7（`F-31` 先修）**：Task 5 先给真实 PG 测试补 teardown，**再**做后续任何全量门禁。理由：Task 11 之后语料无法再用 Chroma 复位，而全量 pytest 每次污染约 5 KB/6 doc。
   代价（若判断错）：Task 5 多改一个测试文件（一行 fixture）。
8. **Ruling 8（`bm25-index-durability` 处理）：删除该 change 目录**，并把作废理由写进 ADR 与 `tasks.md` §2。理由：archive 目录的语义是"已实施并已同步"，它没有实施过；留着会让 `changes/` 永久挂着一个死靶子。
   代价（若判断错）：其 38 个任务的调研要从 git 历史（`git log -- docs/openspec/changes/bm25-index-durability/`）里找。

## Global Constraints

- **不得执行**：`docker compose down -v`、`docker volume prune`、`docker system prune --volumes`、`rm -rf` 任何 `corporate_rag_*` 命名卷。**`corporate_rag_mysql_data` 与孤儿容器 `corporate-rag-mysql` 是 P1 的关系型回滚依据，本阶段不碰。**
- **`data/chroma_persist` 在 Task 11 之前不得删除**（dense 的复现与回滚依据）。
- **embedding 必须在事务外**（`design.md` D7）：DashScope 是外网调用，不得放进事务持连接。
- **不得引入三元表达式**（`a if cond else b`）—— 写完整 `if/else`。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底；用 `x.attr if x is not None else default` 或 `isinstance` 判断。
- **硬编码集中管理**：新增常量进 `src/config/`（环境变量走 `settings.py`，固定值走 `const.py`）。
- **文档与注释**：所有函数写 docstring（含 `__init__` 的参数说明）；dataclass 每个字段加行内注释；注释写**当前状态**不写变更历史；注释陈述契约不写推理记录。
- **一事一档**：不把某归属文档的正文复制进另一文档；一句话的指针引用不算复制。
- **文件红线**：单文件 ≤ 400 行、单函数 ≤ 80 行。
- **门禁（每个 Task 结束都跑）**：`pytest tests/ -v` 相关用例通过、`ruff check .` 无错、`pyright src/` 不新增 error、无新增 `print()`/TODO。
- **宿主侧连库约定（P1 立）**：宿主上跑 alembic / pytest / 脚本**一律加前缀 `POSTGRES_HOST=localhost`**；容器侧用 `.env` 的 `POSTGRES_HOST=postgres`。
- **改依赖后**：必须 `docker compose build --no-cache app`；**改 compose 的 env / volumes / command 后**：`docker compose up -d --force-recreate app`。
- **不要在全仓跑 `ruff format .`**（会重排 `docs/**/*.md` 里的 Python 代码块，属既有漂移）。
- **契约同步**：改了公共方法签名或响应结构时，同步 `docs/agents/api_contract.md` 与受影响测试断言。
- **跑完全量 pytest 后**：dev 库会留测试数据 —— Task 5 之后应不再发生（F-31 已修）；若仍发生，按 Task 0 记录的方式清理，并在报告里说明。

---

## 文件结构（改动的落点与职责）

**新建**

| 文件 | 职责 |
|---|---|
| `src/infra/db/transaction.py` | `session_scope(session_factory, session=None)` —— **事务边界的唯一原语**：外部传会话则复用且不提交，否则自开自提交 |
| `tests/infra/db/test_transaction.py` | 事务语义的守卫：传会话不提交、不传照旧提交、异常回滚 |
| `tests/infra/db/test_atomicity.py` | 三条路径的**故障注入**验收（入库 / 删文档 / 删知识库），真实 PG |
| `tests/config/test_no_chroma_leftovers.py` | Chroma 全清守卫（对齐既有的 `test_no_bm25_leftovers.py`） |
| `docs/adr/0004-storage-consolidation-single-postgres.md` | ADR：三套存储收敛到一个 PostgreSQL 实例（含共享实例的四条运维后果） |
| `docs/adr/0005-hybrid-fusion-in-application-layer.md` | ADR：融合留在应用层（PG 生态无可用融合能力） |
| `docs/adr/0006-chinese-lexical-retrieval-shape.md` | ADR：中文词法检索的落地形态（jieba 预分词 + `tsvector` + 前缀 OR + 存量重写不变量） |

**修改**

| 文件 | 改动 |
|---|---|
| `src/infra/db/mysql_db/chunk_repo.py` | `upsert_chunks` / `delete_tail` / `delete_by_doc` / `delete_by_kb` 接受 `session=None` |
| `src/infra/db/mysql_db/document_repo.py` | `update_document_status` / `soft_delete_document` / `soft_delete_documents_by_kb` 接受 `session=None`；新增 `transaction()` 便利访问器 |
| `src/infra/db/mysql_db/kb_repo.py` | `soft_delete_kb` 接受 `session=None`；新增 `transaction()` |
| `src/infra/db/mysql_db/chat_repo.py` | `get_sessions` 补 `-> list[Row]` 注解（F15） |
| `src/infra/db/vector_store/pg_store.py` | `add_chunks` / `delete_document` / `delete_collection` 接受 `session=None` |
| `src/services/document_service.py` | 入库尾部与 `delete_document` 各包进一个事务（Ruling 1） |
| `src/services/app_service.py` | `delete_knowledge_base` 同事务 + 不吞异常（Ruling 3） |
| `src/api/documents.py` | 删除端点改调 service + 读 `request.state.user_id`；上传端点的 `getattr` 改显式判断（F21） |
| `src/config/settings.py` | 删 4 个 `CHROMA_*` 常量 |
| `src/core/log_events.py` + `log_event_specs.py` | 删 `CHROMA_CLIENT_READY`（两处同删） |
| `scripts/clean_all_data.py` | 删 chroma 分支与其 import |
| `pyproject.toml` | 删 `chromadb==1.5.9` 与 `rank_bm25>=0.2.2` |
| `Dockerfile` | 去掉 `VOLUME` 里的 `/data/chroma` |
| `docker-compose.yml` / `docker-compose.prod.yml` | 去 `chroma_onnx_cache` 卷与其挂载；dev 去 `./data/chroma_persist` 挂载；prod 补 `LOG_DIR` |
| `tests/infra/db/test_db.py` | 补 teardown（F18/F-31） |
| `tests/config/test_no_bm25_leftovers.py` | ALLOWED 表随探针脚本删除而收缩 |
| `docs/agents/{code-map,api_contract,data-flow,glossary,defensive-patterns,cookbook,requirements_pool}.md` | 同步 |
| `docs/openspec/changes/postgres-storage-consolidation/specs/typed-data-layer/spec.md` | 按 Ruling 4 改写实体类型要求 |
| `docs/openspec/changes/postgres-storage-consolidation/tasks.md` | 阶段表 + §2 实施期修正记录 |

**删除**

| 文件 | 理由 |
|---|---|
| `deploy/chroma/Dockerfile` | 无任何引用（F9） |
| `scripts/migrate_chroma_to_pg.py`、`scripts/dense_equivalence_check.py` | Chroma 退役后不可运行（Ruling 5） |
| `scripts/lexical_probe.py`、`scripts/lexical_probe_report.py` | 一次性选型产物；`rank_bm25` 一并退役（Ruling 5） |
| `tests/scripts/test_migrate_chroma_to_pg.py`、`test_dense_equivalence_check.py`、`test_lexical_probe.py` | 随其被测脚本 |
| `tests/fixtures/dense_equivalence_queries.json` | P2 验收固化查询集，随脚本退役 |
| `docs/openspec/changes/bm25-index-durability/` | 已作废（Ruling 8） |
| `data/chroma_persist`、`data/chroma`、`data/bm25_index` | Task 11（不可逆，最后做） |

---

### Task 0: 前置确认（开工前的事实核对）

**Files:**
- Modify: 无（本任务不改仓库文件；产出写进报告）

**Interfaces:**
- Consumes: 无
- Produces: 回滚锚点 + P4 的爆炸半径清单（后续任务的 Global Constraints）

- [ ] **Step 1: 记录回滚锚点**

```bash
git rev-parse HEAD
git log --oneline -1
git status --short
```

把 40 位 SHA 记进报告。**这是 P4 的回滚锚点**（`git revert` / `git reset` 的目标）。工作区必须干净；若不干净先问控制器。

- [ ] **Step 2: 确认语料与门禁基线**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(*) FROM users);"
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check; echo "exit=$?"
```

预期：`5|0|176|1` 与 `total=176 stale=0`、`exit=0`。
**若不符**：先报告再继续 —— 后续 Task 4/12 的"语料复位"判据依赖这个基线。

- [ ] **Step 3: 确认 Chroma 语料仍在（Task 11 之前它是唯一复现依据）**

```bash
ls -d data/chroma_persist data/chroma data/bm25_index 2>&1
ls data/chroma_persist | head -5
```

预期：三个目录都在，`chroma_persist` 含 `chroma.sqlite3` 与若干 UUID 子目录。

- [ ] **Step 4: 盘点事务化涉及的调用点（Ruling 1 的爆炸半径）**

```bash
grep -rn "async with self._sf() as session" src/infra/db/mysql_db/*.py | wc -l
grep -rn "self._sf()" src/infra/db/vector_store/pg_store.py src/services/*.py src/api/*.py
grep -rn "soft_delete_document\b\|soft_delete_documents_by_kb\|delete_by_doc\|delete_by_kb\|update_document_status\|upsert_chunks\|delete_tail" src/ tests/ --include=*.py | grep -v __pycache__
```

把两份清单记进报告。**预期**：`self._sf()` 只在 repo 层出现；参与事务的方法调用点集中在 `document_service.py`、`app_service.py`、`api/documents.py` 与 `tests/` 若干。
**若出现清单外的调用方**，在报告里点出来。

- [ ] **Step 5: 确认 Chroma / rank_bm25 的全部残留与消费方**

```bash
grep -rn "chroma\|CHROMA" src/ scripts/ tests/ deploy/ Dockerfile docker-compose*.yml --include=* 2>/dev/null | grep -v __pycache__ | grep -v "\.superpowers"
grep -rn "rank_bm25\|BM25" src/ tests/ scripts/ --include=*.py | grep -v __pycache__
```

把命中逐条分类：`属于 Task 6 删除范围` / `属 Task 7 compose 范围` / `属 Task 11 数据目录` / `prose 提及（保留）`。
**注意**：`src/` 里的 prose 提及（如 `types.py` 的 `lexical_score` 注释、`web_tools.py` 的 docstring）**不是**残留，`tests/infra/db/test_vector_store_types.py` 里断言"`bm25_score` 字段不存在"的用例更必须保留。

- [ ] **Step 6: 记录真实 PG 测试的 teardown 现状（F18 / F-31）**

```bash
grep -rln "session_factory\|reset_pg\|PostgreSQL" tests/ --include=*.py | grep -v __pycache__
grep -rn "yield\|finally" tests/infra/db/test_db.py | head
```

预期：`tests/infra/db/` 下多数文件有 fixture teardown，而 **`test_db.py` 没有**（F18）。把"哪些文件直写真实 PG"与"哪些有 teardown"两份清单记进报告 —— Task 5 要逐个核。

- [ ] **Step 7: 确认 openspec CLI 与 change 状态**

```bash
which openspec && openspec --version
ls docs/openspec/changes/
openspec validate postgres-storage-consolidation --strict 2>&1 | tail -5
```

预期：CLI 可用（1.5.0），`changes/` 下有 8 个非 archive 目录（含 `bm25-index-durability`），validate 通过（若不通过，把报错原文记进报告 —— Task 10/12 要先修）。

- [ ] **Step 8: 写报告**

把 Step 1–7 的真实输出写进 `TASK0_REPORT`（控制器会在派发时给出路径），并明确列出：回滚锚点 SHA、语料基线、参与的调用点清单、Chroma/BM25 残留的分类清单、真实 PG 测试的 teardown 清单、以及任何与预期不符的项。

---

### Task 1: 事务边界原语（`session_scope` + 参与方法的可选会话）

**Files:**
- Create: `src/infra/db/transaction.py`
- Modify: `src/infra/db/mysql_db/chunk_repo.py`（4 个方法）
- Modify: `src/infra/db/mysql_db/document_repo.py`（3 个方法 + 1 个访问器）
- Modify: `src/infra/db/mysql_db/kb_repo.py`（1 个方法 + 1 个访问器）
- Modify: `src/infra/db/vector_store/pg_store.py`（3 个方法）
- Test: `tests/infra/db/test_transaction.py`（新建）

**Interfaces:**
- Consumes: `src/infra/db/engine.py` 的 `session_factory`（`async_sessionmaker`，`expire_on_commit=False`）
- Produces:
  - `session_scope(session_factory, session: AsyncSession | None = None) -> AsyncIterator[AsyncSession]`（`@asynccontextmanager`）
  - `ChunkRepo.upsert_chunks(rows, session=None) -> int` / `delete_tail(kb_id, doc_id, from_index, session=None) -> int` / `delete_by_doc(kb_id, doc_id, session=None) -> int` / `delete_by_kb(kb_id, session=None) -> int`
  - `DocumentRepo.update_document_status(doc_id, status, session=None, **kwargs) -> None` / `soft_delete_document(doc_id, session=None) -> bool` / `soft_delete_documents_by_kb(kb_id, session=None) -> None` / `transaction()`
  - `KbRepo.soft_delete_kb(kb_id, session=None) -> bool` / `transaction()`
  - `PgVectorStore.add_chunks(kb_id, chunks, doc_id, embeddings=None, session=None) -> int` / `delete_document(kb_id, doc_id, session=None) -> int` / `delete_collection(kb_id, session=None) -> bool`

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_transaction.py`：

```python
"""事务边界原语的语义：外部会话不提交、自开会话提交、异常回滚（真实 PG）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.transaction import session_scope

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def kb_row():
    """建一行真实知识库（用于断言提交/回滚后的可见性），测后清理。"""
    kb_id = f"p4tx-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p4test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p4-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def _count(kb_id: str) -> int:
    """另开一个会话读该行的可见性（用独立会话，避免读到本会话未提交的状态）。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM knowledge_base WHERE id = :k"), {"k": kb_id}
            )
        )


async def test_owning_scope_commits(kb_row):
    """不传 session 时，出块即提交。"""
    marker = f"own-{uuid.uuid4().hex[:8]}"
    async with session_scope(session_factory) as s:
        await s.execute(
            text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
            {"d": marker, "k": kb_row},
        )
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value == marker


async def test_participating_scope_does_not_commit(kb_row):
    """传了 session 时不得提交：外层不提交，改动对别的会话不可见。"""
    marker = f"part-{uuid.uuid4().hex[:8]}"
    async with session_factory() as outer:
        async with session_scope(session_factory, outer) as s:
            assert s is outer
            await s.execute(
                text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
                {"d": marker, "k": kb_row},
            )
        # outer 尚未提交
        await outer.rollback()
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value != marker


async def test_exception_rolls_back(kb_row):
    """自开会话路径上抛异常时不得留下任何改动。"""
    marker = f"boom-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        async with session_scope(session_factory) as s:
            await s.execute(
                text("UPDATE knowledge_base SET description = :d WHERE id = :k"),
                {"d": marker, "k": kb_row},
            )
            raise RuntimeError("inject")
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT description FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert value != marker


async def test_kb_row_still_exists(kb_row):
    """夹具本身：确认那一行真的写进去了（否则上面三条断言都是空转）。"""
    assert await _count(kb_row) == 1
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_transaction.py -v
```

预期：`ModuleNotFoundError: No module named 'src.infra.db.transaction'`。

- [ ] **Step 3: 新建 `src/infra/db/transaction.py`**

```python
"""事务边界原语 —— 跨表原子提交的唯一入口。

每个 Repo 方法默认自开会话并提交（单表操作的合理默认）；当一次业务动作需要
**跨表原子**时（写分块 + 更新文档状态、删分块 + 软删文档/知识库），调用方用
`session_scope(session_factory)` 打开唯一的事务边界，再把该会话传给参与的
Repo / 存储方法 —— 参与者只执行语句、**不提交**，提交与回滚由边界那一层决定。

不传 `session` 的调用点行为与改造前逐字一致：自开会话、出块提交、异常随会话关闭回滚。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def session_scope(
    session_factory, session: AsyncSession | None = None
) -> AsyncIterator[AsyncSession]:
    """提供一个会话：外部传入则复用它且不提交，否则自开、出块提交。

    Args:
        session_factory: `async_sessionmaker` 实例（`src/infra/db/engine.py`）
        session: 外部事务边界提供的会话；None 表示本层自开自提交

    Yields:
        可用于执行的 AsyncSession

    Note:
        外部传入会话时**本函数绝不提交**：提交/回滚由持有该会话的外层决定。
        自开会话路径上抛异常时不提交，`async with` 退出会关闭会话并回滚未提交的改动。
    """
    if session is not None:
        yield session
        return
    async with session_factory() as own:
        yield own
        await own.commit()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_transaction.py -v
```

预期：4 passed。

- [ ] **Step 5: 让 `ChunkRepo` 的 4 个方法接受外部会话**

`src/infra/db/mysql_db/chunk_repo.py`：import 段加

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.transaction import session_scope
```

四个方法按同一形状改造（**只改签名、docstring 与 `async with` 那一行**，方法体其余部分不动）。以 `upsert_chunks` 为例：

```python
    async def upsert_chunks(
        self, rows: list[ChunkRow], session: AsyncSession | None = None
    ) -> int:
        """按 (kb_id, doc_id, chunk_index) 幂等写入，冲突时整行覆盖。

        隐含前提：同一 `doc_id` 不得跨 `kb_id` 出现 —— 主键 `id = {doc_id}:{chunk_index}`
        由本层之外生成，而 `ON CONFLICT` 只面向唯一约束 `uq_chunks_kb_doc_idx`、
        不覆盖 PK `id`；跨 kb 复用同一 `doc_id` 会撞 PK 抛 `IntegrityError`。

        Args:
            rows: 待写入的分块行（形状见 mapping.ChunkRow）
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            实际提交的行数
        """
        if not rows:
            return 0
        async with session_scope(self._sf, session) as session:
            stmt = ...  # 原有语句构造，逐字不动
            await session.execute(stmt)
        return len(rows)
```

`delete_tail` / `delete_by_doc` / `delete_by_kb` 同样处理：`async with self._sf() as session:` → `async with session_scope(self._sf, session) as session:`，**并删掉原本位于块内的 `await session.commit()`**（提交已由 `session_scope` 的自开路径负责）。
这四个方法的 docstring 各补一行 `session` 参数说明（写清"None 时自开会话并提交"）。

> ⚠ **`as session` 与形参同名是刻意的**：`session_scope(self._sf, session)` 里的 `session` 读的是**形参**（在绑定新名字之前求值），`async with … as session` 之后名字指向实际会话。这样方法体只需改一个词，不必给每个方法引入第二个变量名。**不要**改成 `as s` 再去改方法体里所有 `session.` 引用 —— 那会把"一行改动"扩散成整段改动，也让 review 看不出改了什么。

- [ ] **Step 6: 让 `DocumentRepo` / `KbRepo` 的 4 个方法接受外部会话，并暴露 `transaction()`**

`src/infra/db/mysql_db/document_repo.py`：

- import 段加 `from sqlalchemy.ext.asyncio import AsyncSession` 与 `from src.infra.db.transaction import session_scope`。
- `update_document_status` 签名改为 `async def update_document_status(self, doc_id: str, status: str, session: AsyncSession | None = None, **kwargs) -> None:`，体内同样替换 `async with` 一行、删掉块内 `commit()`；docstring 补 `session` 说明。
- `soft_delete_document(self, doc_id: str, session: AsyncSession | None = None) -> bool`：**注意它的返回值来自分支**，改造后形态：

```python
    async def soft_delete_document(
        self, doc_id: str, session: AsyncSession | None = None
    ) -> bool:
        """软删文档；文档不存在返回 False。

        Args:
            doc_id: 文档 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            True = 标记成功；False = 文档不存在
        """
        async with session_scope(self._sf, session) as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return False
            doc.is_deleted = 1
            return True
```

- `soft_delete_documents_by_kb(self, kb_id: str, session: AsyncSession | None = None) -> None` 同形。
- 文件末尾（类内）新增：

```python
    def transaction(self):
        """打开一个事务边界；其中的 Repo / 存储方法须传入 `session=`。

        Returns:
            `session_scope(self._sf)` 异步上下文管理器
        """
        return session_scope(self._sf)
```

`src/infra/db/mysql_db/kb_repo.py`：`soft_delete_kb` 同形改造 + 同样的 `transaction()`。

- [ ] **Step 7: 让 `PgVectorStore` 的 3 个方法接受外部会话**

`src/infra/db/vector_store/pg_store.py`：

- import 段加 `from sqlalchemy.ext.asyncio import AsyncSession`。
- `add_chunks(self, kb_id, chunks, doc_id, embeddings=None, session: AsyncSession | None = None) -> int`：把 `await self._repo.upsert_chunks(rows)` 与 `await self._repo.delete_tail(kb_id, doc_id, len(rows))` 都传入 `session=session`；docstring 补一句：**传入 session 时本方法不提交**，并要求调用方保证 embedding 已在事务外算好。
- `delete_document(self, kb_id, doc_id, session: AsyncSession | None = None) -> int` 与 `delete_collection(self, kb_id, session: AsyncSession | None = None) -> bool` 同样把 `session` 透传给 repo。

- [ ] **Step 8: 追加"参与者不提交"的行为测试**

在 `tests/infra/db/test_transaction.py` 末尾追加（用真实 repo，验证签名扩展后语义仍正确）：

```python
async def test_repo_method_with_outer_session_does_not_commit(kb_row):
    """Repo 方法在外部会话下只执行语句：外层回滚后改动不可见。"""
    from src.infra.db.mysql_db.kb_repo import KbRepo

    repo = KbRepo(session_factory)
    async with repo.transaction() as s:
        ok = await repo.soft_delete_kb(kb_row, session=s)
        assert ok is True
        await s.rollback()
    async with session_factory() as s:
        is_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert is_deleted == 0


async def test_repo_method_without_session_still_commits(kb_row):
    """不传 session 时行为与改造前一致：出块即提交。"""
    from src.infra.db.mysql_db.kb_repo import KbRepo

    repo = KbRepo(session_factory)
    assert await repo.soft_delete_kb(kb_row) is True
    async with session_factory() as s:
        is_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_row}
        )
    assert is_deleted == 1
```

⚠ 这两个用例会改动 `kb_row`，而 fixture 的清理只删行、不还原 `is_deleted` —— 由于每例都新建自己的 kb_id，互不干扰 ✓。

- [ ] **Step 9: 跑全量相关测试与门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/ -v 2>&1 | tail -12
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -4
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
```

预期：`tests/infra/db/` 全过（含既有用例 —— 它们**不传** `session`，走自开自提路径）；全量不回归；`ruff` 无错；`pyright` 不新增 error。

- [ ] **Step 10: 提交**

```bash
git add src/infra/db/transaction.py src/infra/db/mysql_db/ src/infra/db/vector_store/pg_store.py tests/infra/db/test_transaction.py
git commit -m "feat(p4): 事务边界原语 session_scope + 参与方法接受外部会话"
```

---

### Task 2: 入库路径同事务（`add_chunks` 与文档状态一次提交）

**Files:**
- Modify: `src/services/document_service.py`（处理流水线尾部）
- Test: `tests/infra/db/test_atomicity.py`（新建，故障注入）

**Interfaces:**
- Consumes: `DocumentRepo.transaction()`（Task 1）、`PgVectorStore.add_chunks(..., session=)`、`DocumentRepo.update_document_status(..., session=)`
- Produces: 入库路径的原子性；`tests/infra/db/test_atomicity.py` 的 `atomic_kb` fixture（Task 3 复用）

- [ ] **Step 1: 写失败测试（故障注入）**

新建 `tests/infra/db/test_atomicity.py`：

```python
"""三条跨表路径的原子性验收（故障注入，真实 PG）。

判据不是"成功路径能跑通"，而是"中途注入异常后两边都不落库"。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.chunking.validator import ChunkData
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import DocumentRepo
from src.infra.db.vector_store.pg_store import PgVectorStore
from src.services.document_service import DocumentService

pytestmark = pytest.mark.asyncio


class _FakeEmbedder:
    """本文件的用例都显式传 embeddings，故不需要真实向量化；仅为构造 PgVectorStore。"""

    def embed_query(self, text: str) -> list[float]:
        """返回全零 1024 维向量（本文件不会走到这里）。"""
        return [0.0] * 1024

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """逐条返回全零向量（本文件不会走到这里）。"""
        return [[0.0] * 1024 for _ in texts]


@pytest_asyncio.fixture
async def atomic_kb():
    """建真实 KB，返回 (kb_id, doc_repo)，测后清理该 KB 的一切痕迹。"""
    kb_id = f"p4atom-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p4test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p4-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id, DocumentRepo(session_factory)
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM document WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def _insert_doc(
    doc_id: str, kb_id: str, *, status: str = "processing", user_id: str = ""
) -> None:
    """插一行最小 document。

    Args:
        doc_id: 文档 ID
        kb_id: 所属知识库
        status: 文档状态（删文档的用例需要 ready）
        user_id: 属主（删文档的用例需要与调用者一致）
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO document (id, kb_id, filename, file_type, file_size,"
                " status, is_deleted, user_id, processing_state)"
                " VALUES (:i, :k, 'a.txt', 'txt', 10, :st, 0, :u, 'running')"
            ),
            {"i": doc_id, "k": kb_id, "st": status, "u": user_id},
        )
        await s.commit()


async def _status(doc_id: str) -> str:
    """读文档状态。"""
    async with session_factory() as s:
        return str(
            await s.scalar(
                text("SELECT status FROM document WHERE id = :i"), {"i": doc_id}
            )
        )


async def _is_deleted(doc_id: str) -> int:
    """读文档软删标记。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT is_deleted FROM document WHERE id = :i"), {"i": doc_id}
            )
        )


async def _chunk_count(kb_id: str, doc_id: str) -> int:
    """读某文档的分块数。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM chunks WHERE kb_id = :k AND doc_id = :i"),
                {"k": kb_id, "i": doc_id},
            )
        )


async def test_ingest_is_atomic_when_status_update_fails(atomic_kb, monkeypatch):
    """分块写入与文档状态更新同一事务：状态更新失败时**分块也不得落库**。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id)

    svc = DocumentService(
        doc_repo=doc_repo,
        vector_store=PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder()),
        router=None,
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: status update failed")

    monkeypatch.setattr(doc_repo, "update_document_status", _boom)

    with pytest.raises(RuntimeError):
        await svc._write_chunks_and_mark_ready(
            kb_id=kb_id,
            doc_id=doc_id,
            chunks=[ChunkData(content="资产负债率上升", metadata={})],
            embeddings=[[0.1] * 1024],
        )

    assert await _chunk_count(kb_id, doc_id) == 0
    assert await _status(doc_id) == "processing"
```

> `PgVectorStore(chunk_repo=None, …)` 会走它的默认分支自建 `ChunkRepo(session_factory)` —— 与 `doc_repo` 用同一个引擎，故共享同一连接池 ✓。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_atomicity.py -v
```

预期：`AttributeError: 'DocumentService' object has no attribute '_write_chunks_and_mark_ready'`。

- [ ] **Step 3: 在 `DocumentService` 抽出「写分块 + 标记就绪」的事务方法**

`src/services/document_service.py`：把流水线尾部（原 `add_chunks` → `update_document_status` 两段）替换为对下面这个新方法的调用，并把计时日志留在流水线里（用返回的计数）：

```python
    async def _write_chunks_and_mark_ready(
        self,
        kb_id: str,
        doc_id: str,
        chunks: list[ChunkData],
        embeddings: list[list[float]],
        strategy: str = "",
    ) -> int:
        """把「写分块」与「文档标记 ready」放进同一个事务。

        两者必须原子：进程死在中间会留下「有分块、文档未 ready」的孤儿。
        embedding 由调用方在**进入本方法之前**算好（外网调用不得进事务）。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID
            chunks: 分块列表
            embeddings: 与 chunks 一一对应的向量（已在事务外算好）
            strategy: 分块策略（写入文档元信息）

        Returns:
            实际写入的分块数量

        Raises:
            Exception: 任一步失败时整体回滚并向上抛（调用方负责把文档标记为 failed）
        """
        async with self._doc_repo.transaction() as session:
            count = await self.vector_store.add_chunks(
                kb_id, chunks, doc_id, embeddings, session=session
            )
            await self._doc_repo.update_document_status(
                doc_id,
                "ready",
                session=session,
                chunk_count=count,
                processing_state="completed",
                processing_progress=100,
                processing_message=f"处理完成，共 {count} 个分块",
                chunk_strategy=strategy,
            )
        return count
```

调用点（流水线里原来的两段）替换为：

```python
                # 分块写入 + 文档状态：同一事务（embedding 已在上方事务外算好）
                t2 = time.perf_counter()
                count = await self._write_chunks_and_mark_ready(
                    kb_id=kb_id,
                    doc_id=doc_id,
                    chunks=chunks,
                    embeddings=chunk_embeddings,
                    strategy=strategy,
                )
                t3 = time.perf_counter()
```

并把原来紧跟其后的 `logger.info("Document processed: ...")` 里的 `t3 - t2`（store 段耗时）语义改为"事务段耗时"（注释同步写清，别留"store"这个旧词）。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_atomicity.py -v
```

预期：1 passed。

- [ ] **Step 5: 跑受影响的服务层测试**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/ tests/api/test_documents.py -v 2>&1 | tail -12
```

预期：全过。**若失败**：多半是测试替身（`AsyncMock` 的 `doc_repo`）没有 `transaction()` —— 在测试的 `_make_service` 里给 `doc_repo.transaction` 配一个异步上下文管理器替身，例如

```python
    doc_repo = AsyncMock()
    doc_repo.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock()),
            __aexit__=AsyncMock(return_value=False),
        )
    )
```

（**不要**改生产代码去迁就替身。）

- [ ] **Step 6: 提交**

```bash
git add src/services/document_service.py tests/infra/db/test_atomicity.py tests/services/ tests/api/test_documents.py
git commit -m "feat(p4): 入库路径同事务 —— 分块与文档状态一次提交 + 故障注入验收"
```

---

### Task 3: 删除路径收编为一条并同事务

**Files:**
- Modify: `src/services/document_service.py`（`delete_document`）
- Modify: `src/services/app_service.py`（`delete_knowledge_base`）
- Modify: `src/api/documents.py`（删除端点走 service；上传端点的 `getattr` 改显式判断）
- Test: `tests/infra/db/test_atomicity.py`（追加两条故障注入）

**Interfaces:**
- Consumes: Task 1 的 `session_scope` / `transaction()` / 各方法的 `session=`；Task 2 的 `atomic_kb` fixture
- Produces:
  - `DocumentService.delete_document(kb_id, doc_id, user_id)`：内部一个事务（删分块 + 软删文档）
  - `AppService.delete_knowledge_base(kb_id)`：内部一个事务（软删文档 + 删分块 + 软删 KB），失败向上抛
  - `POST /api/kbs/documents/delete`：只转调 service（不再越层）

- [ ] **Step 1: 追加三条失败测试**

在 `tests/infra/db/test_atomicity.py` 追加：

```python
async def test_delete_document_is_atomic_when_chunk_delete_fails(
    atomic_kb, monkeypatch
):
    """删分块失败时文档**不得**被软删（否则产生永久孤儿分块）。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())
    svc = DocumentService(doc_repo=doc_repo, vector_store=store, router=None)

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: chunk delete failed")

    monkeypatch.setattr(store, "delete_document", _boom)
    with pytest.raises(RuntimeError):
        await svc.delete_document(kb_id, doc_id, user_id="p4test")

    assert await _is_deleted(doc_id) == 0


async def test_delete_document_happy_path_removes_both(atomic_kb):
    """两步都成功时：分块归零 + 文档软删。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())
    svc = DocumentService(doc_repo=doc_repo, vector_store=store, router=None)

    await svc._write_chunks_and_mark_ready(
        kb_id=kb_id,
        doc_id=doc_id,
        chunks=[ChunkData(content="资产负债率上升", metadata={})],
        embeddings=[[0.1] * 1024],
    )
    assert await _chunk_count(kb_id, doc_id) == 1

    result = await svc.delete_document(kb_id, doc_id, user_id="p4test")

    assert result["status"] == "deleted"
    assert await _chunk_count(kb_id, doc_id) == 0
    assert await _is_deleted(doc_id) == 1


async def test_delete_knowledge_base_is_atomic_when_chunk_delete_fails(
    atomic_kb, monkeypatch
):
    """删知识库时删分块失败 → 文档与知识库都**不得**被软删（且异常向上抛）。"""
    from src.infra.db.mysql_db import KbRepo
    from src.services.app_service import AppService

    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())
    svc = AppService.__new__(AppService)          # 绕开构造器：本用例只测 delete 编排
    svc._doc_repo = doc_repo
    svc._kb_repo = KbRepo(session_factory)
    svc.vector_store = store

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: chunk delete failed")

    monkeypatch.setattr(store, "delete_collection", _boom)
    with pytest.raises(RuntimeError):
        await svc.delete_knowledge_base(kb_id)

    assert await _is_deleted(doc_id) == 0
    async with session_factory() as s:
        kb_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_id}
        )
    assert kb_deleted == 0
```

> `AppService.__new__(AppService)` 绕开构造器是为了让用例只覆盖"删除编排"这一段（构造器会装 ChatManager / AgentService 等重组件）。**不要**为了这条用例去改 `AppService.__init__` 的签名。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_atomicity.py -v
```

预期：`test_delete_knowledge_base_is_atomic_when_chunk_delete_fails` FAIL（当前实现吞异常 → `pytest.raises(RuntimeError)` 抓不到，且 KB 已被软删）；其余两条的通过与否说明的是**顺序**而非回滚，原子的部分由本任务 Step 4 的反向验证与上面这条用例承担。

- [ ] **Step 3: 改造 `DocumentService.delete_document` 为单事务**

```python
    async def delete_document(self, kb_id: str, doc_id: str, user_id: str) -> dict:
        """删除文档：校验 → 同一事务内「删分块 + 软删文档」。

        两条写操作必须原子：分块删失败时若已软删文档，会留下永久孤儿分块
        （且该文档再也删不掉分块 —— 重试时文档已是 deleted）。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID
            user_id: 调用者用户 ID（与上传时写入的属主比对）

        Returns:
            dict：doc_id / filename / status

        Raises:
            BusinessError: 文档不存在（404）/ 非属主（403）/ 状态不允许（409）
            Exception: 底层删除失败时整体回滚并向上抛
        """
        doc = await self._doc_repo.get_document(doc_id)
        if not doc:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        if doc.user_id != user_id:
            raise BusinessError(
                Code.DOC_DELETE_NOT_ALLOWED,
                Code.DOC_DELETE_NOT_ALLOWED_MSG,
                403,
            )
        if doc.status not in ("ready", "failed"):
            raise BusinessError(
                Code.DOC_STATUS_CONFLICT,
                Code.DOC_STATUS_CONFLICT_MSG,
                409,
            )
        async with self._doc_repo.transaction() as session:
            await self.vector_store.delete_document(kb_id, doc_id, session=session)
            deleted = await self._doc_repo.soft_delete_document(doc_id, session=session)
        if not deleted:
            raise BusinessError(Code.DOC_NOT_FOUND, Code.DOC_NOT_FOUND_MSG, 404)
        logger.info("Document deleted: {} ({})", doc.filename, doc_id)
        return {"doc_id": doc_id, "filename": doc.filename, "status": "deleted"}
```

> ⚠ `soft_delete_document` 的 `False` 分支返回后事务会**提交**（`session_scope` 的出块提交），此时什么也没改 —— 属无害空提交；随后抛 404。

- [ ] **Step 4: 改造 `AppService.delete_knowledge_base` 为单事务且不吞异常**

```python
    async def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库：同一事务内「软删文档 + 删分块 + 软删 KB」。

        三步必须原子：分块删除失败时不得软删知识库（否则留下永久孤儿分块）。
        删除失败 SHALL NOT 被吞掉 —— 静默降级会让孤儿长期存在且不可观测。

        Args:
            kb_id: 知识库 ID

        Returns:
            (是否成功, 提示文案)；知识库不存在时返回 (False, "知识库不存在")

        Raises:
            Exception: 任一步失败时整体回滚并向上抛
        """
        async with self._kb_repo.transaction() as session:
            await self._doc_repo.soft_delete_documents_by_kb(kb_id, session=session)
            deleted = await self.vector_store.delete_collection(kb_id, session=session)
            ok = await self._kb_repo.soft_delete_kb(kb_id, session=session)
        if not ok:
            logger.warning("Knowledge base '{}' not found for deletion", kb_id)
            return False, "知识库不存在"
        logger.info("Knowledge base soft-deleted: {} (chunks deleted={})", kb_id, deleted)
        return True, "知识库已删除"
```

⚠ 改造后 `logger.info("chunks deleted ...")` 与 `except Exception: logger.warning("chunk delete failed ...")` 两段都删除（前者并入上面这条 info，后者按 Ruling 3 去掉）。

- [ ] **Step 5: 改造 API 端点：只转调 service**

`src/api/documents.py` 的删除端点整段替换为：

```python
@router.post("/kbs/documents/delete", response_model=ResponseModel)
async def delete_document(
    body: DocumentDeleteRequest,
    request: Request,
    svc: AppService = Depends(get_app_service),
):
    """删除文档：软删文档 + 删除其全部分块（同一事务）。

    Args:
        body: 文档删除请求体，含 kb_id 和 doc_id
        request: FastAPI 请求（`state.user_id` 由认证中间件写入）
        svc: 应用服务

    Returns:
        ResponseModel: data 含 success 布尔值

    Raises:
        BusinessError: 文档不存在（404）/ 非属主（403）/ 状态不允许（409）
    """
    user_id = request.state.user_id
    result = await svc.document.delete_document(body.kb_id, body.doc_id, user_id)
    logger.info(
        "Document deleted: kb_id={} doc_id={} user_id={}",
        body.kb_id,
        body.doc_id,
        user_id,
    )
    return ResponseModel(data=DocumentDeleteResponse(success=result["status"] == "deleted"))
```

**同时删除该文件里不再需要的 `logger` 之外的依赖？不 —— `logger` 仍在用。** 但要确认 `import` 段没有因这次改动变成未使用的（用 `ruff check src/api/documents.py` 判 F401）。

- [ ] **Step 6: 修掉上传端点的隐式兜底（F21）**

`src/api/documents.py:110` 的

```python
    user_id = getattr(request.state, "user_id", "") if request else ""
```

改为显式判断（该文件其余地方的 `getattr` 若属同一文件同一次改动可一并处理，但**不要**扩散到别的文件）：

```python
    if request is not None and hasattr(request.state, "user_id"):
        user_id = request.state.user_id
    else:
        user_id = ""
```

> `request.state` 是 Starlette 的属性包，`hasattr` 是这里唯一可靠的判据（未经过中间件的路径不会写入该键）；**不得**改成 `request.state.user_id` 裸取，否则无中间件的测试客户端会 `AttributeError`。

- [ ] **Step 7: 跑测试与全量**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_atomicity.py -v
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -4
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
```

预期：`test_atomicity.py` 全过；全量不回归。
**若 `tests/api/test_documents.py` 的删除用例失败**：它此前断言的是"先软删再删分块"的旧行为 —— 按新契约更新它的断言（断言端点调了 service 且返回 `success=True`），**不要**为了迁就旧断言保留两条路径。

- [ ] **Step 8: 加一条"路径唯一"的守卫测试**

新建 `tests/api/test_no_direct_repo_access.py`：

```python
"""文档端点不得直取 Repo / 向量存储（api/ 只做参数校验与路由转发）。"""

import inspect

from src.api import documents


def test_document_routes_do_not_touch_repo_or_store_directly():
    """路由源码里不得出现 `_doc_repo` / `_kb_repo` / `vector_store` 直取。"""
    source = inspect.getsource(documents)
    for forbidden in ("_doc_repo", "_kb_repo", "vector_store"):
        assert forbidden not in source, f"api/documents.py 仍直取 {forbidden}"


def test_document_routes_do_not_import_infra():
    """api/ 不得 import infra/（分层调用规则）。"""
    source = inspect.getsource(documents)
    assert "from src.infra" not in source
    assert "import src.infra" not in source
```

- [ ] **Step 9: 提交**

```bash
git add src/services/ src/api/documents.py tests/infra/db/test_atomicity.py tests/api/
git commit -m "feat(p4): 删除路径收编为一条并同事务 —— 端点只转调 service，KB 删除不再吞异常"
```

---

### Task 4: 事务化验收（E2E + 语料核验）

**Files:**
- Modify: 无代码（本任务只跑验收与数据核验；产出写进报告）
- Test: 无新增（复用 Task 2/3 的故障注入 + 本任务的真实链路）

**Interfaces:**
- Consumes: Task 1–3 的全部产物；dev 栈（app / postgres / redis / minio）
- Produces: E2E 观测记录 + 语料复位证据

- [ ] **Step 1: 确认应用是最新代码**

```bash
docker compose restart app
docker compose logs --tail=20 app | tail -8
```

- [ ] **Step 2: 删文档的真实路径（E2E）**

用 `cookbook.md` 的手工调试凭据（`.env` 的 `TEST_ACCOUNT` / `TEST_PASSWORD`）与 P1 勘误后的端点（**全为 POST**）：

```bash
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"account\":\"$TEST_ACCOUNT\",\"password\":\"$TEST_PASSWORD\"}" | python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
KB=$(curl -s -X POST $BASE/api/kbs -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"p4-smoke","description":"P4 E2E"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['id'])")
echo "kb=$KB"
```

上传一个含中文财务术语的小文本（内容例：「公司资产负债率上升，研发费用增加，净利润同比下降。」），等 `ready`，记录删除前的计数：

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM chunks WHERE kb_id='$KB'), (SELECT count(*) FROM document WHERE kb_id='$KB' AND is_deleted=0);"
```

然后**用文档删除端点**删掉它（cookie 方式最省事：`--cookie "token=$TOKEN"`，与前端一致）：

```bash
curl -s -X POST $BASE/api/kbs/documents/delete --cookie "token=$TOKEN" \
  -H 'Content-Type: application/json' -d "{\"kb_id\":\"$KB\",\"doc_id\":\"<DOC_ID>\"}"
```

预期：`{"code":0,...,"data":{"success":true}}`；随后 chunks 归零、`document.is_deleted=1`：

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM chunks WHERE kb_id='$KB'), (SELECT count(*) FROM document WHERE kb_id='$KB' AND is_deleted=0), (SELECT count(*) FROM document WHERE kb_id='$KB' AND is_deleted=1);"
```

预期：`0|0|1`。

- [ ] **Step 3: 删知识库的真实路径（E2E）**

```bash
curl -s -X POST $BASE/api/kbs/delete --cookie "token=$TOKEN" \
  -H 'Content-Type: application/json' -d "{\"kb_id\":\"$KB\"}"
```

（端点名以 `src/api/knowledge_base.py` 的注册为准 —— 若路径不同，按实际端点调用并在报告里写明。）

预期：`knowledge_base.is_deleted=1`、其 document 全部 `is_deleted=1`、`chunks` 中该 kb 归零：

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT is_deleted FROM knowledge_base WHERE id='$KB'), (SELECT count(*) FROM chunks WHERE kb_id='$KB'), (SELECT count(*) FROM document WHERE kb_id='$KB' AND is_deleted=0);"
```

预期：`1|0|0`。

- [ ] **Step 4: 故障注入的真实证据（不只跑单测）**

单测（Task 2/3）已覆盖；本步再取一次**独立**证据：临时把 `AppService.delete_knowledge_base` 的 `soft_delete_kb` 调用前插一行 `raise RuntimeError("fault-inject")`（或直接在测试里 monkeypatch，视你更顺手），确认：
1. `knowledge_base.is_deleted` 仍为 0；
2. 该 kb 的 `document` 仍 `is_deleted=0`；
3. `chunks` 仍在。

**改完必须还原**（`git checkout -- src/services/app_service.py`）并用 `git status --short` 证明工作区干净。把三段证据（插入、观测、还原）写进报告。

- [ ] **Step 5: 清理 E2E 痕迹并复验语料**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(*) FROM users);"
```

按 Task 0 记录的基线清理本轮新建的 KB / 文档 / 分块（**不要**碰 `users`），使计数回到 `5|0|176|1`。若 Task 5 尚未执行（本任务在 Task 5 之前），**全量 pytest 尚未跑**，所以此时不该有测试残留。

- [ ] **Step 6: 提交（若本任务产生了代码或测试改动）**

```bash
git status --short
# 若只有报告与数据变化，则无需提交；若有改动：
git add -A && git commit -m "test(p4): 事务化 E2E 验收记录"
```

---

### Task 5: 真实 PG 测试的 teardown（修 F-31）

**Files:**
- Modify: `tests/infra/db/test_db.py`
- Modify: 其它直写真实 PG 但无 teardown 的测试文件（以 Task 0 Step 6 的清单为准）
- Test: 既有用例本身

**Interfaces:**
- Consumes: `tests/reset_data.py` 的 `reset_pg`（**不要**在 fixture 里调它：它会清掉 176 分块语料）
- Produces: 直写真实 PG 的测试在结束后不留残留 → 后续全量门禁不再污染 dev 库（这直接决定 Task 11 之后能否安全跑门禁）

- [ ] **Step 1: 建"防污染"的失败测试**

在 `tests/infra/db/test_db.py` 末尾追加（这条测试断言**本文件跑完后不留痕**，天然会先失败）：

```python
async def test_file_leaves_no_rows_behind_note():
    """本文件所有真实 PG 写入都必须被 fixture 清理（防污染 dev 库）。

    该用例本身不做写入：它存在的意义是把"清理"变成 fixture 的契约，
    由下面的 autouse fixture 承担；若 fixture 被删掉，其它用例的残留
    会被 test_no_test_user_rows_after_cleanup 抓到。
    """
    assert True
```

并把该文件的写入用例改为共用带清理的 fixture：文件顶部新增

```python
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_user_rows():
    """每个用例前后清掉 `test-user` 的记录（直写真实 PG 的代价由本 fixture 承担）。

    刻意**不**调 `tests/reset_data.reset_pg()` —— 那会连 176 分块语料一起清掉。
    只删本套测试自己造的数据（`user_id='test-user'` 的知识库及其文档/分块）。
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "DELETE FROM chunks WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(text("DELETE FROM document WHERE user_id = 'test-user'"))
        await s.execute(text("DELETE FROM knowledge_base WHERE user_id = 'test-user'"))
        await s.commit()
    yield
    async with session_factory() as s:
        await s.execute(
            text(
                "DELETE FROM chunks WHERE kb_id IN"
                " (SELECT id FROM knowledge_base WHERE user_id = 'test-user')"
            )
        )
        await s.execute(text("DELETE FROM document WHERE user_id = 'test-user'"))
        await s.execute(text("DELETE FROM knowledge_base WHERE user_id = 'test-user'"))
        await s.commit()
```

- [ ] **Step 2: 证明它真的在清理**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM knowledge_base WHERE user_id='test-user';"
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_db.py -q 2>&1 | tail -3
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM knowledge_base WHERE user_id='test-user';"
```

预期：跑之前 0（或历史残留，先手工清一次）、跑之后仍为 0。

- [ ] **Step 3: 逐个核对其余直写真实 PG 的测试文件**

按 Task 0 Step 6 的清单逐个检查：每个文件是否**在自己造的数据上**有 teardown（`tests/infra/db/p2_fakes.py` 的 `store_and_kb`、`tests/infra/db/test_chunk_repo_lexical.py` 的 `lexical_kb` 是范式）。
**对没有 teardown 的**：按 Step 1 的同款 fixture 补上（只删它自己造的、带唯一前缀的 kb_id）。**不要**顺手重写有 teardown 的文件。

```bash
grep -rn "yield" tests/infra/db/*.py | grep -c "yield"   # 有 teardown 的 fixture 数
```

- [ ] **Step 4: 全量验证不再污染**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks);"
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -4
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks);"
```

预期：两次查询**数字相同**（`5|0|176`），即全量门禁不再改变语料。**这是本任务的核心判据。**

- [ ] **Step 5: 提交**

```bash
git add tests/infra/db/
git commit -m "test(p4): 真实 PG 测试补 teardown —— 全量门禁不再污染 dev 库"
```

---

### Task 6: 依赖、配置与一次性脚本的 Chroma/BM25 清理

**Files:**
- Delete: `deploy/chroma/Dockerfile`、`scripts/{migrate_chroma_to_pg,dense_equivalence_check,lexical_probe,lexical_probe_report}.py`
- Delete: `tests/scripts/{test_migrate_chroma_to_pg,test_dense_equivalence_check,test_lexical_probe}.py`、`tests/fixtures/dense_equivalence_queries.json`
- Modify: `src/config/settings.py`、`src/core/log_events.py`、`src/core/log_event_specs.py`、`scripts/clean_all_data.py`、`pyproject.toml`、`tests/config/test_no_bm25_leftovers.py`
- Test: `tests/config/test_no_chroma_leftovers.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `src/` 内 `chroma` 零命中；`pyproject.toml` 无 `chromadb` / `rank_bm25`；`deploy/chroma/` 不存在

⚠ **本任务要求重建镜像**（依赖集合变了）：`docker compose build --no-cache app`。**不要**跳过 —— 否则容器里仍有 `chromadb`，`test_no_chroma_leftovers` 的 import 断言会失败。

- [ ] **Step 1: 写守卫测试**

新建 `tests/config/test_no_chroma_leftovers.py`（与既有 `test_no_bm25_leftovers.py` 同形）：

```python
"""退役 ChromaDB 与一次性搬迁产物的残留检查。"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests", "scripts", "alembic", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")

ALLOWED = {
    "tests/config/test_no_chroma_leftovers.py",
    "tests/config/test_no_bm25_leftovers.py",
}
PATTERNS = (
    re.compile(r"^\s*(from|import)\s+chromadb", re.MULTILINE),
    re.compile(r"\bCHROMA_[A-Z_]+\b"),
    re.compile(r"\bdeploy/chroma\b"),
)


def test_chroma_dependency_is_gone():
    """依赖与镜像里都不该再有 chromadb。"""
    pytest.importorskip("importlib")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("chromadb")


def test_rank_bm25_dependency_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rank_bm25")


def test_one_shot_scripts_are_gone():
    for name in (
        "migrate_chroma_to_pg.py",
        "dense_equivalence_check.py",
        "lexical_probe.py",
        "lexical_probe_report.py",
    ):
        assert not (REPO / "scripts" / name).exists(), f"{name} 应随 Chroma 退役"


def test_rewrite_content_seg_is_kept():
    """分词器变更的操作协议必须保留（它不是一次性产物）。"""
    assert (REPO / "scripts" / "rewrite_content_seg.py").exists()


def test_deploy_chroma_is_gone():
    assert not (REPO / "deploy" / "chroma").exists()


def test_no_chroma_leftovers_in_code():
    offenders: list[str] = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.exists():
            continue
        for pattern in SCAN_GLOBS:
            for path in root.rglob(pattern):
                rel = str(path.relative_to(REPO))
                if rel in ALLOWED or "__pycache__" in rel:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for regex in PATTERNS:
                    if regex.search(text):
                        offenders.append(f"{rel}: {regex.pattern}")
                        break
    assert offenders == [], f"仍有 Chroma 残留：{offenders}"


def test_settings_no_longer_exports_chroma():
    settings = importlib.import_module("src.config.settings")
    for name in (
        "CHROMA_HOST",
        "CHROMA_PORT",
        "CHROMA_COLLECTION_PREFIX",
        "CHROMA_PERSIST_DIR",
    ):
        assert not hasattr(settings, name), f"settings 仍导出 {name}"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_no_chroma_leftovers.py -v
```

预期：多条 FAIL（依赖仍在、脚本仍在、常量仍在）。

- [ ] **Step 3: 删一次性脚本与其测试、fixture**

```bash
git rm deploy/chroma/Dockerfile
git rm scripts/migrate_chroma_to_pg.py scripts/dense_equivalence_check.py scripts/lexical_probe.py scripts/lexical_probe_report.py
git rm tests/scripts/test_migrate_chroma_to_pg.py tests/scripts/test_dense_equivalence_check.py tests/scripts/test_lexical_probe.py
git rm tests/fixtures/dense_equivalence_queries.json
ls tests/scripts/   # 预期只剩 __init__.py（若有）与 test_rewrite_content_seg.py
```

- [ ] **Step 4: 删 `settings.py` 的四个 `CHROMA_*` 常量**

`src/config/settings.py` 第 184–192 行整段删除：

```python
# ====== ChromaDB ======
# 向量数据库服务地址（Docker 容器内用容器名，开发环境用 localhost）
CHROMA_HOST: str = os.getenv("CHROMA_HOST", "localhost")
# 向量数据库服务端口（ChromaDB 默认 8000）
CHROMA_PORT: int = int(os.getenv("CHROMA_PORT", "8000"))
# collection 名称前缀，每个知识库对应一个 collection（如 kb_<uuid>）
CHROMA_COLLECTION_PREFIX: str = os.getenv("CHROMA_COLLECTION_PREFIX", "kb_")
# （已废弃）向量数据库持久化目录 — 改用独立 ChromaDB 容器后不再需要本地路径
CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_persist")
```

- [ ] **Step 5: 删死日志事件 `CHROMA_CLIENT_READY`（两处同删）**

`src/core/log_events.py:89` 删 `    CHROMA_CLIENT_READY = "chroma client ready"`；
`src/core/log_event_specs.py:153-154` 删对应的 `EventSpec` 两条。
删完立即验证注册表一致：

```bash
.venv/bin/python -c "import src.core.log_events as m; print('registry ok', len(m.Event))"
```

- [ ] **Step 6: `clean_all_data.py` 去掉 chroma 分支**

`scripts/clean_all_data.py`：

- 删 import 段的 `CHROMA_COLLECTION_PREFIX` / `CHROMA_PERSIST_DIR`；
- 删 `reset_chromadb()` 整个函数（含函数内 `import chromadb` / `from chromadb.config import Settings`）；
- 删 `main()` 里对它的调用与清单里的那一行（打印与执行两处）。

改完 `.venv/bin/python -c "import ast; ast.parse(open('scripts/clean_all_data.py').read())"` 确认语法无误，并跑 `ruff check scripts/clean_all_data.py`。

- [ ] **Step 7: 删两个依赖并重建镜像**

`pyproject.toml`：删第 14 行 `    "chromadb==1.5.9",` 与第 34 行 `    "rank_bm25>=0.2.2",`。

```bash
docker compose build --no-cache app
docker compose up -d --force-recreate app
docker compose exec -T app python -c "import importlib
for m in ('chromadb','rank_bm25'):
    try:
        importlib.import_module(m); print('STILL PRESENT', m)
    except ModuleNotFoundError:
        print('gone', m)"
```

预期两行都是 `gone`。

- [ ] **Step 8: 收缩 `test_no_bm25_leftovers.py` 的 ALLOWED 表**

探针脚本已删，其例外不再需要：把 `"scripts/lexical_probe.py"` 从 `ALLOWED` 中移除，并把 `PATTERNS` 里为探针加的 `rank_bm25` 导入模式保留（它现在必须零命中）。
跑一次确认：

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_no_bm25_leftovers.py -v
```

- [ ] **Step 9: 跑守卫与全量**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/ -v
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -4
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
```

预期：两条守卫全绿；全量不回归（注意：`tests/scripts/` 少了三个文件，用例总数会下降）；语料不变（Task 5 已保证）。

- [ ] **Step 10: 提交**

```bash
git add -A
git commit -m "chore(p4): 退役 Chroma 与 rank_bm25 —— 依赖/配置/脚本/事件/守卫全清"
```

---

### Task 7: compose 与 Dockerfile 同构（dev / prod）

**Files:**
- Modify: `docker-compose.yml`（去 chroma 卷与挂载）
- Modify: `docker-compose.prod.yml`（同上 + 补 `LOG_DIR`）
- Modify: `Dockerfile`（去 `/data/chroma`）

**Interfaces:**
- Consumes: Task 6（Chroma 已从代码/依赖中退役）
- Produces: 两份 compose 都不再声明 `chroma_onnx_cache`；prod 的日志落进 `app_logs` 卷

⚠ 改 compose 后要 `docker compose up -d --force-recreate app`（`restart` 不会重建容器、不应用 volumes/env 变化）。

- [ ] **Step 1: 先看差异，确认改动面**

```bash
docker compose config 2>&1 | tail -3
docker compose -f docker-compose.prod.yml config 2>&1 | tail -3
grep -n "chroma" docker-compose.yml docker-compose.prod.yml Dockerfile
```

把命中行记进报告（**预期**：两份 compose 各 2 处 —— volumes 段的卷声明 + app 的挂载；dev 另有一处 `./data/chroma_persist` 挂载；`Dockerfile` 一处 `VOLUME`）。

- [ ] **Step 2: 删 dev 的 chroma 卷与挂载**

`docker-compose.yml`：
- `app.volumes` 里删 `- chroma_onnx_cache:/root/.cache/chroma` 与 `- ./data/chroma_persist:/app/data/chroma_persist`；
- 顶层 `volumes:` 段删 `chroma_onnx_cache:`（含其 `name:` 行）。

- [ ] **Step 3: 删 prod 的同名项并补 `LOG_DIR`**

`docker-compose.prod.yml`：
- `app.volumes` 里删 `- chroma_onnx_cache:/root/.cache/chroma`；
- 顶层 `volumes:` 段删 `chroma_onnx_cache:`；
- `app.environment` 补一行（**与 dev 对齐**）：

```yaml
      LOG_DIR: /data/logs
```

> 这是 F6 的既有缺陷：prod 有 `app_logs:/data/logs` 卷却**没有** `LOG_DIR`，而 `src/core/logging.py` 默认 `logs` → 日志落在容器可写层，容器重建即丢。补上后与 dev 同构。

- [ ] **Step 4: 去掉 Dockerfile 的 `/data/chroma`**

`Dockerfile:36`：

```dockerfile
VOLUME ["/data/chroma", "/data/logs"]
```

改为

```dockerfile
VOLUME ["/data/logs"]
```

- [ ] **Step 5: 确认两份 compose 仍能解析，且保留"不同机"前置**

```bash
docker compose config >/dev/null && echo "dev ok"
docker compose -f docker-compose.prod.yml config >/dev/null && echo "prod ok"
grep -n "不同机\|不同机器\|container_name\|container 名" docker-compose.prod.yml | head
```

预期：两条 `ok`；prod 头部的"prod 与 dev 必须在不同机器上运行（project name / 容器名 / 全部卷名完全相同）"注释仍在。
**若那份注释在 Task 7 之前已被删除**，恢复它（它是 `design.md` D1 的显式要求，且同机 `up` 会直接失败）。

- [ ] **Step 6: 重创容器并验证日志落卷**

```bash
docker compose up -d --force-recreate app
sleep 8
docker compose exec -T app sh -lc 'echo "LOG_DIR=$LOG_DIR"; ls -la /data/logs | head -3'
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/health
docker compose exec -T app sh -lc 'ls -la /data/logs | tail -3'
```

预期：`LOG_DIR=/data/logs`；`/data/logs` 下有当天的 `app_YYYY-MM-DD.log` 且**在重启后仍在增长**（说明落进了 `app_logs` 卷而不是容器层）。

- [ ] **Step 7: 提交**

```bash
git add docker-compose.yml docker-compose.prod.yml Dockerfile
git commit -m "chore(p4): compose/Dockerfile 同构 —— 去 chroma 卷与挂载，prod 补 LOG_DIR"
```

---

### Task 8: ADR（存储收敛 / 融合位置 / 词法形态 / 作废记录）

**Files:**
- Create: `docs/adr/0004-storage-consolidation-single-postgres.md`
- Create: `docs/adr/0005-hybrid-fusion-in-application-layer.md`
- Create: `docs/adr/0006-chinese-lexical-retrieval-shape.md`
- Modify: `docs/adr/README.md`（若它维护索引表；没有则不动）

**Interfaces:**
- Consumes: `design.md` 的 D1/D2/D3/D4（决策与理由）；P2/P3 的验收结论
- Produces: 三条 ADR（**只追加不可变**）

- [ ] **Step 1: 读模板与既有范例**

```bash
sed -n '60,105p' docs/adr/README.md
sed -n '1,30p' docs/adr/0002-prompt-carrier-yaml-two-phase.md
```

按 README 的模板（`Status` / `Date` / `Deciders` / `Supersedes` 四行 frontmatter + `## 背景与问题` / `## 候选方案`（表：方案|做法|代价|收益）/ `## 决策` / `## 理由` / `## 后果`（正面/负面·接受的代价/不解决的问题）/ `## 复查触发条件`）。

- [ ] **Step 2: 写 ADR-0004（存储收敛）**

标题：`# ADR-0004：三套存储收敛到单个 PostgreSQL 实例（含共享实例的运维后果）`

必含内容（**逐条**，来自 `design.md` D1/D3）：
- **背景**：关系型在 MySQL、分块与向量在 Chroma（嵌入式）、词法索引在 `rank_bm25` + pickle 文件 —— 三个独立故障域，可各自半死而系统照打"混合检索完成"（`trace_c54ce259` 的静默缺陷活了半个月）。
- **候选**：① 三套各自加固；② 一个实例、两个 database（应用 + Langfuse）；③ 一个实例、同库不同 schema；④ 两个独立实例。
- **决策**：选 ②（`**选 ②。**`）。
- **理由**：与生产托管形态对齐（生产就是一个 RDS 实例）；成本与运维最省；独立 database 让 Langfuse 的 `prisma migrate deploy` 有独立 DDL 空间。
- **后果 —— 共享实例的四条运维后果必须显式列出**（`design.md` D1 的"显式接受项"）：
  1. 备份与 PITR 是**实例级**的：恢复应用库会同时回退 Langfuse 的数据，两个应用的恢复点被绑死；
  2. 连接数是**共享预算**：prod `--workers 4` ×（`pool_size=10 + max_overflow=10`）≈ 80 连接，再加 Langfuse 与其 worker，可能触及 `max_connections`；
  3. 大版本升级是**实例级维护窗口**：两个应用必须同时兼容新版本；
  4. 账号模型：现有 compose 只创建了一个用户，"独立 database + 独立账号"需要额外的创建步骤与权限划分。
- **不解决的问题**：本阶段只做本地（远程 RDS 与 prod 安装是遗留项 F-16/F-17）；只读副本/跨实例容灾不在范围内。
- **复查触发条件**：① 需要独立备份窗口或独立升级窗口时；② 连接预算逼近实例上限时；③ 迁到 RDS 时（把 2/4 条作为实例规格输入）。

- [ ] **Step 3: 写 ADR-0005（融合位置）**

标题：`# ADR-0005：混合检索的融合留在应用层`

必含内容（`design.md` D2）：
- **一手证据**：pgvector 官方 README 的 Hybrid Search 节全文只有一句 "You can use Reciprocal Rank Fusion or a cross-encoder to combine results"（告知可行、不提供实现）；ParadeDB `pg_search` 到 0.25.9 仍把 Native Hybrid Search 标为 `coming soon`；`|||` 是 match disjunction 算子而非融合算子。
- **必须把两条论据分开**：① **约束**（Postgres 生态缺乏可用的融合能力 → "下推"实质是把手写 RRF 搬进 SQL 字符串，还要自担 tiebreaker 确定性）；② **业界取向**（即便引擎提供原生融合，需要加权与多租户可配的产品仍选应用层 —— 证据：WeKnora 有 OpenSearch 后端却把加权 RRF 写在 Go 里、`financial_rag-main` 在同栈上做按 domain 加权的 Python RRF、Elasticsearch 官方明说其原生 RRF 各路权重必须相等）。**不得**用 WeKnora 支持论据 ①。
- **决策**：选①应用层融合；**两路等权、不引入权重**（加权重属能力新增，须独立变更与自己的验收）。
- **复查触发条件**：① 加权/多租户权重成为硬需求时；② pgvector 或 ParadeDB 发布可用的原生融合且**有托管形态**时。

- [ ] **Step 4: 写 ADR-0006（中文词法检索的落地形态）**

标题：`# ADR-0006：中文词法检索 = jieba 预分词 + tsvector('simple') + 前缀 OR`

必含内容（`design.md` D4/D5 + P3 实测）：
- **背景**：PG 默认分词器不对中文分词（实测 `to_tsvector('simple','营业收入同比增长率保持稳定')` 只产出 1 个 token）；`rank_bm25` 的字符级 unigram 会把「资产负债率」拆成 6 个单字。
- **候选**：① 引入 PG 中文分词扩展（`zhparser` / `pg_jieba`）；② `pg_trgm` 三元组相似度；③ ParadeDB `pg_search`；④ **Python 侧 jieba 预分词 + `to_tsvector('simple')`**。
- **决策**：选④。
- **理由**：`simple` 配置在任何 PG 上都可用（不依赖云厂商预装扩展），而扩展方案都要求 RDS 预装并进 `shared_preload_libraries`（可用性未确认）；`pg_search` 无托管形态，破"能用托管就用托管"原则；`pg_trgm` 语义是三元组相似度、不是词项加权。
- **三个硬约束必须写进 ADR**（它们都是"不报错、只静默降召回"的入口）：① 写入与查询**必须**同一分词入口（`src/infra/search/tokenizer.py`），且 `content_seg` 落库即固化 → 分词器变更**必须**跑 `scripts/rewrite_content_seg.py --apply`；② 查询串在应用层构造（安全字符集剔除 + 前缀通配 + 全滤空时回退原文子串），用户原文不得直接交给 `to_tsquery`（含空格会抛错、`a:` 会被静默吞字符）；③ 字段名不叫 `bm25_score`（`ts_rank` 不是 BM25）。
- **连接符的选择与依据**：取**前缀 OR**。依据分两层：暴露的实测事实是「前缀 AND 在多词自然查询上 `sparse_count=0`、词法路零贡献」（服务路径 E2E），而探针的 `prefix-OR` 高出 `prefix-AND` 恰 10.0pp 属**超集谓词的机械后果、不构成质量证据**；精度交由下游 RRF / 去重 / rerank 承担。**并且写明**：探针的判据是集合成员、候选集 ≤ k，**结构上测不了排序质量**，词法路的端到端质量评估仍在语料到位后（需求池 F-30）。
- **不解决的问题**：`ts_rank` 不是 BM25（真 BM25 需 ParadeDB 或应用层自算，另案）；HNSW 与中文分词的进一步调优不在本变更；176 分块语料上无法给出质量结论。
- **复查触发条件**：① 语料规模到达量产（千文档级）后重做词项命中与端到端评估；② RDS 侧确认可预装 `zhparser`/`pg_jieba` 时对比选型；③ 排名质量成为可观测问题时。

- [ ] **Step 5: 校验三条 ADR 的可解析性与编号唯一**

```bash
ls docs/adr/
grep -l "^# ADR-000" docs/adr/*.md | wc -l
grep -h "^## " docs/adr/0004-storage-consolidation-single-postgres.md
```

预期：编号 0004/0005/0006 各一份，章节齐全（六个 `##`）。若 `README.md` 里有索引表，追加三行。

- [ ] **Step 6: 提交**

```bash
git add docs/adr/
git commit -m "docs(p4): ADR-0004/0005/0006 —— 存储收敛、融合位置、中文词法形态"
```

---

### Task 9: `docs/agents/` 文档同步 + 需求池登记

**Files:**
- Modify: `docs/agents/code-map.md`、`api_contract.md`、`data-flow.md`、`glossary.md`、`defensive-patterns.md`、`cookbook.md`、`requirements_pool.md`

**Interfaces:**
- Consumes: Task 1–8 的最终代码形态；P3 的 `docs/tmp/p3-lexical-probe-2026-09-19.md` 与 `docs/tmp/p3-acceptance-2026-09-19.md`
- Produces: `docs/agents/` 与代码一致；P3 的 parked 残余清掉；需求池登记完成

- [ ] **Step 1: `code-map.md`**

- `src/infra/db/` 的分层描述里补 `transaction.py`（事务边界原语）与 `mysql_db/`（**包名仍名不副实**，见需求池 F-18）中 kebab 的 repo 清单（`chat_repo` / `chunk_repo` / `document_repo` / `eval_repo` / `kb_repo` / `user_repo`）；
- 删除对 `src/infra/search/bm25_index.py`、`deploy/chroma/`、`scripts/migrate_chroma_to_pg.py`、`scripts/dense_equivalence_check.py`、`scripts/lexical_probe*.py` 的任何引用（若有）；
- 补一句"**写路径的事务边界**：跨表原子操作须用 `session_scope(...)`/`Repo.transaction()` 并把 `session=` 传给参与方法（见 `docs/agents/defensive-patterns.md`）"。

- [ ] **Step 2: `api_contract.md`**

- §4（`VectorStore` 契约）里给 `add_chunks` / `delete_document` / `delete_collection` 补 `session` 参数说明（"传入会话时不提交，由调用方的事务边界决定"）；
- 删掉 §4.10 `list_collections` 若因 Chroma 退役而语义已变？**不删** —— 它现在的语义是"枚举含分块的知识库 ID"（P2 已改），保留；
- 新增/更新文档删除端点的契约：`POST /api/kbs/documents/delete` 现在会返回 **403（非属主）/409（状态不允许）**，请求体不变；
- 若 §4.8 `get_all_chunks` 的说明仍提 BM25，改为"空库检查 / 全量读取用"（P3 已改代码 docstring）。

- [ ] **Step 3: `data-flow.md`**

- 文档删除链路：改为"端点 → `DocumentService.delete_document` → 同一事务（删分块 + 软删文档）"；删掉任何"Chroma 清理"字样；
- 知识库删除链路：同上（软删文档 + 删分块 + 软删 KB 同事务）；
- 入库链路：标注"embedding 在事务外、chunks 与文档状态同事务"。

- [ ] **Step 4: `glossary.md`**

- 「存储收敛」词条补 P4 的终局（Chroma 与 BM25 的依赖/配置/卷/数据目录已退役；**回滚到 Chroma 不再可能**）；
- 新增词条「事务边界（session_scope）」与「一次性验收产物（已退役）」两条；
- 清掉 P3 遗留的 `见 design.md D7` → 应为 **D6**（P3 的 re-review 已发现，若 P3 未修则由本任务修）。

- [ ] **Step 5: `defensive-patterns.md`**

新增两条（与既有条目同格式）：
1. **「派生写操作跨事务」**：分块与文档/知识库状态若各自提交，进程死在中间就产生孤儿；**规则**：跨表写用 `session_scope` / `Repo.transaction()`，参与者传 `session=`，失败即整体回滚，**且不得吞异常**（历史缺陷：KB 删除时 `except Exception: logger.warning` 让孤儿永久存在）。
2. **「单 worker 下的阻塞调用」**：已有的 embedding/分词 offload 之外，补上"事务内不得包含外网调用"（`design.md` D7）。

- [ ] **Step 6: `cookbook.md`**

- 在「部署」条目旁补：**改依赖后必须 `docker compose build --no-cache app`；改 compose 的 env/volumes/command 后必须 `up -d --force-recreate app`**（`restart` 不生效）；
- 新增条目「退役 Chroma 之后如何重建 dev 语料」：`data/chroma_persist` 已删除，语料在 PG 里；若被清空，唯一重建路径是**从 MinIO 的原始文件重新上传**（原始文件是唯一不可再生的源头）。写明这一点，避免后人以为还能跑搬迁脚本；
- 保留「词法检索的分词器变更」条目（`rewrite_content_seg.py` 仍在）。

- [ ] **Step 7: `requirements_pool.md` 登记**

- **F-22（`compare_server_default`）**：在"处置"列写明"P4 明确不做（属独立变更：打开后可能一次性暴露既有表的默认值漂移）"，把它从"P4 门禁项"改为"独立变更"，避免下次再被当成本变更的欠账；
- **F-29（`multi-query-retrieval` 陈旧 requirement）**：保持不变（仍属 `retrieval-fetch-and-dedup` 的重定基），但补一句"本变更归档时若该 requirement 仍不成立，须在合并前解决"；
- **F-31（测试污染）**：标记为**已修**（Task 5），并把"跑完全量门禁不再污染"作为结论写进处置列；
- **新增 F-32**：**「词法路的排序质量无判据」的表外补充**？—— **不要**新增，F-30 已覆盖；改为确认 F-30 的现状描述与 ADR-0006 一致（若不一致，以 ADR 为准同步）。

- [ ] **Step 8: 清掉 P3 的 parked 残余（P3 Ruling R11-d）**

三处一行级修正：
1. `scripts/lexical_probe*.py` 已删（Task 6）→ 该残余自动消失，**在报告里确认**；
2. `docs/tmp/p3-acceptance-2026-09-19.md` 的 D9 指针：把"门禁全绿"的指针指向**翻转后**的那次运行（文档已有两节证据，只改指针）；
3. `docs/tmp/p3-lexical-probe-2026-09-19.md` 补齐一行口径说明：**A2 的 `jieba-tsrank` 与 C 组两臂消费生产构造器，其测量值为翻转前（前缀 AND）的 15/30；翻转后同臂为 18/30；三组变量比较本身不受影响**（探针脚本已删，这行是唯一的位置说明）。

- [ ] **Step 9: 跑文档门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_doc_consistency.py -v
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.check_docs
```

预期：path/route 锚点全过（error 档为空）；symbol 档的 warn 逐条确认不是拼写错误。

- [ ] **Step 10: 提交**

```bash
git add docs/agents/
git commit -m "docs(p4): 事务边界/删除链路/退役产物同步 + 需求池收口（F-22/F-31 处置）"
```

---

### Task 10: delta 与在效规格对账（含「关系型实体类型」的落地）

**Files:**
- Modify: `docs/openspec/changes/postgres-storage-consolidation/specs/typed-data-layer/spec.md`
- Modify: `src/infra/db/mysql_db/chat_repo.py`（`get_sessions` 补注解）
- Modify: `docs/openspec/changes/postgres-storage-consolidation/tasks.md`（§2 追加）
- Test: `tests/infra/db/test_db.py`（或既有 chat_repo 测试文件）追加一条属性访问断言

**Interfaces:**
- Consumes: 全部 14 个 delta 文件；实际代码的返回类型
- Produces: delta 正文与代码一致 → 归档后不会写进假陈述

- [ ] **Step 1: 逐条对账 14 个 delta**

对 `docs/openspec/changes/postgres-storage-consolidation/specs/*/spec.md` 的 14 个文件，逐个检查其正文里的**可检验陈述**（类型名、方法名、路径、字段名、行为断言）是否与代码一致：

```bash
for f in docs/openspec/changes/postgres-storage-consolidation/specs/*/spec.md; do
  echo "=== $f"; grep -n "\`[A-Za-z_][A-Za-z0-9_.]*\`" "$f" | head -20
done
```

把每份 delta 的结论记进报告（`一致` / `需改：<具体陈述>`）。**重点核对**：
- `hybrid-retrieval` 的两条同事务 scenario（Task 2/3 已使其为真）；
- `database-orm` 的引用方清单（P3 已修，复核其中不再含已删文件）；
- `observability-logging` 的「稀疏支路贡献可见」字段名（`dense_count` / `sparse_count`）；
- `retrieval-quality` 的探针要求（探针脚本将随 Task 6 删除 —— **这不是 delta 里承诺的**：delta 只要求"探针存在过并产出报告"，未要求脚本长期在库；若 delta 正文写了"脚本 SHALL 落盘"，改为"**报告** SHALL 落盘"）。

- [ ] **Step 2: 按 Ruling 4 改写 `typed-data-layer` 的「关系型实体类型」**

把 `specs/typed-data-layer/spec.md` 的 ADDED「关系型实体类型」改写为与代码一致（**保持"不得 raw dict"的实质**）：

```markdown
### Requirement: 关系型实体类型

每个关系型表的查询结果 SHALL 使用**有具名属性的类型**表达，而非 raw dict —— 即对应的 ORM 模型实例（`KbModel` / `DocModel` / `SessionModel` / `MessageModel` / `UserModel` / `EvalReportModel`），或在聚合查询中带具名属性的 `Row`。

（历史说明：本 requirement 的前身点名了一套 dataclass Entity 层。该层在更早的一次清理中被刻意移除，本变更沿用 ORM 模型实例作为边界类型 —— 要求的实质是"调用方按类型取属性、不自己解 dict"，这一点由下列 scenario 逐条保证。）
```

并把下面 7 条 scenario 的类型名改为实际名：

```markdown
#### Scenario: 知识库查询返回 ORM 实例
- **WHEN** KbRepo.get_all_kb() 被调用
- **THEN** 返回 `list[KbModel]`，每项含 id、user_id、name、doc_count

#### Scenario: 知识库取或建返回元组
- **WHEN** KbRepo.get_or_create_kb() 被调用
- **THEN** 返回 `tuple[str, bool]`（kb_id, is_new）
- **WHEN** KbRepo.get_kb_by_name() 被调用
- **THEN** 返回 `Optional[str]`

#### Scenario: 文档查询返回 ORM 实例
- **WHEN** DocumentRepo.get_documents() 被调用
- **THEN** 返回 `list[DocModel]`

#### Scenario: 会话查询返回 ORM 实例
- **WHEN** ChatRepo.get_session_by_id() 被调用
- **THEN** 返回 `Optional[SessionModel]`

#### Scenario: 会话列表返回带具名属性的 Row
- **WHEN** ChatRepo.get_sessions() 被调用
- **THEN** 返回 `list[Row]`，每项可按属性读出 id / title / kb_id / kb_name / message_count

#### Scenario: 消息查询返回 ORM 实例
- **WHEN** ChatRepo.get_messages() 被调用
- **THEN** 返回 `list[MessageModel]`

#### Scenario: 用户查询返回 ORM 实例
- **WHEN** UserRepo.get_user_by_account() / get_user_by_token() 被调用
- **THEN** 返回 `Optional[UserModel]`
```

- [ ] **Step 3: 给 `ChatRepo.get_sessions` 补类型注解并加断言测试**

`src/infra/db/mysql_db/chat_repo.py`：

```python
from sqlalchemy import Row  # 与既有 import 合并到同一行（按字母序）
```

```python
    async def get_sessions(self, user_id: str = "") -> list[Row]:
        """返回带具名属性的 Row（`.id` / `.kb_name` / `.message_count` 可直接访问）。

        Args:
            user_id: 用户 ID；空串表示不过滤（管理侧视角）

        Returns:
            会话列表（含知识库名与消息数），按 updated_at 倒序、最多 50 条
        """
```

在既有 chat_repo 的测试文件里追加一条：

```python
async def test_get_sessions_rows_expose_named_attributes(chat_fixture):
    """get_sessions 的返回值必须可按属性访问（不是 raw dict）。"""
    rows = await ChatRepo(session_factory).get_sessions(user_id="")
    if not rows:
        pytest.skip("无会话数据，属性契约由类型注解与 docstring 保证")
    row = rows[0]
    for attr in ("id", "title", "kb_id", "created_at", "updated_at", "kb_name", "message_count"):
        assert hasattr(row, attr), f"Row 缺属性 {attr}"
```

（`chat_fixture` 用该测试文件既有的 fixture 名；若该文件没有可用的会话 fixture，就用 `pytest.skip` 之外的写法 —— **不要**为此新建一套 DB fixture，改用"直接构造一条 session + 一条 message 再查"的最小写法，并在测后清理。）

- [ ] **Step 4: 跑 validate 与相关测试**

```bash
openspec validate postgres-storage-consolidation --strict 2>&1 | tail -5
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/ -q 2>&1 | tail -3
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
```

- [ ] **Step 5: `tasks.md` §2 追加实施期修正记录**

按既有五列格式（日期 / 落点 / 事实 / 处置 / 是否回改 design）追加这些行（**`<...>` 用实测值**）：

```markdown
| 2026-09-20 | P4 Task 1（事务边界） | **每个 repo 方法都自开自提交**，跨表原子性不可能；改造方案是 `session_scope(session_factory, session=None)` + 参与方法接受 `session=`（11 处一行级改动），不复制方法体 | 采用该方案；`design.md` D7 的事务边界要求由此落地 | 否（是实现细节，不改决策） |
| 2026-09-20 | P4 Task 3（删除路径） | API 的删除端点走的是**旧序**（先软删文档再删分块）且直取 `svc.document._doc_repo` / `svc.vector_store`（越层，需求池 F-23/F-25）；语义正确的 service 方法**无路由调用** | 端点改调 service（Ruling 2），带上了属主校验与状态校验；两条路径收编为一条 | 否（既有缺陷修复） |
| 2026-09-20 | P4 Task 10（capability 对账） | `typed-data-layer` 的 ADDED「关系型实体类型」点名的 6 个类型（`KbListItem` / `DocEntity` / `SessionEntity` / `SessionListItem` / `MessageEntity` / `UserEntity`）**全部不存在**：`src/infra/db/entities/` 早在 `ce3a6c9` 被刻意清理删除，实际返回 ORM 模型实例 / 具名 `Row` | 按 Ruling 4 把该 requirement 改为如实描述（保留"不得 raw dict"的实质），并给 `ChatRepo.get_sessions` 补 `-> list[Row]` | 否（要求实质未变，只是类型命名如实化） |
| 2026-09-20 | P4 Task 7（prod compose） | prod 的 `app` 有 `app_logs:/data/logs` 卷却**没有 `LOG_DIR`**，而 `logging.py` 默认 `logs` → 日志落容器可写层、重建即丢（与 P1 发现的「prod 的 Chroma 无挂载」同类既有缺陷） | 补 `LOG_DIR: /data/logs`，与 dev 同构；在 ADR-0004 与本表写明是既有缺陷被顺带修掉 | 是（已并入 design.md Risks 的同族叙述） |
| 2026-09-20 | P4 Task 11（数据目录退役） | `data/chroma_persist` 删除后，**Chroma 回滚路径关闭**：回滚到旧实现不再可能，语料重建只能从 MinIO 的原始文件重新上传 | 按 `design.md` Migration Plan「第 9 步必须放在验收全部通过之后」执行；在 ADR-0004/glossary/cookbook 三处写明 | 否（design 已预告） |
```

- [ ] **Step 6: 提交**

```bash
git add docs/openspec/ src/infra/db/mysql_db/chat_repo.py tests/
git commit -m "docs(p4): delta 与代码对账 —— 实体类型如实化、get_sessions 补注解、§2 登记"
```

---

### Task 11: 数据目录退役（**不可逆，最后一个动作**）

**Files:**
- Delete（数据，非 git 跟踪）：`data/chroma_persist`、`data/chroma`、`data/bm25_index`

**Interfaces:**
- Consumes: Task 0–10 全部通过（**这是前置条件，不是建议**）
- Produces: Chroma/BM25 的物理痕迹清零；**从此回滚到 Chroma 不再可能**

- [ ] **Step 1: 确认前置全部满足（逐条打勾，缺一不可）**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_no_chroma_leftovers.py tests/config/test_no_bm25_leftovers.py -v
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(*) FROM users);"
grep -rn "chroma\|rank_bm25" src/ pyproject.toml --include=* 2>/dev/null | grep -v __pycache__ || echo "src/ 与 pyproject 已无残留"
```

预期：全量绿；两条守卫绿；语料 `5|0|176|1`；最后一行为"已无残留"。
**任一条不满足就停下报告** —— 删掉数据目录后没有再来的机会。

- [ ] **Step 2: 删除数据目录**

```bash
du -sh data/chroma_persist data/chroma data/bm25_index 2>/dev/null
rm -rf data/chroma_persist data/chroma data/bm25_index
ls data/
```

预期：`data/` 下只剩 `ragas` / `reports` / `test_docs`（以实际为准），三个目标目录不存在。
**严禁**在同一批命令里出现 `docker volume prune` / `docker compose down -v`。

- [ ] **Step 3: 证明语料仍完好（PG 才是现在的唯一存储）**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(DISTINCT kb_id) FROM chunks);"
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check; echo "exit=$?"
```

预期：`5|176|5` 与 `stale=0`、`exit=0`。

- [ ] **Step 4: 一次真实检索（证明数据目录删除没有伤到检索）**

```bash
POSTGRES_HOST=localhost .venv/bin/python - <<'PY'
import asyncio
from src.infra.db.engine import run_and_dispose
from src.infra.db.vector_store import VectorStore

async def main():
    store = VectorStore()
    kb_id = "<Task 0 记录的 5 个 kb 之一>"
    dense = await store.dense_search(kb_id, "资产负债率", 5)
    sparse = await store.lexical_search(kb_id, "资产负债率", 5)
    print("dense:", len(dense), "sparse:", len(sparse))
    if sparse:
        print("sparse top1 score:", round(sparse[0].lexical_score or 0, 4))

asyncio.run(run_and_dispose(main()))
PY
```

预期：两路都有返回（`dense` 与 `sparse` 均 > 0），`sparse` 的分数 > 0。

- [ ] **Step 5: 更新受影响文档（若它们声称目录仍在）**

```bash
grep -rn "chroma_persist\|bm25_index\|data/chroma" docs/agents/ docs/adr/ | grep -v "已删除\|已退役\|不再可能"
```

把仍声称目录存在的行改为"**已退役**（P4）"，或补上删除说明。**不要**改 `docs/tmp/` 下的历史报告（它们是那次的记录）。

- [ ] **Step 6: 提交**

```bash
git add -A docs/
git commit -m "chore(p4): 退役 Chroma/BM25 的数据目录（回滚路径就此关闭）"
```

（数据目录不在 git 里，本提交只含文档更新。）

---

### Task 12: 归档与收口

**Files:**
- Delete: `docs/openspec/changes/bm25-index-durability/`
- Modify: `docs/openspec/changes/postgres-storage-consolidation/tasks.md`（阶段表 P4 行）
- Move（由 openspec CLI）：`docs/openspec/changes/postgres-storage-consolidation/` → `docs/openspec/changes/archive/…`，delta 同步进 `docs/openspec/specs/`

**Interfaces:**
- Consumes: Task 0–11 全部通过
- Produces: 本 change 归档；`hybrid-retrieval` capability 在效规格中新生；`bm25-index-durability` 作废

- [ ] **Step 1: 归档前最后一次全量门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
ruff check . && .venv/bin/pyright src/ 2>&1 | tail -3
openspec validate postgres-storage-consolidation --strict 2>&1 | tail -5
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base)||'|'||(SELECT count(*) FROM document)||'|'||(SELECT count(*) FROM chunks)||'|'||(SELECT count(*) FROM users);"
git status --short
```

预期：全绿；validate 通过；`5|0|176|1`；工作区干净。

- [ ] **Step 2: 作废 `bm25-index-durability`（Ruling 8）**

```bash
git log --oneline -- docs/openspec/changes/bm25-index-durability/ | head -3
git rm -r docs/openspec/changes/bm25-index-durability
```

把作废理由写进 ADR-0006 的「后果/不解决的问题」一段（"`bm25-index-durability`（38 任务）的靶子在新形态下不存在，已作废；其调研见 git 历史"），并在 `tasks.md` §2 追加一行：

```markdown
| 2026-09-20 | 作废 change `bm25-index-durability` | 其 38 个任务的靶子（索引文件的持久化 / 原子写 / 损坏自愈 / 缺失降级）在"词法检索搬进 PostgreSQL"后不存在 | 删除该 change 目录；作废理由写入 ADR-0006 与 proposal 的既定结论 | 否（proposal D9 已预告） |
```

- [ ] **Step 3: 更新 `tasks.md` 的阶段表**

把 P4 那一行改为：

```markdown
> | **P4** | `docs/superpowers/plans/2026-09-19-postgres-storage-p4-transactions-and-cleanup.md` | 入库/删除两条路径同事务 + 故障注入验收 + 依赖与卷清理（含 Chroma/BM25 的数据目录）+ prod compose 与 dev 同构 + ADR + delta 对账与归档 | **已完成**（收口 `<Task 11 的 commit>`） |
```

并把「分阶段的原因」段落里"P4（待 P3 落地后编写）"之类的前瞻措辞删掉。

- [ ] **Step 4: 同步 delta 到在效规格**

用 `openspec` 的命令（**不要**手工搬文件）：

```bash
openspec --help | head -30          # 确认子命令名
openspec archive postgres-storage-consolidation --yes 2>&1 | tail -10
```

> 若 CLI 的归档子命令名或参数与上不同（`openspec archive --help` 为准），按实际用法执行；**若 CLI 不提供归档**，按 `openspec-sync-specs` 技能的手工流程：把 14 个 delta 的 ADDED/MODIFIED/REMOVED 落到 `docs/openspec/specs/<capability>/spec.md`（`hybrid-retrieval` 新建目录），再把 change 目录移入 `changes/archive/`，最后 `openspec validate --strict --all`。

- [ ] **Step 5: 核对同步结果**

```bash
openspec validate --strict --all 2>&1 | tail -5
ls docs/openspec/specs/ | grep -c .                      # capability 数量应比归档前多 1（hybrid-retrieval）
grep -n "关系型实体类型" -A 6 docs/openspec/specs/typed-data-layer/spec.md | head -12
grep -rn "bm25\|BM25" docs/openspec/specs/ | grep -v "词法\|lexical_score\|不叫 BM25" | head
```

预期：validate 全过；规格数 +1；`typed-data-layer` 的实体要求已换成 Ruling 4 的文本；规格里没有把 BM25 当作现存组件的陈述。

- [ ] **Step 6: 最终提交**

```bash
git add -A
git commit -m "chore(p4): 归档 postgres-storage-consolidation（delta 同步 + 作废 bm25-index-durability）"
git log --oneline -1
```

---

## 覆盖的 spec requirement（对账用）

| capability · requirement | 本 plan 的承载 | 状态 |
|---|---|---|
| `hybrid-retrieval` · 分块与文档状态同事务 | Task 1（原语）+ Task 2（入库）+ Task 4（验收） | ✅ |
| `hybrid-retrieval` · 删除路径同样同事务 | Task 3（两条删除路径）+ Task 4（故障注入与 E2E） | ✅ |
| `hybrid-retrieval` · 分块按知识库归属存储（「归属受约束」「遍历不产生副作用」） | P2/P3 已达成；本阶段不改 | ✅（P2/P3） |
| 其余 `hybrid-retrieval` requirement（两路取数/来源可辨/参数可配/分词同源/查询构造） | P3 已达成 | ✅（P3） |
| `retrieval-quality` · 迁移等价性与词项命中探针 | P2（dense）+ P3（词法）已达成；探针脚本随 Task 6 退役，**报告保留** | ✅（P2/P3） |
| `observability-logging` · 稀疏支路贡献可见 | P3 已达成（`dense_count` / `sparse_count`） | ✅（P3） |
| `typed-data-layer` · 关系型实体类型 | Task 10 按 Ruling 4 如实化 + `get_sessions` 补注解 | ✅ |
| `typed-data-layer` · 检索结果统一类型 / 连接管理拆为 Repo | P2/P3 已达成；Task 10 复核 | ✅（P2/P3） |
| `database-orm` · ORM 模型定义 / 搜索类型搬迁 | P1/P2 已达成；Task 10 复核引用方清单 | ✅（P1/P2） |
| `database-migrations` · 第一版迁移（8 张表 + 扩展断言） | P1 已达成 | ✅（P1） |
| `architecture-tidy` · 无独立词法索引组件 / AppService 直接持有全局依赖 | P3 已达成 | ✅（P3） |
| `agent-service` / `multi-query-retrieval` / `kb-routing` / `model-config` / `chunk-entity-enrichment` / `request-abort` / `streaming-run` 的命名同步 | P1–P3 已达成；Task 10 复核 | ✅ |
| `chunk-data-model`（proposal 提到的"两份 `ChunkData` 定义"） | P1 已统一 | ✅（P1） |
| `proposal.md` 的「依赖增减」（删 chromadb / rank_bm25 / aiomysql，加 asyncpg / pgvector，jieba 接线） | Task 6 收口 Chroma 与 rank_bm25；`aiomysql`/`asyncpg`/`pgvector`/`jieba` 由 P1/P3 完成 | ✅ |
| `proposal.md` 的 Migration Plan 第 7/8/8b/9 步 | Task 2/3（第 7 步事务化与故障注入）、Task 7（第 8 步 prod 同构）、Task 4（第 8b 步 E2E）、Task 6/7/11（第 9 步清理） | ✅ |

## P4 明确不做（与遗留项的边界）

- **远程 RDS 与 prod 安装**（F-16）→ 遗留，随托管化另案；prod 的 compose 本阶段只做到"与 dev 同构"。
- **prod `--workers 4` 与单 worker 规则的冲突**（F-17 / F-19）→ 本阶段**只登记不处置**（涉及流式生成状态的前提，属独立变更；它同时是 RDS 规格的输入）。
- **`alembic/env.py` 打开 `compare_server_default`**（F-22）→ 独立变更（打开后可能一次性暴露既有表的默认值漂移）。Task 9 把它在需求池里从"P4 门禁项"改为"独立变更"。
- **`src/infra/db/mysql_db/` 包改名**（F-18）→ 独立变更（约 20 个 import 点）。
- **`multi-query-retrieval` requirement 的结构性重写**（F-29）→ 与 `retrieval-fetch-and-dedup` 的重定基一并处理。
- **词法路质量判据的重建**（F-30）→ 需 k ≪ 池的新判据或端到端 RAGAS；本阶段只在 ADR-0006 里把结论固化。
- **HNSW / 中文分词扩展对比 / `ts_rank` 之外的排名方案** → 规模与可用性变化后再评估（ADR-0006 的复查条件）。
- **MySQL 卷与孤儿容器的清理** → 不动（P1 的关系型回滚依据）。

## 执行注意（控制器与执行者都看）

1. **Task 11 的顺序不可换**：它是唯一不可逆的一步，必须在全部验收与清理之后。跑它之前逐条打勾 Task 11 Step 1 的前置清单。
2. **Task 5 要早于任何全量门禁**：不修 F-31 的话，每跑一次全量 pytest 就在 dev 库留约 5 KB / 6 doc；而 Task 11 之后**再也没有 Chroma 可以复位语料**。
3. **Task 1 的"一行改动"必须逐方法核对**：把 `async with self._sf() as session:` 换成 `session_scope(self._sf, session)` 的同时**要删掉块内原来的 `await session.commit()`** —— 漏删会让"参与者在事务内提交"，原子性静默失效（且成功路径的测试看不出来）。
4. **Task 3 的 Ruling 2 会改变删除端点的对外行为**：非属主变 403、状态不允许变 409。Task 9 必须把这两条写进 `api_contract.md`，否则前端会踩。
5. **Task 6 之后必须重建镜像**：依赖从 `pyproject.toml` 删掉但容器里还装着，会让守卫测试的 `import chromadb` 断言失败 —— 这不代表改错了，代表镜像没重建。
6. **Task 7 之后必须 `up -d --force-recreate`**：`restart` 不应用 env/volumes 变化，会让人误以为 `LOG_DIR` 没生效。
7. **不要在 Task 11 之前提交任何"数据目录已删"的文档**：文档与事实要同时翻转，否则中间态是假陈述。
8. **`docs/tmp/` 的历史报告不改数字**：P2/P3 的探针与验收报告是那次测量的冻结记录（`feedback-measurement-artifact-frozen`）；要写新结论就写进 ADR 或 `docs/agents/`。
