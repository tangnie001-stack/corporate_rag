# PostgreSQL 向量底座（postgres-storage-consolidation / P2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 dense（向量）检索的存储从 ChromaDB 换成 PostgreSQL + pgvector，并在同一次搬迁上完成两件事——`ChunkResult` 的 `metadata` 回填契约（否则去重/引用/实体透传静默失效），以及「替换前后 top-k 重合率 ≥ 0.9」的可判定迁移等价性验收。

**Architecture:** P1 已经建好 `chunks` 表（`embedding vector(1024)` + `tsv` 生成列 + `kb_id` 外键）但没有一行代码读写它。P2 新增 `ChunkModel` + `ChunkRepo` 作为这张表的唯一访问层，在 `vector_store/` 里新增一个 **PG 后端**（`PgVectorStore`）并**保持 Chroma 实现原样可跑**，直到等价性验收通过，才在最后一个任务里切换装配并删除 Chroma 的代码路径。Chroma 的**依赖、数据目录、`deploy/chroma/`** 留到 P4「依赖与卷清理」——在此期间 `data/chroma_persist` 是等价性验收的唯一语料来源与回滚依据，**不得删除**。

**Tech Stack:** Python 3.11+ / SQLAlchemy 2.x async + asyncpg / pgvector 0.8.6（PG 15.19）/ FastAPI / pytest（真实 PG，非 mock）

**Spec:** `docs/openspec/changes/postgres-storage-consolidation/`（`proposal.md` / `design.md` / `specs/`）。本 plan 实现的 delta：`typed-data-layer`（检索结果统一类型 + `metadata` 回填契约 + 全局路径移除）、`retrieval-quality`（dense 迁移等价性）、`hybrid-retrieval`（分块按知识库归属存储）、`database-orm`、`model-config`、`kb-routing`、`chunk-entity-enrichment` 的命名同步。设计决策以 `design.md` 的 **D3 / D6 / D8** 为准。

## 阶段定位（重要，先读）

本 change 跨 4 个可独立交付的子系统，每个子系统一份 plan。本文件是 **P2**：

| 阶段 | 范围 | 状态 |
|---|---|---|
| P1 | PostgreSQL 关系型底座（配置 / compose / alembic baseline / ORM 合并 / `ChunkData` 统一 / engine 切 asyncpg / repo 幂等 / 退役 MySQL / 文档） | **已完成**（HEAD `8fb581e`，未合并未推送，账本保留） |
| **P2（本文件）** | dense 检索换 pgvector、`ChunkResult` 分路字段与 `metadata` 回填契约、删全局检索入口、Chroma→PG 数据搬迁、dense 迁移等价性验收 | 本次 |
| P3 | jieba 分词入口 + 查询串构造与转义 + `tsv` 生成列写入（替换 P2 的占位）+ 词项命中探针选型 + `rrf_fusion` 迁移 + 删除 `bm25_index.py` | 待 P2 落地后写 |
| P4 | 入库/删除两条路径同事务 + 故障注入验收 + **依赖与卷清理（含 Chroma）** + prod compose 与 dev 同构 + 文档与 ADR + 归档 | 待 P3 落地后写 |

**P2 的边界（明确不做）**：词法路的算法、分词、查询构造（P3）；入库/删除的事务化（P4）；Chroma 的依赖/卷/数据目录清理（P4）；HNSW 索引（`design.md` D3 明确不建，将来是纯增量）；RDS 与 prod 安装（遗留项）。

## ⚠ 已知事实与陷阱（先读这张表）

每一条都已在本仓或上游实测/实读得出，带 F# 的编号在正文里被引用。

| # | 事实 / 陷阱 | 依据 | 怎么避 |
|---|---|---|---|
| **F1** | **`metadata` 回填不是可选项，漏了会静默失效** | `_dedup_by_doc_id` 读 `r.metadata.get("doc_id")`（`src/rag/retrieval.py:53`），而 `doc_id` 在新表里是**列**；`rag_tools.py:171-176` 读 `source`/`page`/`doc_id`；`retrieval.py:187-188` 读实体键 | 读路径统一走一个纯函数 `row_to_chunk_result()`，把**列值 + jsonb 平铺合并**（冲突以列为准）；Task 3 用单测把 5 个契约键与自定义键钉死 |
| **T2** | **jsonb 必须原样承载 chunker 的全部自定义键**，不得只留 5 个契约键 | 1.2 实测库里已有 `parent_content`（另有 `heading_path` / `block_type` / `currency` / `quarter` / `report_type` / `year` / `company` / `report_period` / `sec_code`）；`parent_content` 是 `retrieval-fetch-and-dedup` 做父块级去重的输入 | 写入侧用 `split_metadata()` 只把 5 个契约键摘出去，其余**整包**进 jsonb；搬迁脚本与写入路径**共用**同一个函数 |
| **F3** | **`chunks.kb_id` 有外键**（全 schema 唯一一条 FK：`alembic/versions/0001_pg_baseline.py:303`），而 P1 已把 PG 清空（`knowledge_base` = 0 行），Chroma 的 176 分块属于 5 个**已不在 PG 里**的 kb_id | 控制器 2026-09-19 实测：`knowledge_base` 计数 0；FK 唯一 | 搬迁脚本先按 Chroma 的 kb_id **补建合成 `knowledge_base` 行**（该表 `user_id` 无外键），再写 `chunks`；验收报告写明这些是验收用合成 KB |
| **F4** | `metadata` 是 SQLAlchemy declarative 的**保留属性名**（`Base.metadata`） | `design.md` D3 line 180-181 | ORM 属性名用 `extra`，列名仍是 `metadata`：`extra: Mapped[dict] = mapped_column("metadata", JSONB, ...)` |
| **F5** | `chunks` 表**没有** `created_at` / `updated_at`，`id` 也不是 UUID（是 `{doc_id}:{chunk_index}`） | `0001_pg_baseline.py:276-307` 的列清单 | `ChunkModel` **不得**继承 `IDMixin` / `TimestampMixin`，也不得补时间戳列（否则 ORM metadata 与 baseline 漂移） |
| **F6** | `min(k, 100)` 是 **Chroma 的硬上限**（`vector_store/search.py:44`），PG 无此限 | `design.md` D8 line 379 | 保留上限并注释来源，避免把「能力提升」混进等价性验收 |
| **F7** | Chroma 与 PG 的**距离语义一致**：`<=>` 返回余弦距离 0~2 | `tasks.md` §1 1.1 实测；`test_pg_baseline.py:189-195` | `score = 1 - distance` 契约不动；等价性比对直接用 `distance` 排序 |
| **F8** | **向量维度写在 `pg_attribute.atttypmod`** | pgvector 的行为；用于「维度一致性守卫」把对照对象从 Chroma collection 换成 `chunks.embedding` 列 | Task 2 的测试用 `SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'` 断言 = 1024 |
| **F9** | **不要在仓库根跑 `ruff format .`** | P1 实测：会重排 76 个 `docs/**/*.md`（内含 Python 代码块），属**既有**漂移 | 只格式化改动文件，或跑完 `git status` 把改动集外的文件逐个回退 |
| **F10** | 本 plan 大量改 `VectorStore` 的同步签名 → **所有调用点必须在同一任务里改完**，否则应用处于半死状态 | 见下方 Ruling 1 | 用「新增 PG 后端、最后切换装配」的顺序，让 Chroma 实现**在切换前一直可用** |
| **F11** | `src/infra/db/vector_store/` 的包名与 `src/infra/db/mysql_db/` 一样名不副实（后者已是纯 PG repo） | P1 遗留项 L4/F-18 | P2 **不改包名**（改名触及约 20 个 import 点，属独立变更）；只在 `code-map.md` 继续标注 |

## P2 完成标准（DoD）

**P2 完成的判据是下面全部成立，而不是「Task 0–11 的 checkbox 都打了勾」。**

| # | 判据 | 怎么验 |
|---|---|---|
| **D1** | `chunks` 由 ORM 模型映射，且 **ORM metadata 与手写 baseline 无漂移** | `alembic revision --autogenerate` 的产出对 `chunks` **为空**（Task 2）。⚠️ **该门禁对 `server_default` 是盲的**：`alembic/env.py` 未传 `compare_server_default`，Alembic 默认为 `False` → 三处 `server_default`（`source` / `page` / `metadata`）必须另有只读断言（比对 `information_schema.columns.column_default`）才算被证据覆盖 |
| **D2** | 入库路径写进 PG：上传后 `chunks` 有行、`tsv` 生成列自动填充、`embedding` 为 1024 维 | Task 5、Task 11 的 E2E |
| **D3** | 读取路径走 PG：`similarity_search` 按余弦距离升序返回 `ChunkResult`，`min(k, 100)` 上限保留 | Task 6 的测试 |
| **D4** | **`metadata` 回填契约**：每条结果的 `metadata` 都含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`，且 jsonb 自定义键（含 `parent_content`）原样可读 | Task 3 单测 + Task 6 集成测试 |
| **D5** | 全局检索路径消失：`similarity_search_all` / `similarity_search_multi` 无定义无调用；`retrieval.search` 无 `not kb_id` 分支 | Task 4 |
| **D6** | **dense 迁移等价性**：同一份语料（Chroma 搬迁而来）、同一批 ≥20 条固定查询，替换前后 top-k 重合率 **≥ 0.9** | Task 8 |
| **D7** | Chroma **代码路径**已删除；依赖 / `data/chroma_persist` / `deploy/chroma/` **保留**（回滚与复现依据） | Task 9 |
| **D8** | 门禁全绿 + **PG 上跑通一次真实 E2E 冒烟**（登录 → 建库 → 上传到 ready → 提问 → 引用渲染） | Task 11 |

> **D6 为什么不能省**：`pytest` 全绿只证明新代码自洽，不证明「换存储没改变检索结果」。176 分块 / ≥20 查询是小语料，二值口径（top-k 是否重合）比均值型指标稳定得多，所以这个判据在今天的规模上是可判定的。

## 本 plan 的裁决（需要执行者知道，且已获用户 2026-09-19 确认）

1. **Ruling 1：`VectorStore` 的 IO 方法从同步改为 `async`。**
   asyncpg 只能异步驱动，而 `VectorStore` 现有方法都是同步的（调用点一律用 `asyncio.to_thread` 包）。
   `design.md` D8 说「保持现有 11 个方法的**名称与签名**」—— 本 plan 把「签名不变」解释为**方法名、参数与返回语义不变**；`sync → async` 是引擎替换的必然结果，不是契约新增。调用点从 `to_thread(...)` 改为直接 `await`（顺带去掉一次线程切换）。
   代价（若判断错）：`api_contract.md` 的 `VectorStore` 契约与 ~8 处调用点要改；已由 Task 4/9 覆盖，并同步文档。
2. **Ruling 2：`chunks` 写入用 `ON CONFLICT (kb_id, doc_id, chunk_index) DO UPDATE`。**
   同 doc 重试/重传幂等，`design.md` 的「重复入库不再依赖捕获异常」成立。
   ⚠ 已知残留：若新分块数**少于**旧分块数，`chunk_index` 更大区间的旧行不会被覆盖 → 产生尾部残留。P2 用「同一 `(kb_id, doc_id)` 先按 `chunk_index >= len(rows)` 删除尾部」补齐；P4 事务化时可换成「先 DELETE by doc 再 INSERT」。
   代价（若判断错）：极小概率留下尾部残留行，被检索到时表现为「多出几段陈旧分块」。
3. **Ruling 3：搬迁脚本先补建合成 `knowledge_base` 行**（F3），因为 `chunks.kb_id` 有外键且 P1 已清空 PG。跑完在验收报告里记明；Task 11 的 E2E 另建自己的 KB，收尾时按需清理这批合成 KB。
   代价（若判断错）：dev 库里留 5 个非真实归属的知识库（验收后清理即可）。
4. **Ruling 4：P2 只删 Chroma 的代码路径**，`chromadb` 依赖、`data/chroma_persist`、`deploy/chroma/` 留到 P4。等价性比对脚本**直接使用 `chromadb` 客户端**读原始集合，不经过 `VectorStore`（因为 `VectorStore` 会被切到 PG）。
   代价（若判断错）：仓库里有一段未被应用使用的依赖；P4 清理。
5. **Ruling 5：`content_seg` 在 P2 写 `content` 原值（占位）。**
   `chunks.content_seg` 是 NOT NULL，且 `tsv` 是它的生成列。P2 不做分词，所以先写原文（此时中文 `tsv` 基本无用，但 **P2 的词法路仍是 BM25，不读 PG 的 `tsv`**，不受影响）。P3 换成 jieba 输出并**全量重写存量**（`hybrid-retrieval` delta 的「分词器变更触发存量重写」）。
   代价（若判断错）：P2 期间 PG 的词法能力不可用——但没有任何代码用它。
6. **Ruling 6：`dense_rank` 由读路径填充，`sparse_rank` 由仍存在的 BM25 路填充，`rrf_fusion` 只做透传。**
   `hybrid-retrieval` 要求「融合结果携带两路排名」；P2 先把字段与两侧填充落地，融合的迁移与词法路替换在 P3。
   代价（若判断错）：P2 结束时融合结果已带排名，但排名的**来源**仍是 BM25 而非 PG 词法路——P3 替换来源，字段契约不变。
7. **Ruling 7：验收产物入库保留**：`scripts/migrate_chroma_to_pg.py`（幂等可重跑）、`tests/fixtures/dense_equivalence_queries.json`（固定查询集，spec 要求可复现）、比对脚本 `scripts/dense_equivalence_check.py`；结果记录落 `docs/tmp/`。P4 归档时一并处置。
   代价（若判断错）：仓库多两个一次性脚本；P4 清理。

## Global Constraints

- **契约（P2 不得破坏）**：`ChunkResult.distance` 是**余弦距离**，消费方用 `score = 1 - distance`（`rag_tools.py:168`、`retrieval.py:157-158`）。
- **分块 id 格式** `{doc_id}:{chunk_index}`（`store.py:44`）不变。
- **不得引入三元表达式**（`a if cond else b`）—— 写完整 `if/else`。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底。
- **硬编码集中管理**：新增常量/阈值/文案进 `src/config/`（`settings.py` 环境变量 / `const.py` 固定值 / `prompts.py` 提示词）。
- **文档与注释**：所有函数写 docstring；dataclass 每个字段加行内注释；注释写**当前状态**不写变更历史；注释陈述契约不写推理记录。
- **文件红线**：单文件 ≤ 400 行、单函数 ≤ 80 行。
- **门禁（每个 Task 结束都跑）**：`ruff check .` 无错、`pyright src/` 不新增 error、无**新增** `print()`/TODO。
- **契约同步**：改了公共方法签名或响应结构时，同步 `docs/agents/api_contract.md` 与受影响测试断言。
- **宿主侧连库约定（P1 立）**：dev 的 `postgres` 只发布 `127.0.0.1:5432`；**宿主上跑 alembic / pytest 一律加前缀 `POSTGRES_HOST=localhost`**；容器侧仍用 `.env` 的 `POSTGRES_HOST=postgres`。
- **PG 数据目录**必须用 docker named volume，绝不绑 `/mnt/d`。
- **MySQL 卷与 orphan 容器**（`corporate_rag_mysql_data` / `corporate-rag-mysql`）与 **Chroma 数据目录**（`data/chroma_persist`）是回滚依据：**不得**执行 `docker compose down -v` / `docker volume prune` / `rm -rf data/chroma_persist`。
- **测试** mock 外部依赖（embedding 网络调用），但**存储侧测试打真实 PG**，不用 mock。
- **不要在全仓跑 `ruff format .`**（F9）。

---

## 文件结构（改动的落点与职责）

**新建**

| 文件 | 职责 |
|---|---|
| `src/infra/db/models/chunk.py` | `ChunkModel` —— `chunks` 表的唯一 ORM 映射（`extra` → 列 `metadata`） |
| `src/infra/db/mysql_db/chunk_repo.py` | `ChunkRepo` —— `chunks` 表的唯一 SQL 访问层（upsert / dense 检索 / 按 doc / 分页 / 按 kb / 删除 / 枚举） |
| `src/infra/db/vector_store/mapping.py` | **行 ↔ `ChunkResult` 的映射与 `metadata` 回填契约**（纯函数，无 DB、无 Chroma）；`ChunkRow` dataclass |
| `src/infra/db/vector_store/pg_store.py` | `PgVectorStore` —— dense 检索的 PG 后端（async，11 个方法） |
| `scripts/migrate_chroma_to_pg.py` | 一次性搬迁：Chroma 176 分块 → `chunks`（幂等、可重跑） |
| `scripts/dense_equivalence_check.py` | 等价性比对：直读 Chroma 与 PG，算 top-k 重合率 |
| `tests/fixtures/dense_equivalence_queries.json` | ≥20 条固定查询（中文/数值/时间三类），spec 要求落盘可复现 |

**修改**

| 文件 | 改动 |
|---|---|
| `src/infra/db/vector_store/types.py` | `ChunkResult`：`bm25_score` → `lexical_score`；新增 `dense_rank` / `sparse_rank` |
| `src/infra/db/vector_store/__init__.py` | Task 9 起改为导出 PG 后端（`PgVectorStore as VectorStore`） |
| `src/infra/db/vector_store/embedding.py` | 去掉 Chroma `EmbeddingFunction` 基类，保留 `embed_query`（改名为 `QueryEmbedder`） |
| `src/infra/search/bm25_index.py` | `bm25_score` → `lexical_score`；`search()` 填 `sparse_rank` |
| `src/rag/retrieval.py` | 用 `dense_search`；删 `not kb_id` 分支；`bm25_score` 相关断言 |
| `src/services/document_service.py` | embedding **无条件预计算**；`add_chunks` / `delete_document` / `get_all_chunks` 改 `await` |
| `src/services/app_service.py` | `delete_collection` 改 `await` |
| `src/main.py` | 删 `_warmup_chromadb()` 及其事件 |
| `src/config/const.py` | 删 `CHROMA_WARMUP_*` 事件（若删后无引用） |
| `tests/reset_data.py` | `reset_vector_store()` → 不再删 Chroma 目录，改清 BM25 索引目录 |
| `tests/infra/db/test_vector_store.py` | 改为打真实 PG 的测试 |
| `tests/conftest.py` | `vector_store` fixture 改为 PG 后端 |
| `docs/agents/{code-map,api_contract,data-flow,glossary,defensive-patterns}.md` | 结构/契约/术语同步 |
| `docs/openspec/changes/postgres-storage-consolidation/tasks.md` | P2 状态与 §2 修正记录 |

---

### Task 0: 范围与本地前置确认（不碰 RDS / 不装 prod）

**Files:**
- Modify: 无（本任务不改任何仓库文件；产出写进报告）

**Interfaces:**
- Consumes: 无
- Produces: 一份「前置事实确认」报告 + 回滚锚点 commit（后续任务的 Global Constraints）

- [ ] **Step 1: 记录回滚锚点**

```bash
git rev-parse HEAD
git log --oneline -1
```

把输出的 40 位 SHA 记进报告。**这就是 P2 的回滚锚点**（`git revert` / `git reset` 的目标）。

- [ ] **Step 2: 确认 Chroma 语料仍可原样读出（F3 的前提）**

```bash
POSTGRES_HOST=localhost .venv/bin/python - <<'PY'
import chromadb
from src.config.settings import CHROMA_PERSIST_DIR
c = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
cols = c.list_collections()
print("collections:", len(cols))
total = 0
with_chunks = 0
for col in cols:
    n = col.count()
    total += n
    if n:
        with_chunks += 1
        got = col.get(include=["documents", "metadatas", "embeddings"])
        embs = got["embeddings"]
        bad = sum(1 for e in embs if e is None)
        print(f"  {col.name}: n={n} dim={len(embs[0]) if embs else 0} none={bad} kb_meta_keys={sorted((got['metadatas'][0] or {}).keys())}")
