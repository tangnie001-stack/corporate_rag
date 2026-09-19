# PostgreSQL 词法检索（postgres-storage-consolidation / P3）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把中文词法检索从「进程内 `rank_bm25` + pickle 索引文件 + 字符级分词」换成「PostgreSQL 全文检索 + jieba 预分词」，并把 `rrf_fusion` 迁到独立模块、`bm25_index.py` 整体删除；两路取数同源于一个 PostgreSQL 实例并在应用层融合。

**Architecture:** P2 已把 dense 路与分块存储落到 `chunks` 表（`embedding vector(1024)` + `content_seg` 文本列 + `tsv` 生成列），但 `content_seg` 当时写的是**正文原值占位**（Ruling P2-R5），词法路仍走 BM25。P3 做三件事：① 建立**唯一的 jieba 分词入口**（写入侧与查询侧共用同一函数），并把 `content_seg` 换成空格连接的词项串、**全量重写存量**；② 在 `ChunkRepo` 上加**词法取数**（`tsv @@ to_tsquery` + `ts_rank`，词元全被滤掉时降级为正文子串匹配），在 `PgVectorStore` 上暴露 `lexical_search`；③ 把 `rrf_fusion` 迁到 `src/rag/fusion.py`，`retrieval.search` 改为「dense + 词法」两路并发同源取数，然后删除 `bm25_index.py` 及其装配链。**入库/删除的事务化与 Chroma 的依赖/卷清理不在 P3**（P4）。

**Tech Stack:** Python 3.11+ / jieba 0.42.1（已在 `pyproject.toml:35` 声明但全仓零调用，本阶段接线）/ PostgreSQL 15.19 + pgvector 0.8.6（`to_tsvector('simple')` + GIN）/ SQLAlchemy 2.x async / pytest（存储侧打真实 PG）

**Spec:** `docs/openspec/changes/postgres-storage-consolidation/`（`proposal.md` / `design.md` / `specs/`）。本 plan 实现的 delta：`hybrid-retrieval`（全部 5 条 requirement）、`retrieval-quality`（词项命中探针，dense 部分 P2 已完成）、`observability-logging`（稀疏支路贡献可见）、`database-orm`（搜索类型搬迁的引用方清单）、`architecture-tidy`（无独立词法索引组件）、`agent-service`（检索依赖来自单一存储组件）、`multi-query-retrieval`（两路同源）。设计决策以 `design.md` 的 **D4（含 H1/H2/H3）/ D5 / D6** 为准。

## 阶段定位（重要，先读）

本 change 跨 4 个可独立交付的子系统，每个子系统一份 plan。本文件是 **P3**：

| 阶段 | 范围 | 状态 |
|---|---|---|
| P1 | PostgreSQL 关系型底座（配置 / compose / alembic baseline / ORM 合并 / `ChunkData` 统一 / engine 切 asyncpg / repo 幂等 / 退役 MySQL / 文档） | **已完成**（HEAD `8fb581e`） |
| P2 | dense 检索换 pgvector、`ChunkResult` 分路字段与 `metadata` 回填契约、删全局检索入口、Chroma→PG 数据搬迁、dense 迁移等价性验收 | **已完成**（HEAD `43ab19a`，未合并未推送，账本保留） |
| **P3（本文件）** | jieba 分词入口 + 查询串构造与转义 + `content_seg` 换分词输出与**存量全量重写** + 词项命中探针选型 + `rrf_fusion` 迁移 + 两路同源取数 + 删除 `bm25_index.py` | 本次 |
| P4 | 入库/删除两条路径同事务 + 故障注入验收 + **依赖与卷清理（含 Chroma）** + prod compose 与 dev 同构 + 文档与 ADR + 归档 | 待 P3 落地后写 |

**P3 的边界（明确不做）**：入库/删除的事务化与故障注入（P4）；`chromadb` / `rank_bm25` 的**依赖删除**、`data/chroma_persist` 与 `data/bm25_index` 的**目录删除**（P4，见需求池 F-19）；`alembic/env.py` 的 `compare_server_default`（F-22/F-26）；`src/infra/db/mysql_db/` 包改名（F-18）；`src/api/documents.py:246` 的越层调用与双删除路径收编（F-23/F-25）；HNSW 索引；两路权重（`design.md` D5 明确不引入）；端到端 RAGAS 质量判定。

> **P3 与 P2 的一个关键差异**：P2 的验收判据（dense 等价性）是「不应改变行为」的**差分**判据；P3 的词法侧**必然改变行为**（char-unigram BM25 → jieba 词项 + `ts_rank`），所以它的判据是**词项命中探针的横向相对比较**，不是"与替换前一致"。**不得**把探针数字当作质量基线（176 分块的统计力限制，见 DoD D5）。

## ⚠ 已知事实与陷阱（先读这张表）

每一条都已在本仓或上游实测/实读得出，编号在正文里被引用。

| # | 事实 / 陷阱 | 依据 | 怎么避 |
|---|---|---|---|
| **F1** | **`content_seg` 现在装的是正文原值**（P2 的占位），所以 `tsv` 里的中文是**整串 1 个 token**，词法路现在**完全不可用** | `src/infra/db/vector_store/mapping.py:133-134`；`design.md` D4 实测「`to_tsvector('simple','营业收入同比增长率保持稳定')` → 1 个 token」 | Task 4 把写入侧接上分词；Task 5 用脚本**全量重写存量 176 行**；两件事都做完词法路才成立 |
| **F2** | **写入侧与查询侧口径不一致不会报错，只静默降召回**，且分词结果随 `tsv` 生成列**固化落库** → 依赖版本/词典变更后存量与新查询侧不一致，同一进程内比较的守卫测试**抓不到** | `design.md` D4 的「版本漂移」段；`hybrid-retrieval` delta 的「分词器变更触发存量重写」 | 写入与查询**只调 `tokenizer.tokenize()` 一个函数**（Task 1/2 的守卫测试钉死）；`scripts/rewrite_content_seg.py --check` 是那条不变量的**可执行检查**（Task 5） |
| **F3** | **`plainto_tsquery` 的整词 AND 在中文上会静默 0 命中**：查询「增长」而文档词元是「增长率」；查询「营业收入」而文档词元是「营业」+「收入」 | `design.md` H2 实测表 | 查询侧对每个词元加前缀通配 `:*`（Task 2），**不得**用 `plainto_tsquery` 落地 |
| **F4** | **词元全被滤掉时"回退为不过滤"是无效兜底** —— 写入侧已把单字从 `content_seg` 剔除，tsv 里根本没有单字 lexeme（实测 `plainto_tsquery('simple','月')` / `'涨'` 均 0 命中，而 `content LIKE '%月%'` 命中） | `design.md` H1 实测表 | 兜底走**正文子串匹配**（Task 2/3），并显式覆盖「涨了吗」「5 月」这类全单字查询 |
| **F5** | **`to_tsquery` 对用户原文会抛异常**：`研发费用 5 月`（含空格）→ `ERROR: syntax error in tsquery`；`a:` 会被**静默吞字符**成 `'a'` | `design.md` H3 实测表 | 查询串在应用层由**安全词元**拼装（Task 2）：先剔除合法字符集之外的字符，再拼 `词元:*` |
| **F6** | 子串兜底用 `LIKE` 时，用户原文里的 `%` / `_` / `\` 是**通配符** | SQL LIKE 语义 | `escape_like()` + `.like(..., escape="\\")`（Task 2/3），否则查询「%」会命中全库 |
| **F7** | `rrf_fusion` 的签名是 `(dense, bm25_res, k=60, top_n=50)`，**没有权重参数**；`design.md` D5 要求「原样迁移、行为不变」，`hybrid-retrieval` 要求「参数来自配置」 | `src/infra/search/bm25_index.py:152-157`；delta「融合参数可配置」 | Task 7 原样搬函数体，只把两个默认值改为从 `settings.RRF_K` / `settings.RRF_TOP_N` 取；**不引入权重** |
| **F8** | **`multi-query-retrieval` 的 delta 正文引用的 `retrieve_node` / `rewritten_queries` 在代码里已不存在**（harness 改造后检索是 `retrieve_kb` 工具 + 单条 query），`rrf_fusion_multi` 也**全仓无调用方**（只有测试） | `grep -rn "rewritten_queries\|retrieve_node" src/` 零命中；`rrf_fusion_multi` 仅 `bm25_index.py` 定义 + 测试引用 | 本阶段只做该 requirement 的**命名同步**（`dense + BM25 混合检索` → `dense + 词法两路检索`）；`rrf_fusion_multi` **按 design D5 原样迁移保留**（删它要改 spec，属独立变更）⚠ **该 requirement 正文的陈旧陈述登记为发现**（Task 10 写进 `tasks.md` §2 与需求池） |
| **F9** | `build_graph(vector_store, bm25, llm, reranker, prompt_manager)` 的 `bm25` 是**第 2 个位置参数**，`make_rag_tools(vector_store, bm25, reranker, prompt_manager)` 的 `bm25` 是第 2 个；删它会让全部调用点**位置错位** | `src/agents/graph/workflow.py:41-51`、`src/agents/tools/rag_tools.py:63-69` | 签名变更与其**全部调用点（含测试）在同一 Task 内改完**（Task 8）；调用点清单见 F9 的 Task 0 Step 5 |
| **F10** | 仓库根跑 `ruff format .` 会重排 `docs/**/*.md` 里的 Python 代码块（P1 实测 76 个文件），属**既有漂移** | P2 plan F9 | 只格式化改动文件（`ruff format <paths>`），或跑完 `git status` 把改动集外的文件逐个回退 |
| **F11** | **`tests/reset_data.py` 的 `reset_all()` 现为「PG + BM25 目录 + Redis」三合一**，且它删的是 `data/bm25_index` 整个目录 | `tests/reset_data.py:52-64,103` | Task 9 删掉 BM25 那一段（改成 PG + Redis）；⚠ **`data/bm25_index` 目录本身不要删** —— 它是词法路的回滚依据，随 F-19 在 P4 清理 |
| **F12** | `data/chroma_persist` 同样是**回滚与复现依据**，`scripts/migrate_chroma_to_pg.py` / `dense_equivalence_check.py` 仍依赖它 | 需求池 F-19 | P3 全程**不得**删除或改动该目录 |

## P3 完成标准（DoD）

**P3 完成的判据是下面全部成立，而不是「Task 0–11 的 checkbox 都打了勾」。**

| # | 判据 | 怎么验 |
|---|---|---|
| **D1** | **分词是单一入口**：写入侧（`content_seg`）与查询侧（tsquery 词元）调用同一个 `tokenize()`，长度 < 2 的词项被过滤 | Task 1 单测 + Task 2 的「两侧词元相等」守卫测试 |
| **D2** | **查询条件的安全化与兜底**：`&`/`\|`/`!`/`:`/空格等输入**不抛错**；词元加前缀通配；词元全被滤掉时走**正文子串**（H1）而非空条件；`LIKE` 通配符被转义 | Task 2/3 的守卫测试（含「涨了吗」「5 月」「C&C」「研发费用 5 月」「%」） |
| **D3** | **词法路读 PostgreSQL**：`lexical_search` 按 `ts_rank` 降序返回 `ChunkResult`，`lexical_score` 与 `sparse_rank` 被填充；无进程内索引、无索引文件 | Task 3 的集成测试（打真实 PG） |
| **D4** | **存量已全量重写**：`content_seg` 全部等于 `to_lexical_text(content)`；重写脚本幂等（再跑 `--apply` 变更 0 行、`--check` 退出码 0） | Task 5 实跑 + `--check` |
| **D5** | **词项命中探针完成横向比较并产出报告**：探针词项从**原始正文**派生、按 df 筛选；① 分词配置（同打分算法：`char-bm25` 基线 / `jieba-bm25`）② 查询构造（前缀 AND / 前缀 OR / 整词 AND / 原文子串）③ 打分算法（`ts_rank` / `ts_rank_cd`）三组**分开**比较；报告含命中数 / 词项总数 / 完整词项清单，并**显式声明小语料限制与"仅供相对比较"** | Task 6 实跑 + `docs/tmp/p3-lexical-probe-2026-09-19.md` |
| **D6** | **融合迁移完成**：`rrf_fusion` / `rrf_fusion_multi` 在 `src/rag/fusion.py`，**不依赖数据库**（可用纯列表验证）；平滑常数与保留条数来自配置；两路等权 | Task 7 单测（无 DB、无网络） |
| **D7** | **两路同源并发 + 贡献可见**：`retrieval.search` 用 `asyncio.gather` 并发取 dense 与词法两路，两路都走同一个 `VectorStore`；`hybrid done` 日志同时反映两路各自的条数，任一路为 0 可直接看出 | Task 8 测试 + Task 11 的 E2E 日志核对 |
| **D8** | **无独立词法索引组件**：`bm25_index.py` / `BM25Index` / `BM25_INDEX_DIR` / `rank_bm25` 在 `src/` 内零命中（`rebuild_bm25` CLI 删除） | Task 9 的残留守卫测试 |
| **D9** | 门禁全绿（`pytest tests/ -v` 全过、`ruff check .` 无错、`pyright src/` 不新增 error）+ **在 PG 上跑通一次真实 E2E 冒烟**，其中检索用**中文词项**且能命中引用 | Task 11 |

> **D5 为什么不能省**：换掉词法算法后「检索结果必然变化」，而端到端质量判定已明确推到语料到位之后（`retrieval-quality` delta）。探针是这次唯一能给出**可判定相对结论**的手段 —— 没有它，「词法路换完了」这句话无法验证，且历史上的静默失效（`trace_c54ce259`）无人能发现。

## 本 plan 的裁决（需要执行者知道）

1. **Ruling 1：分词入口落在 `src/infra/search/tokenizer.py`。**
   写入侧在 `infra/db`、查询侧在 `infra/db`，两处都要能 import；放在 `src/rag/` 会让 `infra/` 反向依赖 `rag/`（上行依赖，且易与 `rag/fusion.py` 形成环）。函数两个：`tokenize(text) -> list[str]`（过滤长度 < 2）与 `to_lexical_text(text) -> str`（空格连接）。
   代价（若判断错）：位置不合适要挪 import（约 4 处）。
2. **Ruling 2：查询条件由应用层拼 `token:* & token:*`（前缀 AND），并由探针确认。**
   `design.md` 的落地形态是前缀通配 + AND，OR 与整词 AND 作为**探针对照项**；若 Task 6 实测 OR 的命中率显著更高，则改 `lexical_query.py` 的连接符并同步 Task 2 的断言（**只改一处**，因为只有这一个构造点）。
   代价（若判断错）：误召回偏多（`增长:*` 命中 `增长量`），由探针报告量化，可后续单独调。
3. **Ruling 3：打分算法用 `ts_rank`，`ts_rank_cd` 作为探针对照项。**
   `design.md` Open Question 3 要求由探针决定；本 plan 先落 `ts_rank`（当前在效行为更常见、量纲更平滑），Task 6 出结论后如需更换只改 `ChunkRepo.search_lexical` 一处。**命名不得叫 `bm25_score`**（`ts_rank` 不是 BM25）—— 字段已是 `lexical_score`（P2 已改）。
   代价（若判断错）：排序质量次优，改一处函数名即可。
4. **Ruling 4：`rrf_fusion_multi` 原样迁移保留，即使当前 `src/` 无调用方。**
   `design.md` D5 明文要求两个纯函数一并迁移；删除它会与 `multi-query-retrieval` 的 delta 正文冲突（该 delta 声称 N 路融合存在）。该 requirement 正文引用的 `retrieve_node` / `rewritten_queries` 在代码中已不存在，属**既有陈旧陈述**，登记为发现（Task 10），不在 P3 修。
   代价（若判断错）：仓库多一个只有测试用的函数；P4 归档时可按 spec 一并处置。
5. **Ruling 5：P3 不删任何依赖、不删任何数据目录。**
   `rank_bm25` 从 `pyproject.toml` 移除、`data/bm25_index` 与 `data/chroma_persist` 的目录删除，全部随 F-19 在 P4 做。理由：`data/bm25_index` 是词法路的**回滚依据**（回滚 = 恢复旧代码 + 旧索引文件），而 `data/chroma_persist` 仍是 dense 的复现依据。唯一例外：`jieba` 从 `>=0.42.1` 收成 `==0.42.1`（`design.md` D4 把「pin 精确版本」列为版本漂移的缓解手段，属本变更的不变量要求）。
   代价（若判断错）：依赖与目录多留一个阶段。
6. **Ruling 6：两路贡献用**现有** `hybrid done` 事件加字段，不新开事件。**
   加 `dense_count` / `sparse_count`（`result_count` 保留为融合后条数）。`observability-logging` delta 的三条 scenario 都由此满足：任一路为 0 时该字段就是 0；两路非空时不产生任何 warning。**不开新事件**是因为前缀表是开放登记制但事件名应克制，且这一条语义就是"同一轮融合的两路贡献"。
   代价（若判断错）：日志字段变多，`log_event_specs.py` 的 `fields` 需同步（Task 8 已含）。
7. **Ruling 7：探针与重写脚本入库保留。**
   `scripts/lexical_probe.py`（探针，可重跑）、`scripts/rewrite_content_seg.py`（分词器变更的迁移步骤，`--check` 是那条不变量的可执行检查）都留在仓库；报告落 `docs/tmp/`。P4 归档时一并处置。
   代价（若判断错）：仓库多两个脚本。
8. **Ruling 8：`jieba` 的首次调用（加载词典，约 0.5–1 s CPU）必须不在事件循环上。**
   `PgVectorStore.add_chunks` 把 `build_rows`（含分词）放进 `asyncio.to_thread`；`lexical_search` 把 `build_lexical_query`（含分词）放进 `asyncio.to_thread`。与 P2 的「embedding 是同步调用必须 offload」同一理由（单 worker 下阻塞事件循环会冻住所有请求与 SSE）。
   代价（若判断错）：每次多一次线程切换（微秒级）。

## Global Constraints

- **契约（P3 不得破坏）**：`ChunkResult.distance` 是**余弦距离**，消费方用 `score = 1 - distance`；分块 id 格式 `{doc_id}:{chunk_index}`；`metadata` 的 5 个契约键与 jsonb 自定义键的回填契约（P2 建立，见 `mapping.py`）。
- **两路必须同源**：dense 与词法都走 `VectorStore`（背后是同一个 PostgreSQL 实例的 `chunks` 表），不得分别读两个存储。
- **融合留在应用层**，不得下推 SQL；不得引入两路权重。
- **不得引入三元表达式**（`a if cond else b`）—— 写完整 `if/else`。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底。
- **硬编码集中管理**：新增常量进 `src/config/`（可配置阈值走 `settings.py` 的 `os.getenv`，固定值走 `const.py`）。
- **文档与注释**：所有函数写 docstring；dataclass 每个字段加行内注释；注释写**当前状态**不写变更历史；注释陈述契约不写推理记录。
- **文件红线**：单文件 ≤ 400 行、单函数 ≤ 80 行。
- **门禁（每个 Task 结束都跑）**：`pytest tests/ -v` 相关用例通过、`ruff check .` 无错、`pyright src/` 不新增 error、无**新增** `print()`/TODO。
- **契约同步**：改了公共方法签名时同步 `docs/agents/api_contract.md` 与受影响测试断言。
- **宿主侧连库约定（P1 立）**：dev 的 `postgres` 只发布 `127.0.0.1:5432`；**宿主上跑 alembic / pytest / 脚本一律加前缀 `POSTGRES_HOST=localhost`**；容器侧仍用 `.env` 的 `POSTGRES_HOST=postgres`。
- **不得执行**：`docker compose down -v` / `docker volume prune` / `rm -rf data/chroma_persist` / `rm -rf data/bm25_index` / `rm -rf` 任何 `corporate_rag_*` 卷。
- **测试** mock 外部依赖（embedding / LLM），但**存储侧测试打真实 PG**，不用 mock。
- **不要在全仓跑 `ruff format .`**（F10）。
- **改容器内 `.py` 后**：`docker compose restart app` 即生效（override 挂载 `./src`）；P3 **不改依赖集合**，因此**不需要** `build`。

---

## 文件结构（改动的落点与职责）

**新建**

| 文件 | 职责 |
|---|---|
| `src/infra/search/tokenizer.py` | **唯一的 jieba 分词入口**：`tokenize()`（过滤长度 < 2）/ `to_lexical_text()`（空格连接，写入 `content_seg`） |
| `src/infra/db/lexical_query.py` | **查询侧词元口径的唯一入口**：`build_lexical_query()`（安全化 + 前缀通配 + 全滤空兜底）/ `LexicalQuery` / `escape_like()` |
| `src/rag/fusion.py` | `rrf_fusion` / `rrf_fusion_multi` / `_merge_path_ranks`（从 `bm25_index.py` **原样迁移**，纯函数、无 DB、无 IO） |
| `scripts/rewrite_content_seg.py` | 存量 `content_seg` 全量重写（`--check` 只读比对 / `--apply` 逐批重写，幂等） |
| `scripts/lexical_probe.py` | 词项命中探针：三组变量的横向比较 + 报告落盘 |
| `tests/infra/search/test_tokenizer.py` | 分词入口契约（单字过滤、写入/查询同源） |
| `tests/infra/db/test_lexical_query.py` | 查询构造与转义（H1/H2/H3 三个硬缺陷的守卫） |
| `tests/infra/db/test_chunk_repo_lexical.py` | `ChunkRepo.search_lexical` + `PgVectorStore.lexical_search`（真实 PG） |
| `tests/rag/test_fusion.py` | `rrf_fusion` / `rrf_fusion_multi`（纯函数，无 DB） |
| `tests/config/test_no_bm25_leftovers.py` | 无独立词法索引组件的残留守卫（D8） |

**修改**

| 文件 | 改动 |
|---|---|
| `src/config/const.py` | 新增 `LEXICAL_MIN_TOKEN_LEN = 2` |
| `src/config/settings.py` | 新增 `RRF_K` / `RRF_TOP_N`；**删除** `BM25_INDEX_DIR` |
| `src/infra/db/vector_store/mapping.py` | `build_rows` 的 `content_seg` 由正文原值改为 `to_lexical_text(content)` |
| `src/infra/db/vector_store/pg_store.py` | 新增 `lexical_search`；`add_chunks` 的 `build_rows` 改 `to_thread` |
| `src/infra/db/mysql_db/chunk_repo.py` | 新增 `search_lexical`（`tsv @@ to_tsquery` + `ts_rank`，子串兜底） |
| `src/infra/search/bm25_index.py` | Task 7 删掉三个纯函数；Task 9 **整个文件删除** |
| `src/rag/retrieval.py` | `rrf_fusion` 改从 `fusion` import；`search` 两路同源并发、去 `bm25` 形参、两路贡献进日志 |
| `src/agents/tools/rag_tools.py` | `make_rag_tools` 去 `bm25` 形参；`retrieval.search` 调用点同步 |
| `src/agents/graph/workflow.py` | `build_graph` 去 `bm25` 形参（第 2 个位置参数） |
| `src/services/agent_service.py` | `AgentService.__init__` 去 `bm25` 形参 |
| `src/services/app_service.py` | 去 `bm25` 组装与 KB 删除时的索引清理 |
| `src/services/document_service.py` | 去 `bm25` 形参与 `_rebuild_kb_index` 及其两个调用点 |
| `src/core/log_event_specs.py` | `hybrid done` 的 `fields` 增 `dense_count` / `sparse_count` |
| `src/cli/{check_abstain,eval_ragas,replay_trace}.py` | 去 BM25 装配 |
| `src/cli/rebuild_bm25.py` | **删除** |
| `tests/reset_data.py` | 删 `reset_bm25_index` 及其调用与 `__main__` 的 BM25 段 |
| `tests/rag/test_retrieval.py`、`tests/agents/tools/test_rag_tools.py`、`tests/agents/graph/test_graph.py`、`tests/agents/graph/test_direct_skill_round.py`、`tests/agents/skills/test_delegate_task.py`、`tests/cli/test_replay_trace.py`、`tests/services/test_*` | 签名与 mock 边界同步 |
| `tests/infra/search/test_bm25_index.py` | **删除**（`rrf_fusion` 用例迁入 `tests/rag/test_fusion.py`） |
| `docs/agents/{code-map,api_contract,data-flow,glossary,defensive-patterns,cookbook}.md` | 结构 / 契约 / 术语 / 防复发 / 操作协议同步 |
| `docs/agents/requirements_pool.md` | P3 遗留项登记 + F-26 并入 F-22（Task 0 的 parked 残留） |
| `docs/openspec/changes/postgres-storage-consolidation/tasks.md` | P3 状态与 §2 修正记录 |
| `docs/tmp/p2-dense-equivalence-2026-09-19.md` | Task 0：补灵敏度上限说明（parked 残留） |

---

### Task 0: 前置确认 + 4 条 parked 文档残留

**Files:**
- Modify: `docs/agents/code-map.md:78`（`MAX_QUERY_K` 归属）
- Modify: `docs/agents/api_contract.md:831`（同上）
- Modify: `docs/agents/requirements_pool.md`（F-26 并入 F-22）
- Modify: `docs/tmp/p2-dense-equivalence-2026-09-19.md`（补灵敏度上限）
- Test: 无（本任务不改代码）

**Interfaces:**
- Consumes: 无
- Produces: 回滚锚点 commit、P3 的爆炸半径清单、parked 残留已清

- [ ] **Step 1: 记录回滚锚点**

```bash
git rev-parse HEAD
git log --oneline -1
git status --short
```

把 40 位 SHA 记进报告。**这是 P3 的回滚锚点**（`git revert` / `git reset` 的目标）。工作区必须干净；若不干净先问控制器。

- [ ] **Step 2: 清理 parked 残留（R17-a）—— 给 P2 等价性报告补灵敏度上限**

打开 `docs/tmp/p2-dense-equivalence-2026-09-19.md`，在 `- 结论：**通过**` 之后插入：

```markdown
> **判据的灵敏度上限（不得据此宣称"迁移零风险"）**：判据是「24 条查询、k=30、**均值** top-k 重合率 ≥ 0.9」，
> 即单条查询最多允许 3 个位次不一致、均值口径最多容忍 2 条查询完全错位。指标**只比 id 集合、不比 distance/score**，
> 且两侧共用同一 embedder 并原样搬运 embedding（1.0000 是可推导的必然）→ 对**系统性错误**有效，
> 对**细微不等价**不敏感；若向量已单位化，cosine / L2 / 内积排序等价，**distance 语义回归会被漏检**。
```

- [ ] **Step 3: 清理 parked 残留（R17-b/c）—— 修正 `MAX_QUERY_K` 的归属**

`MAX_QUERY_K` 定义在 `src/config/const.py:172`，`pg_store.py` 只是导入方。两处措辞都要改：

`docs/agents/code-map.md` 的向量存储段，把

```
  `pg_store.py`（`PgVectorStore` + `QueryEmbedder` + `MAX_QUERY_K`）、`mapping.py`（行↔`ChunkResult`