print("total chunks:", total, "non-empty collections:", with_chunks)
PY
```

预期：`collections: 691`、`total chunks: 176`、`non-empty collections: 5`、`dim=1024`、`none=0`。
**若不符**：停下并报告——等价性验收的语料前提不成立，需要先与控制器确认（`design.md` 的 1.2 实测是 2026-09-19 早先时点）。

- [ ] **Step 3: 确认 PG 侧 `chunks` 表的真实列与 FK（F3 / F5 的前提）**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -c "\d chunks"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc "SELECT count(*) FROM chunks;"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc "SELECT count(*) FROM knowledge_base;"
```

预期：12 列（`id, kb_id, doc_id, chunk_index, chunk_total, content, content_seg, tsv, embedding, source, page, metadata`）+ 1 条 FK + 唯一约束 `uq_chunks_kb_doc_idx` + 3 个索引；`chunks` 0 行；`knowledge_base` 0 行。

- [ ] **Step 4: 统计受 Ruling 1 影响的调用点（改 async 的爆炸半径）**

```bash
grep -rn "asyncio.to_thread" src/ tests/ | grep -i "vector_store\|similarity_search\|add_chunks\|get_all_chunks\|delete_collection\|delete_document\|get_chunks_by_doc_id\|get_chunks_paginated\|list_collections\|get_or_create_collection"
```

把命中清单记进报告。预期命中在 `src/services/document_service.py`、`src/services/app_service.py`、`src/rag/retrieval.py`、`tests/` 若干。**若出现清单外的文件**，在报告里点出来（说明爆炸半径比本 plan 估计的大）。

- [ ] **Step 5: 统计 `bm25_score` 与 `ChunkResult(` 位置参数的改动面（F1 / T11 类陷阱）**

```bash
grep -rn "bm25_score" src/ tests/ docs/agents/
grep -rn "ChunkResult(" src/ tests/ | head -40
```

对每处 `ChunkResult(` 判断传参方式：**位置参数**构造会在 Task 1 加字段后静默漂移（P1 的 T11 同类陷阱）。把「哪些调用点是位置参数」列进报告。

- [ ] **Step 6: 确认 `similarity_search_multi` 无调用方（D5 的前提）**

```bash
grep -rn "similarity_search_multi" src/ tests/
```

预期：只有 `src/infra/db/vector_store/__init__.py` 的定义行。**若 tests/ 里有调用**，本 plan 的 Task 4 需要额外删除那些用例。

- [ ] **Step 7: 确认 Chroma 数据目录当前未被测试或重置脚本清掉**（F9 / 回滚依据）

```bash
ls -la data/chroma_persist | head
grep -n "CHROMA_PERSIST_DIR\|reset_vector_store" tests/reset_data.py
```

预期：目录存在且有内容；`reset_vector_store()` 会 `rmtree` 该目录 —— **这正是 Task 9 要改掉的**（现在若跑 `reset_all()` 会毁掉验收语料与回滚依据）。

- [ ] **Step 8: 写报告**

把 Step 1–7 的真实输出写进 `TASK0_REPORT`（控制器会在派发时给出路径），并明确列出：回滚锚点 SHA、语料口径、受影响调用点清单、位置参数构造清单、任何与预期不符的项。

---

### Task 1: `ChunkResult` 字段改造（更名 + 分路排名）

**Files:**
- Modify: `src/infra/db/vector_store/types.py:6-23`
- Modify: `src/infra/search/bm25_index.py:95-117`
- Modify: `src/rag/retrieval.py`（`bm25_score` 引用处）
- Modify: `src/agents/tools/rag_tools.py`（`bm25_score` 引用处）
- Test: `tests/infra/db/test_vector_store_types.py`（新建）
- Test: `tests/rag/test_retrieval.py`、`tests/infra/search/test_bm25_index.py`（断言同步）

**Interfaces:**
- Consumes: 无
- Produces:
  - `ChunkResult` 的最终字段集：`id: str` / `content: str` / `metadata: dict` / `distance: float | None` / `lexical_score: float | None` / `dense_rank: int | None` / `sparse_rank: int | None`
  - `BM25Index.search(kb_id: str, query: str, k: int = 150) -> list[ChunkResult]`（返回项填 `lexical_score` 与 `sparse_rank`）

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_vector_store_types.py`：

```python
"""ChunkResult 的字段契约：分路得分与分路排名。"""

from src.infra.db.vector_store.types import ChunkResult


def test_chunk_result_has_split_fields():
    """分路字段必须存在且默认缺席（None）。"""
    r = ChunkResult(id="d:0", content="正文")
    assert r.lexical_score is None
    assert r.dense_rank is None
    assert r.sparse_rank is None


def test_chunk_result_carries_path_specific_values():
    """两路的得分与排名各自独立。"""
    dense = ChunkResult(id="d:0", content="正文", distance=0.12, dense_rank=0)
    lexical = ChunkResult(id="d:1", content="正文2", lexical_score=3.7, sparse_rank=1)
    assert dense.dense_rank == 0
    assert dense.dense_rank != lexical.sparse_rank
    assert lexical.sparse_rank == 1


def test_chunk_result_has_no_bm25_named_field():
    """旧名必须消失：它会把与引擎无关的词法得分误导成 BM25。"""
    r = ChunkResult(id="d:0", content="正文")
    assert not hasattr(r, "bm25_score")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store_types.py -v`
Expected: FAIL —— `lexical_score` / `dense_rank` / `sparse_rank` 不存在（`TypeError` 或 `AttributeError`）。

- [ ] **Step 3: 改 `ChunkResult`**

`src/infra/db/vector_store/types.py`，把 `ChunkResult` 换成：

```python
@dataclass(slots=True)
class ChunkResult:
    """检索结果统一类型。

    替代 similarity_search / 词法检索 / RRF fusion / rerank 之间的 list[dict]。
    统一 dense 与词法两路的输出格式；两路同源于一个 PostgreSQL 实例。
    """

    id: str
    """分块 ID，格式为 {doc_id}:{chunk_index}。"""
    content: str
    """分块的文本内容，由文档解析器生成，可能包含 Markdown 格式。"""
    metadata: dict = field(default_factory=dict)
    """元数据字典，含 doc_id / chunk_index / chunk_total / source / page 五个契约键，
    以及 chunker 产出的全部自定义键（如 parent_content / block_type / heading_path）。
    由列值与 jsonb 平铺合并回填，冲突以列为准。"""
    distance: float | None = None
    """余弦距离，仅 dense 检索时有值（越小越相似）；词法检索与分页查询时为 None。"""
    lexical_score: float | None = None
    """词法检索得分，仅词法检索时有值；dense 检索与分页查询时为 None。
    与引擎无关的命名：P3 后它来自 PostgreSQL 全文检索，不再是 BM25。"""
    dense_rank: int | None = None
    """该结果在 dense 路的排名（0 起）；未出现在 dense 路时为 None。"""
    sparse_rank: int | None = None
    """该结果在词法路的排名（0 起）；未出现在词法路时为 None。"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store_types.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 全仓替换旧字段名**

```bash
grep -rn "bm25_score" src/ tests/
```

把每一处 `bm25_score` 改成 `lexical_score`（Task 0 Step 5 已列清单）。典型落点：

- `src/infra/search/bm25_index.py:105` 与 `:114`（`ChunkResult(..., bm25_score=float(scores[idx]))`）
- `tests/` 中的断言与构造

`src/infra/search/bm25_index.py` 的 `search()` 同时补 `sparse_rank`：把第 95-117 行的循环改成

```python
        results = []
        for rank, idx in enumerate(ranked):
            chunk = chunks[idx]
            # 兼容旧格式：chunks 可能是 dict（历史 pickle）或 ChunkData（新格式）
            if isinstance(chunk, dict):
                results.append(
                    ChunkResult(
                        id=chunk.get("id", chunk.get("chunk_id", "")),
                        content=chunk.get("content", ""),
                        metadata=chunk.get("metadata", {}),
                        lexical_score=float(scores[idx]),
                        sparse_rank=rank,
                    )
                )
            else:
                results.append(
                    ChunkResult(
                        id=chunk.chunk_id,
                        content=chunk.content,
                        metadata=chunk.metadata,
                        lexical_score=float(scores[idx]),
                        sparse_rank=rank,
                    )
                )
        return results
```

- [ ] **Step 6: 把所有位置参数构造改成关键字（防 T11 类静默漂移）**

对 Task 0 Step 5 列出的每一处**位置参数** `ChunkResult(...)`，改成全部关键字传参。示例（`tests/rag/test_retrieval.py` 的 `_cr` 辅助函数）：

```python
def _cr(cid: str, *, doc_id: str = "", content: str = "c") -> ChunkResult:
    """构造测试用 ChunkResult（全关键字，避免字段序变动时静默漂移）。"""
    return ChunkResult(
        id=cid,
        content=content,
        metadata={"doc_id": doc_id} if doc_id else {},
    )
```

- [ ] **Step 7: 跑受影响的测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store_types.py tests/infra/search/test_bm25_index.py tests/rag/test_retrieval.py tests/rag/test_retrieval_dedup.py -q`
Expected: PASS（全绿）。

- [ ] **Step 8: `rrf_fusion` 的透传确认（加断言，不改实现）**

在 `tests/infra/search/test_bm25_index.py` 增一条测试：融合后每个点的 `dense_rank` / `sparse_rank` 与它在输入哪一路出现一致，**不被融合抹掉**。

```python
def test_fusion_preserves_path_ranks():
    """融合只按 RRF 重排，不抹掉分路排名（来源可辨）。"""
    from src.infra.db.vector_store.types import ChunkResult
    from src.infra.search.bm25_index import rrf_fusion

    dense = [
        ChunkResult(id="a", content="A", distance=0.1, dense_rank=0),
        ChunkResult(id="b", content="B", distance=0.2, dense_rank=1),
    ]
    sparse = [
        ChunkResult(id="b", content="B", lexical_score=9.0, sparse_rank=0),
        ChunkResult(id="c", content="C", lexical_score=8.0, sparse_rank=1),
    ]
    fused = rrf_fusion(dense, sparse)
    by_id = {r.id: r for r in fused}
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].dense_rank == 1
    assert by_id["b"].sparse_rank == 0  # 先见的那条即 dense 里的对象，sparse 排名不因融合而丢失
    assert by_id["c"].sparse_rank == 1
```

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/search/test_bm25_index.py -q`
Expected: PASS。

> ⚠ 若该测试失败：说明 `rrf_fusion` 的「先见者胜」（`if doc_id not in data`）会丢掉后一路的排名。
> **不要**在 P2 改融合语义（那会污染等价性验收）——把两个排名分别取最大值合并（`dense_rank` 取 dense 侧、`sparse_rank` 取 sparse 侧）改为「按路合并字段」的最小改法，并在报告里说明这是 Task 1 的必要修正。

- [ ] **Step 9: 提交**

```bash
git add src/infra/db/vector_store/types.py src/infra/search/bm25_index.py \
        src/rag/retrieval.py src/agents/tools/rag_tools.py \
        tests/infra/db/test_vector_store_types.py tests/infra/search/test_bm25_index.py \
        tests/rag/test_retrieval.py tests/rag/test_retrieval_dedup.py
git commit -m "refactor(retrieval): ChunkResult 词法得分更名 lexical_score，新增 dense_rank/sparse_rank"
```

---

### Task 2: `chunks` ORM 模型 + `ChunkRepo`

**Files:**
- Create: `src/infra/db/models/chunk.py`
- Create: `src/infra/db/mysql_db/chunk_repo.py`
- Modify: `src/infra/db/models/__init__.py`（若该文件聚合导出模型）
- Test: `tests/infra/db/test_chunk_repo.py`（新建）

**Interfaces:**
- Consumes: `src/infra/db/base.py` 的 `Base`；`src/infra/db/engine.py` 的 `session_factory`
- Produces:
  - `ChunkModel`（表 `chunks`，属性 `extra` 映射列 `metadata`）
  - `ChunkRow`（dataclass，见 Task 3 —— 本任务先只用它做类型标注，Task 3 落地其定义）
  - `ChunkRepo(session_factory)` 的方法：
    - `async def upsert_chunks(self, rows: list[ChunkRow]) -> int`
    - `async def delete_tail(self, kb_id: str, doc_id: str, from_index: int) -> int`
    - `async def search_dense(self, kb_id: str, query_vec: list[float], k: int) -> list[tuple[ChunkRow, float]]`
    - `async def get_by_doc(self, doc_id: str, kb_id: str) -> list[ChunkRow]`
    - `async def get_paginated(self, doc_id: str, kb_id: str, page: int, page_size: int) -> tuple[list[ChunkRow], int]`
    - `async def get_by_kb(self, kb_id: str) -> list[ChunkRow]`
    - `async def list_kb_ids(self) -> list[str]`
    - `async def delete_by_doc(self, kb_id: str, doc_id: str) -> int`
    - `async def delete_by_kb(self, kb_id: str) -> int`

> ⚠ 本任务与 Task 3 有循环依赖（`ChunkRepo` 用 `ChunkRow`，`ChunkRow` 定义在 Task 3 的 `mapping.py`）。
> **执行顺序：先做 Task 3 的 Step 1（只落地 `ChunkRow` 定义与 `mapping.py` 骨架），再回来做本任务。** 控制器会在派发本任务时带上这条。

- [ ] **Step 1: 写失败测试（列结构与维度守卫）**

新建 `tests/infra/db/test_chunk_repo.py`：

```python
"""ChunkRepo 与 chunks 表的一致性测试（打真实 PG）。"""

import uuid

import pytest
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db.chunk_repo import ChunkRepo


@pytest.fixture
async def kb_row():
    """建一个真实 knowledge_base 行（chunks.kb_id 有外键），测后清理。"""
    kb_id = f"p2test-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p2test-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def test_chunks_embedding_dimension_is_1024():
    """维度一致性守卫的对照对象：chunks.embedding 列的维度（F8）。"""
    async with session_factory() as s:
        result = await s.execute(
            text(
                "SELECT atttypmod FROM pg_attribute"
                " WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
        assert result.scalar_one() == 1024


async def test_upsert_then_read_back(kb_row):
    """写入后能按 doc 读回，且 content_seg 已落库（tsv 由生成列算）。"""
    repo = ChunkRepo(session_factory)
    rows = [
        ChunkRow(
            id=f"{doc}:0", kb_id=kb_row, doc_id=doc, chunk_index=0, chunk_total=1,
            content="贵州茅台2024年营业收入1741亿元", content_seg="贵州茅台2024年营业收入1741亿元",
            embedding=[0.5] * 1024, source="a.pdf", page=3, extra={"parent_content": "母公司"},
        ),
    ]
    assert await repo.upsert_chunks(rows) == 1
    got = await repo.get_by_doc(doc, kb_row)
    assert len(got) == 1
    assert got[0].extra["parent_content"] == "母公司"
    assert got[0].source == "a.pdf"
    assert got[0].page == 3
```

（`doc` 用 `uuid.uuid4().hex` 在测试内生成；`ChunkRow` 从 `src.infra.db.vector_store.mapping` 导入。）

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_repo.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.infra.db.mysql_db.chunk_repo`。

- [ ] **Step 3: 写 `ChunkModel`**

`src/infra/db/models/chunk.py`：

```python
"""分块表 ORM 模型。