```

改为

```
  `pg_store.py`（`PgVectorStore` + `QueryEmbedder`；k 上限 `MAX_QUERY_K` 定义在 `src/config/const.py`）、`mapping.py`（行↔`ChunkResult`
```

`docs/agents/api_contract.md` 的

```
**已知限制：** `k` 最大 100（`pg_store.MAX_QUERY_K`）。
```

改为

```
**已知限制：** `k` 最大 100（`src/config/const.py` 的 `MAX_QUERY_K`；`pg_store` 为导入方）。
```

- [ ] **Step 4: 清理 parked 残留（R17-d）—— F-26 并入 F-22**

`requirements_pool.md` 里 F-26 与 F-22 是同一件事（`alembic/env.py` 的 `compare_server_default`）。做法：

1. 删除 F-26 那一整行（`| F-26 | **（P4 覆盖...`）。
2. 在 F-22 那一行的「处置」列末尾追加一句：

```
。**（P4 门禁项）**终审要求把「有归属 P4 的门禁项」显式登记，本条即该项（原 F-26 已并入本条）
```

- [ ] **Step 5: 记录签名级联的全部调用点（F9 的爆炸半径）**

```bash
grep -rn "make_rag_tools(\|build_graph(\|AgentService(\|DocumentService(\|retrieval.search(\|from src.rag.retrieval import\|search(" \
  src/ tests/ --include=*.py | grep -v "__pycache__" > /tmp/p3_callsites.txt