chunks 由 P1 的 baseline 从零建立；本模型是它在 ORM 侧的**唯一映射**，
列清单必须与 alembic/versions/0001_pg_baseline.py 的 op.create_table("chunks", ...) 逐列一致，
否则下次 autogenerate 会把差异生成为变更。
"""

from sqlalchemy import Computed, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from src.infra.db.base import Base


class ChunkModel(Base):
    """分块表：一张表承载全部知识库的分块，以 kb_id 列表达归属。"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(Text, primary_key=True, comment="分块 ID，格式 {doc_id}:{chunk_index}")
    kb_id: Mapped[str] = mapped_column(Text, nullable=False, comment="所属知识库")
    doc_id: Mapped[str] = mapped_column(Text, nullable=False, comment="所属文档")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="该文档内的分块序号，0 起")
    chunk_total: Mapped[int] = mapped_column(Integer, nullable=False, comment="该文档的分块总数")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分块正文原文")
    content_seg: Mapped[str] = mapped_column(
        Text, nullable=False, comment="词法检索文本；分词器变更时必须全量重写（P3 起为 jieba 输出）"
    )
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', content_seg)", persisted=True),
        nullable=True,
        comment="由 content_seg 自动生成的检索向量，不写入",
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024), nullable=True, comment="DashScope text-embedding-v3，1024 维"
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="", comment="来源文件名"
    )
    page: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="页码，无页码时为 0"
    )
    # 属性名不得叫 metadata（Base.metadata 是保留属性），列名保持 metadata 不改
    extra: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="'{}'::jsonb",
        comment="chunker 产出的自定义键（不含 doc_id/chunk_index/chunk_total/source/page 五个契约键）",
    )

    __table_args__ = (
        ForeignKeyConstraint(["kb_id"], ["knowledge_base.id"]),
        UniqueConstraint("kb_id", "doc_id", "chunk_index", name="uq_chunks_kb_doc_idx"),
        Index("ix_chunks_kb_id", "kb_id"),
        Index("ix_chunks_doc_id", "doc_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )
```

> ⚠ **不继承 `IDMixin` / `TimestampMixin`**（F5）：`chunks` 既没有 UUID 主键也没有时间戳列。
> ⚠ `session_default` 必须与 baseline 一致（`source` 是 `''`、`page` 是 `0`、`metadata` 是 `'{}'::jsonb`），否则 D1 的 autogenerate 空产出会被打破。

- [ ] **Step 4: 写 `ChunkRepo`**

`src/infra/db/mysql_db/chunk_repo.py`：

```python
"""分块 Repo — chunks 表 CRUD 与 dense 检索。

本模块是 chunks 表的唯一 SQL 访问层：向量排序、jsonb 读写、按 doc/kb 的增删
都在这里，向量存储层（vector_store）只做编排与结果映射。
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infra.db.models.chunk import ChunkModel
from src.infra.db.vector_store.mapping import ChunkRow


class ChunkRepo:
    """分块表的 SQL 访问层。"""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    async def upsert_chunks(self, rows: list[ChunkRow]) -> int:
        """按 (kb_id, doc_id, chunk_index) 幂等写入，冲突时整行覆盖。

        Args:
            rows: 待写入的分块行（形状见 mapping.ChunkRow）

        Returns:
            实际提交的行数
        """
        if not rows:
            return 0
        async with self._sf() as session:
            stmt = pg_insert(ChunkModel).values(
                [
                    {
                        "id": r.id,
                        "kb_id": r.kb_id,
                        "doc_id": r.doc_id,
                        "chunk_index": r.chunk_index,
                        "chunk_total": r.chunk_total,
                        "content": r.content,
                        "content_seg": r.content_seg,
                        "embedding": r.embedding,
                        "source": r.source,
                        "page": r.page,
                        "extra": r.extra,
                    }
                    for r in rows
                ]
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_chunks_kb_doc_idx",
                set_={
                    "content": stmt.excluded.content,
                    "content_seg": stmt.excluded.content_seg,
                    "embedding": stmt.excluded.embedding,
                    "source": stmt.excluded.source,
                    "page": stmt.excluded.page,
                    "extra": stmt.excluded.extra,
                    "chunk_total": stmt.excluded.chunk_total,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    async def delete_tail(self, kb_id: str, doc_id: str, from_index: int) -> int:
        """删除某文档 chunk_index >= from_index 的尾部残留（重传后分块数变少时用）。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id,
                    ChunkModel.doc_id == doc_id,
                    ChunkModel.chunk_index >= from_index,
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def search_dense(
        self, kb_id: str, query_vec: list[float], k: int
    ) -> list[tuple[ChunkRow, float]]:
        """按余弦距离取 top-k（越小越相似）。"""
        async with self._sf() as session:
            stmt = (
                select(ChunkModel, ChunkModel.embedding.cosine_distance(query_vec).label("distance"))
                .where(ChunkModel.kb_id == kb_id, ChunkModel.embedding.is_not(None))
                .order_by(ChunkModel.embedding.cosine_distance(query_vec))
                .limit(k)
            )
            result = await session.execute(stmt)
            return [(row_to_chunk_row(m), float(dist)) for m, dist in result.all()]

    async def get_by_doc(self, doc_id: str, kb_id: str) -> list[ChunkRow]:
        """取某文档的全部分块，按 chunk_index 升序。"""
        async with self._sf() as session:
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
                .order_by(ChunkModel.chunk_index)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()]

    async def get_paginated(
        self, doc_id: str, kb_id: str, page: int, page_size: int
    ) -> tuple[list[ChunkRow], int]:
        """分页取某文档的分块，返回 (当页行, 总数)。"""
        async with self._sf() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
            )
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id)
                .order_by(ChunkModel.chunk_index)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()], int(total or 0)

    async def get_by_kb(self, kb_id: str) -> list[ChunkRow]:
        """取整个知识库的全部分块（BM25 全量重建用）。"""
        async with self._sf() as session:
            stmt = (
                select(ChunkModel)
                .where(ChunkModel.kb_id == kb_id)
                .order_by(ChunkModel.doc_id, ChunkModel.chunk_index)
            )
            result = await session.execute(stmt)
            return [row_to_chunk_row(m) for m in result.scalars().all()]

    async def list_kb_ids(self) -> list[str]:
        """枚举有哪些知识库含分块（只读，不创建任何东西）。"""
        async with self._sf() as session:
            result = await session.execute(select(ChunkModel.kb_id).distinct())
            return [kb for kb in result.scalars().all()]

    async def delete_by_doc(self, kb_id: str, doc_id: str) -> int:
        """删除某文档的全部分块。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(
                    ChunkModel.kb_id == kb_id, ChunkModel.doc_id == doc_id
                )
            )
            await session.commit()
            return result.rowcount or 0

    async def delete_by_kb(self, kb_id: str) -> int:
        """删除某知识库的全部分块。"""
        async with self._sf() as session:
            result = await session.execute(
                delete(ChunkModel).where(ChunkModel.kb_id == kb_id)
            )
            await session.commit()
            return result.rowcount or 0
```

`row_to_chunk_row(model)` 定义在 Task 3 的 `mapping.py`（`ChunkModel` → `ChunkRow`），从该模块导入：

```python
from src.infra.db.vector_store.mapping import ChunkRow, row_to_chunk_row
```

> ⚠ `ChunkModel.embedding.cosine_distance(...)` 由 `pgvector.sqlalchemy.Vector` 提供（`<=>`）。**不要**手写 `text("embedding <=> :q")`——ORM 会在迁移/比较上给出更早的错误。

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_repo.py -v`
Expected: PASS。

- [ ] **Step 6: D1 门禁——ORM metadata 与 baseline 无漂移**

```bash
POSTGRES_HOST=localhost .venv/bin/alembic revision --autogenerate -m "p2-drift-check"
git diff --stat alembic/versions/
```

预期：生成的迁移文件对 `chunks` **没有任何 `op.` 调用**（可能为空迁移或只含无关内容）。
**若有 `chunks` 相关改动**：说明 `ChunkModel` 与 baseline 不一致 → 逐列对照修正模型（不是改 baseline），然后删除该临时迁移文件重跑。

清理临时迁移：

```bash
rm -f alembic/versions/*p2_drift_check*.py alembic/versions/*p2-drift-check*.py
git status --short alembic/
```

- [ ] **Step 7: 提交**

```bash
git add src/infra/db/models/chunk.py src/infra/db/mysql_db/chunk_repo.py \
        src/infra/db/vector_store/mapping.py tests/infra/db/test_chunk_repo.py
git commit -m "feat(db): 新增 chunks 的 ORM 模型与 ChunkRepo（含 pgvector 余弦检索）"
```

---

### Task 3: 行 ↔ `ChunkResult` 映射与 `metadata` 回填契约（纯函数）

**Files:**
- Create: `src/infra/db/vector_store/mapping.py`
- Test: `tests/infra/db/test_chunk_mapping.py`（新建）

**Interfaces:**
- Consumes: `src/chunking/validator.py` 的 `ChunkData`；`src/infra/db/models/chunk.py` 的 `ChunkModel`（仅类型）
- Produces（Task 2、5、6、7 全都消费本模块，**不得各自实现**）：
  - `CONTRACT_KEYS: tuple[str, ...]`
  - `@dataclass ChunkRow`（11 字段）
  - `@dataclass SplitMetadata`（`extra` / `source` / `page`）
  - `def split_metadata(metadata: dict) -> SplitMetadata`
  - `def build_rows(kb_id: str, doc_id: str, chunks: list[ChunkData], embeddings: list[list[float]]) -> list[ChunkRow]`
  - `def row_to_chunk_row(model) -> ChunkRow`
  - `def row_to_chunk_result(row: ChunkRow, *, distance=None, lexical_score=None, dense_rank=None, sparse_rank=None) -> ChunkResult`

> ⚠ **本任务是 P2 风险最高的地方**（F1）。漏回填不会报错：去重对所有结果取 `None` → 走「无 doc_id 则保留」分支 → 同文档分块占满候选窗口；同时引用与实体透传一并变空。所以本条契约必须由**一个**模块承担，并用单测钉死。

- [ ] **Step 1: 先只落地 `ChunkRow` / `SplitMetadata` / `CONTRACT_KEYS`（供 Task 2 导入）**

> 执行顺序提示：若本任务尚未开始而 Task 2 已被派发，控制器会要求先做本步。

- [ ] **Step 2: 写失败测试**

新建 `tests/infra/db/test_chunk_mapping.py`：

```python
"""metadata 回填契约的单测 —— 不碰数据库、不碰 Chroma。"""

from src.chunking.validator import ChunkData
from src.infra.db.vector_store.mapping import (
    CONTRACT_KEYS,
    ChunkRow,
    build_rows,
    row_to_chunk_result,
    split_metadata,
)


def test_contract_keys_are_the_five_agreed_names():
    assert CONTRACT_KEYS == ("doc_id", "chunk_index", "chunk_total", "source", "page")


def test_split_metadata_keeps_custom_keys_verbatim():
    """jsonb 必须原样承载 chunker 的自定义键（含 parent_content）。"""
    split = split_metadata(
        {
            "source": "茅台2024.pdf",
            "page": 7,
            "parent_content": "母公司报表",
            "block_type": "table",
            "heading_path": ["一、经营情况"],
            "doc_id": "should-be-dropped",
        }
    )
    assert split.source == "茅台2024.pdf"
    assert split.page == 7
    assert split.extra == {
        "parent_content": "母公司报表",
        "block_type": "table",
        "heading_path": ["一、经营情况"],
    }


def test_split_metadata_defaults_and_coercion():
    """缺 source/page 时给契约默认值；page 强制成 int（Chroma 侧可能是 float）。"""
    split = split_metadata({})
    assert split.source == ""
    assert split.page == 0
    assert split.extra == {}

    split2 = split_metadata({"page": 3.0})
    assert split2.page == 3
    assert isinstance(split2.page, int)


def test_build_rows_sets_columns_and_placeholder_segment():
    """行形状：id 格式、chunk_total、content_seg 占位（P2 写原文）。"""
    chunks = [
        ChunkData(content="第一段", metadata={"source": "a.pdf", "page": 1}, chunk_id="a:0"),
        ChunkData(content="第二段", metadata={"source": "a.pdf", "page": 2, "parent_content": "P"}, chunk_id="a:1"),
    ]
    rows = build_rows("kb1", "doc1", chunks, [[0.1] * 1024, [0.2] * 1024])
    assert [r.id for r in rows] == ["doc1:0", "doc1:1"]
    assert [r.chunk_index for r in rows] == [0, 1]
    assert all(r.chunk_total == 2 for r in rows)
    assert [r.content_seg for r in rows] == ["第一段", "第二段"]
    assert rows[1].extra == {"parent_content": "P"}
    assert rows[1].source == "a.pdf"
    assert rows[1].page == 2


def test_row_to_chunk_result_backfills_columns_and_jsonb():
    """回填：列值 + jsonb 平铺；5 个契约键必须可读（去重/引用/实体透传依赖它们）。"""
    row = ChunkRow(
        id="doc1:3",
        kb_id="kb1",
        doc_id="doc1",
        chunk_index=3,
        chunk_total=9,
        content="正文",
        content_seg="正文",
        embedding=None,
        source="茅台2024.pdf",
        page=12,
        extra={"parent_content": "母公司报表", "block_type": "table"},
    )
    result = row_to_chunk_result(row, distance=0.25, dense_rank=2)
    assert result.id == "doc1:3"
    assert result.content == "正文"
    assert result.distance == 0.25
    assert result.dense_rank == 2
    assert result.lexical_score is None
    assert result.sparse_rank is None
    for key in CONTRACT_KEYS:
        assert key in result.metadata
    assert result.metadata["doc_id"] == "doc1"
    assert result.metadata["chunk_index"] == 3
    assert result.metadata["chunk_total"] == 9
    assert result.metadata["source"] == "茅台2024.pdf"
    assert result.metadata["page"] == 12
    assert result.metadata["parent_content"] == "母公司报表"
    assert result.metadata["block_type"] == "table"


def test_row_to_chunk_result_column_wins_on_conflict():
    """冲突以列为准：jsonb 里若混进契约键，列值覆盖它。"""
    row = ChunkRow(
        id="doc1:0",
        kb_id="kb1",
        doc_id="doc1",
        chunk_index=0,
        chunk_total=1,
        content="正文",
        content_seg="正文",
        embedding=None,
        source="列里的.pdf",
        page=1,
        extra={"source": "jsonb里的.pdf", "page": 999, "doc_id": "伪造"},
    )
    result = row_to_chunk_result(row)
    assert result.metadata["source"] == "列里的.pdf"
    assert result.metadata["page"] == 1
    assert result.metadata["doc_id"] == "doc1"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_mapping.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.infra.db.vector_store.mapping`。

- [ ] **Step 4: 实现 `mapping.py`**

```python
"""chunks 行 ↔ ChunkResult 的映射 —— metadata 回填契约的唯一归属。