grep -rn "bm25\|BM25" src/ tests/ --include=*.py | grep -v "__pycache__" > /tmp/p3_bm25.txt
wc -l /tmp/p3_callsites.txt /tmp/p3_bm25.txt
cat /tmp/p3_callsites.txt
```

把两份清单记进报告。**预期清单**（供 Task 8/9 对账）：

- `make_rag_tools`：`src/agents/graph/workflow.py:79`、`tests/agents/skills/test_delegate_task.py:372,383`、`tests/agents/tools/test_rag_tools.py:57,111,155,199,348,385`
- `build_graph`：`src/cli/check_abstain.py:196`、`src/cli/eval_ragas.py:533`、`src/services/agent_service.py:811`、`tests/agents/graph/test_graph.py:22,277,741,746`、`tests/agents/graph/test_direct_skill_round.py:84`
- `AgentService(`：`src/services/app_service.py:60`、`tests/services/*`、`tests/api/conftest.py`
- `retrieval.search`：`src/agents/tools/rag_tools.py:136`、`src/cli/replay_trace.py:112`、`tests/rag/test_retrieval.py`

**若出现清单外的文件**，在报告里点出来。

- [ ] **Step 6: 确认 jieba 的宿主与容器版本一致**

```bash
.venv/bin/python -c "import jieba, sys; print('host', jieba.__version__, sys.executable)"
docker compose exec -T app python -c "import jieba; print('container', jieba.__version__)"
```

预期两边都是 `0.42.1`。**若容器缺失或版本不同**：停下报告 —— Ruling 5 把 `jieba` 收成精确版本后需要重建镜像，那会改变本 plan 的执行前提。

- [ ] **Step 7: 确认 `content_seg` 的占位现状（F1 的前提）**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*), count(*) FILTER (WHERE content_seg = content), count(*) FILTER (WHERE content_seg <> content) FROM chunks;"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM chunks WHERE tsv IS NULL;"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(DISTINCT kb_id), count(DISTINCT doc_id) FROM chunks;"
```

预期：`176|176|0`（全部是占位）、`tsv` 无 NULL、`5|9`。
**若行数不是 176**：语料已被改动，先报告再继续（Task 5 的重写数量与 Task 6 的探针词项都会随之变化）。

- [ ] **Step 8: 确认 `to_tsquery` 的三种输入行为仍成立（F3/F4/F5 的前提）**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c \
  "SELECT to_tsvector('simple','营业收入 同比 增长 率 保持 稳定');"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c \
  "SELECT to_tsquery('simple','研发费用 5 月');" 2>&1 | tail -3
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c \
  "SELECT to_tsquery('simple','a:');"
```

预期：第一条给出 6 个 token（按空格切分）；第二条 **`ERROR: syntax error in tsquery`**；第三条静默给出 `'a'`。三条都与 `design.md` H1–H3 一致。

- [ ] **Step 9: 写报告**

把 Step 1–8 的真实输出写进 `TASK0_REPORT`（控制器会在派发时给出路径），并明确列出：回滚锚点 SHA、parked 残留的 diff 摘要、两份调用点/残留清单、任何与预期不符的项。

---

### Task 1: 分词入口（唯一的 jieba 调用点）

**Files:**
- Create: `src/infra/search/tokenizer.py`
- Modify: `src/config/const.py`（新增 `LEXICAL_MIN_TOKEN_LEN`）
- Modify: `pyproject.toml:35`（`jieba>=0.42.1` → `jieba==0.42.1`）
- Test: `tests/infra/search/test_tokenizer.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `tokenize(text: str) -> list[str]` —— 按 jieba 精确模式切词、过滤长度 < `LEXICAL_MIN_TOKEN_LEN`
  - `to_lexical_text(text: str) -> str` —— 空格连接的词项串（写入 `content_seg`）
  - `src/config/const.py` 的 `LEXICAL_MIN_TOKEN_LEN: int = 2`

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/search/test_tokenizer.py`：

```python
"""分词入口契约：单字过滤与写入/查询同源。"""

import inspect

from src.config.const import LEXICAL_MIN_TOKEN_LEN
from src.infra.search import tokenizer


def test_filters_single_char_tokens():
    """长度小于阈值的词项必须被剔除（含空格与中文标点）。"""
    tokens = tokenizer.tokenize("公司资产负债率上升，研发费用 5 月增加")
    assert "，" not in tokens
    assert " " not in tokens
    assert "5" not in tokens
    assert "月" not in tokens
    assert "公司" in tokens
    assert "资产负债率" in tokens


def test_all_single_char_query_yields_no_token():
    """全单字查询必须返回空列表 —— 这是子串兜底的触发条件。"""
    assert tokenizer.tokenize("涨了吗") == []
    assert tokenizer.tokenize("5 月") == []


def test_lexical_text_is_space_joined_tokens():
    """写入检索文本 = 词项以空格连接，且不含被过滤项。"""
    text = tokenizer.to_lexical_text("腾讯控股2024年全年营收6603亿元")
    assert text == " ".join(tokenizer.tokenize("腾讯控股2024年全年营收6603亿元"))
    assert "  " not in text
    assert not text.startswith(" ")
    assert not text.endswith(" ")


def test_threshold_comes_from_config():
    """阈值必须取自 config（不得在 tokenizer 内联字面量）。"""
    source = inspect.getsource(tokenizer)
    assert "LEXICAL_MIN_TOKEN_LEN" in source
    assert LEXICAL_MIN_TOKEN_LEN == 2
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/search/test_tokenizer.py -v
```

预期：`ModuleNotFoundError` / `ImportError: cannot import name 'LEXICAL_MIN_TOKEN_LEN'`。

- [ ] **Step 3: 在 `src/config/const.py` 新增阈值**

在 `MAX_QUERY_K` 那段之后追加：

```python
# ── 词法检索分词 ──
# 词项长度下限：中文高频虚词与量词本身就是单字（本/及/不/年/月/了/吗），
# 文档频率接近 1，纳入检索文本会淹没精确词项的排序；该过滤同时去掉空格与
# 中文标点（长度均为 1），因此不需要另建停用词表
LEXICAL_MIN_TOKEN_LEN = 2
```

- [ ] **Step 4: 新建 `src/infra/search/tokenizer.py`**

```python
"""词法检索的分词入口 —— 写入侧与查询侧的唯一分词口径。

写入（`chunks.content_seg`）与查询（tsquery 词元）必须调用**同一个函数、
同一份配置**：两侧口径不一致不会报错，只会静默降召回。

分词结果会随 `chunks.tsv` 生成列**固化落库**，因此 `jieba` 的版本或词典
一旦变更，存量 `content_seg` 与新的查询侧就不再一致 —— 那是同一种静默失效的
第二种形态（进程内比较的守卫测试抓不到）。缓解有两条：依赖 pin 精确版本
（`pyproject.toml`），以及变更后必须跑 `scripts/rewrite_content_seg.py --apply`。
"""

import jieba

from src.config.const import LEXICAL_MIN_TOKEN_LEN


def tokenize(text: str) -> list[str]:
    """按 jieba 精确模式切词并过滤长度不足的词项。

    Args:
        text: 正文原文或用户查询

    Returns:
        词项列表（保留原顺序与重复项）；长度 < LEXICAL_MIN_TOKEN_LEN 的被剔除
    """
    return [t for t in jieba.lcut(text) if len(t) >= LEXICAL_MIN_TOKEN_LEN]


def to_lexical_text(text: str) -> str:
    """把正文转成写入 `chunks.content_seg` 的检索文本。

    `tsv` 是 `content_seg` 的生成列，用空格切出词边界后，`to_tsvector('simple')`
    只需按非字母数字切分即可得到词项级 lexeme。

    Args:
        text: 分块正文原文

    Returns:
        空格连接的词项串；无词项时为空串
    """
    return " ".join(tokenize(text))
```

- [ ] **Step 5: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/search/test_tokenizer.py -v
```

预期：4 passed。

- [ ] **Step 6: 收窄 jieba 版本（Ruling 5）**

把 `pyproject.toml` 的

```toml
    "jieba>=0.42.1",
```

改为

```toml
    # pin 精确版本：分词结果随 chunks.tsv 生成列固化落库，版本漂移会让存量与新
    # 查询侧口径不一致（静默降召回），变更必须配 scripts/rewrite_content_seg.py --apply
    "jieba==0.42.1",
```

**不要**顺手删 `rank_bm25`（Ruling 5：依赖删除随 F-19 在 P4 做）。改完确认没有触发依赖安装：

```bash
git diff --stat pyproject.toml
docker compose exec -T app python -c "import jieba; print(jieba.__version__)"
```

预期仍为 `0.42.1`（容器无需重建）。

- [ ] **Step 7: 提交**

```bash
ruff check src/infra/search/tokenizer.py src/config/const.py tests/infra/search/test_tokenizer.py
git add src/infra/search/tokenizer.py src/config/const.py pyproject.toml tests/infra/search/test_tokenizer.py
git commit -m "feat(p3): jieba 分词入口 —— 写入与查询的唯一口径"
```

---

### Task 2: 查询条件的构造与转义

**Files:**
- Create: `src/infra/db/lexical_query.py`
- Test: `tests/infra/db/test_lexical_query.py`（新建）

**Interfaces:**
- Consumes: `tokenizer.tokenize()`（Task 1）
- Produces:
  - `LexicalQuery`（frozen dataclass）：`terms: tuple[str, ...]` / `tsquery: str` / `raw: str` / `use_substring: bool`
  - `build_lexical_query(query: str) -> LexicalQuery`
  - `escape_like(value: str) -> str`

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_lexical_query.py`：

```python
"""词法查询构造与转义 —— H1/H2/H3 三个静默缺陷的守卫。"""

import pytest

from src.infra.db.lexical_query import LexicalQuery, build_lexical_query, escape_like
from src.infra.search.tokenizer import tokenize


def test_prefix_wildcard_per_token():
    """H2：每个词元必须带前缀通配，否则词形不一致时静默 0 命中。"""
    plan = build_lexical_query("营业收入增长")
    assert plan.use_substring is False
    assert plan.tsquery == " & ".join(f"{t}:*" for t in plan.terms)
    assert plan.tsquery.count(":*") == len(plan.terms)


def test_query_terms_equal_tokenize_output():
    """写入侧与查询侧必须同源：查询词元等于 tokenize 的输出（无二次加工）。"""
    query = "腾讯控股2024年全年营收6603亿元"
    plan = build_lexical_query(query)
    assert plan.terms == tuple(tokenize(query))


def test_all_single_char_query_falls_back_to_substring():
    """H1：词元全被滤掉时必须走原文子串，不得提交空条件。"""
    plan = build_lexical_query("涨了吗")
    assert plan.use_substring is True
    assert plan.tsquery == ""
    assert plan.raw == "涨了吗"

    plan = build_lexical_query("5 月")
    assert plan.use_substring is True
    assert plan.raw == "5 月"


@pytest.mark.parametrize(
    "query",
    ["研发费用 5 月", "C&C", "a:", "a | b", "!重要", "(测试)", "报告<->期", "50%-100%", "a_b"],
)
def test_special_chars_never_raise_and_never_leak_operators(query):
    """H3：查询语法字符不得进入 tsquery，也不得抛错。"""
    plan = build_lexical_query(query)
    for op in ("&", "|", "!", "(", ")", "<->", ":"):
        if plan.use_substring:
            assert plan.tsquery == ""
            continue
        # 词元之间才允许出现 ` & `，单看词元部分不得含任何操作符
        for term in plan.terms:
            assert op not in term


def test_sanitize_drops_unsafe_token_but_keeps_chinese():
    """安全化只剔字符，不改变中文词元的可用性。"""
    plan = build_lexical_query("资产负债率")
    assert plan.use_substring is False
    assert plan.terms == ("资产负债率",)


def test_escape_like_neutralizes_wildcards():
    """F6：子串兜底的 LIKE 模式必须转义 % 与 _。"""
    assert escape_like("50%") == "50\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("c\\d") == "c\\\\d"


def test_dataclass_is_frozen():
    """查询条件是不可变值对象，避免被下游改写。"""
    plan = build_lexical_query("资产负债率")
    assert isinstance(plan, LexicalQuery)
    with pytest.raises(Exception):
        plan.tsquery = "x"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_lexical_query.py -v
```

预期：`ModuleNotFoundError: No module named 'src.infra.db.lexical_query'`。

- [ ] **Step 3: 新建 `src/infra/db/lexical_query.py`**

```python
"""词法检索查询条件的构造与转义 —— 查询侧词元口径的唯一入口。

用户原文**不得**直接交给 PostgreSQL 的 tsquery 解析器：`&` / `|` / `!` / `:` /
空格会在解析期抛错（实测 `to_tsquery('simple','研发费用 5 月')` →
`ERROR: syntax error in tsquery`），而 `a:` 这类输入会被**静默吞字符**（得到 `'a'`）。
在并发聚合取数的路径上，抛错的影响面比静默降召回更大。

因此查询串在此由**安全词元**拼装：每个词元先按允许字符集剔除，再拼成
`词元:*`（前缀通配，词形不一致时的唯一救回手段），以 ` & ` 连接。
剔除后不剩词元时降级为**原文子串匹配** —— 写入侧已把单字从 `content_seg`
剔除，tsv 里没有单字 lexeme，"回退为不过滤"命中不了任何东西。
"""

import re
from dataclasses import dataclass

from src.infra.search.tokenizer import tokenize

# 允许进入 tsquery 的字符：Unicode 词字符 = 中日韩字符 / 字母 / 数字 / 下划线。
# 其余（含 tsquery 操作符与空白）一律剔除 —— 剔除而非转义，因为 tsquery 没有
# 可用的通用转义形式，白名单是唯一无歧义的做法
_UNSAFE_RE = re.compile(r"\W", re.UNICODE)

# 词元之间的连接符：前缀 AND
_JOINER = " & "


@dataclass(frozen=True)
class LexicalQuery:
    """词法检索的查询条件。"""

    terms: tuple[str, ...]
    """安全化后的词元，顺序与分词结果一致。"""
    tsquery: str
    """`词元:*` 以 ` & ` 连接；词元为空时为空串（此时不得提交给数据库）。"""
    raw: str
    """用户原文，作为原文子串匹配（LIKE）的匹配串。"""
    use_substring: bool
    """True = 词元全被滤掉，改走原文子串匹配。"""


def build_lexical_query(query: str) -> LexicalQuery:
    """把用户查询转成可安全提交的检索条件。

    Args:
        query: 用户原始查询文本

    Returns:
        LexicalQuery；`use_substring` 为 True 时只有 `raw` 有意义
    """
    terms: list[str] = []
    for token in tokenize(query):
        safe = _UNSAFE_RE.sub("", token)
        if safe:
            terms.append(safe)
    if not terms:
        return LexicalQuery(terms=(), tsquery="", raw=query, use_substring=True)
    tsquery = _JOINER.join(f"{t}:*" for t in terms)
    return LexicalQuery(
        terms=tuple(terms), tsquery=tsquery, raw=query, use_substring=False
    )


def escape_like(value: str) -> str:
    """转义 LIKE 模式里的特殊字符。

    不转义时用户查询里的 `%` 会匹配任意串（查 "%" 命中全库）、`_` 会匹配单字符。

    Args:
        value: 原始子串

    Returns:
        转义后的模式片段（配合 `.like(..., escape="\\\\")` 使用）
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_lexical_query.py -v
```

预期：全部 passed（参数化 8 条 + 6 条单测）。

- [ ] **Step 5: 提交**

```bash
ruff check src/infra/db/lexical_query.py tests/infra/db/test_lexical_query.py
git add src/infra/db/lexical_query.py tests/infra/db/test_lexical_query.py
git commit -m "feat(p3): 词法查询条件的构造与转义（前缀通配 + 子串兜底）"
```

---

### Task 3: 词法取数（`ChunkRepo.search_lexical` + `PgVectorStore.lexical_search`）

**Files:**
- Modify: `src/infra/db/mysql_db/chunk_repo.py`（新增 `search_lexical`）
- Modify: `src/infra/db/vector_store/pg_store.py`（新增 `lexical_search`）
- Test: `tests/infra/db/test_chunk_repo_lexical.py`（新建）

**Interfaces:**
- Consumes: `LexicalQuery` / `escape_like`（Task 2）、`row_to_chunk_row` / `row_to_chunk_result`（P2）、`MAX_QUERY_K`（`src/config/const.py:172`）
- Produces:
  - `ChunkRepo.search_lexical(kb_id: str, plan: LexicalQuery, k: int) -> list[tuple[ChunkRow, float]]`
  - `async PgVectorStore.lexical_search(kb_id: str, query: str, k: int = 5) -> list[ChunkResult]`（填 `lexical_score` 与 `sparse_rank`）

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_chunk_repo_lexical.py`：

```python
"""词法取数：tsquery 命中、ts_rank 排序、子串兜底（真实 PG）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.lexical_query import build_lexical_query
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.pg_store import PgVectorStore
from src.infra.search.tokenizer import to_lexical_text

pytestmark = pytest.mark.asyncio

_DOCS = [
    # 取自 design.md D4 的实测 jieba 输出：content_seg 为「营业 收入 同比 增长率 保持稳定」，
    # 因此查询「营业收入」被切成「营业」+「收入」后仍能靠前缀通配召回（H2）
    ("d1", "营业收入同比增长率保持稳定"),
    # 实测输出含单字被滤掉的「5」与「月」，是 H1 的子串兜底用例
    ("d2", "公司资产负债率上升，研发费用 5 月增加"),
    ("d3", "前五名客户合计销售金额 36.18%，占年度销售总额"),
    # 与 d2 共享 资产负债率，用于验证排序与 sparse_rank 序列
    ("d4", "公司资产负债率保持稳定，研发费用增加"),
]


@pytest_asyncio.fixture
async def lexical_kb():
    """建真实 KB 与 4 条已分词的分块，返回 (repo, store, kb_id)。"""
    kb_id = f"p3lex-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p3test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p3-{kb_id[-6:]}"},
        )
        for doc_id, content in _DOCS:
            await s.execute(
                text(
                    "INSERT INTO chunks (id, kb_id, doc_id, chunk_index, chunk_total,"
                    " content, content_seg, source, page, metadata)"
                    " VALUES (:i, :k, :d, 0, 1, :c, :seg, 'r.pdf', 1, '{}'::jsonb)"
                ),
                {
                    "i": f"{doc_id}:0",
                    "k": kb_id,
                    "d": doc_id,
                    "c": content,
                    "seg": to_lexical_text(content),
                },
            )
        await s.commit()
    repo = ChunkRepo(session_factory)
    yield repo, PgVectorStore(chunk_repo=repo), kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def test_prefix_wildcard_recalls_different_word_form(lexical_kb):
    """H2：查询「营业收入」而文档词元是「营业」+「收入」，必须仍能召回 d1。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("营业收入"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d1"]


async def test_prefix_wildcard_recalls_longer_document_token(lexical_kb):
    """H2：查询「增长」而文档词元是「增长率」，必须靠前缀通配召回 d1。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("增长"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d1"]


async def test_rank_is_descending_and_reproducible(lexical_kb):
    """ts_rank 排序：得分单调不增，且同一查询两次结果一致。"""
    repo, _store, kb_id = lexical_kb
    plan = build_lexical_query("公司")
    first = await repo.search_lexical(kb_id, plan, 10)
    second = await repo.search_lexical(kb_id, plan, 10)
    assert {row.doc_id for row, _ in first} == {"d2", "d4"}
    scores = [score for _, score in first]
    assert scores == sorted(scores, reverse=True)
    assert [row.id for row, _ in first] == [row.id for row, _ in second]


async def test_substring_fallback_hits_raw_content(lexical_kb):
    """H1：全单字查询走正文子串兜底，且能命中。"""
    repo, _store, kb_id = lexical_kb
    plan = build_lexical_query("5 月")
    assert plan.use_substring is True
    pairs = await repo.search_lexical(kb_id, plan, 10)
    assert [row.doc_id for row, _ in pairs] == ["d2"]
    assert [score for _, score in pairs] == [0.0]


async def test_like_wildcard_is_escaped(lexical_kb):
    """F6：子串兜底的 % 必须被转义 —— 只命中正文里真的有 % 的那一条，而非全库。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("%"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d3"]


async def test_other_kb_is_not_visible(lexical_kb):
    """词法取数必须限定在单个知识库内。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("资产负债率"), 10)
    assert {row.kb_id for row, _ in pairs} == {kb_id}


async def test_store_lexical_search_fills_rank_and_score(lexical_kb):
    """VectorStore 层：sparse_rank 为 0 起名次，lexical_score 有值，distance 为 None。"""
    _repo, store, kb_id = lexical_kb
    results = await store.lexical_search(kb_id, "资产负债率", 10)
    assert [r.sparse_rank for r in results] == list(range(len(results)))
    assert all(r.lexical_score is not None for r in results)
    assert all(r.distance is None for r in results)
    assert {r.metadata["doc_id"] for r in results} == {"d2", "d4"}
    assert all(r.metadata["source"] == "r.pdf" for r in results)
    assert all(r.metadata["page"] == 1 for r in results)


async def test_store_lexical_search_empty_for_unknown_kb(lexical_kb):
    """无分块的知识库返回空列表而不是抛错。"""
    _repo, store, _kb_id = lexical_kb
    assert await store.lexical_search("no-such-kb", "资产负债率", 10) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_repo_lexical.py -v
```

预期：`AttributeError: 'ChunkRepo' object has no attribute 'search_lexical'`。

- [ ] **Step 3: 在 `ChunkRepo` 增加 `search_lexical`**

`src/infra/db/mysql_db/chunk_repo.py`：把 import 段改为

```python
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infra.db.lexical_query import LexicalQuery, escape_like
from src.infra.db.models.chunk import ChunkModel
from src.infra.db.vector_store.mapping import ChunkRow, row_to_chunk_row
```

在 `search_dense` 之后插入：

```python
    async def search_lexical(
        self, kb_id: str, plan: LexicalQuery, k: int
    ) -> list[tuple[ChunkRow, float]]:
        """按词法相关性取 top-k。

        词元非空时走 `tsv @@ to_tsquery(...)` 并按 `ts_rank` 降序；词元全被滤掉时
        （见 lexical_query 的 H1）降级为正文子串匹配，此时得分恒为 0.0。
        两种路径都以 `id` / `(doc_id, chunk_index)` 作 tiebreaker，保证同查询可复现
        （融合是纯 Python 排序，上游结果顺序不定会让 RRF 输出漂移）。

        Args:
            kb_id: 知识库 ID
            plan: 查询条件（由调用方构造，见 lexical_query.build_lexical_query）
            k: 返回条数上限

        Returns:
            (行, 词法得分) 列表；tsquery 路径按得分降序，子串兜底路径按文档与序号升序
        """
        async with self._sf() as session:
            if plan.use_substring:
                stmt = (
                    select(ChunkModel, literal(0.0).label("lexical_score"))
                    .where(
                        ChunkModel.kb_id == kb_id,
                        ChunkModel.content.like(
                            f"%{escape_like(plan.raw)}%", escape="\\"
                        ),
                    )
                    .order_by(ChunkModel.doc_id, ChunkModel.chunk_index)
                    .limit(k)
                )
            else:
                tsquery = func.to_tsquery("simple", plan.tsquery)
                lexical_score = func.ts_rank(ChunkModel.tsv, tsquery).label(
                    "lexical_score"
                )
                stmt = (
                    select(ChunkModel, lexical_score)
                    .where(
                        ChunkModel.kb_id == kb_id,
                        ChunkModel.tsv.op("@@")(tsquery),
                    )
                    .order_by(lexical_score.desc(), ChunkModel.id)
                    .limit(k)
                )
            result = await session.execute(stmt)
            return [(row_to_chunk_row(m), float(score)) for m, score in result.all()]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_repo_lexical.py -v
```

预期：8 passed。**若 `test_prefix_wildcard_recalls_different_word_form` 失败**：说明 `content_seg` 没走分词（本任务的 fixture 自己写 `to_lexical_text`，所以失败意味着 Task 1/2 有问题），停下排查而不是放宽断言。

- [ ] **Step 5: 在 `PgVectorStore` 增加 `lexical_search`**

`src/infra/db/vector_store/pg_store.py`：import 段加

```python
from src.infra.db.lexical_query import build_lexical_query
```

在 `dense_search` 之后插入：

```python
    async def lexical_search(
        self, kb_id: str, query: str, k: int = 5
    ) -> list[ChunkResult]:
        """词法路取 top-k（ts_rank 降序），并填充 sparse_rank。

        与 dense_search 对称：分词（含首次加载 jieba 词典，CPU 约 0.5–1 s）
        放在线程池，避免阻塞事件循环。

        Args:
            kb_id: 知识库 ID
            query: 用户查询文本
            k: 返回条数上限（内部再按 MAX_QUERY_K 截断）

        Returns:
            按词法得分降序的 ChunkResult；每项 lexical_score 有值、
            sparse_rank 为 0 起的名次、distance 为 None
        """
        effective_k = min(k, MAX_QUERY_K)
        plan = await asyncio.to_thread(build_lexical_query, query)
        pairs = await self._repo.search_lexical(kb_id, plan, effective_k)
        results = [
            row_to_chunk_result(row, lexical_score=score, sparse_rank=rank)
            for rank, (row, score) in enumerate(pairs)
        ]
        logger.debug(
            "[PG] method=lexical_search | kb_id={} | rows={} | substring={} | data={}",
            kb_id,
            len(results),
            plan.use_substring,
            str(results)[:LOG_MAX_BODY],
        )
        return results
```

- [ ] **Step 6: 补两条断言并跑全量存储侧测试**

在 `tests/infra/db/test_chunk_repo_lexical.py` 末尾追加：

```python
async def test_store_lexical_search_respects_max_query_k(lexical_kb):
    """k 上限仍是 100（MAX_QUERY_K），与 dense_search 一致。"""
    _repo, store, kb_id = lexical_kb
    results = await store.lexical_search(kb_id, "公司", 500)
    assert len(results) <= 100


async def test_store_lexical_search_handles_tsquery_syntax_chars(lexical_kb):
    """H3：含查询语法字符的输入不得抛错（并发取数路径上抛错影响面更大）。"""
    _repo, store, kb_id = lexical_kb
    for query in ("研发费用 5 月", "C&C", "a:", "a | b", "!重要", "(测试)"):
        results = await store.lexical_search(kb_id, query, 10)
        assert isinstance(results, list)
```

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/ tests/rag/ -v
```

预期：全过（含 P2 的既有用例）。

- [ ] **Step 7: 提交**

```bash
ruff check src/infra/db/ tests/infra/db/test_chunk_repo_lexical.py
git add src/infra/db/mysql_db/chunk_repo.py src/infra/db/vector_store/pg_store.py tests/infra/db/test_chunk_repo_lexical.py
git commit -m "feat(p3): 词法取数 —— tsquery 命中 + ts_rank 排序 + 子串兜底"
```

---

### Task 4: 写入侧接上分词（替换 P2 的正文占位）

**Files:**
- Modify: `src/infra/db/vector_store/mapping.py:133-134`
- Modify: `src/infra/db/vector_store/pg_store.py:108`（`build_rows` 走 `to_thread`）
- Test: `tests/infra/db/test_chunk_mapping.py`（追加）、`tests/infra/db/test_pg_vector_store_write.py`（追加）

**Interfaces:**
- Consumes: `tokenizer.to_lexical_text()`（Task 1）
- Produces: `build_rows()` 产出的 `ChunkRow.content_seg` 是**分词输出**；`PgVectorStore.add_chunks` 在写入后 `chunks.tsv` 立即可被词法路命中

- [ ] **Step 1: 写失败测试**

在 `tests/infra/db/test_chunk_mapping.py` 末尾追加：

```python
def test_content_seg_is_tokenized_not_raw():
    """写入检索文本必须是分词输出，不得是正文原值（P2 的占位已过期）。"""
    from src.chunking.validator import ChunkData
    from src.infra.db.vector_store.mapping import build_rows
    from src.infra.search.tokenizer import to_lexical_text

    content = "公司资产负债率上升，研发费用 5 月增加"
    rows = build_rows("kb1", "doc1", [ChunkData(content=content, metadata={})], [[0.0] * 1024])
    assert rows[0].content == content
    assert rows[0].content_seg == to_lexical_text(content)
    assert rows[0].content_seg != content
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_mapping.py -v
```

预期：FAIL（`content_seg == content`，即占位值）。

- [ ] **Step 3: 改 `build_rows` 并 offload**

`src/infra/db/vector_store/mapping.py`：import 段加

```python
from src.infra.search.tokenizer import to_lexical_text
```

把 `build_rows` 里的

```python
                content=chunk.content,
                # P2 占位：content_seg 写正文原值，P3 换成 jieba 分词输出并全量重写
                content_seg=chunk.content,
```

改为

```python
                content=chunk.content,
                # 检索文本 = jieba 词项以空格连接；与查询侧共用 tokenizer，
                # 两侧口径不一致不会报错、只会静默降召回
                content_seg=to_lexical_text(chunk.content),
```

`src/infra/db/vector_store/pg_store.py`：把

```python
        rows = build_rows(kb_id, doc_id, chunks, embeddings)
```

改为

```python
        # 分词是 CPU 工作且 jieba 首次调用要加载词典（约 0.5–1 s）→ offload，
        # 否则阻塞事件循环（单 worker 下会冻住所有请求与 SSE）
        rows = await asyncio.to_thread(build_rows, kb_id, doc_id, chunks, embeddings)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_mapping.py tests/infra/db/test_pg_vector_store_write.py -v
```

预期：全过。

- [ ] **Step 5: 加一条端到端写入→词法命中测试**

在 `tests/infra/db/test_pg_vector_store_write.py` 末尾追加：

```python
async def test_written_chunk_is_immediately_lexically_searchable(store_and_kb):
    """写入后 tsv 生成列自动填充，词法路立即可命中（不需要额外重建索引）。"""
    from src.chunking.validator import ChunkData

    store, kb_id = store_and_kb
    chunks = [
        ChunkData(
            content="公司资产负债率上升，研发费用 5 月增加，净利润同比下降。",
            metadata={"source": "r.pdf", "page": 3},
        )
    ]
    await store.add_chunks(kb_id, chunks, "d-lex", [[0.1] * 1024])
    results = await store.lexical_search(kb_id, "资产负债率", 10)
    assert [r.metadata["doc_id"] for r in results] == ["d-lex"]
    assert results[0].metadata["source"] == "r.pdf"
```

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_pg_vector_store_write.py -v
```

预期：全过。

- [ ] **Step 6: 提交**

```bash
ruff check src/infra/db/vector_store/ tests/infra/db/
git add src/infra/db/vector_store/mapping.py src/infra/db/vector_store/pg_store.py tests/infra/db/test_chunk_mapping.py tests/infra/db/test_pg_vector_store_write.py
git commit -m "feat(p3): 写入侧 content_seg 改为分词输出（替换 P2 占位）"
```

---

### Task 5: 存量 `content_seg` 全量重写

**Files:**
- Create: `scripts/rewrite_content_seg.py`
- Modify: 无（脚本写入数据库）
- Test: `tests/scripts/test_rewrite_content_seg.py`（新建，测纯函数）

**Interfaces:**
- Consumes: `tokenizer.to_lexical_text()`（Task 1）
- Produces: `scripts/rewrite_content_seg.py` 的 `check(session) -> tuple[int, int]`（总行数, 不匹配行数）与 `apply(session) -> int`（改写行数），两个 `--check` / `--apply` 入口

- [ ] **Step 1: 写失败测试**

新建 `tests/scripts/test_rewrite_content_seg.py`：

```python
"""存量重写脚本的纯逻辑（不连库）。"""

from scripts.rewrite_content_seg import is_stale


def test_stale_when_raw_text():
    """P2 的正文占位必须被判定为过期。"""
    assert is_stale("公司资产负债率上升，研发费用增加。", "公司资产负债率上升，研发费用增加。") is True


def test_fresh_when_tokenized():
    """分词输出必须被判定为最新。"""
    content = "公司资产负债率上升，研发费用增加。"
    seg = "公司 资产负债率 上升 研发 费用 增加"
    assert is_stale(content, seg) is False


def test_stale_when_tokenizer_changed():
    """分词口径变更（词典升级）后存量必须被判过期。"""
    assert is_stale("营业收入同比增长率保持稳定", "营业收入 同比 增长 率 保持 稳定") is True
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_rewrite_content_seg.py -v
```

预期：`ModuleNotFoundError: No module named 'scripts.rewrite_content_seg'`。

- [ ] **Step 3: 新建 `scripts/rewrite_content_seg.py`**

```python
"""存量 `content_seg` 全量重写 —— 分词器变更后的唯一迁移步骤。

`content_seg` 是 `chunks.tsv` 生成列的输入，一旦落库即**固化**：jieba 版本或
词典变更后，存量检索文本与新的查询侧口径不一致，**不会报错、只会静默降召回**。
本脚本就是那条不变量的可执行检查（`--check` 非零退出即表示存量已过期）。

幂等：`--apply` 只改写与 `to_lexical_text(content)` 不一致的行，重复执行改写 0 行。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check
    POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
"""

import argparse
import asyncio
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.engine import run_and_dispose, session_factory
from src.infra.db.models.chunk import ChunkModel
from src.infra.search.tokenizer import to_lexical_text

# 逐批处理：避免一次把全部分块正文读进内存（量产规模约 2.5–4 万分块）
BATCH_SIZE = 500


def is_stale(content: str, content_seg: str) -> bool:
    """判断一行的检索文本是否与当前分词口径不一致。

    Args:
        content: 分块正文原文
        content_seg: 库中现存的检索文本

    Returns:
        True = 需要重写
    """
    return content_seg != to_lexical_text(content)


async def check(session: AsyncSession) -> tuple[int, int]:
    """只读比对，返回 (总行数, 过期行数)。"""
    total = 0
    stale = 0
    offset = 0
    while True:
        result = await session.execute(
            select(ChunkModel.id, ChunkModel.content, ChunkModel.content_seg)
            .order_by(ChunkModel.id)
            .offset(offset)
            .limit(BATCH_SIZE)
        )
        rows = result.all()
        if not rows:
            break
        total += len(rows)
        for _id, content, content_seg in rows:
            if is_stale(content, content_seg):
                stale += 1
        offset += len(rows)
    return total, stale


async def apply(session: AsyncSession) -> int:
    """逐批重写过期行的检索文本，返回改写行数。"""
    rewritten = 0
    offset = 0
    while True:
        result = await session.execute(
            select(ChunkModel.id, ChunkModel.content, ChunkModel.content_seg)
            .order_by(ChunkModel.id)
            .offset(offset)
            .limit(BATCH_SIZE)
        )
        rows = result.all()
        if not rows:
            break
        for _id, content, content_seg in rows:
            if not is_stale(content, content_seg):
                continue
            await session.execute(
                update(ChunkModel)
                .where(ChunkModel.id == _id)
                .values(content_seg=to_lexical_text(content))
            )
            rewritten += 1
        await session.commit()
        offset += len(rows)
    return rewritten


async def _main(mode: str) -> int:
    async with session_factory() as session:
        if mode == "check":
            total, stale = await check(session)
            print(f"total={total} stale={stale}")
            if stale:
                print("存量检索文本已过期：跑 --apply 重写（分词器变更必须触发全量重写）")
                return 1
            return 0
        rewritten = await apply(session)
        print(f"rewritten={rewritten}")
        return 0


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="存量 content_seg 全量重写")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只读比对，过期则退出码 1")
    group.add_argument("--apply", action="store_true", help="逐批重写过期行")
    args = parser.parse_args()
    mode = "check" if args.check else "apply"
    code = asyncio.run(run_and_dispose(_main(mode)))
    sys.exit(code)


if __name__ == "__main__":
    main()
```

> `run_and_dispose` 由 P2 引入（`src/infra/db/engine.py`），保证脚本结束时 `engine.dispose()`，避免 asyncpg 连接绑定到已关闭事件循环。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_rewrite_content_seg.py -v
```

预期：3 passed。

- [ ] **Step 5: 实跑 `--check`（预期全部过期）**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check; echo "exit=$?"
```

预期：`total=176 stale=176`、`exit=1`。把真实输出记进报告。
**若 `stale=0`**：说明 Task 4 已让写入侧生效且存量恰好都是分词结果 —— 不可能（存量是 P2 写的占位），停下核对。

- [ ] **Step 6: 实跑 `--apply` 并复验**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check; echo "exit=$?"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*), count(*) FILTER (WHERE content_seg = content) FROM chunks;"
```

预期：`rewritten=176`；随后 `total=176 stale=0`、`exit=0`；DB 侧 `176|0`。
再跑一次 `--apply` 确认幂等：预期 `rewritten=0`。

- [ ] **Step 7: 提交**

```bash
ruff check scripts/rewrite_content_seg.py tests/scripts/test_rewrite_content_seg.py
git add scripts/rewrite_content_seg.py tests/scripts/test_rewrite_content_seg.py
git commit -m "feat(p3): 存量 content_seg 全量重写脚本（分词器变更的迁移步骤）"
```

---

### Task 6: 词项命中探针与选型固化

**Files:**
- Create: `scripts/lexical_probe.py`
- Create: `docs/tmp/p3-lexical-probe-2026-09-19.md`（脚本产出）
- Test: `tests/scripts/test_lexical_probe.py`（新建，测纯函数）
- Modify: `src/infra/db/lexical_query.py`（仅当探针结论要求改连接符或打分算法时）
- Modify: `src/infra/db/mysql_db/chunk_repo.py`（仅当换 `ts_rank_cd` 时）

**Interfaces:**
- Consumes: `tokenizer.tokenize()`（Task 1）、`build_lexical_query()`（Task 2）、`chunks` 表已重写（Task 5）
- Produces:
  - `derive_terms(docs: list[str], max_terms: int) -> list[str]`（纯函数：n-gram 抽取 + df 筛选）
  - `hit_rate(ranked_contents: list[str], term: str) -> bool`
  - 报告 `docs/tmp/p3-lexical-probe-2026-09-19.md` + 选型结论（Ruling 2 / 3 的确认或翻转）

- [ ] **Step 1: 写失败测试**

新建 `tests/scripts/test_lexical_probe.py`：

```python
"""探针的纯函数：词项派生与命中共 2 项。"""

from scripts.lexical_probe import derive_terms, hit_rate


def test_derive_terms_excludes_df_one_and_high_frequency():
    """df 筛选：df=1 无从判断召回、高频词平凡通过，二者都要剔除。"""
    docs = [
        "资产负债率 资产负债率 净利润",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
    ]
    terms = derive_terms(docs, max_terms=50)
    assert "资产负债率" not in terms  # df = 11 > 0.1 * 11 → 高频，剔除
    assert "净利润" in terms  # df = 11 → 同上，剔除
    assert all(len(t) >= 2 for t in terms)


def test_derive_terms_is_deterministic():
    """同一语料两次派生必须完全一致（报告可复现）。"""
    docs = ["营业收入 同比 增长", "资产负债率 上升", "研发 费用 增加"]
    assert derive_terms(docs, 10) == derive_terms(docs, 10)


def test_hit_rate_uses_raw_containment():
    """命中判据是原始正文的字符串包含，与分词器无关。"""
    assert hit_rate(["公司资产负债率上升", "无关内容"], "资产负债率") is True
    assert hit_rate(["公司资产负债率上升"], "营业收入") is False
    assert hit_rate([], "营业收入") is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_lexical_probe.py -v
```

预期：`ModuleNotFoundError: No module named 'scripts.lexical_probe'`。

- [ ] **Step 3: 新建 `scripts/lexical_probe.py`**

```python
"""词项命中探针 —— 词法路的分词配置、查询构造、打分算法的横向比较。

**判据口径**（`design.md` D6 固化）：
- 探针词项从**原始正文**抽取（CJK 滑窗 n-gram），`SHALL NOT` 用分词后的
  `content_seg` 或 tsquery 自判 —— 否则同一分词器既造索引又造标签，构成自我循环；
- 词项按文档频率筛选 `2 <= df <= DF_RATIO * N`（剔除 df=1 的无从判断项与
  高频的平凡通过项），长度 >= 2；
- 命中以**原始正文的字符串包含**判定，与分词器无关；
- 跨分词器比较**固定打分算法**（`rank_bm25` 的 BM25Okapi 同时跑在字符级与
  jieba 词项两套 tokenization 上，即干净的一对）；查询构造单独成一组比较；
- 报告只报命中数与词项总数及完整词项清单，**仅供相对比较**。

**小语料限制（必须写进报告）**：176 分块、k=30 时单词项的命中集合可能已占语料
17%–28%，多数词项会平凡通过。探针的用途是**在同一语料上横向比配置**，
`SHALL NOT` 作为质量基线或发布判据。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/lexical_probe.py \
        --out docs/tmp/p3-lexical-probe-2026-09-19.md
"""

import argparse
import asyncio
import re
from pathlib import Path

from rank_bm25 import BM25Okapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.const import MAX_QUERY_K
from src.config.settings import TOP_K_RETRIEVAL
from src.infra.db.engine import run_and_dispose, session_factory
from src.infra.db.lexical_query import build_lexical_query, escape_like
from src.infra.search.tokenizer import tokenize

# 探针参数（本脚本专用的分析参数，不是业务阈值）
NGRAM_MIN = 2
NGRAM_MAX = 4
DF_MIN = 2
DF_RATIO = 0.1
MAX_TERMS = 30
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

# 显式覆盖的失效类（探针自带的 df 筛选与"原文包含"判据测不到它们）
EXPLICIT_CASES = ("涨了吗", "5 月", "增长", "营业收入", "资产负债率", "净利润")


def derive_terms(docs: list[str], max_terms: int) -> list[str]:
    """从原始正文派生探针词项。

    候选 = CJK 连续段的 2..4 元 n-gram（机械抽取，不经任何分词器），
    再按文档频率筛选，最后按 (df 升序, 词项) 取前 max_terms 个。

    Args:
        docs: 原始正文列表（每条一个分块）
        max_terms: 返回词项数上限

    Returns:
        词项列表（确定性顺序）
    """
    total = len(docs)
    if total == 0:
        return []
    upper = int(DF_RATIO * total)
    df: dict[str, int] = {}
    for doc in docs:
        candidates: set[str] = set()
        for run in CJK_RUN.findall(doc):
            for size in range(NGRAM_MIN, NGRAM_MAX + 1):
                for start in range(0, len(run) - size + 1):
                    candidates.add(run[start : start + size])
        for term in candidates:
            df[term] = df.get(term, 0) + 1
    kept = [term for term, count in df.items() if DF_MIN <= count <= upper]
    kept.sort(key=lambda t: (df[t], t))
    return kept[:max_terms]


def hit_rate(ranked_contents: list[str], term: str) -> bool:
    """判定某词项是否被召回（原始正文的字符串包含）。

    Args:
        ranked_contents: 检索返回结果的原始正文（按得分降序）
        term: 探针词项

    Returns:
        True = 至少一条含该词项的正文进了返回集
    """
    return any(term in content for content in ranked_contents)


async def _bm25_top_contents(
    docs: list[str], term: str, k: int, tokenizer_fn
) -> list[str]:
    """用 BM25Okapi 在给定 tokenization 上取 top-k 的原始正文。

    Args:
        docs: 语料（原始正文）
        term: 查询词项
        k: 返回条数
        tokenizer_fn: 分词函数（字符级用 list，词项级用 tokenize）

    Returns:
        命中文档的原始正文，按得分降序
    """
    corpus = [tokenizer_fn(doc) for doc in docs]
    query_tokens = tokenizer_fn(term)
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [docs[i] for i in ranked]


async def _sql_top_contents(
    session: AsyncSession, sql: str, params: dict, k: int
) -> list[str]:
    """执行一条候选 SQL 并返回命中文档的原始正文（kb_id 已在 params 里绑定）。"""
    result = await session.execute(text(sql), {**params, "k": k})
    return [row[0] for row in result.all()]


async def _term_kb(session: AsyncSession, term: str) -> str | None:
    """取包含该词项的、按 id 排序的第一个知识库（保持单库路径）。"""
    result = await session.execute(
        text(
            "SELECT kb_id FROM chunks WHERE content LIKE :pat ESCAPE '\\'"
            " ORDER BY kb_id LIMIT 1"
        ),
        {"pat": f"%{escape_like(term)}%"},
    )
    row = result.first()
    if row is None:
        return None
    return row[0]


async def _probe_group_tokenizer(session, corpus, k) -> dict[str, float]:
    """分组 A：分词配置（BM25Okapi 同时跑在字符级与词项级 = 固定打分算法）。"""
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    hits: dict[str, int] = {"char-bm25": 0, "jieba-bm25": 0, "jieba-tsrank": 0, "pg-trgm": 0}
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        docs = corpus["by_kb"][kb_id]
        if hit_rate(await _bm25_top_contents(docs, term, k, list), term):
            hits["char-bm25"] += 1
        if hit_rate(await _bm25_top_contents(docs, term, k, tokenize), term):
            hits["jieba-bm25"] += 1
        plan = build_lexical_query(term)
        rows = await _sql_top_contents(
            session,
            "SELECT content FROM chunks WHERE kb_id = :kb_id"
            " AND tsv @@ to_tsquery('simple', :tsq)"
            " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
            {"kb_id": kb_id, "tsq": plan.tsquery},
            k,
        )
        if hit_rate(rows, term):
            hits["jieba-tsrank"] += 1
        rows = await _sql_top_contents(
            session,
            "SELECT content FROM chunks WHERE kb_id = :kb_id"
            " ORDER BY similarity(content, :term) DESC LIMIT :k",
            {"kb_id": kb_id, "term": term},
            k,
        )
        if hit_rate(rows, term):
            hits["pg-trgm"] += 1
    return {name: count / len(terms) if terms else 0.0 for name, count in hits.items()}


async def _probe_group_construction(session, corpus, k) -> dict[str, float]:
    """分组 B：查询构造（固定分词 = jieba，固定打分 = ts_rank）。"""
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    variants = ("prefix-AND", "prefix-OR", "plainto-AND", "substring")
    hits: dict[str, int] = {name: 0 for name in variants}
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        plan = build_lexical_query(term)
        and_tsq = plan.tsquery
        or_tsq = " | ".join(plan.tsquery.split(" & "))
        table = {
            "prefix-AND": (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND tsv @@ to_tsquery('simple', :tsq)"
                " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
                {"kb_id": kb_id, "tsq": and_tsq},
            ),
            "prefix-OR": (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND tsv @@ to_tsquery('simple', :tsq)"
                " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
                {"kb_id": kb_id, "tsq": or_tsq},
            ),
            "plainto-AND": (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND tsv @@ plainto_tsquery('simple', :seg)"
                " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :seg)) DESC, id LIMIT :k",
                {"kb_id": kb_id, "seg": " ".join(plan.terms)},
            ),
            "substring": (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND content LIKE :pat ESCAPE '\\'"
                " ORDER BY doc_id, chunk_index LIMIT :k",
                {"kb_id": kb_id, "pat": f"%{escape_like(term)}%"},
            ),
        }
        for name, (sql, params) in table.items():
            rows = await _sql_top_contents(session, sql, params, k)
            if hit_rate(rows, term):
                hits[name] += 1
    return {name: count / len(terms) if terms else 0.0 for name, count in hits.items()}


async def _probe_group_scoring(session, corpus, k) -> dict[str, float]:
    """分组 C：打分算法（固定 jieba + 前缀 AND）。"""
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    hits: dict[str, int] = {"ts_rank": 0, "ts_rank_cd": 0}
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        plan = build_lexical_query(term)
        for name, fn in (("ts_rank", "ts_rank"), ("ts_rank_cd", "ts_rank_cd")):
            rows = await _sql_top_contents(
                session,
                f"SELECT content FROM chunks WHERE kb_id = :kb_id"
                f" AND tsv @@ to_tsquery('simple', :tsq)"
                f" ORDER BY {fn}(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
                {"kb_id": kb_id, "tsq": plan.tsquery},
                k,
            )
            if hit_rate(rows, term):
                hits[name] += 1
    return {name: count / len(terms) if terms else 0.0 for name, count in hits.items()}


async def _probe_explicit_cases(session, corpus, k) -> list[tuple[str, str, bool, str]]:
    """显式失效用例：① 全单字查询 ② 词形与文档词元不一致。"""
    rows: list[tuple[str, str, bool, str]] = []
    for case in EXPLICIT_CASES:
        kb_id = await _term_kb(session, case)
        if kb_id is None:
            continue
        plan = build_lexical_query(case)
        if plan.use_substring:
            sql = (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND content LIKE :pat ESCAPE '\\' ORDER BY doc_id, chunk_index LIMIT :k"
            )
            params = {"kb_id": kb_id, "pat": f"%{escape_like(case)}%"}
            mode = "substring"
        else:
            sql = (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND tsv @@ to_tsquery('simple', :tsq)"
                " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k"
            )
            params = {"kb_id": kb_id, "tsq": plan.tsquery}
            mode = "prefix-AND"
        contents = await _sql_top_contents(session, sql, params, k)
        rows.append((case, mode, hit_rate(contents, case), kb_id))
    return rows


def _render_report(
    total: int,
    kb_count: int,
    terms: list[str],
    tokenizer_group: dict[str, float],
    construction_group: dict[str, float],
    scoring_group: dict[str, float],
    explicit: list[tuple[str, str, bool, str]],
    k: int,
) -> str:
    """渲染 markdown 报告。"""
    lines = [
        "# P3 词项命中探针（jieba + PG 全文检索 横向比较）",
        "",
        f"- 语料：{total} 个分块 / {kb_count} 个知识库",
        f"- 检索条数 k：{k}（= TOP_K_RETRIEVAL，上限 {MAX_QUERY_K}）",
        f"- 探针词项数：{len(terms)}（n-gram {NGRAM_MIN}..{NGRAM_MAX}，df ∈ [{DF_MIN}, {DF_RATIO}×N]）",
        "",
        "> **仅供相对比较，不得作为质量基线或发布判据。**",
        f"> 176 分块、k={k} 时单词项的命中集合可能已占语料 17%–28%，多数词项会**平凡通过**；",
        "> 探针的用途是在同一语料上横向比配置。",
        "",
        "## 分组 A：分词配置（固定打分算法 = BM25Okapi，字符级 vs 词项级）",
        "",
        "| 配置 | 词项命中率 |",
        "|---|---|",
    ]
    for name, rate in tokenizer_group.items():
        lines.append(f"| {name} | {rate:.4f} |")
    lines += [
        "",
        "> 注：`jieba-tsrank` 的生产落地形态（存储侧依赖 `scripts/rewrite_content_seg.py` 的重写结果）；",
        "> `pg-trgm` 是本地唯一可对照的扩展方案（`similarity()` 排序）。",
        "",
        "## 分组 B：查询构造（固定分词 = jieba，固定打分 = ts_rank）",
        "",
        "| 构造方式 | 词项命中率 |",
        "|---|---|",
    ]
    for name, rate in construction_group.items():
        lines.append(f"| {name} | {rate:.4f} |")
    lines += [
        "",
        "## 分组 C：打分算法（固定 jieba + 前缀 AND）",
        "",
        "| 打分函数 | 词项命中率 |",
        "|---|---|",
    ]
    for name, rate in scoring_group.items():
        lines.append(f"| {name} | {rate:.4f} |")
    lines += [
        "",
        "## 显式失效用例（探针的 df 筛选测不到，必须单列）",
        "",
        "| 查询 | 走的路径 | 是否命中 | kb |",
        "|---|---|---|---|",
    ]
    for case, mode, hit, kb_id in explicit:
        lines.append(f"| {case} | {mode} | {'✅' if hit else '❌'} | {kb_id[:8]}… |")
    lines += [
        "",
        "## 词项清单（完整，供复现）",
        "",
        "```",
        " ".join(terms),
        "```",
        "",
    ]
    return "\n".join(lines)


async def _main(out: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT content, kb_id FROM chunks ORDER BY kb_id, doc_id, chunk_index")
        )
        rows = result.all()
        contents = [r[0] for r in rows]
        by_kb: dict[str, list[str]] = {}
        for content, kb_id in rows:
            by_kb.setdefault(kb_id, []).append(content)
        corpus = {"all_contents": contents, "by_kb": by_kb}
        terms = derive_terms(contents, MAX_TERMS)
        k = min(TOP_K_RETRIEVAL, MAX_QUERY_K)
        tokenizer_group = await _probe_group_tokenizer(session, corpus, k)
        construction_group = await _probe_group_construction(session, corpus, k)
        scoring_group = await _probe_group_scoring(session, corpus, k)
        explicit = await _probe_explicit_cases(session, corpus, k)
    report = _render_report(
        len(contents),
        len(by_kb),
        terms,
        tokenizer_group,
        construction_group,
        scoring_group,
        explicit,
        k,
    )
    Path(out).write_text(report, encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="词项命中探针")
    parser.add_argument("--out", required=True, help="报告输出路径")
    args = parser.parse_args()
    asyncio.run(run_and_dispose(_main(args.out)))
```

> `pg_trgm` 的 `similarity()` 需要在库里存在该扩展（本地实测 1.6 可用）。**若报 `function similarity(text, text) does not exist`**：先跑
> `docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"`，
> 并把该前置写进报告（这是**探针**的前置，不是生产依赖 —— 生产只用 `simple` 配置与 `to_tsquery`）。

- [ ] **Step 4: 跑纯函数测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_lexical_probe.py -v
```

预期：3 passed。

- [ ] **Step 5: 实跑探针**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/lexical_probe.py \
  --out docs/tmp/p3-lexical-probe-2026-09-19.md
```

把完整输出记进报告。**必查三项**：

1. 分组 A 里 `jieba-bm25` 与 `jieba-tsrank` 明显高于 `char-bm25`（否则本次替换没有收益，停下报告）；
2. 显式失效用例全部 ✅（「涨了吗」「5 月」走 substring 命中；「增长」「营业收入」靠前缀通配命中）——**任一 ❌ 即为任务未完成**，`retrieval-quality` 的「探针覆盖被切碎与全单字查询」scenario 要求它们不得静默 0 命中；
3. 分组 B 中 `prefix-AND` 与 `prefix-OR` 的差距 —— 决定 Ruling 2 是否翻转。

- [ ] **Step 6: 依实测固化选型（Ruling 2 / Ruling 3）**

看分组 B 与分组 C 的结果，按下面规则处置并把结论写进报告：

- **若 `prefix-OR` 的命中率高于 `prefix-AND` 超过 10 个百分点**：把 `src/infra/db/lexical_query.py` 的
  `_JOINER = " & "` 改为 `_JOINER = " | "`，并把 `tests/infra/db/test_lexical_query.py` 的
  `test_prefix_wildcard_per_token` 断言改为 `plan.tsquery == " | ".join(...)`（只有这一个构造点，改动面就是这两处）。
- **否则保持 `prefix-AND`**（Ruling 2 默认），把「实测差距」记进报告作为依据。
- **若 `ts_rank_cd` 的命中率高于 `ts_rank` 超过 10 个百分点**：把 `ChunkRepo.search_lexical` 里的
  `func.ts_rank(` 改为 `func.ts_rank_cd(`（**只改这一处**），并在报告里记录为该函数的依据。
- **否则保持 `ts_rank`**（Ruling 3 默认）。

无论是否翻转，都在报告末尾追加一节（**下面三项必须写实测结论与实测命中率，不得留空或写"待定"**）：

```markdown
## 选型结论

- 查询构造：prefix-AND（分组 B 命中率：prefix-AND = 0.xxxx / prefix-OR = 0.xxxx / plainto-AND = 0.xxxx / substring = 0.xxxx）
- 打分算法：ts_rank（分组 C 命中率：ts_rank = 0.xxxx / ts_rank_cd = 0.xxxx）
- 分词方案：jieba + `to_tsvector('simple')`（分组 A 命中率：char-bm25 = 0.xxxx / jieba-bm25 = 0.xxxx / jieba-tsrank = 0.xxxx / pg-trgm = 0.xxxx）
```

- [ ] **Step 7: 跑相关测试**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/ tests/scripts/ -v
```

预期：全过（若 Step 6 翻转了连接符，`test_lexical_query.py` 的断言已同步）。

- [ ] **Step 8: 提交**

```bash
ruff check scripts/lexical_probe.py tests/scripts/test_lexical_probe.py
git add scripts/lexical_probe.py tests/scripts/test_lexical_probe.py docs/tmp/p3-lexical-probe-2026-09-19.md
git status --short
git commit -m "feat(p3): 词项命中探针 —— 分词/查询构造/打分算法三组横向比较"
```

**若 Step 6 改了生产默认值**，把 `src/infra/db/lexical_query.py`（或 `chunk_repo.py`）与 `tests/infra/db/test_lexical_query.py` 一并加进这一条提交。

---

### Task 7: 融合函数迁移到 `src/rag/fusion.py`

**Files:**
- Create: `src/rag/fusion.py`
- Modify: `src/infra/search/bm25_index.py`（只留 `BM25Index` 类，删三个纯函数）
- Modify: `src/rag/retrieval.py:25,86`（import 与调用点）
- Modify: `src/config/settings.py`（新增 `RRF_K` / `RRF_TOP_N`）
- Test: `tests/rag/test_fusion.py`（新建，从 `tests/infra/search/test_bm25_index.py` 迁移融合用例）
- Test: `tests/infra/search/test_bm25_index.py`（删除融合相关用例）

**Interfaces:**
- Consumes: `ChunkResult`（P2）
- Produces:
  - `rrf_fusion(dense: list[ChunkResult], sparse: list[ChunkResult], k: int, top_n: int) -> list[ChunkResult]`（签名不变，去掉默认值由调用方传配置）
  - `rrf_fusion_multi(results_groups: list[list[ChunkResult]], k: int, top_n: int) -> list[ChunkResult]`
  - `settings.RRF_K: int`（默认 60）/ `settings.RRF_TOP_N: int`（默认 50）

- [ ] **Step 1: 写失败测试**

新建 `tests/rag/test_fusion.py`（把 `tests/infra/search/test_bm25_index.py` 里 `TestRRFFusion` 的用例搬过来并加两条）：

```python
"""RRF 融合：纯函数，无需数据库，两路等权。"""

from src.infra.db.vector_store.types import ChunkResult


def _r(chunk_id: str, *, dense_rank=None, sparse_rank=None) -> ChunkResult:
    return ChunkResult(
        id=chunk_id,
        content=f"内容 {chunk_id}",
        metadata={},
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )


def test_fusion_empty_inputs():
    from src.rag.fusion import rrf_fusion

    assert rrf_fusion([], [], k=60, top_n=50) == []


def test_fusion_dense_only_keeps_dense_rank():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [], k=60, top_n=10)
    assert [r.id for r in result] == ["a", "b"]
    assert [r.dense_rank for r in result] == [0, 1]
    assert [r.sparse_rank for r in result] == [None, None]


def test_fusion_sparse_only_keeps_sparse_rank():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([], [_r("a"), _r("b")], k=60, top_n=10)
    assert [r.sparse_rank for r in result] == [0, 1]
    assert [r.dense_rank for r in result] == [None, None]


def test_fusion_both_paths_preserve_both_ranks():
    """同一结果出现在两路时，两侧排名都要保留（来源可辨）。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [_r("b"), _r("a")], k=60, top_n=10)
    by_id = {r.id: r for r in result}
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].sparse_rank == 0


def test_fusion_two_paths_are_equal_weight():
    """两路等权：同一 id 在任一路排第 1 的得分贡献相同。"""
    from src.rag.fusion import rrf_fusion

    dense_first = rrf_fusion([_r("x")], [_r("y"), _r("z")], k=60, top_n=10)
    sparse_first = rrf_fusion([_r("y"), _r("z")], [_r("x")], k=60, top_n=10)
    assert {r.id for r in dense_first} == {r.id for r in sparse_first}


def test_fusion_respects_top_n():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r(f"d{i}") for i in range(10)], [], k=60, top_n=3)
    assert len(result) == 3


def test_fusion_multi_three_way():
    from src.rag.fusion import rrf_fusion_multi

    merged = rrf_fusion_multi(
        [[_r("a"), _r("b")], [_r("b"), _r("c")], [_r("c"), _r("a")]], k=60, top_n=5
    )
    assert {r.id for r in merged} == {"a", "b", "c"}


def test_fusion_has_no_database_dependency():
    """融合必须能仅凭两路结果列表验证（spec 的 scenario）。"""
    import inspect

    from src.rag import fusion

    source = inspect.getsource(fusion)
    for forbidden in ("sqlalchemy", "session_factory", "asyncpg", "asyncio"):
        assert forbidden not in source
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_fusion.py -v
```

预期：`ModuleNotFoundError: No module named 'src.rag.fusion'`。

- [ ] **Step 3: 新建 `src/rag/fusion.py`（函数体从 `bm25_index.py:122-213` 原样搬）**

```python
"""RRF（Reciprocal Rank Fusion）融合 —— 纯函数，无数据库、无 IO。

融合留在应用层：pgvector 本体不提供融合能力，ParadeDB 到 0.25.9 仍把
Native Hybrid Search 标为未发布；把 RRF 写进 SQL 等于把手写公式搬进字符串
并自担 tiebreaker 确定性，收益为零、代价明确（见 design.md D2）。

**两路等权**：同一个 `1/(k+rank+1)` 公式作用于两路，不引入权重参数 ——
加权重会改变融合输出，从而污染"存储替换不改变行为"的验证框架。
"""

from src.infra.db.vector_store.types import ChunkResult


def _merge_path_ranks(
    existing: ChunkResult | None,
    incoming: ChunkResult,
    *,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """把一路的名次合并进已有结果。

    位置名次兜底（生产者未填时用融合时的位置），生产者已填的值优先；
    同一 id 出现在另一路时，把那一侧的排名携带过来 —— 融合只按 RRF 重排，
    不得抹掉任一路的排名（来源可辨）。
    """
    if existing is None:
        if dense_rank is not None and incoming.dense_rank is None:
            incoming.dense_rank = dense_rank
        if sparse_rank is not None and incoming.sparse_rank is None:
            incoming.sparse_rank = sparse_rank
        return incoming
    if dense_rank is not None and existing.dense_rank is None:
        existing.dense_rank = dense_rank
    if sparse_rank is not None and existing.sparse_rank is None:
        existing.sparse_rank = sparse_rank
    if existing.dense_rank is None:
        existing.dense_rank = incoming.dense_rank
    if existing.sparse_rank is None:
        existing.sparse_rank = incoming.sparse_rank
    return existing


def rrf_fusion(
    dense: list[ChunkResult],
    sparse: list[ChunkResult],
    k: int,
    top_n: int,
) -> list[ChunkResult]:
    """RRF 融合 dense 语义检索与词法检索结果。

    融合只按 RRF 得分重排；每个结果的 dense_rank / sparse_rank 按路保留，
    某条结果未出现在某一路时该路排名为 None。

    Args:
        dense: dense（向量）路结果，已按余弦距离升序
        sparse: 词法路结果，已按词法得分降序
        k: RRF 平滑常数（settings.RRF_K），控制排名权重衰减速度
        top_n: 融合后保留条数（settings.RRF_TOP_N）

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}

    for rank, doc in enumerate(dense):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, dense_rank=rank)

    for rank, doc in enumerate(sparse):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, sparse_rank=rank)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [merged[doc_id] for doc_id, _ in ranked[:top_n]]


def rrf_fusion_multi(
    results_groups: list[list[ChunkResult]],
    k: int,
    top_n: int,
) -> list[ChunkResult]:
    """任意路 RRF 融合多组检索结果。

    每路按排名贡献 1/(k+rank+1)，跨路累加后按得分降序取 top_n。
    此函数不区分 dense / 词法，排名按融合时的位置兜底写入 dense_rank，
    sparse_rank 保持生产者已填的值（缺省为 None）。

    Args:
        results_groups: 多组检索结果（每组一个查询的 dense 或词法结果）
        k: RRF 平滑常数（settings.RRF_K）
        top_n: 融合后保留条数（settings.RRF_TOP_N）

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}
    for group in results_groups:
        for rank, doc in enumerate(group):
            scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
            merged[doc.id] = _merge_path_ranks(
                merged.get(doc.id), doc, dense_rank=rank
            )
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [merged[doc_id] for doc_id, _ in ranked[:top_n]]
```

> ⚠ `_merge_path_ranks` 会**原地修改** `ChunkResult`（P2 起 `ChunkResult` 不是 frozen）。同时 `inspect.getsource` 里出现 `asyncio` 的断言要求本模块**不 import asyncio** —— 上面已满足。

- [ ] **Step 4: 在 `src/config/settings.py` 增加融合参数**

把 Hybrid Search 段

```python
# BM25 索引持久化根目录（每个知识库独立子目录）
BM25_INDEX_DIR: str = os.getenv("BM25_INDEX_DIR", "data/bm25_index")
```

替换为

```python
# RRF 融合的平滑常数：控制排名权重衰减速度（沿用替换前的取值）
RRF_K: int = int(os.getenv("RRF_K", "60"))
# RRF 融合后保留条数：交给下游按 doc_id 去重与 rerank 截断
RRF_TOP_N: int = int(os.getenv("RRF_TOP_N", "50"))
```

> `BM25_INDEX_DIR` 的**删除**在 Task 9（此时 `app_service` / CLI 仍在 import 它）。

- [ ] **Step 5: 从 `bm25_index.py` 删掉三个纯函数，并切换 `retrieval.py` 的 import**

`src/infra/search/bm25_index.py`：删除 `_merge_path_ranks` / `rrf_fusion` / `rrf_fusion_multi`（原第 122–213 行）与随之不再需要的 `ChunkResult` import。

`src/rag/retrieval.py`：把

```python
from src.infra.search.bm25_index import BM25Index, rrf_fusion
```

改为

```python
from src.infra.search.bm25_index import BM25Index
from src.rag.fusion import rrf_fusion
```

并把

```python
        results = rrf_fusion(d or [], b or [])
```

改为

```python
        results = rrf_fusion(d or [], b or [], k=RRF_K, top_n=RRF_TOP_N)
```

同时在 `src/config` 的 import 列表里补 `RRF_K, RRF_TOP_N`（该文件顶部从 `src.config` import 的常量列表，按字母序插入）。

- [ ] **Step 6: 从旧测试文件删掉融合用例**

`tests/infra/search/test_bm25_index.py`：删除 `TestRRFFusion` 类与所有 `rrf_fusion` / `rrf_fusion_multi` 引用；文件头 docstring 里「`rrf_fusion`：RRF 融合算法的正确性」一行一并删除。
**不要删整个文件**（`BM25Index` 类仍在，Task 9 才删）。

- [ ] **Step 7: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/ tests/infra/search/ -v
```

预期：全过（`test_fusion.py` 8 条 + `test_retrieval.py` 既有用例 + `bm25_index` 剩余用例）。

- [ ] **Step 8: 提交**

```bash
ruff check src/rag/fusion.py src/rag/retrieval.py src/infra/search/bm25_index.py src/config/settings.py tests/rag/
git add src/rag/fusion.py src/rag/retrieval.py src/infra/search/bm25_index.py src/config/settings.py tests/rag/test_fusion.py tests/infra/search/test_bm25_index.py
git commit -m "refactor(p3): rrf_fusion 迁到 src/rag/fusion.py，融合参数进配置"
```

---

### Task 8: 检索链路切换（两路同源并发 + 贡献可见 + 签名级联）

**Files:**
- Modify: `src/rag/retrieval.py:65-108`（`search` 重写、去 `bm25` 形参）
- Modify: `src/agents/tools/rag_tools.py:31,63-69,74,136`（去 `bm25`）
- Modify: `src/agents/graph/workflow.py:22,41-51,79-85`（去 `bm25`）
- Modify: `src/services/agent_service.py:48,734-754,811-815`（去 `bm25`）
- Modify: `src/services/app_service.py:13,23,49-51,60-68`（去 BM25 组装）
- Modify: `src/core/log_event_specs.py`（`hybrid done` 增两个字段）
- Test: `tests/rag/test_retrieval.py`、`tests/agents/tools/test_rag_tools.py`、`tests/agents/graph/test_graph.py`、`tests/agents/graph/test_direct_skill_round.py`、`tests/agents/skills/test_delegate_task.py`、`tests/services/test_app_service.py`、`tests/services/test_agent_service.py`、`tests/api/conftest.py`

**Interfaces:**
- Consumes: `PgVectorStore.lexical_search`（Task 3）、`rrf_fusion`（Task 7）、`settings.RRF_K` / `RRF_TOP_N`（Task 7）
- Produces:
  - `async search(query: str, kb_id: str, vector_store: VectorStore) -> list[ChunkResult]`（**无 `bm25` 形参**）
  - `make_rag_tools(vector_store, reranker, prompt_manager, delegate_task=None) -> list[BaseTool]`
  - `build_graph(vector_store, llm, reranker, prompt_manager, tools=None, delegate_task=None, tool_sink=None, skill_direct_node=None)`
  - `AgentService(vector_store, chat_manager, llm=None, reranker=None, prompt_manager=None)`

- [ ] **Step 1: 写/改失败测试**

在 `tests/rag/test_retrieval.py` 末尾追加（并在文件内已有的 `HYBRID_SEARCH_ENABLED` patch 用例中把 `bm25=` 传参去掉）：

```python
async def test_hybrid_runs_both_paths_concurrently_on_same_store(monkeypatch):
    """两路并发、同源于一个 VectorStore，且各带各的排名与得分。"""
    from src.infra.db.vector_store.types import ChunkResult
    from src.rag import retrieval

    started: list[str] = []

    class _Store:
        async def dense_search(self, kb_id, query, k):
            started.append("dense")
            return [
                ChunkResult(id="a", content="A", metadata={}, distance=0.1, dense_rank=0),
                ChunkResult(id="b", content="B", metadata={}, distance=0.2, dense_rank=1),
            ]

        async def lexical_search(self, kb_id, query, k):
            started.append("sparse")
            return [
                ChunkResult(id="b", content="B", metadata={}, lexical_score=0.9, sparse_rank=0),
            ]

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    results = await retrieval.search("资产负债率", "kb1", _Store())

    assert set(started) == {"dense", "sparse"}
    by_id = {r.id: r for r in results}
    assert by_id["b"].dense_rank == 1
    assert by_id["b"].sparse_rank == 0
    assert by_id["a"].sparse_rank is None


async def test_hybrid_logs_both_path_contributions(monkeypatch, capsys):
    """两路贡献必须都进日志：任一路为 0 时可被直接看出。"""
    from src.infra.db.vector_store.types import ChunkResult
    from src.rag import retrieval

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", True)
    logged: dict = {}
    monkeypatch.setattr(
        retrieval,
        "log_event",
        lambda event, **fields: logged.update({"event": event, **fields}),
    )
    await retrieval.search("资产负债率", "kb1", _Store())

    assert logged["dense_count"] == 1
    assert logged["sparse_count"] == 0


async def test_dense_only_when_hybrid_disabled(monkeypatch):
    """混合关闭时只走 dense，不调词法路。"""
    from src.infra.db.vector_store.types import ChunkResult
    from src.rag import retrieval

    called: list[str] = []

    class _Store:
        async def dense_search(self, kb_id, query, k):
            return [ChunkResult(id="a", content="A", metadata={}, distance=0.1)]

        async def lexical_search(self, kb_id, query, k):
            called.append("sparse")
            return []

    monkeypatch.setattr(retrieval, "HYBRID_SEARCH_ENABLED", False)
    results = await retrieval.search("资产负债率", "kb1", _Store())

    assert called == []
    assert [r.id for r in results] == ["a"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval.py -v
```

预期：新增三条 FAIL（`search()` 当前不接受 3 个参数 / 无 `lexical_search` 调用）。

- [ ] **Step 3: 重写 `retrieval.search`**

把 `src/rag/retrieval.py` 的 `async def search(...)` 整段（原第 65–108 行）替换为：

```python
async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
) -> list[ChunkResult]:
    """执行检索：dense + 词法两路同源并发取数，融合与去重在应用层。

    两路都经同一个 VectorStore（背后是同一个 PostgreSQL 实例的 chunks 表）——
    「某一支路半死而整体正常」的结构性原因由此消失。

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID（调用方保证非空：`rag_tools.py` 在 kb_id 为空时直接返回空结果）
        vector_store: 向量存储实例（dense 与词法两路的共同入口）

    Returns:
        检索结果列表，按相关性降序排列；混合模式为 RRF 融合结果
    """
    if HYBRID_SEARCH_ENABLED:
        dense_coro = vector_store.dense_search(kb_id, query, TOP_K_RETRIEVAL)
        lexical_coro = vector_store.lexical_search(kb_id, query, TOP_K_RETRIEVAL)
        dense, sparse = await asyncio.gather(dense_coro, lexical_coro)
        dense_results = dense or []
        sparse_results = sparse or []
        results = rrf_fusion(dense_results, sparse_results, k=RRF_K, top_n=RRF_TOP_N)
        # 两路各自的贡献必须可见：任一路为 0 时该字段就是 0。
        # 只记融合后的总数会让"某一路长期失效"不可发现（trace_c54ce259 的教训）。
        log_event(
            Event.HYBRID_DONE,
            kb_id=kb_id,
            query_len=len(query),
            dense_count=len(dense_results),
            sparse_count=len(sparse_results),
            result_count=len(results),
        )
        results = _dedup_by_doc_id(results)
        return results

    results = await vector_store.dense_search(kb_id, query, k=TOP_K_RETRIEVAL)
    if results:
        result_count = len(results)
    else:
        result_count = 0
    log_event(
        Event.SEARCH_DONE,
        kb_id=kb_id,
        query_len=len(query),
        result_count=result_count,
    )
    results = _dedup_by_doc_id(results or [])
    return results
```

同时把 `src/config` 的 import 列表补上 `RRF_K, RRF_TOP_N`（Task 7 已在 `settings.py` 定义）。

> 注意删掉了原条件里的 `bm25` 与 `kb_id` 判断：`bm25` 组件已退役；`kb_id` 非空由调用方硬保证（`rag_tools.py:135-139` 在 kb_id 为空时直接返回 `[]`，该分支在 P2 已确认为不可达）。

- [ ] **Step 4: `rag_tools` 去 `bm25`**

`src/agents/tools/rag_tools.py`：

- 删 `from src.infra.search.bm25_index import BM25Index`（第 31 行）。
- `make_rag_tools` 签名与 docstring 改为

```python
def make_rag_tools(
    vector_store: VectorStore,
    reranker,
    prompt_manager,
    delegate_task: BaseTool | None = None,
) -> list[BaseTool]:
    """构建工具列表：注册表管理；retrieve_kb 始终注册（KB=RAG 开关在工具内实现）。

    Args:
        vector_store: 向量存储实例（闭包注入；dense 与词法两路同源于它）
        reranker: Reranker 模型实例（闭包注入，rerank_results 使用）
        prompt_manager: 提示词管理器（闭包注入，当前工具未直接使用，保留签名）
        delegate_task: 可选 delegate_task 工具（skill 库有内容时由调用方注入并注册；
            无 skill 时传 None 不注册，主 agent 工具集保持固定三件套）

    Returns:
        工具列表：retrieve_kb（知识库检索）、ask_user（澄清追问）；开启 web 兜底时追加
        search_web；delegate_task 非空时追加 delegate_task；经 ToolRegistry.enabled_tools() 过滤启用项
    """
```

- 调用点（第 136 行）改为

```python
            results = await retrieval.search(query, kb_id, vector_store)
```

- 文件头 docstring（第 3 行）里的 `（vector_store/bm25/reranker）` 改为 `（vector_store/reranker）`。

- [ ] **Step 5: `workflow.build_graph` 去 `bm25`**

`src/agents/graph/workflow.py`：

- 删 `from src.infra.search.bm25_index import BM25Index`（第 22 行）。
- 签名改为

```python
def build_graph(
    vector_store: VectorStore,
    llm,
    reranker,
    prompt_manager,
    tools=None,
    delegate_task: BaseTool | None = None,
    tool_sink: list | None = None,
    skill_direct_node=None,
) -> CompiledStateGraph:
```

- 调用点（第 79–85 行）改为

```python
        base_tools = make_rag_tools(
            vector_store,
            reranker,
            prompt_manager,
            delegate_task=delegate_task,
        )
```

- [ ] **Step 6: `agent_service` 与 `app_service` 去 `bm25`**

`src/services/agent_service.py`：

- 删 `from src.infra.search.bm25_index import BM25Index`（第 48 行）。
- 构造函数签名与赋值改为

```python
    def __init__(
        self,
        vector_store: VectorStore,
        chat_manager: ChatManager,
        llm=None,
        reranker=None,
        prompt_manager: PromptManager | None = None,
    ):
```

```python
        self._vector_store = vector_store
        self._llm = llm or get_llm()
```

- `build_graph` 调用点（第 811–815 行）去掉 `bm25,` 实参。

`src/services/app_service.py`：

- import 段（第 13 行）改为 `from src.config import HYBRID_SEARCH_ENABLED`。
- 删 `from src.infra.search.bm25_index import BM25Index`（第 23 行）。
- 删

```python
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )
```

- `AgentService(...)` 调用去掉 `bm25=self.bm25,`。
- `DocumentService(...)` 调用改为 `DocumentService(self._doc_repo, self.vector_store, self.router)`（其 `bm25` 形参有默认值 `None`，Task 9 才删形参）。

  ⚠ **这会让 BM25 索引不再随入库更新** —— 这是**有意的**：本任务之后检索已不再读 BM25（Step 3 已切换），陈旧索引文件随 F-19 在 P4 删除。

- `delete_knowledge_base` 里删掉 BM25 清理段（原第 106–110 行）：

```python
        if self.bm25 is not None:
            try:
                await asyncio.to_thread(self.bm25.delete_index, kb_id)
            except Exception:  # noqa: BLE001
                logger.warning("BM25 index delete failed for kb={}", kb_id)
```

  该方法 docstring 保持原样（分块删除与软删之间尚无事务，那是 P4）。若 `asyncio` 在 `app_service.py` 里已无其他用途，一并删掉 `import asyncio`（用 `ruff check` 的 F401 判定）。

- [ ] **Step 7: 日志事件补两个字段**

`src/core/log_event_specs.py` 的

```python
    "hybrid done": EventSpec(
        "hybrid done", "retrieval", "info", ("kb_id", "query_len", "result_count")
    ),
```

改为

```python
    "hybrid done": EventSpec(
        "hybrid done",
        "retrieval",
        "info",
        ("kb_id", "query_len", "dense_count", "sparse_count", "result_count"),
    ),
```

> `fields` 不参与运行时校验（`log_events._validate_registry` 只校验 name / prefix / level），但它是 review 与文档对照的依据，必须同步。

- [ ] **Step 8: 同步全部调用点的测试**

按 Task 0 Step 5 的清单逐处改（**位置参数会静默错位，必须逐个看**）：

- `tests/agents/tools/test_rag_tools.py`：6 处 `fake_search(query, kb_id, vector_store, bm25)` → `fake_search(query, kb_id, vector_store)`；6 处 `bm25=None,` 实参删除。
- `tests/agents/skills/test_delegate_task.py:372,383`：`make_rag_tools(MagicMock(), None, MagicMock(), MagicMock())` → 去掉第 2 个 `None`。
- `tests/agents/graph/test_graph.py:22,277`：`build_graph(...)` 去掉 `None,  # bm25` 那一行（第 279 行）；`test_graph.py:741` 的 `fake_make_rag_tools(vector_store, bm25, reranker, prompt_manager, **kwargs)` → 去掉 `bm25` 形参（**须与 Step 4 的新签名一致**）；`test_graph.py:746` 的 monkeypatch 调用同步。
- `tests/agents/graph/test_direct_skill_round.py:84-86`：`build_graph(...)` 去掉 `bm25=None,`。
- `tests/services/test_app_service.py`、`tests/services/test_agent_service.py`、`tests/api/conftest.py`：`AgentService(...)` / `AppService(...)` 的 `bm25=` 实参删除；若有对 `app.bm25` 的断言，改为 `assert not hasattr(app, "bm25")`。

```bash
grep -rn "bm25\|BM25" tests/ --include=*.py | grep -v "__pycache__" | grep -v "test_no_bm25_leftovers"
```

把剩余命中逐条判断：属于 Task 9 范围的（`test_bm25_index.py`、`reset_data.py`）留着，其余在本步清掉。

- [ ] **Step 9: 跑测试**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/ tests/agents/ tests/services/ -v
```

预期：全过。

- [ ] **Step 10: 提交**

```bash
ruff check src/ tests/
git add src/rag/retrieval.py src/agents/tools/rag_tools.py src/agents/graph/workflow.py \
  src/services/agent_service.py src/services/app_service.py src/core/log_event_specs.py \
  tests/rag/test_retrieval.py tests/agents tests/services tests/api/conftest.py
git commit -m "feat(p3): 检索链路切到两路同源并发，两路贡献进日志"
```

---

### Task 9: 装配收尾与 `bm25_index.py` 删除

**Files:**
- Modify: `src/services/document_service.py`（去 `bm25` 形参与 `_rebuild_kb_index` 及两个调用点）
- Modify: `src/cli/check_abstain.py`、`src/cli/eval_ragas.py`、`src/cli/replay_trace.py`（去 BM25 装配）
- Delete: `src/cli/rebuild_bm25.py`
- Delete: `src/infra/search/bm25_index.py`
- Delete: `tests/infra/search/test_bm25_index.py`
- Modify: `src/config/settings.py`（删 `BM25_INDEX_DIR`）
- Modify: `tests/reset_data.py`（删 BM25 段）
- Test: `tests/config/test_no_bm25_leftovers.py`（新建，D8 守卫）

**Interfaces:**
- Consumes: Task 8 已把全部检索调用点切走
- Produces: `src/` 内无 `BM25Index` / `bm25_index` / `rank_bm25` / `BM25_INDEX_DIR`；`DocumentService(doc_repo, vector_store, router)`

- [ ] **Step 1: 写守卫测试**

新建 `tests/config/test_no_bm25_leftovers.py`：

```python
"""退役进程内词法索引（BM25）的残留检查。

`architecture-tidy` 的「无独立词法索引组件」要求：无进程内索引对象、无索引文件
路径配置。本文件是那条要求的守卫 —— 只扫代码与配置，不扫 docs（变更文档里必然
会引用 BM25 这个名字）。
"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests", "scripts", "alembic", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")

# 允许的例外：本文件自身（持有用于比对的正则）、探针脚本（分组 A 的基线对照）
ALLOWED = {
    "tests/config/test_no_bm25_leftovers.py",
    "scripts/lexical_probe.py",
}
PATTERNS = (
    re.compile(r"from\s+src\.infra\.search\.bm25_index"),
    re.compile(r"\bBM25Index\b"),
    re.compile(r"\bBM25_INDEX_DIR\b"),
)


def test_bm25_index_module_is_gone():
    """模块文件与模块导入都必须消失。"""
    assert not (REPO / "src" / "infra" / "search" / "bm25_index.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.search.bm25_index")


def test_rebuild_bm25_cli_is_gone():
    assert not (REPO / "src" / "cli" / "rebuild_bm25.py").exists()


def test_no_bm25_leftovers_in_code():
    """源码与配置里不得再出现 BM25 组件名或索引目录配置。"""
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
    assert offenders == [], f"仍有 BM25 残留：{offenders}"


def test_settings_no_longer_exports_bm25_index_dir():
    settings = importlib.import_module("src.config.settings")
    assert not hasattr(settings, "BM25_INDEX_DIR")
```

> `scripts/lexical_probe.py` 在例外表里：它用 `rank_bm25.BM25Okapi` 做**探针的基线对照**（分组 A 要求"固定打分算法、只换 tokenization"），不是生产依赖。该例外已在 F-28 中登记。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_no_bm25_leftovers.py -v
```

预期：4 条全部 FAIL。

- [ ] **Step 3: `DocumentService` 去 BM25**

`src/services/document_service.py`：

- 删 `from src.infra.search.bm25_index import BM25Index`（第 21 行）。
- 构造函数改为

```python
    def __init__(
        self,
        doc_repo: DocumentRepo,
        vector_store: VectorStore,
        router: DocRouter,
    ) -> None:
        self._doc_repo = doc_repo
        self.vector_store = vector_store
        self.router = router
```

- 删 `_rebuild_kb_index` 整个方法（第 131–147 行）与它的两个调用点：`delete_document` 第 127 行、处理流水线第 567–568 行的注释与调用。

  **注意**：`delete_document` 删掉重建索引后方法体只剩「校验 → 删分块 → 软删文档」，**保持原顺序不改**（入库/删除事务化是 P4）。

- 流水线里第 567 行的注释「BM25 词法索引随分块入库后全量重建，保持两路检索一致」一并删除（`tsv` 是生成列，写入即生效，不需要重建步骤）。

- [ ] **Step 4: 三个 CLI 去 BM25**

`src/cli/check_abstain.py`（第 173、195–196 行）：import 改为 `from src.config import HYBRID_SEARCH_ENABLED`（若该文件已无其他用途则整行删除）；删 `bm25 = BM25Index(...)` 与 `BM25Index` 的局部 import（第 178 行）；`build_graph(vector_store, llm, reranker, prompt_manager)`。

`src/cli/eval_ragas.py`（第 502、506、532–533 行）：同上改法。

`src/cli/replay_trace.py`（第 20、109、112、133、135 行）：

- import 段改为 `from src.config import HYBRID_SEARCH_ENABLED, TOP_K_RERANK, settings`（去掉 `BM25_INDEX_DIR`）。
- `_replay_all(rows, vector_store, bm25, reranker)` → `_replay_all(rows, vector_store, reranker)`；函数内 `search(fields["query"], fields["kb_id"], vector_store, bm25)` → `search(fields["query"], fields["kb_id"], vector_store)`。
- 删 `bm25 = BM25Index(...)` 与其在 `_replay_all` 调用里的实参。

  ⚠ `replay_trace.py:92` 的 `current_hybrid = settings.HYBRID_SEARCH_ENABLED` 与 drift 对照逻辑**保留**（混合开关仍在）。

```bash
git rm src/cli/rebuild_bm25.py
grep -rn "rebuild_bm25" src/ tests/ docs/agents/ --include=*.py --include=*.md | grep -v __pycache__
```

若有文档登记（`code-map.md` / `cookbook.md` / `cli/README.md`），记进 Task 10 一并改，**不要在本步改文档**。

- [ ] **Step 5: 清理随之失效的注释与陈旧措辞（注释写当前状态）**

BM25 重建路径消失后，下面三处描述已变成假陈述，按代码现状改：

1. `src/infra/db/vector_store/pg_store.py` 的 `get_all_chunks` docstring：

```python
        """取整个知识库的全部分块（空库检查 / 全量读取用）。"""
```

2. `src/chunking/validator.py` 的 `ChunkData.chunk_id` 行内注释（第 17–19 行）后半句：

```python
    # 来源标识：解析阶段按 "{source}:{index}" 或 "{source}:p{page}:{index}" 生成，
    # 入库写入侧不填、默认空串
    chunk_id: str = ""
```

3. `tests/infra/db/test_pg_vector_store_read.py:121` 里 `test_get_all_chunks_returns_sorted_rows` 的 docstring，把「该方法是 BM25 全量重建的数据源」改为「该方法的调用方是评测脚本的空库检查」。

`src/config/settings.py` 的 `HYBRID_SEARCH_ENABLED` 注释（「是否启用 BM25 + Dense 混合检索（通过 RRF 融合）」）改为：

```python
# 是否启用 dense + 词法两路混合检索（RRF 融合，两路同源于 PostgreSQL）
```

- [ ] **Step 6: 删除 `bm25_index.py` 与旧测试，清理配置**

```bash
git rm src/infra/search/bm25_index.py tests/infra/search/test_bm25_index.py
grep -n "BM25_INDEX_DIR" src/config/settings.py
```

第二条预期零命中（Task 7 已替换为 `RRF_K` / `RRF_TOP_N`）；若仍有残留行，删除它。

- [ ] **Step 7: `tests/reset_data.py` 去 BM25**

- import 段（第 21 行）改为 `from src.config import REDIS_URL`。
- 删 `reset_bm25_index()` 函数（第 52–64 行）。
- `reset_all` 删掉第 102–103 行的 BM25 段；docstring 第 88 行改为「一键重置全部数据存储（PostgreSQL + Redis）」。
- `__main__` 块删掉第 161–169 行的 BM25 段。
- 文件头 docstring 第 1 行改为「一键清除 PostgreSQL、Redis 的全部数据。」

  ⚠ **不要删 `data/bm25_index` 目录本身**（F11：它是词法路的回滚依据，随 F-19 在 P4 清理）。

```bash
grep -rn "reset_bm25_index" src/ tests/ --include=*.py
```

预期：零命中。

- [ ] **Step 8: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/ tests/cli/ tests/services/ tests/rag/ -v
```

预期：全过（`test_no_bm25_leftovers.py` 4 条转绿）。

- [ ] **Step 9: 全量测试并收口**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
ruff check .
pyright src/ 2>&1 | tail -5
```

预期：全过；`ruff` 无错；`pyright` 不新增 error。
**若全量测试出现与 bm25 无关的失败**：先判断是不是 Task 8/9 的签名级联漏改（用 `grep -rn "bm25" tests/ --include=*.py | grep -v __pycache__` 收口）。
**若失败在 `tests/cli/test_doc_consistency.py`**：说明 `docs/agents/*.md` 里还有指向已删 `src/...` 路径的引用（path 锚点是 **error 档**，会被断言）—— 按报错行把该引用改掉（本阶段被删的路径只有 `src/infra/search/bm25_index.py` 与 `src/cli/rebuild_bm25.py`；`grep -rn "bm25_index\|rebuild_bm25" docs/agents/` 核对）。符号锚点是 warn 档、不影响退出码，留到 Task 10 统一改。

- [ ] **Step 10: 提交**

```bash
git add -A src/ tests/
git commit -m "refactor(p3): 删除进程内 BM25 索引组件与装配链"
```

---

### Task 10: 文档同步

**Files:**
- Modify: `docs/agents/code-map.md`、`api_contract.md`、`data-flow.md`、`glossary.md`、`defensive-patterns.md`、`cookbook.md`
- Modify: `docs/agents/requirements_pool.md`
- Modify: `docs/openspec/changes/postgres-storage-consolidation/tasks.md`
- Test: `tests/cli/test_doc_consistency.py`（既有门禁）

**Interfaces:**
- Consumes: Task 1–9 的最终代码形态
- Produces: `docs/agents/` 与代码一致；P3 的发现与遗留已登记

- [ ] **Step 1: `code-map.md`**

按代码现状改三处：

1. `chunks` 表那段：补一句「`content_seg` 存 jieba 词项（空格连接，见 `src/infra/search/tokenizer.py`）；分词器变更必须跑 `scripts/rewrite_content_seg.py --apply`」。
2. 向量存储段：`pg_store.py` 的职责补上 `lexical_search`（词法取数，`ts_rank` 降序），并注明 `MAX_QUERY_K` 定义在 `src/config/const.py`。
3. `src/infra/search/` 的组成里删掉 `bm25_index.py`，补上 `tokenizer.py`（唯一的 jieba 分词入口）；若登记过 `cli/rebuild_bm25.py` 一并删。

- [ ] **Step 2: `api_contract.md`**

在 §4.4 之后插入新的一节，并把其后的 `4.5`…`4.11` 顺延为 `4.6`…`4.12`（标题编号是本文档自有，无外部引用）：

```markdown
### 4.5 `async VectorStore.lexical_search(kb_id, query, k=5) → list[ChunkResult]`

词法路取 top-k，按 `ts_rank` 降序。与 `dense_search` 对称：

| 字段 | 值 |
|---|---|
| `distance` | `None`（词法路无距离） |
| `lexical_score` | 词法得分（`ts_rank`；子串兜底时为 `0.0`） |
| `sparse_rank` | 该结果在词法路的排名（0 起） |
| `dense_rank` | `None` |

**查询条件在应用层构造**（`src/infra/db/lexical_query.py`）：每个词元先按安全字符集（中日韩字符 / 字母 / 数字 / 下划线）剔除，再拼成 `词元:*` 并以 ` & ` 连接；词元全被滤掉时降级为 `content LIKE '%原文%'`（LIKE 通配符已转义）。**用户原文不得直接交给 `to_tsquery`** —— 含空格会抛语法错误。
```

同时把 §4.4 的「已知限制」脚注改为：

```markdown
**已知限制：** `k` 最大 100（`src/config/const.py` 的 `MAX_QUERY_K`；`pg_store` 为导入方）。`lexical_search` 同样按其截断。
```

并把 §4.8（`get_all_chunks`，约第 847 行）的说明

```
取整个知识库的全部分块，供 BM25 索引全量重建。
```

改为

```
取整个知识库的全部分块；调用方是评测脚本（`src/cli/eval_ragas.py`）的空库检查。
```

- [ ] **Step 3: `data-flow.md`**

把第 145 行的

```
  │   词法路：BM25（现状；P3 换 PostgreSQL 全文检索，读 chunks.tsv）
```

改为

```
  │   词法路：PostgreSQL 全文检索（chunks.tsv @@ to_tsquery('simple', 词元:* & …)，
  │            按 ts_rank 降序；词元全被滤掉时降级为 content 子串匹配）
```

并在该段附近补一句：「两路同源于同一个 PostgreSQL 实例的 `chunks` 表；融合（RRF）在应用层 `src/rag/fusion.py`，不下推数据库」。

- [ ] **Step 4: `glossary.md`**

新增三个词条（与既有词条格式一致）：

```markdown
### 词法检索（lexical retrieval）

与 dense 路并列的第二路取数，由 PostgreSQL 全文检索承担：`chunks.tsv`（`content_seg` 的生成列）
用 `@@ to_tsquery('simple', …)` 匹配，按 `ts_rank` 降序取 top-k。
**不叫 BM25** —— `ts_rank` 是 cover-density 排名，不是 BM25（沿用 `bm25_score` 的命名会误导）。
本地与托管的 PostgreSQL 都能用 `simple` 配置，不依赖任何中文分词扩展。

### 分词口径（tokenization contract）

写入侧（`content_seg`）与查询侧（tsquery 词元）必须调用同一个 `tokenize()`
（`src/infra/search/tokenizer.py`），并过滤长度 < 2 的词项。两侧不一致**不会报错**，
只会静默降召回；分词结果随 `tsv` 生成列固化落库，因此 **jieba 版本或词典变更必须触发
存量全量重写**（`scripts/rewrite_content_seg.py --apply`，`--check` 是那条不变量的检查）。

### RRF 融合（Reciprocal Rank Fusion）

把两路已排序结果按 `1/(k+rank+1)` 累加后重排，融合在**应用层**（`src/rag/fusion.py`），
参数 `RRF_K` / `RRF_TOP_N` 来自配置，**两路等权**（不引入权重）。融合只重排，
不得抹掉任一结果的 `dense_rank` / `sparse_rank` —— 那正是"某一路其实没有贡献"的观测手段。
```

- [ ] **Step 5: `defensive-patterns.md`**

在检索相关小节后新增：

```markdown
### 分词口径漂移（写入与查询两侧不一致）

**形态**：写入侧（`chunks.content_seg`）与查询侧（tsquery 词元）用了不同的分词器、
不同的词典，或同一分词器的不同版本。**不报错，只静默降召回**；最隐蔽的一种是
**版本漂移** —— 分词结果随 `tsv` 生成列固化落库，升级 jieba 后存量与新的查询侧不一致，
而"同一进程内两个函数比较"的守卫测试抓不到它。

**防复发**：
1. 两侧只调 `tokenizer.tokenize()` 一个函数（守卫测试断言两侧词元逐字相等）；
2. `jieba` pin 精确版本；
3. 变更分词器配置/版本后**必须**跑 `scripts/rewrite_content_seg.py --apply`，
   并用 `--check`（退出码非 0 即存量已过期）作为验收动作；
4. 用户原文不得直接进 `to_tsquery` —— 含空格会抛错、`a:` 会被静默吞字符；
   查询串一律经 `lexical_query.build_lexical_query` 构造；
5. 词元全被滤掉时兜底走**正文子串**（不是"回退为不过滤"——写入侧没有单字 lexeme）。
```

- [ ] **Step 6: `cookbook.md`**

按既有条目格式追加一条操作记录：

```markdown
### 词法检索的分词器变更（jieba 升级 / 词典调整）

**何时用**：升级 `jieba` 版本、调整 `tokenizer.py` 的分词配置或过滤规则之后。

**步骤**：
1. 改 `pyproject.toml`（pin 到新版本）并重建镜像：`docker compose build --no-cache app`；
2. 宿主侧跑检查：`POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check`
   —— 退出码 1 且打印 `stale=N` 表示存量已过期；
3. 重写：`POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply`；
4. 复验：`--check` 退出码 0；再跑一次 `--apply` 应为 `rewritten=0`（幂等）；
5. 重启应用：`docker compose restart app`。

**为什么不能省**：`content_seg` 是 `tsv` 生成列的输入，落库即固化；不重写会静默降召回，
没有任何日志或异常提示。
```

- [ ] **Step 7: `requirements_pool.md` 登记 P3 遗留**

在 F-27 之后追加（格式与既有条目一致）：

```markdown
| F-28 | **（P3 遗留）`rank_bm25` 依赖未删**：P3 删除了 `bm25_index.py` 与全部装配链，但 `pyproject.toml` 的 `rank_bm25` 仍在（`scripts/lexical_probe.py` 的分组 A 仍用 `BM25Okapi` 做基线对照 —— 那是探针需要，不是生产依赖）。同批还有 `data/bm25_index` 目录（词法路的回滚依据，P3 刻意保留） | 随 F-19 的 P4 依赖与卷清理一并删；删依赖需要 `docker compose build --no-cache app` | postgres-storage-consolidation P3 遗留项 | P3 | 低 | F-19 |
| F-29 | **（P3 遗留，spec 陈旧）`multi-query-retrieval` 的 requirement 正文引用已不存在的组件**：正文写「`retrieve_node` 并行遍历 `rewritten_queries` 列表逐条执行 dense + 词法两路检索」，但 harness 改造后检索是 `retrieve_kb` 工具 + 单条 query，`grep -rn "rewritten_queries\|retrieve_node" src/` **零命中**；`rrf_fusion_multi` 也随之全仓无调用方（仅测试）。该 change 的 delta 会把这句写进在效规格 | 由后续变更重写该 requirement（把"多查询合并检索"重述为与 `retrieve_kb` 工具链一致的形态），或与 `retrieval-fetch-and-dedup` 的重定基一并处理。P3 只做了命名同步（`dense + BM25 混合检索` → `dense + 词法两路检索`），**未改**结构性陈述 | 2026-09-19 P3 执行期发现 | P2 | 中 | retrieval-fetch-and-dedup |
```

- [ ] **Step 8: `tasks.md` 更新**

- 阶段表：P3 那一行状态改为「**已完成**（收口 `<Task 9 的 commit>`）」；P2 那一行的收口 hash 校正为 `43ab19a`（现写 `dc8f1db`，那是 P2 中途的提交）。
- §2 追加三行（`<...>` 必须替换为**实测值**，不得留占位符）：

```markdown
| 2026-09-19 | P3 Task 6（词项命中探针） | 探针实测：查询构造 `<prefix-AND/prefix-OR>` 与打分 `<ts_rank/ts_rank_cd>` 的命中率差距为 `<实测值>` | 依实测固化生产默认（Ruling 2/3 的确认或翻转）；报告落 `docs/tmp/p3-lexical-probe-2026-09-19.md` | <是/否> |
| 2026-09-19 | P3 Task 5（存量重写） | `content_seg` 的 P2 占位存量共 **176** 行全部被判定过期；重写后 `--check` 通过且幂等（`rewritten=0`） | 重写脚本入库保留（`--check` 是「分词器变更触发存量重写」不变量的可执行检查） | 否（P2 Ruling 5 已预告） |
| 2026-09-19 | P3 Task 10（capability 核查） | `multi-query-retrieval` 的 delta 正文引用的 `retrieve_node` / `rewritten_queries` 在代码中已不存在（harness 改造后检索是工具），`rrf_fusion_multi` 无调用方 | 本阶段只做命名同步；结构性陈旧登记为需求池 F-29，不在 P3 修 | 否（既有陈旧陈述） |
```

- [ ] **Step 9: 跑文档一致性门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_doc_consistency.py -v
```

预期：通过。若失败，按报错指出的文件与规则修（该测试扫 `docs/agents/*.md`）。

> 锚点分三档：**path / route 是 error 档**（被断言，必须指向存在的代码），**symbol 是 warn 档**（不影响退出码）。本步新增的 `src/...` 路径引用（`src/infra/search/tokenizer.py`、`src/infra/db/lexical_query.py`、`src/rag/fusion.py`）都已在 Task 1/2/7 建出，因此 path 锚点应当全过；若报 symbol warn，用 `python -m src.cli.check_docs --verbose` 看清单，确认不是拼写错误即可。

- [ ] **Step 10: 提交**

```bash
git add docs/
git commit -m "docs(p3): 词法检索链路/契约/术语/防复发/操作协议同步 + P3 遗留登记"
```

---

### Task 11: DoD 验收

**Files:**
- Create: `docs/tmp/p3-acceptance-2026-09-19.md`
- Modify: `docs/openspec/changes/postgres-storage-consolidation/tasks.md`
- 无代码改动（若发现问题则回到对应 Task 修）

**Interfaces:**
- Consumes: Task 0–10 的全部产物
- Produces: P3 的 DoD 逐条结论、E2E 观测记录、残留清单

- [ ] **Step 1: 门禁三连**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
ruff check .
pyright src/ 2>&1 | tail -5
```

预期：全过；`ruff` 无错；`pyright` 不新增 error（存量第三方库误报除外，以"不新增"为准）。三条输出记进报告。

- [ ] **Step 2: 重写脚本的可执行检查（DoD D4）**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check; echo "exit=$?"
POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
```

预期：`stale=0`、`exit=0`；`rewritten=0`（幂等）。记进报告。

- [ ] **Step 3: 真实 E2E 冒烟（DoD D9，不可省）**

```bash
docker compose restart app
docker compose logs --tail=30 app
```

按 `cookbook.md` 的手工调试凭据（`.env` 的 `TEST_ACCOUNT` / `TEST_PASSWORD`）登录，端点**全为 POST**：

```bash
BASE=http://localhost:8000
TOKEN=$(curl -s -X POST $BASE/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"account\":\"$TEST_ACCOUNT\",\"password\":\"$TEST_PASSWORD\"}" | python -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
KB=$(curl -s -X POST $BASE/api/kbs -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"p3-smoke","description":"P3 E2E"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['id'])")
echo "kb=$KB"
```

上传一个**含中文财务术语**的小文本文件（例：内容为「公司资产负债率上升，研发费用增加，净利润同比下降。」），等 `ready` 后提一个只能用该内容回答的问题（例：「资产负债率有什么变化」），确认：

1. 答案引用了上传内容；
2. `citations` 非空且 `source` / `page` 正确；
3. 日志里有 `[retrieval] hybrid done ... dense_count=N sparse_count=M result_count=K`，且 **`sparse_count > 0`**。

```bash
docker compose logs --since=5m app | grep "hybrid done"
```

**判定**：`sparse_count` 为 0 即 **DoD D7 未达成**，回 Task 3/5 排查（首选怀疑 `content_seg` 未重写）。把日志行原样记进报告。

- [ ] **Step 4: 反向证伪（词法路真的在贡献，而不是被 dense 兜住）**

```bash
POSTGRES_HOST=localhost .venv/bin/python - <<'PY'
import asyncio
from src.infra.db.engine import run_and_dispose
from src.infra.db.vector_store import VectorStore

async def main():
    store = VectorStore()
    kb_id = "<第 3 步的 kb>"
    rows = await store.lexical_search(kb_id, "资产负债率", 10)
    print("lexical hits:", [(r.id, round(r.lexical_score or 0, 4)) for r in rows])
    fallback = await store.lexical_search(kb_id, "涨了吗", 10)
    print("substring fallback:", [(r.id, r.lexical_score) for r in fallback])

asyncio.run(run_and_dispose(main()))
PY
```

预期：第一条给出该文档的分块且 `lexical_score > 0`；第二条走子串兜底，**不抛错**。记进报告。

- [ ] **Step 5: 清理测试污染并恢复语料**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base) || '|' || (SELECT count(*) FROM document) || '|' || (SELECT count(*) FROM chunks) || '|' || (SELECT count(*) FROM users);"
```

按 P1/P2 的既有做法清掉本轮 E2E 产生的 `knowledge_base` / `document` / `chunks` / `conversation_history`（**不要清 `users`**），把语料恢复到 **176 分块**：

- 若第 3 步的 KB 与文档是本轮新建的，删掉它们产生的行；
- 若需要恢复 Chroma 搬迁语料，跑 `POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py`（幂等），**随后必须**跑 `rewrite_content_seg.py --apply` —— 顺序不可颠倒（搬迁脚本走 `build_rows`，Task 4 之后它写的就是分词结果；只有非本路径写入的旧行才需要重写）。

收尾后计数应回到 `5|0|176|1`（kb / doc / chunks / users；`users` 为 1 是登录自动注册的账号）。把真实计数记进报告，并确认工作区干净：

```bash
git status --short
```

- [ ] **Step 6: 写验收报告**

新建 `docs/tmp/p3-acceptance-2026-09-19.md`：

```markdown
# P3 词法检索验收（BM25 → PostgreSQL 全文检索）

- HEAD：<收口 commit>
- 回滚锚点：<Task 0 记录的 SHA>
- 测试：<N passed, M skipped>（<耗时>）
- ruff：<结论>；pyright：<结论>

## DoD 逐条结论

| # | 判据 | 结论 | 证据 |
|---|---|---|---|
| D1 | 分词单一入口 | | Task 1/2 测试 |
| D2 | 查询安全化与兜底 | | Task 2/3 测试 |
| D3 | 词法路读 PG | | Task 3 测试 |
| D4 | 存量已全量重写 | | `--check` 退出码 0 |
| D5 | 探针完成横向比较 | | `docs/tmp/p3-lexical-probe-2026-09-19.md` |
| D6 | 融合迁移完成 | | Task 7 测试 |
| D7 | 两路同源并发 + 贡献可见 | | E2E 的 `hybrid done` 行 |
| D8 | 无独立词法索引组件 | | `test_no_bm25_leftovers.py` |
| D9 | 门禁全绿 + E2E | | 本报告 |

## E2E 观测记录

<三条：检索日志行原文 / 答案与 citations 摘要 / lexical_search 独立调用输出>

## 回滚依据核查

- `data/bm25_index`：<存在/不存在>（P3 刻意保留）
- `data/chroma_persist`：<存在>（P3 未触碰）
- MySQL 卷 `corporate_rag_mysql_data`：<未触碰>

## 残留清单

<逐条列出本阶段发现的、不属于 P3 的问题及其归属（需求池编号或 P4）>
```

把 `<...>` 全部替换为实测值。**不得留占位符**。

- [ ] **Step 7: 回填 `tasks.md` 并提交**

在 `tasks.md` 的阶段表 P3 行后补一句「DoD D1–D9 全部达成，验收报告见 `docs/tmp/p3-acceptance-2026-09-19.md`」。

```bash
git add docs/
git commit -m "docs(p3): 验收报告与 DoD 结论"
```

---

## 覆盖的 spec requirement（对账用）

| capability · requirement | 本 plan 的承载 | 状态 |
|---|---|---|
| `hybrid-retrieval` · 混合检索的两路取数与融合位置 | Task 8（`asyncio.gather` + 同一 `VectorStore`）；Task 7（融合在应用层、纯函数） | ✅ |
| `hybrid-retrieval` · 融合结果的来源可辨 | Task 3（填 `sparse_rank`）+ Task 8（融合透传，两路排名都保留） | ✅ |
| `hybrid-retrieval` · 融合参数可配置 | Task 7（`RRF_K` / `RRF_TOP_N` 进 settings）+ Task 8（调用点传参，无内联字面量） | ✅ |
| `hybrid-retrieval` · 写入与查询的分词口径同源 | Task 1（唯一入口）+ Task 2（守卫测试）+ Task 5（存量重写不变量）+ Task 1 Step 6（pin 版本） | ✅ |
| `hybrid-retrieval` · 查询条件的构造与转义 | Task 2（安全词元 + 前缀通配 + 子串兜底 + LIKE 转义） | ✅ |
| `hybrid-retrieval` · 分块按知识库归属存储 | Task 3（`kb_id` 限定）；「遍历不产生副作用」「归属受约束」由 P2 达成；**「同事务」两条归 P4** | ◐ P2+P3 |
| `retrieval-quality` · 迁移等价性与词项命中探针 | Task 6（词法侧探针：词项派生 / 三组变量分开比较 / 显式失效用例 / 报告含限制声明）；dense 侧由 P2 达成 | ✅ |
| `retrieval-quality` · Rerank context passthrough | P2 已达成（`chunks` 表）；本阶段不改 | ✅（P2） |
| `observability-logging` · 稀疏支路贡献可见 | Task 8（`hybrid done` 增 `dense_count` / `sparse_count`）+ Task 8 Step 7（EventSpec 同步） | ✅ |
| `database-orm` · 搜索类型搬迁（引用方清单含 `bm25_index.py`） | Task 9（删该文件）+ Task 10（delta 对账） | ✅ |
| `architecture-tidy` · 无独立词法索引组件 | Task 9（删 `BM25Index` / `BM25_INDEX_DIR` / 索引文件路径配置）+ Task 9 Step 1 守卫测试 | ✅ |
| `agent-service` · 检索依赖来自单一存储组件 | Task 8（`make_rag_tools` / `build_graph` / `AgentService` 去 `bm25`） | ✅ |
| `multi-query-retrieval` · 两路同源 | Task 8 | ◐ 仅命名同步；**结构性陈旧见 F-29** |
| `typed-data-layer` · 检索结果统一类型 | `ChunkResult` 的 `lexical_score` / `sparse_rank` 在 P2 已就位，本阶段只是把**填充来源**换成 PG 词法路 | ✅（P2 建立，P3 接源） |
| `database-migrations` / `kb-routing` / `model-config` / `chunk-entity-enrichment` / `request-abort` / `streaming-run` | 不在 P3 | — |

## P3 明确不做（与后续阶段的边界）

- **入库/删除两条路径同事务 + 故障注入验收** → P4（`hybrid-retrieval` 的两条同事务 scenario）。
- **依赖与卷清理**：删 `rank_bm25`（F-28）、`chromadb`（F-19）、`data/chroma_persist`、`data/bm25_index`、`deploy/chroma/` → P4。
- **`src/api/documents.py:246` 越层与双删除路径收编**（F-23 / F-25）→ P4 的同事务改造一并做。
- **`alembic/env.py` 的 `compare_server_default=True`**（F-22）→ 独立变更。
- **`src/infra/db/mysql_db/` 包改名**（F-18）→ 独立变更。
- **HNSW 近似索引** → 规模或延迟要求变化时的纯增量。
- **两路权重（加权 RRF）** → 能力新增，独立变更 + 自己的验收。
- **端到端答案质量（RAGAS）** → 语料到位后的独立评估活动（`retrieval-quality` delta 明确要求显式登记）。
- **`multi-query-retrieval` 的陈旧陈述重写**（F-29）→ 与 `retrieval-fetch-and-dedup` 的重定基一并处理。

## 执行注意（控制器与执行者都看）

1. **顺序不可换**：Task 5（存量重写）必须早于 Task 8（检索切换）。反之会出现"词法路已上线、但 `content_seg` 仍是正文原值"的状态 —— 中文 `tsv` 里是整串 1 个 token，词法路静默 0 命中，而日志只会显示 `sparse_count=0`，很容易被当成"语料太小"而不是缺陷。
2. **Task 6 的探针必须在 Task 5 之后跑**：否则分组 A 的 `jieba-tsrank` 测的是占位数据。
3. **Task 8 的签名级联是原子的**：`retrieval.search` → `make_rag_tools` → `build_graph` → `AgentService` → `AppService` 五处的 `bm25` 形参是**位置参数**（F9），漏改一处不会报错、只会静默错位，因此必须在同一 Task 内改完并跑全量测试。
4. **Task 9 才删 `bm25_index.py`**：Task 7 只搬走纯函数、Task 8 只切调用点；提前删会让中间态无法运行（`retrieval.py` 仍 import `BM25Index` 直到 Task 8 完成）。
5. **不要碰 `data/bm25_index` 与 `data/chroma_persist`**（F11 / F12，回滚与复现依据）。
6. **`jieba` 的首调用要 offload**（Ruling 8）：漏了会在首次检索时冻住整个 worker（单 worker 下所有请求与 SSE 一起卡）。
7. **探针数字不是质量基线**（D5）：报告里必须保留那句限制声明，否则后人会把 176 分块的相对比较当成发布判据。