读取时 ChunkResult.metadata 由【列值 + jsonb 平铺合并】得到，冲突以列为准，
至少含 doc_id / chunk_index / chunk_total / source / page；jsonb 侧原样承载
chunker 产出的全部自定义键（parent_content / block_type / heading_path / ...）。

写入侧（split_metadata / build_rows）、读取侧（row_to_chunk_result）与
搬迁脚本必须共用本模块：三处各自实现会让契约漂移，而漂移不会报错 ——
它只会让按 doc_id 去重、引用渲染（source/page）与实体透传静默失效。
"""

from dataclasses import dataclass, field

from src.chunking.validator import ChunkData
from src.infra.db.vector_store.types import ChunkResult

CONTRACT_KEYS: tuple[str, ...] = ("doc_id", "chunk_index", "chunk_total", "source", "page")


@dataclass
class SplitMetadata:
    """把 chunker 的 metadata 拆成「上列的契约键」与「进 jsonb 的自定义键」。"""

    extra: dict
    """进 jsonb 的自定义键（不含任何契约键）。"""
    source: str
    """来源文件名列值；缺失时为空串。"""
    page: int
    """页码列值；缺失时为 0。"""


@dataclass
class ChunkRow:
    """chunks 表的一行 —— 读、写两侧共用的形状。"""

    id: str
    """分块 ID，格式 {doc_id}:{chunk_index}。"""
    kb_id: str
    """所属知识库 ID（外键 → knowledge_base.id）。"""
    doc_id: str
    """所属文档 ID。"""
    chunk_index: int
    """该文档内的分块序号，0 起。"""
    chunk_total: int
    """该文档的分块总数。"""
    content: str
    """分块正文原文。"""
    content_seg: str
    """词法检索文本；P2 写正文原值作占位，P3 换成分词输出并全量重写。"""
    embedding: list[float] | None
    """1024 维向量；None 表示尚未算好（不应入库）。"""
    source: str
    """来源文件名（契约键，升为列）。"""
    page: int
    """页码（契约键，升为列）。"""
    extra: dict
    """chunker 的自定义键（jsonb 列 metadata 的内容）。"""


def split_metadata(metadata: dict) -> SplitMetadata:
    """把 metadata 拆成契约键（上列）与自定义键（进 jsonb）。

    Args:
        metadata: chunker 或搬迁脚本给出的元数据字典

    Returns:
        SplitMetadata(extra=自定义键, source=文件名, page=页码)

    Note:
        契约键一律**从 extra 中剔除**（含 doc_id / chunk_index / chunk_total，
        它们以函数参数为准，不从 metadata 取），避免 jsonb 里出现与列冲突的副本。
    """
    extra = dict(metadata)
    source = extra.pop("source", "")
    page_raw = extra.pop("page", 0)
    for key in CONTRACT_KEYS:
        extra.pop(key, None)
    if not isinstance(source, str):
        source = str(source)
    if isinstance(page_raw, bool) or page_raw is None:
        page = 0
    elif isinstance(page_raw, int):
        page = page_raw
    elif isinstance(page_raw, float):
        page = int(page_raw)
    else:
        page = 0
    return SplitMetadata(extra=extra, source=source, page=page)


def build_rows(
    kb_id: str,
    doc_id: str,
    chunks: list[ChunkData],
    embeddings: list[list[float]],
) -> list[ChunkRow]:
    """把分块与向量组装成待写入的 chunks 行。

    Args:
        kb_id: 所属知识库 ID
        doc_id: 所属文档 ID
        chunks: 分块列表
        embeddings: 与 chunks 一一对应的 1024 维向量

    Returns:
        待写入的 ChunkRow 列表（顺序即 chunk_index 顺序）

    Raises:
        ValueError: chunks 与 embeddings 长度不一致时
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks 与 embeddings 数量不一致: {len(chunks)} != {len(embeddings)}"
        )
    total = len(chunks)
    rows: list[ChunkRow] = []
    for i, chunk in enumerate(chunks):
        split = split_metadata(chunk.metadata)
        rows.append(
            ChunkRow(
                id=f"{doc_id}:{i}",
                kb_id=kb_id,
                doc_id=doc_id,
                chunk_index=i,
                chunk_total=total,
                content=chunk.content,
                # P2 占位：content_seg 写正文原值，P3 换成 jieba 分词输出并全量重写
                content_seg=chunk.content,
                embedding=embeddings[i],
                source=split.source,
                page=split.page,
                extra=split.extra,
            )
        )
    return rows


def row_to_chunk_row(model) -> ChunkRow:
    """把 ChunkModel（或任何具备同名属性的对象）转成 ChunkRow。"""
    embedding = None
    if model.embedding is not None:
        embedding = list(model.embedding)
    extra = {}
    if model.extra is not None:
        extra = dict(model.extra)
    return ChunkRow(
        id=model.id,
        kb_id=model.kb_id,
        doc_id=model.doc_id,
        chunk_index=model.chunk_index,
        chunk_total=model.chunk_total,
        content=model.content,
        content_seg=model.content_seg,
        embedding=embedding,
        source=model.source,
        page=model.page,
        extra=extra,
    )


def row_to_chunk_result(
    row: ChunkRow,
    *,
    distance: float | None = None,
    lexical_score: float | None = None,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """把一行映射成 ChunkResult，并按契约回填 metadata（列值优先）。

    Args:
        row: chunks 行
        distance: 余弦距离（dense 路才有）
        lexical_score: 词法得分（词法路才有）
        dense_rank: dense 路排名
        sparse_rank: 词法路排名

    Returns:
        ChunkResult，其 metadata 含 5 个契约键 + jsonb 的全部自定义键
    """
    metadata = dict(row.extra)
    # 列值后写 = 冲突以列为准
    metadata["doc_id"] = row.doc_id
    metadata["chunk_index"] = row.chunk_index
    metadata["chunk_total"] = row.chunk_total
    metadata["source"] = row.source
    metadata["page"] = row.page
    return ChunkResult(
        id=row.id,
        content=row.content,
        metadata=metadata,
        distance=distance,
        lexical_score=lexical_score,
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_mapping.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 6: 回到 Task 2 的 Step 4/5 完成 `ChunkRepo` 并跑通其测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_chunk_mapping.py tests/infra/db/test_chunk_repo.py -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/infra/db/vector_store/mapping.py tests/infra/db/test_chunk_mapping.py
git commit -m "feat(db): chunks 行映射与 metadata 回填契约（列值优先，jsonb 原样承载自定义键）"
```

---

### Task 4: 删除全局检索与无调用方入口

**Files:**
- Modify: `src/infra/db/vector_store/__init__.py:86-132`（删两个方法）
- Modify: `src/infra/db/vector_store/search.py:75-113`（删实现）
- Modify: `src/rag/retrieval.py:98-116`（删 `not kb_id` 分支）
- Test: `tests/infra/db/test_vector_store.py:110-133`（删 `test_similarity_search_all`；`test_similarity_search_all_no_collections` 一并删）
- Test: `tests/rag/test_retrieval.py:72-83`（删 `test_search_all_when_no_kb`）
- Modify: `docs/agents/api_contract.md`（§4 VectorStore 契约）

**Interfaces:**
- Consumes: 无
- Produces: `VectorStore` 不再有 `similarity_search_all` / `similarity_search_multi`；`retrieval.search(query, kb_id, vector_store, bm25=None)` 只走单库路径（签名的 `bm25` 形参保留到 P3）

- [ ] **Step 1: 写失败测试（契约守卫）**

在 `tests/infra/db/test_vector_store.py` 顶部（class 之外）加：

```python
def test_global_retrieval_entries_are_gone():
    """全局检索路径已移除：不指定知识库的检索在生产链路上不可达，只被测试养着。"""
    vs = VectorStore.__dict__
    assert "similarity_search_all" not in vs
    assert "similarity_search_multi" not in vs


def test_search_source_has_no_global_branch():
    """retrieval.search 源码里不得再出现 not kb_id 的全局分支。"""
    import inspect

    from src.rag import retrieval

    src = inspect.getsource(retrieval.search)
    assert "similarity_search_all" not in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store.py -k global_retrieval -v`
Expected: FAIL（`similarity_search_all` 仍在）。

- [ ] **Step 3: 删实现**

- `src/infra/db/vector_store/search.py`：删除 `similarity_search_all` 整个函数（第 75-113 行）。同时删掉它专用的 import（若 `TOP_K_RETRIEVAL` 不再被本文件使用）。
- `src/infra/db/vector_store/__init__.py`：删除 `similarity_search_all`（第 86-107 行）与 `similarity_search_multi`（第 109-132 行）两个方法。

- [ ] **Step 4: 删 `retrieval.py` 的全局分支**

把 `src/rag/retrieval.py:98-116` 改成：

```python
    results = await asyncio.to_thread(
        vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
    )
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

同时把 `search()` 的 docstring 第 75 行「kb_id: 知识库 ID，为空时执行全局检索」改为「kb_id: 知识库 ID（调用方保证非空：`rag_tools.py` 在 kb_id 为空时直接返回空结果）」。

- [ ] **Step 5: 删对应测试**

- `tests/infra/db/test_vector_store.py`：删 `test_similarity_search_all`（:110-133）与 `test_similarity_search_all_no_collections`（:135-138）。
- `tests/rag/test_retrieval.py`：删 `test_search_all_when_no_kb`（:72-83）。

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store.py tests/rag/test_retrieval.py -q`
Expected: PASS（已删掉的用例自然不再计入）。

- [ ] **Step 7: 同步 `api_contract.md`**

在 `docs/agents/api_contract.md` 的 `VectorStore` 契约章节（§4 起）：
- 删除 `similarity_search_all(query, k)` 与 `similarity_search_multi(kb_ids, query, k)` 两行。
- 加一句当前状态说明：**「全局检索路径已移除。`rag_tools` 在 `kb_id` 为空时直接返回空结果，不再有『不指定知识库』的检索入口。」**

- [ ] **Step 8: 提交**

```bash
git add src/infra/db/vector_store/__init__.py src/infra/db/vector_store/search.py \
        src/rag/retrieval.py tests/infra/db/test_vector_store.py tests/rag/test_retrieval.py \
        docs/agents/api_contract.md
git commit -m "refactor(retrieval): 删除不可达的全局检索入口（similarity_search_all/multi）"
```

---

### Task 5: `PgVectorStore` 写入路径（async）

**Files:**
- Create: `src/infra/db/vector_store/pg_store.py`
- Test: `tests/infra/db/test_pg_vector_store_write.py`（新建）

**Interfaces:**
- Consumes: `ChunkRepo`（Task 2）、`build_rows`（Task 3）、`get_embeddings()`（`src/models.py:113`）
- Produces:
  - `class QueryEmbedder`（`embed_query(text) -> list[float]`、`embed_documents(list[str]) -> list[list[float]]`）
  - `class PgVectorStore`：`__init__(self, chunk_repo: ChunkRepo | None = None, embed_fn: QueryEmbedder | None = None)`；本任务只实现 `async def add_chunks(self, kb_id: str, chunks: list[ChunkData], doc_id: str, embeddings: list[list[float]] | None = None) -> int`

> ⚠ 本任务**不改**任何调用点：应用此刻仍用 Chroma。`PgVectorStore` 是并行新增的后端（Ruling 1 / F10），Task 9 才切换装配。

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_pg_vector_store_write.py`：

```python
"""PgVectorStore 写入路径（打真实 PG，embedding 用假的，不发网络）。"""

import uuid

import pytest
from sqlalchemy import text

from src.chunking.validator import ChunkData
from src.infra.db.engine import session_factory
from src.infra.db.vector_store.pg_store import PgVectorStore


class FakeEmbedder:
    """确定性假向量：按文本长度区分方向，便于断言排序。"""

    def embed_query(self, text: str) -> list[float]:
        vec = [0.0] * 1024
        vec[len(text) % 1024] = 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]


@pytest.fixture
async def store_and_kb():
    """建真实 KB 行（chunks 有外键），返回 (PgVectorStore, kb_id)，测后清理。"""
    kb_id = f"p2write-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p2-{kb_id[-6:]}"},
        )
        await s.commit()
    store = PgVectorStore(embed_fn=FakeEmbedder())
    yield store, kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def _count(kb_id: str) -> int:
    async with session_factory() as s:
        return int(
            await s.scalar(text("SELECT count(*) FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        )


async def test_add_chunks_writes_rows_with_columns_and_jsonb(store_and_kb):
    """入库：行落库、契约键升列、自定义键进 jsonb。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content="第一段", metadata={"source": "a.pdf", "page": 1, "parent_content": "P"}, chunk_id="x:0"),
        ChunkData(content="第二段", metadata={"source": "a.pdf", "page": 2}, chunk_id="x:1"),
    ]
    assert await store.add_chunks(kb_id, chunks, doc_id, store._embed_fn.embed_documents([c.content for c in chunks])) == 2
    assert await _count(kb_id) == 2
    async with session_factory() as s:
        row = (
            await s.execute(
                text("SELECT source, page, metadata, chunk_index, chunk_total FROM chunks"
                     " WHERE kb_id = :k AND chunk_index = 0"),
                {"k": kb_id},
            )
        ).one()
    assert row[0] == "a.pdf"
    assert row[1] == 1
    assert row[2] == {"parent_content": "P"}  # 契约键不得混进 jsonb
    assert row[3] == 0
    assert row[4] == 2


async def test_add_chunks_is_idempotent_and_replaces_content(store_and_kb):
    """同 doc 重传（Ruling 2）：覆盖内容，不产生重复行。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    first = [ChunkData(content="旧内容", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(kb_id, first, doc_id, store._embed_fn.embed_documents(["旧内容"]))
    second = [ChunkData(content="新内容", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(kb_id, second, doc_id, store._embed_fn.embed_documents(["新内容"]))
    assert await _count(kb_id) == 1
    async with session_factory() as s:
        content = await s.scalar(
            text("SELECT content FROM chunks WHERE kb_id = :k AND chunk_index = 0"), {"k": kb_id}
        )
    assert content == "新内容"


async def test_add_chunks_removes_tail_when_chunk_count_shrinks(store_and_kb):
    """重传后分块数变少：尾部残留必须删掉（Ruling 2 的已知残留）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    three = [ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}") for i in range(3)]
    await store.add_chunks(kb_id, three, doc_id, store._embed_fn.embed_documents([c.content for c in three]))
    assert await _count(kb_id) == 3
    one = [ChunkData(content="只剩一段", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(kb_id, one, doc_id, store._embed_fn.embed_documents(["只剩一段"]))
    assert await _count(kb_id) == 1


async def test_add_chunks_empty_returns_zero(store_and_kb):
    store, kb_id = store_and_kb
    assert await store.add_chunks(kb_id, [], uuid.uuid4().hex, []) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_pg_vector_store_write.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.infra.db.vector_store.pg_store`。

- [ ] **Step 3: 实现 `pg_store.py` 的写入路径**

```python
"""dense 检索的 PostgreSQL + pgvector 后端。

与 Chroma 后端（client.py / store.py / search.py）的关系：二者接口相同，
P2 期间并存；等价性验收通过后由 Task 9 切换装配并删除 Chroma 实现。

契约要点：
- distance 是**余弦距离**（pgvector `<=>`），消费方用 score = 1 - distance；
- k 的上限仍是 100 —— 该上限源自 Chroma（vector_store/search.py:44），
  PG 无此限制，这里保留它是为了不把「能力提升」混进迁移等价性验收。
"""

import asyncio

from loguru import logger

from src.chunking.validator import ChunkData
from src.core import logging as core_logging
from src.core.log_events import Event
from src.core.logging import LOG_MAX_BODY
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import build_rows
from src.models import get_embeddings

# Chroma 后端沿用的硬上限（vector_store/search.py:44）。PG 无此限制，
# 保留它是为了让等价性验收只度量「存储替换」，不混入能力变化。
MAX_QUERY_K = 100


class QueryEmbedder:
    """查询/文档的向量化入口 —— 薄封装，便于测试注入假实现。"""

    def embed_query(self, text: str) -> list[float]:
        """把查询文本向量化。

        Args:
            text: 查询文本

        Returns:
            1024 维向量
        """
        return get_embeddings().embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本。

        Args:
            texts: 文本列表

        Returns:
            与输入一一对应的向量列表
        """
        return get_embeddings().embed_documents(texts)


class PgVectorStore:
    """分块向量存储的 PostgreSQL 后端（单表 + kb_id 列，无 collection 概念）。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo | None = None,
        embed_fn: QueryEmbedder | None = None,
    ) -> None:
        """初始化。

        Args:
            chunk_repo: chunks 表访问层；缺省时用应用默认 session_factory 构造
            embed_fn: 向量化入口；缺省时用 QueryEmbedder（DashScope）
        """
        if chunk_repo is None:
            from src.infra.db.engine import session_factory

            chunk_repo = ChunkRepo(session_factory)
        self._repo = chunk_repo
        if embed_fn is None:
            embed_fn = QueryEmbedder()
        self._embed_fn = embed_fn

    async def add_chunks(
        self,
        kb_id: str,
        chunks: list[ChunkData],
        doc_id: str,
        embeddings: list[list[float]] | None = None,
    ) -> int:
        """批量写入分块（按 (kb_id, doc_id, chunk_index) 幂等覆盖）。

        Args:
            kb_id: 知识库 ID
            chunks: 分块数据列表
            doc_id: 文档 ID
            embeddings: 预计算向量；为 None 时在此处补算（document_service 恒预计算，
                因此正常路径不会走到这里；保留它是为了不改变既有方法契约）

        Returns:
            实际写入的分块数量
        """
        if not chunks:
            return 0
        if embeddings is None:
            # 向量化是同步的 HTTP 调用 → 必须 offload，否则阻塞事件循环（单 worker 下会冻住所有请求与 SSE）
            embeddings = await asyncio.to_thread(
                self._embed_fn.embed_documents, [c.content for c in chunks]
            )
        rows = build_rows(kb_id, doc_id, chunks, embeddings)
        await self._repo.upsert_chunks(rows)
        # 分块数变少时删掉尾部残留（upsert 只覆盖 [0, len(rows)) 区间）
        await self._repo.delete_tail(kb_id, doc_id, len(rows))
        core_logging.log_event(
            Event.CHUNKS_ADDED, kb_id=kb_id, doc_id=doc_id, count=len(rows)
        )
        logger.debug(
            "[PG] method=add_chunks | kb_id={} | doc_id={} | rows={} | data={}",
            kb_id,
            doc_id,
            len(rows),
            str(rows)[:LOG_MAX_BODY] if rows else "[]",
        )
        return len(rows)
```

> ⚠ 上面的 import 里 **`Event` / `log_event` / `LOG_MAX_BODY` 的确切模块路径以仓库现状为准**：
> 直接把 `vector_store/search.py` 顶部的同类导入照抄过来即可（Chroma 侧已在用这三个名字）。
> **不要**新建事件；`add_chunks` 复用 `Event.CHUNKS_ADDED`、`dense_search` 复用 `Event.SEARCH_RESULT`、
> `get_all_chunks` 复用 `Event.CHUNKS_READ`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_pg_vector_store_write.py -v`
Expected: PASS（4 passed）。

- [ ] **Step 5: 提交**

```bash
git add src/infra/db/vector_store/pg_store.py tests/infra/db/test_pg_vector_store_write.py
git commit -m "feat(vector-store): 新增 PG 后端的写入路径（pgvector upsert + 尾部残留清理）"
```

---

### Task 6: `PgVectorStore` 读取路径（async）

**Files:**
- Modify: `src/infra/db/vector_store/pg_store.py`
- Test: `tests/infra/db/test_pg_vector_store_read.py`（新建）

**Interfaces:**
- Consumes: `ChunkRepo.search_dense / get_by_doc / get_paginated / get_by_kb / list_kb_ids / delete_by_doc / delete_by_kb`（Task 2）；`row_to_chunk_result`（Task 3）
- Produces: `PgVectorStore` 的其余方法（全部 async）：
  - `async def similarity_search(self, kb_id, query, k=5) -> list[ChunkResult]`（= `dense_search` 的别名，保留 D8 的 11 方法契约）
  - `async def dense_search(self, kb_id, query, k=5) -> list[ChunkResult]`（填充 `dense_rank`）
  - `async def get_chunks_by_doc_id(self, doc_id, kb_id) -> list[ChunkResult]`
  - `async def get_chunks_paginated(self, doc_id, kb_id, page=1, page_size=50) -> ChunkQueryResult`
  - `async def get_all_chunks(self, kb_id) -> list[ChunkResult]`
  - `async def list_collections(self) -> list[str]`
  - `async def delete_document(self, kb_id, doc_id) -> int`
  - `async def delete_collection(self, kb_id) -> bool`
  - `async def get_or_create_collection(self, kb_id) -> str`

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/db/test_pg_vector_store_read.py`（沿用 Task 5 的 `FakeEmbedder` 与 `store_and_kb` fixture，把它们提到一个共享的 `tests/infra/db/p2_fakes.py` 或本文件内重复定义 —— 二选一，但 Task 5/6 必须共用同一个假实现）：

```python
async def test_dense_search_orders_by_distance_and_ranks(store_and_kb):
    """dense 检索：按余弦距离升序、dense_rank 从 0 连续、limit 生效。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [ChunkData(content=f"文本{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}") for i in range(4)]
    embeddings = [[0.0] * 1024 for _ in chunks]
    embeddings[2][0] = 1.0  # 让第 3 个与查询向量（在轴 0）最接近
    await store.add_chunks(kb_id, chunks, doc_id, embeddings)

    results = await store.dense_search(kb_id, "查询", k=3)
    assert len(results) == 3
    assert [r.dense_rank for r in results] == [0, 1, 2]
    assert results[0].id == f"{doc_id}:2"
    assert all(r.distance is not None for r in results)
    assert results[0].distance <= results[1].distance <= results[2].distance


async def test_similarity_search_is_the_same_entry(store_and_kb):
    """similarity_search 与 dense_search 是同一实现的两个名字（D8 契约保留）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="唯一一段", metadata={"source": "a.pdf"}, chunk_id="x:0")],
        doc_id,
        [[1.0] + [0.0] * 1023],
    )
    a = await store.similarity_search(kb_id, "查询", k=5)
    b = await store.dense_search(kb_id, "查询", k=5)
    assert [r.id for r in a] == [r.id for r in b]


async def test_k_is_capped_at_100(store_and_kb):
    """上限 100 保留（F6）：请求 500 也只取 100。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}") for i in range(105)]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])
    results = await store.dense_search(kb_id, "查询", k=500)
    assert len(results) == 100


async def test_metadata_contract_is_backfilled_on_every_result(store_and_kb):
    """D4：每条结果的 metadata 都含 5 个契约键 + 自定义键。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="正文", metadata={"source": "茅台.pdf", "page": 5, "parent_content": "母公司"}, chunk_id="x:0")],
        doc_id,
        [[1.0] + [0.0] * 1023],
    )
    for result in await store.dense_search(kb_id, "查询", k=5):
        assert result.metadata["doc_id"] == doc_id
        assert result.metadata["chunk_index"] == 0
        assert result.metadata["chunk_total"] == 1
        assert result.metadata["source"] == "茅台.pdf"
        assert result.metadata["page"] == 5
        assert result.metadata["parent_content"] == "母公司"


async def test_get_chunks_by_doc_id_and_paginated(store_and_kb):
    """按 doc 取全量 / 分页；分路字段为 None。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}") for i in range(5)]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])

    all_chunks = await store.get_chunks_by_doc_id(doc_id, kb_id)
    assert [c.id for c in all_chunks] == [f"{doc_id}:{i}" for i in range(5)]
    assert all(c.distance is None and c.dense_rank is None for c in all_chunks)

    page = await store.get_chunks_paginated(doc_id, kb_id, page=2, page_size=2)
    assert page.total == 5
    assert page.page == 2
    assert page.page_size == 2
    assert [c.id for c in page.items] == [f"{doc_id}:2", f"{doc_id}:3"]


async def test_list_collections_returns_kbs_with_chunks_without_side_effects(store_and_kb):
    """枚举不产生副作用（hybrid-retrieval 的「遍历不产生副作用」）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="段0", metadata={"source": "a.pdf"}, chunk_id="x:0")],
        doc_id,
        [[0.0] * 1024],
    )
    names = await store.list_collections()
    assert kb_id in names
    # 再枚举一次，结果不变（没有「读时创建」）
    assert await store.list_collections() == names


async def test_delete_document_and_collection(store_and_kb):
    """删除路径：按 doc 删除返回行数；按 kb 删除返回布尔。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}") for i in range(3)]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])

    assert await store.delete_document(kb_id, doc_id) == 3
    assert await store.get_chunks_by_doc_id(doc_id, kb_id) == []
    assert await store.delete_collection(kb_id) is False  # 已无行


async def test_get_or_create_collection_is_side_effect_free(store_and_kb):
    """PG 没有 collection：该方法退化为返回 kb_id，且不写任何数据。"""
    store, kb_id = store_and_kb
    assert await store.get_or_create_collection(kb_id) == kb_id
    async with session_factory() as s:
        n = await s.scalar(text("SELECT count(*) FROM chunks WHERE kb_id = :k"), {"k": kb_id})
    assert n == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_pg_vector_store_read.py -v`
Expected: FAIL —— `AttributeError: 'PgVectorStore' object has no attribute 'dense_search'`。

- [ ] **Step 3: 实现读取路径**

追加到 `src/infra/db/vector_store/pg_store.py`（`PgVectorStore` 类内）：

```python
    async def dense_search(self, kb_id: str, query: str, k: int = 5) -> list[ChunkResult]:
        """dense 路取 top-k（余弦距离升序），并填充 dense_rank。

        Args:
            kb_id: 知识库 ID
            query: 查询文本
            k: 返回条数上限（内部再按 MAX_QUERY_K 截断）

        Returns:
            按余弦距离升序的 ChunkResult；每项 distance 有值、dense_rank 为 0 起的名次
        """
        effective_k = min(k, MAX_QUERY_K)
        query_vec = await asyncio.to_thread(self._embed_fn.embed_query, query)
        pairs = await self._repo.search_dense(kb_id, query_vec, effective_k)
        results = [
            row_to_chunk_result(row, distance=distance, dense_rank=rank)
            for rank, (row, distance) in enumerate(pairs)
        ]
        core_logging.log_event(
            Event.SEARCH_RESULT,
            kb_id=kb_id,
            query_len=len(query),
            result_count=len(results),
        )
        logger.debug(
            "[PG] method=dense_search | kb_id={} | rows={} | data={}",
            kb_id,
            len(results),
            str(results)[:LOG_MAX_BODY] if results else "[]",
        )
        return results

    async def similarity_search(self, kb_id: str, query: str, k: int = 5) -> list[ChunkResult]:
        """dense 检索入口（dense_search 的别名，保留既有方法名与语义）。

        语义与 Chroma 后端一致：余弦**距离**，越小越相似；消费方用 score = 1 - distance。
        """
        return await self.dense_search(kb_id, query, k=k)

    async def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        """取某文档的全部分块（分路字段为 None）。"""
        rows = await self._repo.get_by_doc(doc_id, kb_id)
        return [row_to_chunk_result(row) for row in rows]

    async def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        """分页取某文档的分块。"""
        rows, total = await self._repo.get_paginated(doc_id, kb_id, page, page_size)
        return ChunkQueryResult(
            items=[row_to_chunk_result(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_all_chunks(self, kb_id: str) -> list[ChunkResult]:
        """取整个知识库的全部分块（BM25 全量重建用）。"""
        rows = await self._repo.get_by_kb(kb_id)
        core_logging.log_event(Event.CHUNKS_READ, kb_id=kb_id, count=len(rows))
        return [row_to_chunk_result(row) for row in rows]

    async def list_collections(self) -> list[str]:
        """枚举含分块的知识库 ID（PG 无 collection，语义是「有哪些 kb 有分块」）。

        只读，不创建任何东西。
        """
        return await self._repo.list_kb_ids()

    async def delete_document(self, kb_id: str, doc_id: str) -> int:
        """删除某文档的全部分块，返回删除行数。"""
        return await self._repo.delete_by_doc(kb_id, doc_id)

    async def delete_collection(self, kb_id: str) -> bool:
        """删除某知识库的全部分块；返回是否删除了行。"""
        deleted = await self._repo.delete_by_kb(kb_id)
        return deleted > 0

    async def get_or_create_collection(self, kb_id: str) -> str:
        """PG 无 collection 概念：该方法不产生副作用，直接返回 kb_id。

        保留是为了维持既有方法名契约；调用方不应依赖它「创建」任何东西。
        """
        return kb_id
```

补充 import（文件头）：`import asyncio`、`from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult`、`from src.infra.db.vector_store.mapping import build_rows, row_to_chunk_result`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_pg_vector_store_write.py tests/infra/db/test_pg_vector_store_read.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/infra/db/vector_store/pg_store.py tests/infra/db/test_pg_vector_store_read.py
git commit -m "feat(vector-store): PG 后端读取路径（pgvector 余弦 top-k + metadata 回填 + 分路排名）"
```

---

### Task 7: Chroma → PG 一次性搬迁（幂等可重跑）

**Files:**
- Create: `scripts/migrate_chroma_to_pg.py`
- Test: `tests/scripts/test_migrate_chroma_to_pg.py`（新建；只测纯函数，不连库不连 Chroma）

**Interfaces:**
- Consumes: `split_metadata`（Task 3，**必须共用**，不得另写一套拆分逻辑）；`ChunkRepo`（Task 2）
- Produces:
  - `def collection_name_to_kb_id(name: str) -> str`
  - `def chroma_record_to_row(record: ChromaRecord) -> ChunkRow`（纯函数，`ChromaRecord` 是本模块的 dataclass）
  - `async def ensure_kb_row(kb_id: str) -> None`
  - `async def migrate(dry_run: bool = False) -> dict`

> ⚠ **kb_id 的还原限制（必须写进报告）**：Chroma 的 collection 名是 `kb_<hex>`，其中 `<hex>` 是 **kb_id 去掉连字符**后的 32 位十六进制（`client.py:_collection_name` 做 `kb_id.replace("-", "")`）。**去连字符不可逆** —— 原始 UUID 的连字符位置无法还原。因此搬迁时 `kb_id := collection 名去掉前缀的 32 位串`，并**由脚本自己创建对应的 `knowledge_base` 行**（Ruling 3），使外键自洽。等价性验收只需要「同一批分块在两侧可比」，不依赖 kb_id 与原 UUID 相同。
> ⚠ **搬迁必须在 Task 9 之前完成**（`VectorStore` 仍指向 Chroma 时 Chroma 侧才是权威语料；Task 9 之后 Chroma 不再被写入，冻结）。

- [ ] **Step 1: 写失败测试（纯函数）**

新建 `tests/scripts/test_migrate_chroma_to_pg.py`：

```python
"""搬迁脚本的纯函数部分：Chroma 记录 → ChunkRow。"""

from scripts.migrate_chroma_to_pg import ChromaRecord, chroma_record_to_row, collection_name_to_kb_id


def test_collection_name_to_kb_id_strips_prefix():
    assert collection_name_to_kb_id("kb_53890512f25245bf948525b4253cb4f1") == (
        "53890512f25245bf948525b4253cb4f1"
    )


def test_record_to_row_splits_contract_keys_and_keeps_custom_keys():
    """契约键升列、自定义键整包进 jsonb（parent_content 必须活下来）。"""
    record = ChromaRecord(
        id="docabc:7",
        document="贵州茅台2024年营业收入1741亿元",
        metadata={
            "doc_id": "docabc",
            "chunk_index": 7,
            "chunk_total": 12,
            "source": "茅台2024.pdf",
            "page": 3,
            "parent_content": "母公司报表",
            "block_type": "table",
        },
        embedding=[0.1] * 1024,
    )
    row = chroma_record_to_row(record)
    assert row.id == "docabc:7"
    assert row.doc_id == "docabc"
    assert row.chunk_index == 7
    assert row.chunk_total == 12
    assert row.source == "茅台2024.pdf"
    assert row.page == 3
    assert row.extra == {"parent_content": "母公司报表", "block_type": "table"}
    assert row.content_seg == "贵州茅台2024年营业收入1741亿元"  # P2 占位：原文
    assert len(row.embedding) == 1024


def test_record_to_row_falls_back_to_id_suffix_for_missing_chunk_index():
    """Chroma 侧 metadata 缺 chunk_index 时，从 id 的 {doc_id}:{i} 后缀还原。"""
    record = ChromaRecord(
        id="docabc:4",
        document="正文",
        metadata={"doc_id": "docabc", "chunk_total": 9},
        embedding=[0.0] * 1024,
    )
    row = chroma_record_to_row(record)
    assert row.chunk_index == 4
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_migrate_chroma_to_pg.py -v`
Expected: FAIL —— `ModuleNotFoundError: scripts.migrate_chroma_to_pg`。

- [ ] **Step 3: 实现脚本**

`scripts/migrate_chroma_to_pg.py`：

```python
"""一次性搬迁：ChromaDB 的分块（含 embedding）→ PostgreSQL 的 chunks 表。

用途：让 dense 迁移等价性成为**可判定的差分** —— 语料与查询都不变，只换存储。
若走「重新入库」，分块与 embedding 都会重算，等价性就失去依据。

幂等：KB 行 ON CONFLICT DO NOTHING，chunks 行按 (kb_id, doc_id, chunk_index) 覆盖写，
因此可以反复重跑（例如等价性不达标、修完再搬）。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
    POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py --dry-run
"""

import argparse
import asyncio
from dataclasses import dataclass

import chromadb
from loguru import logger
from sqlalchemy import text

from src.config.settings import CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import ChunkRow, split_metadata


@dataclass
class ChromaRecord:
    """Chroma 侧的一条分块记录（搬迁的输入形状）。"""

    id: str
    """Chroma 的 id，格式 {doc_id}:{chunk_index}。"""
    document: str
    """分块正文。"""
    metadata: dict
    """Chroma metadata（含 5 个契约键 + chunker 自定义键）。"""
    embedding: list[float]
    """1024 维向量。"""


def collection_name_to_kb_id(name: str) -> str:
    """从 collection 名还原 kb_id（去掉前缀的 32 位十六进制串）。

    Note:
        collection 名由 kb_id 去掉连字符后加前缀得到，该变换不可逆 ——
        因此这里得到的是「搬迁自造的 kb_id」，并由本脚本创建对应的
        knowledge_base 行使其外键自洽（见模块 docstring）。
    """
    if name.startswith(CHROMA_COLLECTION_PREFIX):
        return name[len(CHROMA_COLLECTION_PREFIX):]
    return name


def _chunk_index_from_id(chunk_id: str) -> int:
    """从 id 的 {doc_id}:{i} 后缀还原 chunk_index；无法解析时返回 0。"""
    _, _, suffix = chunk_id.rpartition(":")
    if suffix.isdigit():
        return int(suffix)
    return 0


def chroma_record_to_row(record: ChromaRecord) -> ChunkRow:
    """把一条 Chroma 记录转成 chunks 行（契约键升列、自定义键进 jsonb）。

    Args:
        record: Chroma 记录

    Returns:
        ChunkRow；kb_id 由调用方在写出前补齐（此处留空串占位）
    """
    split = split_metadata(record.metadata)
    chunk_index = record.metadata.get("chunk_index")
    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool):
        chunk_index = _chunk_index_from_id(record.id)
    chunk_total = record.metadata.get("chunk_total")
    if not isinstance(chunk_total, int) or isinstance(chunk_total, bool):
        chunk_total = 0
    doc_id = record.metadata.get("doc_id")
    if not isinstance(doc_id, str) or not doc_id:
        doc_id = record.id.rpartition(":")[0]
    return ChunkRow(
        id=record.id,
        kb_id="",  # 由 migrate() 回填
        doc_id=doc_id,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        content=record.document,
        content_seg=record.document,  # P2 占位：原文（P3 换分词输出并全量重写）
        embedding=list(record.embedding),
        source=split.source,
        page=split.page,
        extra=split.extra,
    )


async def ensure_kb_row(kb_id: str) -> None:
    """为该 kb_id 补建 knowledge_base 行（幂等）。

    仅用于搬迁：这些 KB 是**验收用的合成归属**（见 Ruling 3）。
    knowledge_base.user_id 无外键，故用一个标注性占位账号。
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2-migration', :n, :d, 0, 0)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"k": kb_id, "n": f"p2-equiv-{kb_id[:8]}", "d": "P2 dense 等价性验收用的合成知识库"},
        )
        await s.commit()


def _read_all_records() -> list[tuple[str, ChromaRecord]]:
    """直读 Chroma 的全部非空 collection，返回 (collection 名, 记录) 列表。"""
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    out: list[tuple[str, ChromaRecord]] = []
    for name in sorted(c.name for c in client.list_collections()):
        col = client.get_collection(name)
        if col.count() == 0:
            continue
        got = col.get(include=["documents", "metadatas", "embeddings"])
        ids = got["ids"]
        docs = got["documents"] or [""] * len(ids)
        metas = got["metadatas"] or [{}] * len(ids)
        embs = got["embeddings"]
        for i, cid in enumerate(ids):
            out.append(
                (
                    name,
                    ChromaRecord(
                        id=cid,
                        document=docs[i] if docs else "",
                        metadata=metas[i] if metas else {},
                        embedding=list(embs[i]),
                    ),
                )
            )
    return out


async def migrate(dry_run: bool = False) -> dict:
    """执行搬迁。

    Args:
        dry_run: 为真时只统计与校验，不写库

    Returns:
        统计字典：collections / records / kb_ids / written / dimension_mismatch
    """
    repo = ChunkRepo(session_factory)
    records = _read_all_records()
    by_kb: dict[str, list[ChunkRow]] = {}
    dimension_mismatch = 0
    for name, record in records:
        kb_id = collection_name_to_kb_id(name)
        row = chroma_record_to_row(record)
        row.kb_id = kb_id
        if len(row.embedding or []) != 1024:
            dimension_mismatch += 1
        by_kb.setdefault(kb_id, []).append(row)

    written = 0
    if not dry_run:
        for kb_id, rows in by_kb.items():
            await ensure_kb_row(kb_id)
            written += await repo.upsert_chunks(rows)

    stats = {
        "collections": len(by_kb),
        "records": len(records),
        "kb_ids": sorted(by_kb.keys()),
        "written": written,
        "dimension_mismatch": dimension_mismatch,
        "dry_run": dry_run,
    }
    logger.info("migrate chroma->pg done: {}", stats)
    return stats


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Chroma → PostgreSQL chunks 搬迁")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑单测确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_migrate_chroma_to_pg.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 先 dry-run，再实跑**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py --dry-run
POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
```

预期 dry-run：`collections: 5`、`records: 176`、`dimension_mismatch: 0`、`written: 0`。
预期实跑：`written: 176`。

- [ ] **Step 6: 独立核验落库结果**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*), count(DISTINCT kb_id), count(DISTINCT doc_id), count(*) FILTER (WHERE embedding IS NULL) FROM chunks;"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM chunks WHERE metadata ? 'parent_content';"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM chunks WHERE jsonb_exists_any(metadata, ARRAY['doc_id','chunk_index','chunk_total','source','page']);"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*) FROM chunks WHERE tsv IS NULL;"
```

预期：`176 | 5 | <doc 数> | 0`；`parent_content` 计数 **> 0**；契约键混进 jsonb 的计数 **0**；`tsv IS NULL` **0**。

- [ ] **Step 7: 幂等复跑验证**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/migrate_chroma_to_pg.py
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc "SELECT count(*) FROM chunks;"
```

预期：仍为 176（不产生重复行）。

- [ ] **Step 8: 提交**

```bash
git add scripts/migrate_chroma_to_pg.py tests/scripts/test_migrate_chroma_to_pg.py
git commit -m "feat(scripts): Chroma→PG 一次性搬迁（幂等、共用 metadata 拆分契约）"
```

---

### Task 8: 固定查询集 + dense 迁移等价性验收（Gate ≥ 0.9）

**Files:**
- Create: `tests/fixtures/dense_equivalence_queries.json`
- Create: `scripts/dense_equivalence_check.py`
- Create: `docs/tmp/p2-dense-equivalence-2026-09-19.md`（验收报告）
- Test: `tests/scripts/test_dense_equivalence_check.py`（新建；测重合率纯函数）

**Interfaces:**
- Consumes: `PgVectorStore.dense_search`（Task 6）；Chroma 原始客户端（不经 `VectorStore`，见 Ruling 4）；`QueryEmbedder`
- Produces:
  - `def overlap_ratio(dense_ids: list[str], chroma_ids: list[str], k: int) -> float`
  - `async def run_check(queries: list[QueryCase], k: int) -> CheckReport`
  - `tests/fixtures/dense_equivalence_queries.json`：`[{"kb": "<32位hex>", "query": "...", "category": "中文|数值|时间"}, ...]`

> ⚠ **spec 的硬要求**（`retrieval-quality`）：
> - 查询集 **≥20 条**、覆盖**中文 / 数值 / 时间**三类、**落盘可复现**（不得只存在于任务描述里）；
> - **只覆盖单知识库路径**；
> - `k` 取 `TOP_K_RETRIEVAL`；
> - 重合率 **≥ 0.9**；未达标**不得进入后续步骤**，先查 distance 语义与过滤条件。

- [ ] **Step 1: 读语料，写出查询集**

先看语料再写查询（查询必须落在真实文档上）：

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT kb_id, count(*) FROM chunks GROUP BY kb_id ORDER BY 2 DESC;"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT kb_id, left(content, 60) FROM chunks WHERE kb_id = '<某个 kb_id>' LIMIT 20;"
```

然后写 `tests/fixtures/dense_equivalence_queries.json`，形如：

```json
[
  {"kb": "53890512f25245bf948525b4253cb4f1", "query": "公司营业收入同比增长情况", "category": "中文"},
  {"kb": "53890512f25245bf948525b4253cb4f1", "query": "资产负债率", "category": "中文"},
  {"kb": "53890512f25245bf948525b4253cb4f1", "query": "1741亿元", "category": "数值"},
  {"kb": "53890512f25245bf948525b4253cb4f1", "query": "2024年", "category": "时间"},
  {"kb": "...", "query": "...", "category": "中文"}
]
```

要求（自查并写进报告）：
- **≥20 条**；
- 三条 category 各 **≥4 条**；
- 每条只指定**一个** kb（单库路径）；
- kb 取值来自上一步的真实 `kb_id` 列表；
- 至少 3 个不同的 kb（避免只验证一个集合）。

- [ ] **Step 2: 写失败测试（重合率纯函数）**

新建 `tests/scripts/test_dense_equivalence_check.py`：

```python
"""等价性比对的纯函数：top-k 重合率。"""

from scripts.dense_equivalence_check import overlap_ratio


def test_overlap_ratio_identical_is_one():
    assert overlap_ratio(["a", "b", "c"], ["a", "b", "c"], 3) == 1.0


def test_overlap_ratio_uses_intersection_over_k():
    # 交集 {a,b} / k=3
    assert abs(overlap_ratio(["a", "b", "c"], ["a", "b", "z"], 3) - 2 / 3) < 1e-9


def test_overlap_ratio_empty_is_zero():
    assert overlap_ratio([], ["a"], 3) == 0.0
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_dense_equivalence_check.py -v`
Expected: FAIL —— `ModuleNotFoundError`。

- [ ] **Step 4: 实现比对脚本**

`scripts/dense_equivalence_check.py`：

```python
"""dense 迁移等价性：同一语料、同一批查询，Chroma 与 pgvector 的 top-k 重合率。

两侧都**直读**自己的存储（Chroma 用原始客户端、PG 用 PgVectorStore），
不经过同一个 VectorStore 实例 —— 因为 VectorStore 在 Task 9 后指向 PG，
而 Chroma 侧必须一直可读到最后一次验收。

判据（retrieval-quality delta）：单知识库、k = TOP_K_RETRIEVAL、重合率 ≥ 0.9。
未达标不得进入后续步骤，先查 distance 语义与过滤条件。
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import chromadb
from loguru import logger

from src.config.const import TOP_K_RETRIEVAL
from src.config.settings import CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR
from src.infra.db.vector_store.pg_store import PgVectorStore, QueryEmbedder

QUERIES_PATH = Path("tests/fixtures/dense_equivalence_queries.json")
REPORT_PATH = Path("docs/tmp/p2-dense-equivalence-2026-09-19.md")
PASS_THRESHOLD = 0.9


@dataclass
class QueryCase:
    """一条固定查询。"""

    kb: str
    """目标知识库（单库路径）。"""
    query: str
    """查询文本。"""
    category: str
    """分类：中文 / 数值 / 时间。"""


@dataclass
class QueryOutcome:
    """一条查询的比对结果。"""

    case: QueryCase
    """查询本身。"""
    chroma_ids: list[str]
    """Chroma 侧 top-k 的 id（按距离升序）。"""
    pg_ids: list[str]
    """PG 侧 top-k 的 id（按余弦距离升序）。"""
    overlap: float
    """重合率 = |交集| / k。"""


def overlap_ratio(dense_ids: list[str], chroma_ids: list[str], k: int) -> float:
    """计算 top-k 重合率。

    Args:
        dense_ids: PG 侧 id 列表
        chroma_ids: Chroma 侧 id 列表
        k: top-k 的 k

    Returns:
        |交集| / k；k <= 0 或无结果时为 0.0
    """
    if k <= 0:
        return 0.0
    return len(set(dense_ids) & set(chroma_ids)) / k


def load_queries(path: Path = QUERIES_PATH) -> list[QueryCase]:
    """读取固定查询集。

    Args:
        path: JSON 清单路径

    Returns:
        QueryCase 列表
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [QueryCase(kb=item["kb"], query=item["query"], category=item["category"]) for item in raw]


def _chroma_top_ids(client, kb_id: str, embedder: QueryEmbedder, query: str, k: int) -> list[str]:
    """直读 Chroma 取 top-k id（不经过 VectorStore，见模块 docstring）。

    Args:
        client: chromadb 原始客户端
        kb_id: 知识库 ID（32 位 hex，无连字符）
        embedder: 查询向量化入口
        query: 查询文本
        k: top-k

    Returns:
        按距离升序的 id 列表；集合不存在时返回空列表
    """
    name = f"{CHROMA_COLLECTION_PREFIX}{kb_id}"
    try:
        col = client.get_collection(name)
    except Exception:  # noqa: BLE001
        logger.warning("chroma collection missing: {}", name)
        return []
    if col.count() == 0:
        return []
    vec = embedder.embed_query(query)
    result = col.query(query_embeddings=[vec], n_results=min(k, MAX_QUERY_K))
    ids = result.get("ids") or [[]]
    if not ids or not ids[0]:
        return []
    return list(ids[0])


async def run_check(queries: list[QueryCase], k: int = TOP_K_RETRIEVAL) -> list[QueryOutcome]:
    """对每条查询跑两侧并算重合率。

    两侧共用同一个 embedder 实例，保证查询向量完全一致 —— 否则重合率的差异
    无法归因到存储。

    Args:
        queries: 固定查询集
        k: top-k 的 k，默认取 TOP_K_RETRIEVAL

    Returns:
        每条查询的结果列表
    """
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    embedder = QueryEmbedder()
    store = PgVectorStore(embed_fn=embedder)

    outcomes: list[QueryOutcome] = []
    for case in queries:
        chroma_ids = _chroma_top_ids(client, case.kb, embedder, case.query, k)
        pg_results = await store.dense_search(case.kb, case.query, k=k)
        pg_ids = [r.id for r in pg_results]
        outcomes.append(
            QueryOutcome(
                case=case,
                chroma_ids=chroma_ids,
                pg_ids=pg_ids,
                overlap=overlap_ratio(pg_ids, chroma_ids, k),
            )
        )
    return outcomes


def _write_report(outcomes: list[QueryOutcome], k: int) -> dict:
    """写验收报告并返回汇总统计。

    Args:
        outcomes: 逐条比对结果
        k: top-k 的 k

    Returns:
        统计字典：count / mean / min / passed / below_threshold
    """
    overlaps = [o.overlap for o in outcomes]
    if overlaps:
        mean = sum(overlaps) / len(overlaps)
        minimum = min(overlaps)
    else:
        mean = 0.0
        minimum = 0.0
    below = [o for o in outcomes if o.overlap < PASS_THRESHOLD]

    lines = [
        "# P2 dense 迁移等价性验收（Chroma → pgvector）",
        "",
        f"- top-k 的 k：{k}",
        f"- 查询数：{len(outcomes)}",
        f"- 重合率均值：**{mean:.4f}**（判据 ≥ {PASS_THRESHOLD}）",
        f"- 重合率最小值：{minimum:.4f}",
        f"- 未达 {PASS_THRESHOLD} 的查询数：{len(below)}",
        f"- 结论：{'**通过**' if mean >= PASS_THRESHOLD else '**未通过**'}",
        "",
        "| kb | category | query | overlap | chroma top3 | pg top3 |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        lines.append(
            f"| {o.case.kb[:8]}… | {o.case.category} | {o.case.query} | {o.overlap:.3f} | "
            f"{', '.join(o.chroma_ids[:3])} | {', '.join(o.pg_ids[:3])} |"
        )
    lines.append("")
    lines.append("> 本报告仅供 P2 存储替换的**差分**判定；不构成检索质量基线。")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    return {
        "count": len(outcomes),
        "mean": mean,
        "min": minimum,
        "passed": mean >= PASS_THRESHOLD,
        "below_threshold": [f"{o.case.query}({o.overlap:.2f})" for o in below],
    }


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="dense 迁移等价性比对")
    parser.add_argument("--k", type=int, default=TOP_K_RETRIEVAL, help="top-k")
    args = parser.parse_args()
    queries = load_queries()
    outcomes = asyncio.run(run_check(queries, k=args.k))
    stats = _write_report(outcomes, args.k)
    logger.info("dense equivalence: {}", stats)
    if not stats["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

补充 import（文件头）：`from src.infra.db.vector_store.pg_store import MAX_QUERY_K, PgVectorStore, QueryEmbedder`。

> ⚠ `_write_report` 的报告表里 `chroma top3` 与 `pg top3` 都是 id（形如 `{doc_id}:{i}`），
> 两侧 id 相同是**预期**（搬迁原样保留 id）——若出现「同一行两侧 id 完全不同」，
> 说明 kb_id 还原或集合映射错了，先修这个再看重合率。

- [ ] **Step 5: 跑纯函数测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/scripts/test_dense_equivalence_check.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 6: 跑验收**

```bash
POSTGRES_HOST=localhost .venv/bin/python scripts/dense_equivalence_check.py --k 30
```

打印并记录：**均值重合率**、**最小值**、逐条明细。

- [ ] **Step 7: 判据 Gate**

- **若均值 ≥ 0.9**：通过，进入 Task 9。把报告路径写进提交信息。
- **若均值 < 0.9**：**停下，不得进入 Task 9**。按以下顺序排查并记录：
  1. 两侧的 `k` 是否一致（`min(k,100)` 截断是否只在一侧生效）；
  2. 查询向量是否同一实例、同一模型产出；
  3. PG 侧是否漏了 `embedding IS NOT NULL` 的过滤（不该有 NULL，但要确认）；
  4. 距离语义：`<=>` 在 pgvector 返回余弦距离，与 Chroma 的 cosine 距离应同向；
  5. 若以上都排除：把差异最大的前 5 条查询的**两侧完整 top-k id 与 distance 并排贴进报告**，交控制器裁决。

- [ ] **Step 8: 提交**

```bash
git add tests/fixtures/dense_equivalence_queries.json scripts/dense_equivalence_check.py \
        tests/scripts/test_dense_equivalence_check.py docs/tmp/p2-dense-equivalence-2026-09-19.md
git commit -m "test(retrieval): dense 迁移等价性验收（固定查询集 + top-k 重合率 ≥ 0.9）"
```

---

### Task 9: 切换装配、删 Chroma 代码路径、embedding 无条件预计算

**Files:**
- Modify: `src/infra/db/vector_store/__init__.py`（改为导出 PG 后端）
- Delete: `src/infra/db/vector_store/client.py`、`embedding.py`、`store.py`、`search.py`
- Modify: `src/services/document_service.py:106-150`、`:508-572`
- Modify: `src/services/app_service.py:98-116`
- Modify: `src/rag/retrieval.py:82-117`
- Modify: `src/main.py:47,53-69`
- Modify: `src/config/const.py`（删 `CHROMA_WARMUP_*`）
- Modify: `tests/reset_data.py:52-62,83-121`
- Modify: `tests/conftest.py:37-41`
- Modify: `tests/infra/db/test_vector_store.py`（重写为打 PG 的公开入口测试）
- Modify: `docs/agents/api_contract.md`、`docs/agents/code-map.md`

**Interfaces:**
- Consumes: `PgVectorStore`（Task 5/6）
- Produces:
  - `from src.infra.db.vector_store import VectorStore` 仍然可用，但背后是 PG 实现（**所有既有 import 点不需改动**）
  - `VectorStore` 的 IO 方法全部为 `async`（Ruling 1）

- [ ] **Step 1: 写失败测试（公开入口是 PG）**

在 `tests/infra/db/test_vector_store.py` 顶部加：

```python
def test_public_vector_store_is_pg_backed():
    """公开入口 VectorStore 背后必须是 PG 实现，且不再有 Chroma 的模块。"""
    import importlib

    module = importlib.import_module("src.infra.db.vector_store")
    assert module.VectorStore.__module__ == "src.infra.db.vector_store.pg_store"
    for gone in ("client", "embedding", "store", "search"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"src.infra.db.vector_store.{gone}")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/test_vector_store.py -k pg_backed -v`
Expected: FAIL（`VectorStore.__module__` 仍是 `src.infra.db.vector_store`）。

- [ ] **Step 3: 切公开入口并删 Chroma 实现**

`src/infra/db/vector_store/__init__.py` 整体替换为：

```python
"""向量存储的公开入口。

后端是 PostgreSQL + pgvector（单表 chunks + kb_id 列，无 collection 概念）。
历史上这里是 ChromaDB 实现；P2 起 Chroma 的代码路径已删除，其依赖与数据目录
保留到 P4 的「依赖与卷清理」——数据目录是 dense 等价性验收与回滚的依据。
"""

from src.infra.db.vector_store.pg_store import PgVectorStore as VectorStore
from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult

__all__ = ["ChunkQueryResult", "ChunkResult", "VectorStore"]
```

```bash
git rm src/infra/db/vector_store/client.py src/infra/db/vector_store/embedding.py \
       src/infra/db/vector_store/store.py src/infra/db/vector_store/search.py
```

> ⚠ `ChunkResult` / `ChunkQueryResult` 仍从 `types.py` 导入，**不要**把它们搬进 `pg_store.py`（几十处 import 依赖这个路径）。

- [ ] **Step 4: 改调用点（Ruling 1：去掉 `to_thread`）**

`src/services/document_service.py`：

1. 入库路径（第 508-537 行）：把 embedding 计算**移出** `if CHUNK_EVAL_ENABLED:` 块，使其无条件执行：

```python
                # embedding 无条件预计算：写入事务只接收已算好的向量，
                # 避免外网调用落在事务内（开关只决定是否额外做分块质量评估）。
                chunk_embeddings = await asyncio.to_thread(
                    get_embeddings().embed_documents,
                    [c.content for c in chunks],
                )
                # 分块质量评估 — 开关控制，只记录不拦截
                if CHUNK_EVAL_ENABLED:
                    try:
                        scorer = ChunkQualityScorer()
                        eval_result = await asyncio.to_thread(
                            scorer.evaluate,
                            chunks,
                            filename,
                            strategy,
                            chunk_embeddings,
                        )
                        await self._doc_repo.update_document_meta_info(
                            doc_id, {"eval": eval_result}
                        )
                        logger.info(
                            "Chunk eval for '{}': score={} passed={}",
                            filename,
                            eval_result.get("overall_score"),
                            eval_result.get("passed"),
                        )
                    except Exception as eval_err:  # noqa: BLE001
                        logger.warning(
                            "Chunk eval failed for '{}': {}", filename, eval_err
                        )
```

2. `add_chunks` 调用（第 539-547 行）改为：

```python
                t2 = time.perf_counter()
                count = await self.vector_store.add_chunks(
                    kb_id,
                    chunks,
                    doc_id,
                    chunk_embeddings,
                )
```

3. `delete_document`（第 123-126 行）改为：

```python
        await self.vector_store.delete_document(kb_id, doc_id)
```

并删除原来的 `try/except ... logger.warning("ChromaDB delete failed ...")` —— **P2 不再吞删除失败**（P4 会把它纳入事务；现在让它抛错比静默产生孤儿分块更安全）。把 docstring 第 107 行的「ChromaDB 清理」改为「分块清理」。

4. `_rebuild_kb_index`（第 143-150 行）改为：

```python
        if self.bm25 is None:
            return
        try:
            results = await self.vector_store.get_all_chunks(kb_id)
            await asyncio.to_thread(self.bm25.rebuild_from_results, kb_id, results)
            logger.info("BM25 index rebuilt: kb_id={} chunks={}", kb_id, len(results))
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25 index rebuild failed for kb_id={}: {}", kb_id, e)
```

`src/services/app_service.py`（第 102 行）：

```python
        deleted = await self.vector_store.delete_collection(kb_id)
        logger.info("chunks deleted for kb_id={} deleted={}", kb_id, deleted)
```

（保留 `try/except` 与否由实现者按现状判断；**若保留，warning 文案不得再提 ChromaDB**。）

`src/rag/retrieval.py`：把两处调用改成 `await`：

```python
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        dense_coro = vector_store.dense_search(kb_id, query, TOP_K_RETRIEVAL)
        bm25_coro = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_coro, bm25_coro)
        results = rrf_fusion(d or [], b or [])
        ...
    results = await vector_store.dense_search(kb_id, query, k=TOP_K_RETRIEVAL)
```

`src/main.py`：删除 `_warmup_chromadb()` 整个函数（第 53-69 行）与 `lifespan` 里的调用（第 47 行）。

`src/config/const.py`：删除 `Event.CHROMA_WARMUP_DONE` / `Event.CHROMA_WARMUP_FAILED`（先 `grep -rn "CHROMA_WARMUP" src/ tests/` 确认删后无引用）。

- [ ] **Step 5: 改 reset 脚手架（保住 Chroma 数据目录）**

`tests/reset_data.py`：

```python
def reset_bm25_index() -> None:
    """清空 BM25 索引目录（词法索引仍是磁盘文件，P3 由 PG 全文检索取代）。

    注意：**不再删除 Chroma 的 persist 目录** —— 自 P2 起 dense 数据由
    chunks 表承载（reset_pg 已清），而 data/chroma_persist 是 dense 等价性
    验收与回滚的依据，删除它会使两者同时失效。
    """
    path = Path(BM25_INDEX_DIR)
    if path.exists():
        shutil.rmtree(path)
        logger.info("BM25: 已删除索引目录 '{}'", BM25_INDEX_DIR)
    path.mkdir(parents=True, exist_ok=True)
    logger.info("BM25: 已重建空目录")
```

并把 `reset_all()` 里的 `reset_vector_store()` 调用改成 `reset_bm25_index()`，`reset_all` 的 docstring 第 86 行「（PostgreSQL + ChromaDB + Redis）」改为「（PostgreSQL + BM25 索引 + Redis）」。

（`BM25_INDEX_DIR` 从 `src/config/settings.py` 导入，与 `app_service.py:50` 用的是同一个常量。）

- [ ] **Step 6: 重写 `tests/infra/db/test_vector_store.py` 为公开入口冒烟测试**

保留并适配：`test_get_or_create_collection`（现在断言返回 kb_id 且不写库）、`test_add_chunks_and_search`、`test_delete_collection`、`test_delete_nonexistent_collection`、`test_list_collections_*`、`test_concurrent_similarity_search_safe`（改成 `asyncio.gather` 并发，不再依赖 Chroma 的线程锁）。
删除：`test_collection_name_format`（collection 概念消失）、`test_similarity_search_all`（Task 4 已删）。
fixture `vs` 改为：

```python
@pytest.fixture
async def vs():
    """公开入口 VectorStore（PG 后端），配假 embedder，不发网络。"""
    from src.infra.db.vector_store import VectorStore

    return VectorStore(embed_fn=FakeEmbedder())
```

每个写库测试必须**先建 `knowledge_base` 行**（`chunks.kb_id` 有外键，F3），并在结束时清理 —— 直接复用 Task 5 的 `store_and_kb` fixture。

`tests/conftest.py` 的 `vector_store` fixture 同步改为 PG 后端（`VectorStore(embed_fn=...)` 或直接复用应用装配）；`service` fixture 不变（`AppService()` 会自动拿到 PG 后端）。

- [ ] **Step 7: 跑测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/db/ tests/rag/ tests/services/ tests/scripts/ -q`
Expected: PASS。

- [ ] **Step 8: 全量门禁**

```bash
.venv/bin/ruff check .
.venv/bin/pyright src/
grep -rn "chromadb\|ChromaClient\|DashScopeEmbeddingFunction" src/
```

预期：ruff 无错；pyright 不新增 error；**`src/` 里不再有 chromadb 引用**（`tests/` 与 `scripts/` 的直读脚本除外）。

- [ ] **Step 9: 同步文档**

- `docs/agents/api_contract.md`：`VectorStore` 契约改为**异步**（每个方法前加 `async def`），删 `similarity_search_all` / `similarity_search_multi`（Task 4 已删，此处核对），补 `dense_search` 与 `dense_rank`/`sparse_rank`/`lexical_score` 字段说明，并写明 `list_collections` 的语义已变为「含分块的知识库 ID」、`get_or_create_collection` 为无副作用兼容方法。
- `docs/agents/code-map.md`：`src/infra/db/vector_store/` 的模块清单改为 `__init__.py / pg_store.py / mapping.py / types.py`，删除 `client.py`/`embedding.py`/`store.py`/`search.py`；补 `src/infra/db/models/chunk.py` 与 `src/infra/db/mysql_db/chunk_repo.py`；`chunks` 表「无 ORM 模型」的说法改为「由 `ChunkModel` 映射（属性名 `extra` → 列 `metadata`）」。

- [ ] **Step 10: 提交**

```bash
git add -A src/infra/db/vector_store src/services src/rag src/main.py src/config/const.py \
           tests/ docs/agents/api_contract.md docs/agents/code-map.md
git commit -m "refactor(vector-store): 切换 dense 检索到 pgvector，删除 Chroma 代码路径"
```

---

### Task 10: 文档同步与修正记录（一事一档）

**Files:**
- Modify: `docs/agents/data-flow.md`
- Modify: `docs/agents/glossary.md`
- Modify: `docs/agents/defensive-patterns.md`
- Modify: `docs/agents/code-map.md`（核对 Task 9 的改动是否完整）
- Modify: `docs/openspec/changes/postgres-storage-consolidation/tasks.md`（P2 状态 + §2 修正记录）
- Modify: `docs/agents/requirements_pool.md`（若有新遗留项）

**Interfaces:**
- Consumes: Task 1–9 的实际产物
- Produces: 文档与代码一致（`docs/agents/` 是代码结构的唯一归属）

- [ ] **Step 1: `data-flow.md` 的 dense 链路改到 PG**

把 dense 检索链路（Chroma/collection → `chunks` 表 + `kb_id` 列 + pgvector `<=>`）与入库链路（写 `chunks`）逐条更新。**保留**词法链路（BM25）现状，并标注「P3 换 PostgreSQL 全文检索」。

- [ ] **Step 2: `glossary.md` 登记新术语**

至少四条：`lexical_score`（与引擎无关的词法得分名，替代 `bm25_score`）、`dense_rank` / `sparse_rank`（分路排名）、`metadata 回填契约`（列值 + jsonb 平铺、冲突以列为准）、`chunks 表`（单表 + `kb_id` 取代 collection-per-KB；`content_seg` 与 `tsv` 的定位）。

- [ ] **Step 3: `defensive-patterns.md` 登记 P2 暴露的缺陷类别**

新增两条（这是 P2 最有复用价值的产出）：

1. **「派生副本与权威来源分离」类**：当一个字段从 blob 升为列、或从集合改为单表时，**读取侧必须显式重建契约键**；漏掉不会报错，只会让依赖它的下游（去重 / 引用 / 实体透传）静默失效。防复发规则：跨存储的字段搬迁必须有一个**唯一**的映射函数，且该函数有单测钉住契约键。
2. **「存储替换的验收必须与算法变更分离」类**：存储替换**不应改变行为**，因此必须用**差分**（同一语料 + 同一查询，比对替换前后 top-k）验收，而不是用「新实现自己跑得通」。把两者混在一个判据里，整个变更不可验证。

- [ ] **Step 4: 核对 `code-map.md`**

确认 Task 9 Step 9 的改动已完整：`vector_store/` 的模块清单、新增的 `models/chunk.py` 与 `mysql_db/chunk_repo.py`、`chunks` 表由 `ChunkModel` 映射（属性名 `extra` → 列 `metadata`）、以及 `chunks` 行不再写「无 ORM 模型」。

- [ ] **Step 5: 核对三个 capability 的命名同步（P2 承担的部分）**

对照 `docs/openspec/changes/postgres-storage-consolidation/specs/` 里这三个 delta 的正文，逐条确认代码与 `docs/agents/` 中**不再有 collection 概念的残留陈述**：

- `kb-routing`：正文的「直接在 kb_a 的 collection 检索」→ 实际已是按 `kb_id` 单表检索；
- `chunk-entity-enrichment`：「before storing in ChromaDB」→ 实体键进 `chunks.metadata`（jsonb）；
- `model-config`：「维度一致性对照对象是 ChromaDB collection」→ 现在是 `chunks.embedding` 列（Task 2 的 `atttypmod` 测试就是它）。

```bash
grep -rni "collection\|chroma" docs/agents/ | grep -v "postgres-storage\|P4\|历史\|已删除"
```

把命中逐条判断：**是「当前状态」的陈述就改**，**是历史/遗留说明就保留**。

- [ ] **Step 6: 更新 `tasks.md`**

- P2 行：`未编写` → `**已完成**（收口 commit `<Task 9 的 SHA>`）`。
- §2 修正记录追加 P2 实测出的与设计不符的事实。至少登记这几条（若执行时另有发现，一并加）：
  1. **kb_id 不可还原**：Chroma collection 名是 kb_id 去连字符后的 hex，不可逆 → 搬迁自造 kb_id 并补建合成 `knowledge_base` 行（Ruling 3）。
  2. **`chunks.kb_id` 外键与「P1 已清空 PG」冲突**（F3）—— 契约文档没写这条，属实施期发现。
  3. **`VectorStore` 必须异步化**（Ruling 1）：asyncpg 只能异步驱动，`design.md` D8 的「保持签名」解释为方法名与参数/返回语义不变。
  4. **`content_seg` 在 P2 是占位**（Ruling 5）—— `design.md` 没写「P3 之前它填什么」。
  5. `chunk_index` 尾部残留问题与处置（Ruling 2）。

- [ ] **Step 7: 需求池登记**

若 P2 产生了新的遗留项（例如「`ruff format .` 全仓漂移」「Chroma 依赖与目录待 P4 清理」「`content_seg` 待 P3 全量重写」尚未登记），按 `requirements_pool.md` 的格式追加。**不要**重复登记已存在的 F-18。

- [ ] **Step 8: 文档一致性门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_doc_consistency.py -q
```

Expected: PASS（`docs/` 中被校验的 `src/` 路径锚点全部存在；Task 9 删了 4 个文件，若 `docs/` 里还引用它们，这条会红）。

- [ ] **Step 9: 提交**

```bash
git add docs/
git commit -m "docs: 同步 P2 的 dense 存储链路、术语与防复发模式；补 tasks.md 修正记录"
```

---

### Task 11: 收尾门禁 + PG 上的真实 E2E 冒烟

**Files:**
- Modify: 无代码改动（除非 E2E 发现缺陷——那时回对应 Task 修，**不要**在本任务里改）

**Interfaces:**
- Consumes: Task 0–10 的全部产物
- Produces: P2 的 DoD D1–D8 逐条结论 + 验收报告

> ⚠ **端点路径以 P1 plan 的 Task 10 实测表为准**：本项目的接口**全部是 POST**（`/api/kbs`、`/api/kbs/list`、`/api/kbs/documents/upload` …），且 chat 请求体字段是 `query` 而非 `message`。**不要**照 OpenAPI 文档猜方法。
> ⚠ **本任务的价值是「发现」问题，不是「就地修」**。任一步失败 → 回对应 Task 修，并在报告里写明归因。

- [ ] **Step 1: 全量门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check .
.venv/bin/pyright src/
grep -rn "print(" src/ | grep -v "src/cli/\|pdf_heading_extractor"
grep -rn "TODO\|FIXME" src/
```

预期：pytest 全绿（失败则回对应 Task）；ruff 无错；pyright 不新增 error；`print()`/TODO 无**新增**命中（P1 已登记的存量豁免见 P1 账本 Ruling R20）。

- [ ] **Step 2: E2E 冒烟（登录 → 建库 → 上传到 ready → 提问 → 引用渲染）**

先在 PG 里做一个干净的起点（**保留搬迁来的等价性语料**，只清业务残留）：

```bash
POSTGRES_HOST=localhost .venv/bin/python -c "from tests.reset_data import reset_pg; reset_pg()"
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT (SELECT count(*) FROM knowledge_base), (SELECT count(*) FROM document), (SELECT count(*) FROM chunks), (SELECT count(*) FROM users);"
```

> ⚠ `reset_pg()` 会把 `chunks` 一起清掉（含搬迁语料）。若 Task 8 的报告还没落盘、或你还想复跑等价性，**先跑等价性再执行本步**。清理后要复现搬迁语料，重跑 `scripts/migrate_chroma_to_pg.py` 即可（幂等）。

然后按 P1 plan Task 10 的端点表走完整链路，并逐条核对：

| # | 预期 | 观测点 |
|---|---|---|
| 1 | 登录成功（账号不存在时自动注册） | 响应含 token |
| 2 | 建库成功 | `knowledge_base` 计数 +1 |
| 3 | 上传文档到 `ready` | `document.status='ready'`、`chunk_count>0` |
| 4 | **分块真的进了 PG** | `chunks` 计数 = `chunk_count`；`embedding IS NOT NULL`；`tsv IS NOT NULL` |
| 5 | 提问命中并渲染引用 | 回答里出现来源文件名/页码；`sources` 非空 |
| 6 | **去重未被破坏**（D4 的核心） | 同一文档在回答的来源里**不重复占满**；`metadata.doc_id` 在结果中可读 |
| 7 | 实体透传未被破坏 | 若语料含 `company`/`report_period`，`RAGContext.entities` 非空 |

- [ ] **Step 3: 独立核验「分块确实来自 PG 而不是 Chroma」**

```bash
docker compose exec -T postgres psql -U corporate_rag -d corporate_rag -tAc \
  "SELECT count(*), count(embedding), count(tsv) FROM chunks;"
```

预期三列相等（每行都有向量与词法向量）。
再补一条**反向**证据：临时把 PG 的 `chunks` 删掉一个 kb 的行，检索该 kb 应立即返回空（证明读的是 PG）——做完记得重跑搬迁脚本还原。

- [ ] **Step 4: 逐条判定 DoD D1–D8**

对照本文档顶部的 DoD 表逐条写结论与证据（命令 + 真实输出）。任何一条不成立 → 写明归因到哪个 Task。

- [ ] **Step 5: 回滚依据与清理核查**

```bash
docker volume ls | grep -E "corporate_rag_mysql_data|postgres"
ls -la data/chroma_persist | head -3
docker ps --format '{{.Names}}' | grep -E "mysql|chroma"
```

预期：`corporate_rag_mysql_data` 仍在；`data/chroma_persist` **未被删除**；orphan `corporate-rag-mysql` 仍在（**不要**去清理它）。
若 `data/chroma_persist` 不见了 → 等价性语料与回滚依据同时失效，**立即报告**。

- [ ] **Step 6: 写报告**

报告须含：门禁输出、E2E 六条观测、DoD 逐条结论、回滚依据核查、清理后的计数、以及 P2 的**遗留项清单**（例如：`content_seg` 待 P3 全量重写、Chroma 依赖与目录待 P4 清理、`chunks` 无 HNSW 索引）。

---

## 自审（Self-Review，控制器已跑）

**1. Spec 覆盖**

| spec / delta | 承载任务 | 状态 |
|---|---|---|
| `typed-data-layer` · 检索结果统一类型（`ChunkResult` / `lexical_score` / 分路排名 / metadata 回填） | Task 1、3、6 | ✅ |
| `typed-data-layer` · 全局检索路径已移除 | Task 4 | ✅ |
| `typed-data-layer` · **关系型实体类型**（`KbListItem` / `DocEntity` / `SessionEntity` / `SessionListItem` / `MessageEntity` / `UserEntity`） | **无** | ❌ **缺口，见下** |
| `typed-data-layer` · 连接管理拆为 Repo | P1 已达成 | ✅（P2 只需核对，未改动） |
| `retrieval-quality` · dense 迁移等价性（≥20 查询 / 重合率 ≥0.9 / 单库 / 可复现） | Task 8 | ✅ |
| `retrieval-quality` · 词项命中探针 | P3 | 不在 P2 |
| `retrieval-quality` · Rerank context passthrough（实体经 `chunks` 表透传） | Task 3、6（metadata 回填把实体键带出来） | ✅（Task 6 的 `test_metadata_contract_is_backfilled_on_every_result` 覆盖自定义键；E2E 第 7 条再验一次） |
| `hybrid-retrieval` · 分块按知识库归属存储（单表 + FK + 遍历无副作用） | P1 建表 + Task 2、5、6 | ✅ |
| `hybrid-retrieval` · 分块与文档状态同事务 / 删除路径同事务 | P4 | 不在 P2 |
| `hybrid-retrieval` · 两路取数并发 | 现状已并发（`asyncio.gather`），Task 9 保持 | ✅ |
| `hybrid-retrieval` · 融合结果携带两路排名 | Task 1（字段）+ Task 6（dense 侧填充）；词法侧填充与融合迁移在 P3 | ✅（P2 部分） |
| `database-orm` · 搜索类型搬迁（引用方移除 `bm25_index.py`） | P3 | 不在 P2 |
| `model-config` · 维度一致性对照对象 → `chunks.embedding` | Task 2 Step 1 的 `atttypmod` 测试 | ✅ |
| `kb-routing` · collection 概念消失 | Task 9 + Task 10 Step 5 核对 | ✅ |
| `chunk-entity-enrichment` · 实体写分块表而非 ChromaDB | Task 3、5 | ✅ |
| `architecture-tidy` · AppService 不再持有 `BM25Index` | P3 | 不在 P2 |
| `agent-service` / `multi-query-retrieval` 的命名同步 | P3（随词法路一起） | 不在 P2 |
| `observability-logging` · 稀疏支路贡献可见 | P3 | 不在 P2 |

**发现的缺口（需控制器裁决）**

> **`typed-data-layer` 的 ADDED「关系型实体类型」在 P1–P4 里没有承载任务。**
> 该 requirement 要求 6 个 Repo 的查询结果返回 dataclass Entity（`KbListItem` / `DocEntity` / `SessionEntity` / `SessionListItem` / `MessageEntity` / `UserEntity`）而非 ORM 模型或 raw dict。现状：`KbRepo.get_all_kb()` 返回 `list[KbModel]`、`DocumentRepo.get_documents()` 返回 `list[DocModel]` —— 都是 ORM 模型，不满足。
> **建议归到 P4**（收尾阶段：它与检索链路无关，且 P4 本来就要做「文档与 ADR + 归档」，是最后一个能承载它的阶段）。**不要**塞进 P2：它会扩到 6 个 repo + 大量调用点，与 dense 存储替换无关，会污染等价性验收的范围。
> 若控制器选择不在本 change 内做，则**必须**在归档前把它显式登记为遗留项，否则归档后 `specs/` 会留下一条假陈述。

**2. 占位符扫描**

已逐段检查：无 `TODO` / `TBD` / 「类似 Task N」/ 「添加适当错误处理」类表述。Task 8 原先的 `NotImplementedError` 骨架已替换为完整实现。

**3. 类型一致性**

- `ChunkRow` 的 11 个字段在 Task 3 定义、Task 2/5/6/7 使用，字段名一致（`extra` 而非 `metadata`）。
- `row_to_chunk_result(row, *, distance, lexical_score, dense_rank, sparse_rank)` 的关键字参数名与 Task 6 的调用一致。
- `ChunkResult` 的最终字段集（Task 1）与 Task 6 的填充一致；`bm25_score` 全仓不再出现。
- `ChunkQueryResult(items, total, page, page_size)` 与 Task 6 的构造一致。
- `PgVectorStore` 的方法名在 Task 5/6 定义、Task 8/9 调用，一致（`dense_search` / `similarity_search` / `add_chunks` / `get_chunks_by_doc_id` / `get_chunks_paginated` / `get_all_chunks` / `list_collections` / `delete_document` / `delete_collection` / `get_or_create_collection`）。
- `MAX_QUERY_K` 在 Task 5 定义（`pg_store.py`），Task 8 导入使用。

---

## 执行方式

Plan complete and saved to `docs/superpowers/plans/2026-09-19-postgres-storage-p2-pgvector-dense.md`。两种执行方式：

1. **Subagent-Driven（推荐）** —— 每个 Task 派发一个全新的实现子代理，任务之间做评审，迭代快
2. **Inline Execution** —— 在本会话里按 executing-plans 批量执行，带检查点

选哪种？

---

## 执行顺序提醒（给控制器）

- **Task 0 必须先做**：它的 Step 2/3 会证伪或证实本 plan 的两个前提（Chroma 语料仍可读、PG `chunks` 为空且 FK 如描述）。前提不成立时，Task 7/8 的整个验收框架要重新设计。
- **Task 3 的 Step 1 要先于 Task 2**（循环依赖），派发 Task 2 时把这条带上。
- **Task 7 必须在 Task 9 之前**（Chroma 侧要先冻结才有权威语料）。
- **Task 8 的 Gate 不通过时不得进入 Task 9**。
- 本 plan 有 **7 条 Ruling**（见「本 plan 的裁决」），其中 Ruling 1（异步化）与 Ruling 5（`content_seg` 占位）是对 `design.md` 字面表述的解释性裁决，评审时请让 reviewer 明确表态是否接受。
