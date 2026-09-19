## Context

**当前存储形态（三套独立存储，各自可有独立故障）**

| 数据 | 载体 | 位置 |
|---|---|---|
| 关系型（7 张表） | MySQL 8.0 + SQLAlchemy 2.0 async（`mysql+aiomysql`） | `src/infra/db/` |
| 分块与向量 | ChromaDB 嵌入式 `PersistentClient`，**每知识库一个 collection**（`kb_<hex>`） | `data/chroma_persist` |
| 词法索引 | 进程内 `rank_bm25.BM25Okapi` + pickle 索引文件 | `data/bm25_index/{kb_id}/bm25.pkl` |

**检索链路**（`src/rag/retrieval.py:82-96`）：`asyncio.gather(dense, bm25)` → `rrf_fusion` → `_dedup_by_doc_id` → rerank（`qwen3-rerank`）→ 生成。融合与去重都在应用层。

**实测环境事实（2026-09-18/19）**

> §1 前置核查（1.1–1.4）的完整实测记录见 `docs/tmp/postgres-probe-2026-09-19.md`。
> 结论摘要：1.2 **通过**（embeddings 可原样读出）；1.3 本地得 pgvector **0.8.6**；1.1 机制已证、RDS 权限未证（**阻塞**）；1.4 结论比初稿更硬（见 D1）。

- 未删除知识库 **1340** 个；Chroma 有 **691** 个 collection、**176** 条向量，其中**只有 5 个 collection 含分块**。单库最大 121 分块；`b9e74e82…` 51 分块 / 31,881 字符。
- **embeddings 可原样读出**（5 个 collection / 176 分块全部可读，维度 1024，无 `None` 行）→ 数据搬迁与 dense 等价性验收成立。
- 分块的 metadata 里**已含 `parent_content`**（另有 `heading_path` / `block_type` / `year` 等）→ `metadata jsonb` 必须原样承载，不得只保留契约 5 键（与 `retrieval-fetch-and-dedup` 的父块级去重直接相关）。
- `pickle.load` 单次 **58.2 ms**（冷页缓存 191.9 ms）；`BM25Okapi` 从原文重建 **5.6 ms**；`search` 每次调用都全量重载索引（`bm25_index.py:86-90`）。
- 向量规模：天花板千文档 ≈ 2.5–4 万分块 × 1024 维 ≈ **164 MB**。
- `data/` 在 `/mnt/d`（**9p**，fsync/原子性弱）且被 gitignore。`postgres_data` 是 **docker named volume**（在 docker VM 内），**已避开 9p 坑**。
- 当前 `postgres` 服务：dev 有 `profiles: ["langfuse"]` + `mem_limit: 256m` + DB/user 均为 `langfuse`；prod **本来就没有** `profiles:`，`mem_limit: 4g`。两份 compose 的 `postgres_data` 卷名相同（`corporate_rag_postgres_data`）。
- Langfuse worker/web 通过锚点 `&langfuse-depends` 依赖 `postgres`（`service_healthy`）；**`app` 目前不依赖 postgres**。

**既有烂账（本次必须一并处理，否则会带着错误走）**

> 以下事实于 2026-09-19 逐条核过（含对两套 ORM、两套 alembic、建表机制的全仓追查），并更正了初稿的三处错述。

- **两套 ORM 模型**：`src/infra/db/models/` 是**运行时唯一被 import 的那套**（5 个 repo、`services/document_service.py`、`chat/persistence.py`、`cli/eval_ragas.py`、根 `alembic/env.py:9` 全都指向它）；`src/infra/db/mysql_db/models/` **除自身 `__init__.py` 与那个不生效的 `mysql_db/alembic/env.py:16` 外无人 import —— 是死代码**。两者的差异是：本套 `SessionModel` 有 `agent`、`MessageModel` 有 `process`（`models/chat.py:17-21,43-45`，后者还是 `sqlalchemy.dialects.mysql.MEDIUMTEXT`），另一套没有；`feedback.py` 只存在于本套。
  ⚠ **更正初稿**：初稿称"alembic `target_metadata` 指向 `mysql_db/models/`" —— **反了**，生效链指向的是运行时那套（见下条）。
- **两套 alembic 目录**：**实际生效的是根 `alembic/`**（`alembic.ini:8` → 根 `env.py:9` 的 `from src.infra.db.models import *`）。`src/infra/db/mysql_db/alembic/` 未被任何配置指向。
  ⚠ **更正初稿**：初稿称两者的 `e6304ba3a9ef_init_models.py` "**内容不同**" —— **不成立**。逐行比对后 `upgrade()` 的**全部表/列操作完全一致**，仅 `UTCDateTime` 的引用写法不同（根用 `src.infra.db.base.UTCDateTime()`，另一套 `from ... import UTCDateTime`）。它们是**重复但等价**的，不是"分叉"。
  **但真正的问题在别处**：未被指向的那套里另有 `4f7b9c1d2e30_create_feedback.py`（`down_revision="e6304ba3a9ef"`）与 `5a8c2d1e9f34_add_feedback_trace_id.py`，即 **`feedback` 表的建表语句只在链条之外**。
- **`eval_report` 表从来没有被任何机制创建过**：全仓 `CREATE TABLE` 只出现在 `deploy/mysql/init/001_schema.sql`（5 张表：users / knowledge_base / document / sessions / conversation_history）与 `docs/` 下的历史计划文本。根 `alembic/versions/e6304ba3a9ef_init_models.py:241-249` 却直接 `op.add_column("eval_report", …)`，**预设该表已存在**。
- **根迁移不是从零建表，而是针对已存在表的 MySQL 增量 diff**：它通篇是 `op.alter_column(..., existing_type=mysql.*)` / `drop_index` / `create_foreign_key`，依赖 `deploy/mysql/init/001_schema.sql` 先建表。**换 PG 不能移植它，必须重写为一条全新 baseline。**
- ⚠ **由上面两条推出一个既有真实缺陷**：在**全新**部署（空库 + init SQL + 根 alembic `upgrade head`）下，**`eval_report` 与 `feedback` 两张表都不存在**，而 `EvalRepo.insert_report` 与 `ChatRepo.save_feedback` 会写它们 → 评测与反馈功能在全新环境上是坏的。当前环境能用只是因为有人手工建过表。**本变更的 PG baseline 迁移会一并修掉它**（baseline 必须建全 8 张表：7 张关系表 + `chunks`），这条要在 proposal/ADR 里写明是既有缺陷被顺带修掉。
- **`ChunkData` 两处定义**：`src/chunking/validator.py:9-21`（3 字段：`content` / `metadata` / `tokens: int = 0`）与 `src/parsers/base.py:17-31`（3 字段：`content` / `metadata` / `chunk_id: str` 必填）。三个 parser 都以关键字传 `chunk_id`（`txt_parser.py:64-68`、`docx_parser.py:83-87`、`pymupdf_parser.py:217-226`），写入侧（`validator` 版）则从不传。

**必须保持的契约（否则上层连带改动）**

- `ChunkResult.distance` 是**余弦距离**（0~2）：`rag_tools.py:168` 与 `retrieval.py:157-158` 用 `score = 1 - distance`。pgvector 的 `<=>` 正好返回余弦距离。
- 分块 id 形如 `{doc_id}:{chunk_index}`（`store.py:44`）。
- `metadata` 必带 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`（`store.py:47-51`）。
- `VectorStore` 有 6 个方法有真实调用方，其中 `get_chunks_paginated` 服务 `api/documents.py:187`（前端分块查看器，需 `total`）。

## Goals / Non-Goals

**Goals**

- 三套存储收敛为**一个 PostgreSQL 实例**（两个 database），失败模式原子化：不再存在"某一支路半死而整体表现正常"的状态。
- 分块写入与文档状态在**同一事务**内提交，孤儿类缺陷结构性消失。
- 空 collection 这一类缺陷结构性消失（`kb_id` 列 + 外键取代 collection-per-KB）。
- 中文词法检索**真正可用**，且**不依赖任何 PG 扩展**（用 Python 侧 jieba 预分词）。
- 生产可托管化：向量与词法检索挂到已是高可用的 RDS PostgreSQL，消掉最后一个可托管的派生单点。
- 清理四类既有烂账：双套 ORM 模型、双套 alembic、双套 `ChunkData`、Chroma/BM25 的持久化与依赖。

**Non-Goals**

- 不做端到端答案质量（RAGAS）的回归判定（语料仅 176 分块，指标方差盖过信号）。
- 不改检索取数口径、去重策略、rerank 模型与阈值（除"来源变成同一库"外）。
- 不引入 PG 分词扩展（`zhparser` / `pg_jieba` / `pg_bigm`）作为**前置依赖**；若 RDS 提供，作为后续优化项。
- 不引入 HNSW 近似索引作为前置（164 MB 规模下精确扫描足够，且顺带避开"近似 + WHERE 过滤导致召回不足"）。
- 不动 Redis（会话历史 / token 缓存 / 会话锁，已是托管且与本变更正交）。
- 不动 MinIO（原始文件是唯一不可再生的源头，与本变更正交）。
- 不做 Langfuse 服务端版本选择（v2 与"仅 PG"的决议另案）。

## Decisions

### D1：一个 PostgreSQL 实例、两个 database（应用 + Langfuse）

**候选**：① 两个独立实例；② 一个实例、同库不同 schema；③ 一个实例、两个 database。

**选 ③。** 与生产托管形态对齐（生产就是一个 RDS 实例）；成本与运维最省；用独立 database 而非同 schema，是为了让 Langfuse 的 `prisma migrate deploy` 有独立的 DDL 空间（Langfuse 需要迁移权限，官方支持用 `DIRECT_URL` 单独给迁移账号）。

**代价**：故障域共享（Langfuse 的负载会影响应用），且它与应用共用连接池。缓解：独立 database + 独立账号 + Langfuse 属于低写入负载（只有元数据，trace 在 ClickHouse）。**这一条要在 ADR 里记录为显式接受的代价。**

**dev 侧**：`postgres` 服务去掉 `profiles: ["langfuse"]`（应用现在依赖它，不能再挂在 Langfuse profile 下）、`mem_limit` 从 256m 上调（256m 对一个跑业务表 + 向量 + 全文索引的库偏紧）、增建应用 database。`app` 增加 `depends_on: postgres(service_healthy)`。

**prod 侧**：指向阿里云 RDS，不再本地起库。**前提**：prod 与 dev 不同机 —— 两份 compose 的 `name`（`corporate_rag`）、postgres 的 `container_name`（`corporate-rag-postgres`）与**全部卷名**（`corporate_rag_postgres_data` 等）**完全相同**，同机不是"数据互相污染"，而是**容器名冲突、第二个 `up` 直接失败**（`--force-recreate` 还会把先起的那套拆掉）。这条要写进 compose 注释与部署文档。

**镜像**：dev 现在用的 `postgres:15-alpine` **不自带 pgvector**（官方 postgres 镜像不含第三方扩展）。改用 pgvector 官方镜像 `pgvector/pgvector:pg15`（或 pin 到 `pgvector/pgvector:0.8.6-pg15`），且**大版本必须与 RDS 对齐**，否则本地绿、上线行为不同。

**应用 database 与账号的创建（初稿想当然了，评审 F2）**：`postgres` 镜像只在**数据目录首次初始化**时执行 `docker-entrypoint-initdb.d` 下的脚本。因此：

- **dev**：既有卷 `corporate_rag_postgres_data` 不会重跑 init 脚本 → 必须显式一次性 `CREATE DATABASE`（或删卷重建并挂载 `./deploy/postgres/init`）。这是两条不同的动作，`tasks.md` 不得只写"改路径"。
- **prod**：RDS 由控制台/SQL 预建应用库与**最小权限账号**，init 脚本根本不参与。
- `vector` 扩展的创建**不依赖 initdb**（见 D3）：放进 alembic 首版迁移，幂等且两条路径都生效。

**共享实例的运维后果（初稿只写了"故障域共享"，不完整，评审 F11）——以下四条为显式接受项，须记入 ADR**：

1. **备份与 PITR 是实例级的**：恢复应用库会同时回退 Langfuse 的数据（反之亦然）。两个应用的恢复点被绑死。
2. **连接数是共享预算**：prod compose 实为 `--workers 4`（`docker-compose.prod.yml:202`），叠加 `pool_size=10 + max_overflow=10`（`engine.py:23-24`）≈ **80 连接**，再加 Langfuse 与其 worker，可能触及 RDS 的 `max_connections`。**连接预算必须作为 RDS 规格的输入**（与"生产单 worker"规则一并决策，见 Open Questions）。
3. **大版本升级是实例级维护窗口**：两个应用必须同时兼容新版本。
4. **账号模型**：现有 compose 只创建了 `langfuse` 一个用户；"独立 database + 独立账号"需要额外的创建步骤与权限划分（与上面的 F2 同源）。

### D2：融合留在应用层 —— 不是"退而求其次"，而是 PG 上没有可下推的东西

**候选**：① 应用层融合（现状形态，改后端）；② RRF 写进 SQL；③ 换用有原生融合的引擎（Milvus / OpenSearch）；④ ParadeDB `pg_search` 的原生 hybrid。

**选 ①。**

**一手证据**（详见 `docs/tmp/deep-research-hybrid-fusion-location.md`）：

- **pgvector 官方不提供任何融合能力。** README「Hybrid Search」节全文只有一句 "You can **use** Reciprocal Rank Fusion or a cross-encoder to combine results" —— 告知可行，不提供实现。
- **ParadeDB `pg_search` 到 0.25.9（2026-09-11）仍把 `Native Hybrid Search` 标为 `coming soon`**；`|||` 是 BM25 的 match disjunction 算子，**不是融合算子**。
- 反观 10 个系统里 8 个**有**原生融合（ES 8.16+ / OpenSearch 2.19+ / Milvus 2.4+ / Weaviate 1.20+ / Qdrant 1.10+(加权 1.17) / Azure / Vespa / MongoDB 8.0+），官方文档也一律推荐库内融合。**但 pgvector 恰是"没有"的那一个。**

**因此 ② 的实质不是"利用引擎能力"，而是"把 RRF 公式手写进 SQL 字符串"**（ParadeDB / Apache Doris / Azure HorizonDB 三家官方示例都是手写 CTE + `ROW_NUMBER()` + `1.0/(60+r)`），代价明确而收益为零：

- ParadeDB 官方警告**必须加 tiebreaker**，否则同一条查询两次运行可能返回不同候选 → 与现有 `rrf_fusion`（`src/infra/search/bm25_index.py:120-152`，纯 Python dict + `sorted`）的确定性需要逐条对齐，否则引入不可复现的漂移。
- 三家示例都绑上各自专有语法（`|||`/`@@@`/`pdb.score()`、`Doris` 的 `MATCH_ANY`、HorizonDB 的 `azure_ai`）→ 换实现即重写。
- 2 次往返变 1 次、去重下推省下的传输量：`k=30` 下 60 行 vs 50 行，**无关痛痒**。

**必须把两条不同的论据分开**（初稿把它们揉成一条，是论证错误）：

- **论据 ①（约束，本项目适用）：Postgres 生态缺乏可用的融合能力。** pgvector 本体不提供；ParadeDB 到 0.25.9 仍标 `coming soon`；其余可选项要么无托管（破"能用托管就用托管"原则），要么需要 RDS 预装扩展（可用性未确认）。**因此"下推"在本栈上只是把手写 RRF 搬进 SQL。**
- **论据 ②（业界取向，只用来说明"应用层融合不是落后"）：即便引擎提供原生融合，需要加权与多租户可配的产品仍会选择应用层。** 证据：WeKnora 有 OpenSearch 后端（其 2.19 有原生 RRF）却把**加权 RRF 写在 Go 里**（`internal/application/service/knowledgebase_search_fusion.go:84-125`，配置项 `RRFK` / `RRFVectorWeight=0.5` / `RRFKeywordWeight=0.3`，per-tenant 可配）；`financial_rag-main`（pgvector + tsvector，与本变更同栈）在 Python 里做**按 domain 加权**的 RRF；Dify 连关键词打分都在 Python。**Elasticsearch 原生 RRF 官方明说"各 child retriever 权重必须相等"** —— 加权能力正是原生方案的缺口。

⚠ **WeKnora 属论据 ②，不能用来支持论据 ①。** 它是"有原生能力却不用"，不是"没有原生能力"。初稿把它当作"PG 无融合"的佐证，属类比错位，此处更正。

**未选 ③**：会把选型从"收敛到一个托管 PG"推回"PG + 另一个检索引擎"（Milvus 需托管版；OpenSearch 需自建或托管版），与本变更的首要目标（收敛、少一个自建状态组件）冲突。若将来"融合由引擎负责"成为硬需求，这是一条需要重开评估的路，**不是本变更可以顺手兼容的**。

### D3：`chunks` 表结构 —— `kb_id` 列取代 collection-per-KB

```
chunks
  id            text  PK          -- 保持 "{doc_id}:{chunk_index}" 格式（store.py:44）
  kb_id         text  NOT NULL FK → knowledge_base(id)   -- 取代"每库一个 collection"
  doc_id        text  NOT NULL
  chunk_index   int   NOT NULL
  chunk_total   int   NOT NULL
  content       text  NOT NULL
  content_seg   text  NOT NULL    -- jieba 预分词后的文本（见 D4）
  tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', content_seg)) STORED
  embedding     vector(1024)      -- DashScope text-embedding-v3
  source        text  NOT NULL DEFAULT ''
  page          int   NOT NULL DEFAULT 0
  metadata      jsonb NOT NULL DEFAULT '{}'   -- chunker 产出、非上列的所有其它键
唯一约束  (kb_id, doc_id, chunk_index)
索引      (kb_id) btree  ·  tsv GIN  ·  (doc_id) btree
```

**DDL 前置**：该表依赖 `vector` 扩展，创建表之前 SHALL 执行 `CREATE EXTENSION IF NOT EXISTS vector;`。**该语句放在 alembic 首版迁移里，不放在 `deploy/postgres/init/`** —— initdb 脚本只在数据目录首次初始化时执行（见 D8 与 tasks 2.6），dev 的既有数据卷不会重跑、prod 指向 RDS 时脚本根本不参与；只有放进迁移才幂等且两条路径都生效。

**读取契约（必须显式约定，否则静默破坏去重与引用）**：`ChunkResult.metadata` SHALL 在读取时由**列值 + jsonb 平铺合并**回填（冲突以列为准），至少包含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`。

⚠ **jsonb 侧必须原样承载 chunker 的全部自定义键，不得只保留上面 5 个契约键。** 1.2 实测里库里已有 `parent_content`（另有 `heading_path` / `block_type` / `currency` / `quarter` / `report_type` / `year` / `company` / `report_period` / `sec_code`）—— `parent_content` 是 `retrieval-fetch-and-dedup` 做**父块级去重**的输入；搬迁与写入两侧都丢掉它，会让那个变更的靶子凭空消失。

理由不是洁癖：`_dedup_by_doc_id` 读的是 `r.metadata.get("doc_id")`（`src/rag/retrieval.py:53`），而 `doc_id` 在新表里是**列**。若不回填，该分支对所有结果取 `None` → 走"无 doc_id 则保留"路径 → **去重在无声中完全失效**，同文档分块重新占满候选窗口；同时 `rag_tools.py:171-176` 的 `source`/`page`/`doc_id` 变空、`retrieval.py:187-188` 的实体透传归零（引用与来源渲染一并损坏）。三者都不会报错。

**决策与理由**

- **`doc_id` / `chunk_index` / `chunk_total` / `source` / `page` 升为列**（不留在 jsonb 里）：这五个键是**契约字段**（`store.py:47-51`、`rag_tools.py:171-176` 读 `source`/`page`/`doc_id`），升列后可被约束与索引，且 jsonb 只承载 chunker 的自定义键。
- **`id` 格式保持不变**：`ChunkResult.id` 被 `RagContext.chunk_id`（`rag_tools.py:174`）与引用渲染消费，改格式会波及 citation。
- **不建 HNSW**：164 MB / 4 万分块规模下，`kb_id` btree + 精确扫描是几十毫秒级且**召回精确**，顺带避开 pgvector 与 Chroma 共有的"近似索引 + WHERE 过滤导致返回不足 k 条"的坑。若将来规模或延迟要求变化，再加 HNSW 是纯增量（`CREATE INDEX` 不改 schema）。
- **`upsert` 语义**：Chroma 的 `collection.add` 遇重复 id 抛错（无 upsert），PG 侧用 `INSERT ... ON CONFLICT` —— 重复入库不再依赖捕获异常。
  ⚠ **但只有纯幂等插入能这么改**（P1 实施时据实收窄）：`ChatRepo.create_session` 是「主键冲突静默跳过」→ `ON CONFLICT DO NOTHING` 等价。`KbRepo.get_or_create_kb` 是**三态语义**（新建 / 复活软删 / 已存在活跃），返回值 `(kb_id, created)` 被 `kb_service` 消费，而 `ON CONFLICT DO UPDATE` 的 `RETURNING` 只能看到更新后的行，**无法区分"原本活跃"与"刚被复活"** → 硬改会改掉返回值。该处保持「插入撞唯一键 → 回滚 → 回读」的异常兜底，并补 docstring 写明三态契约。`document` / `eval` / `user` 三个 repo 本就没有 `IntegrityError` 捕获，无需改动。因此本变更实际改动的是 **1 处，不是 5 处**。

### D4：中文词法检索 —— jieba 预分词 + `to_tsvector('simple')`，**不依赖任何 PG 扩展**

**问题（已实测确认，不再是推断）**：PG 默认的 tsvector 分词器**不对中文分词**。2026-09-19 在 `postgres:15-alpine` 上实测：

```
to_tsvector('simple','营业收入同比增长率保持稳定')
  → '营业收入同比增长率保持稳定':1                    ← 整串 CJK 只产出 1 个 token
  count(*) = 1

to_tsvector('simple','营业收入 同比 增长 率 保持 稳定')   ← 手工空格切分（只为验证 PG 侧行为，非 jieba 输出）
  → '保持':5 '同比':2 '增长':3 '率':4 '稳定':6 '营业收入':1   ← 6 个 token，按词切分 ✅

plainto_tsquery('simple','营业收入 增长')  → '营业收入' & '增长'   ← AND 语义
to_tsvector('simple','营业收入 同比 增长') @@ plainto_tsquery('simple','营业收入 增长')  → t
to_tsvector('simple','营业收入 同比 增长') @@ plainto_tsquery('simple','营业收入 负债')  → f
```

这同时回答了为什么 `financial_rag-main` 的稀疏侧在中文上失效：它存的是**未预分词**的 `content`，却用 `to_tsvector('simple', content)`（`530.sql:585-586`）。

**真实 `jieba.lcut` 输出（jieba 0.42.1，2026-09-19 实测，替换初稿的人工构造示例）**：

| 原文 | `lcut`（未过滤） | 过滤 `len>=2` 后 |
|---|---|---|
| `营业收入同比增长率保持稳定` | `['营业','收入','同比','增长率','保持稳定']` | 同左 |
| `公司资产负债率上升，研发费用 5 月增加` | `['公司','资产负债率','上升','，','研发','费用',' ','5',' ','月','增加']` | `['公司','资产负债率','上升','研发','费用','增加']` |
| `腾讯控股2024年全年营收6603亿元，净利润1941亿元` | `['腾讯','控股','2024','年','全年','营收','6603','亿元','，','净利润','1941','亿元']` | `['腾讯','控股','2024','全年','营收','6603','亿元','净利润','1941','亿元']` |

两个必须记住的推论：

1. **`率` 并不会被切出来** —— `增长率` 成词（初稿的更正成立）。过滤单字的**依据不是"jieba 会切出单字"**，而是：中文高频虚词（`的`/`了`/`是`/`在`/`本`/`及`）本身就是单字，文档频率接近 1，纳入检索文本会淹没精确词项的排序。
2. **`len>=2` 过滤顺带干掉了空格与中文标点**（`' '`、`'，'` 均为 len 1）→ 不需要额外维护停用词表，只做这一条硬规则即可（支持 Open Question #5 的结论）。
3. **`营业收入` 不是 jieba 词元**（被切成 `营业`+`收入`）→ 初稿与 specs 中把 `营业收入` 当词项的举例在真实 tsv 中**不存在**。这一事实同时是下面 H2 的直接成因。

**方案**：

```
写入： content ──jieba.lcut──▶ 过滤 len<2 ──▶ " ".join(tokens) ──▶ content_seg
        tsv = to_tsvector('simple', content_seg)     ← 空格已切出词边界，
                                                       `simple` 只需按非字母数字切分
查询： query ──jieba.lcut──▶ 过滤 len<2 ──▶ 逐词元转义 + 后缀 :* ──▶ to_tsquery('simple', …)
        词元全被滤掉时 ──▶ 回退子串匹配：content LIKE '%' || :q || '%'
```

⚠ **查询侧不是 `plainto_tsquery`**（初稿的写法已被实测否决，见 H2）。

**为什么可行**：`simple` 配置**永远可用**（不依赖云厂商扩展）。真正的依赖只有 Python 侧的 `jieba` —— 它**已声明在 `pyproject.toml:38`**，但**全仓库零调用**（`src/`、`tests/` 的 `.py` 里 grep 无命中；唯一用途是 `financial_rag-main` 拿它做实体提取）。本变更让这个僵尸依赖终于接线。

**为什么不用扩展**（备选与否决理由）：

| 方案 | 中文能力 | 需求 | 否决理由 |
|---|---|---|---|
| `zhparser` / `pg_jieba` | 真分词，最接近"中文 BM25" | 扩展需云厂商预装 | **可用性未确认**；不作为前置依赖（若 RDS 提供，见 Open Questions） |
| `pg_bigm` | bigram，中日韩常用 | 同上 | 同上 |
| `pg_trgm` | 子串/近似，无需分词器 | 官方镜像自带（本地已实测可用，v1.6） | 可用，但语义是 `similarity()` 三元组，与"词项加权"不同；**作为探针的对照候选之一**，不作首选 |
| ParadeDB `pg_search` | 真 BM25（tantivy） | **无托管** | 破"能用托管就用托管"原则 |

> **本地可对照面（1.3 实测）**：`pgvector/pgvector:pg15` 里 `pg_available_extensions` 只有 `vector` (0.8.6) 与 `pg_trgm` (1.6) —— **本地 dev 无法验证任何中文分词扩展**。`zhparser` / `pg_bigm` 只能若 RDS 提供才在 RDS 上测；`pg_trgm` 是本地唯一可对照项。

### D4 的三个硬缺陷（2026-09-19 实测暴露，初稿与两轮评审均未发现）

#### H1：「查询侧过滤后为空 → 回退为不过滤」是**无效兜底**

实测（写入侧按 `len>=2` 过滤后）：

| 查询 | 结果 |
|---|---|
| `plainto_tsquery('simple','月')` | **0 命中** |
| `plainto_tsquery('simple','涨')` | **0 命中** |
| `content LIKE '%月%'` | **1 命中** |
| `plainto_tsquery('simple','')` | 0 命中（`NOTICE: text-search query doesn't contain lexemes`） |

原因：写入侧已把单字从 `content_seg` 剔除，**tsv 里根本没有单字 lexeme** —— 所以查询侧"不过滤"得到的 `涨 & 了 & 吗` 同样搜不到任何东西。

**决定**：兜底 SHALL 落到**原始 `content` 的子串匹配**（`content LIKE '%' || :q || '%'`，与探针的命中判据同口径），SHALL NOT 写"回退为不过滤"。

#### H2：词形不一致 → **即使单词项查询也会 0 命中**

| 查询 | 结果 | 说明 |
|---|---|---|
| `plainto_tsquery('simple','增长')` | **0 命中** | 文档 tsv 里是 `增长率` |
| `plainto_tsquery('simple','营业收入')` | **0 命中** | 文档 tsv 里是 `营业` + `收入` |
| `plainto_tsquery('simple','营业')` | 1 命中 | 恰好等于词元 |
| `to_tsquery('simple','增长:*')` | **1 命中** | ✅ 前缀匹配救回 |
| `to_tsquery('simple','营业 & 收入:*')` | **1 命中** | ✅ |

这比初稿担心的"AND 语义使长查询召回偏严"**严重一个量级**：不是"长查询偏严"，而是**用户输入的自然词与文档词元不相等就完全搜不到**。`营业`/`收入` 这类切分尤甚。

**决定**：查询构造 SHALL 对每个词元加前缀通配 `:*`；`plainto_tsquery` 的 AND 语义**降为对照项**，不作落地形态。AND（前缀后）与 OR 两种组合由探针比较选定。

#### H3：改前缀匹配会引入 `tsquery` 解析面（H2 的修复自带新风险）

`plainto_tsquery` 对任意用户输入**安全**；`to_tsquery` 要求**预格式化语法**，实测：

| 输入 | `to_tsquery` | `plainto_tsquery` |
|---|---|---|
| `C&C` | `'c' & 'c'`（静默） | `'c' & 'c'` |
| `a:` | `'a'`（**静默丢弃** `:`） | 不适用 |
| `研发费用 5 月`（含空格） | **ERROR: syntax error in tsquery** | `'研发费用' & '5' & '月'` |

**决定**：查询串 SHALL 在程序侧构造 —— 每个词元先按安全字符集（CJK / 字母 / 数字 / 下划线）剔除，再输出 `token:*`，以 ` & ` 连接；剔除后为空则整体降级为 H1 的子串兜底。
**该构造点是继"分词同源"之后第二个可静默降召回或抛错的入口，SHALL 有自己的守卫测试。**

**硬约束（必须做成守卫测试）**：以下三条都不报错、只静默降召回或抛错，每条都要有守卫测试：

1. 写入侧与查询侧**必须用同一份 jieba 配置与词典**；
2. 查询串的**词元转义与前缀拼接**必须正确（H3）；
3. 词元全被过滤掉时必须走**子串兜底**而非空条件（H1）。

**版本漂移是同一风险的第二种形态**：`tsv` 是 `content_seg` 的 `STORED` 生成列，因此**分词结果一旦落库就固化了**。jieba 升级或词典变更后，存量 `content_seg` 与查询侧新分词器不一致 → 同样静默降召回，而"同一进程内两函数比较"的守卫测试**抓不到**它。缓解：`jieba` 依赖 pin 精确版本；并把"分词器配置或版本变更必须触发 `content_seg` 全量重写"写成不变量与迁移检查。

**命名纪律**：`ts_rank` / `ts_rank_cd` **不是 BM25**（是 cover-density 排名）。字段名**不得**沿用 `bm25_score` 的叫法（那是 `financial_rag-main` 的命名错误），用 `ts_rank_score` 或 `lexical_score`。

### D5：删除 `bm25_index.py`；`rrf_fusion` 保留并迁移

- `bm25_index.py` 的**索引生命周期**（`build_index` / `rebuild_from_results` / `delete_index` / pickle 读写 / 索引文件路径）**全部删除** —— 其载体（独立索引文件）不复存在。
- `rrf_fusion` / `rrf_fusion_multi`（`bm25_index.py:120-181`）是**纯函数**，**原样迁移**到独立模块（建议 `src/rag/fusion.py`），其测试与调用点只需改 import。
- `document_service.py:572` 的"每文档入库后全量重建词法索引"**删除** —— 该 O(n) 重建与随之而来的失败静默（`:145-150`）一并消失。

**融合参数保持现状：仅 `k` 与 `top_n` 可配，本变更 SHALL NOT 引入两路权重。** 现行 `rrf_fusion`（`bm25_index.py:120-125`）的签名是 `(dense, bm25_res, k=60, top_n=50)`，**没有权重参数**。初稿的 `hybrid-retrieval` delta 要求"两路权重可配"，与"原样迁移、行为不变"直接矛盾 —— 加权重会改变融合输出，从而污染 dense 迁移等价性的验证框架。加权 RRF 是**能力新增**（参照项目 WeKnora / `financial_rag-main` 都有），应作为**独立变更**并配自己的验收，不塞进这次存储替换。

### D6：两套验收 —— 因为分词口径变了，两路不能共用同一判据

**背景**：本次同时发生两件事 ——（a）存储替换（**不应改变行为**）、（b）词法侧算法替换（**必然改变行为**：char-unigram `rank_bm25` → jieba 词项 + `ts_rank`）。把两者混在一个判据里，整个变更就变成不可验证的。

| 支路 | 判据 | 为什么在 176 分块上可判定 |
|---|---|---|
| **dense** | **迁移等价性**：同一份语料、同一批 query，pgvector 的 top-k 与 Chroma 的 top-k 高度重合（差异只来自浮点与索引近似） | 差分测量：语料与查询不变，只有存储换了 → 噪声抵消 |
| **sparse** | **词项命中探针**：取语料里实际出现的 N 个有区分度中文词项，断言"含该词项的 chunk"进 top-k；并**横向比较** `字符级`(今天，基线) / `jieba+simple` / `pg_trgm`(本地已可用) / （若 RDS 提供）`zhparser`·`pg_bigm` | 每个词项是**二值命中**判断，不是连续质量分 —— 二值量的方差远小于均值型指标 |
| **端到端答案（RAGAS）** | **明确不在本次验收内**，登记为"语料到位后再做" | 176 分块上 RAGAS 的方差会盖过信号 |

**探针集从语料自身派生**，不需要外部标注 —— 这是它能在小语料上成立的原因。它也正好打在今天最弱的地方：字符级 unigram 会把「资产负债率」拆成 6 个单字，任何含「资」或「产」的 chunk 都被命中，精确词项反而被淹没。

**但"可判定"必须落到具体判据，初稿只写了原则（评审 F6）**。固化如下：

| 判据 | 具体定义 |
|---|---|
| dense 阈值 | 固定查询集（**≥20 条**，覆盖单 KB 的中文/数值/时间三类），比对替换前后 top-k（k 取 `TOP_K_RETRIEVAL`）的**重合率 ≥ 0.9**；未达标先查 distance 语义与 WHERE 条件，不进入下一步。**只覆盖单 `kb_id` 路径**（全局路径随 `similarity_search_all` 一并删除） |
| 探针词项来源 | 从**原始 `content`** 正则抽取（**不得**用 `content_seg` 或 tsquery 自判，那会让同一分词器既造索引又造标签 = 自我循环） |
| 探针词项筛选 | 统计每个候选词的文档频率 `df`，**只保留 `2 ≤ df ≤ 0.1 × 语料分块数`** 的词项 —— 剔除 `df=1`（无从判断召回）与高频词（平凡通过）。长度 ≥ 2 字（与写入侧的单字过滤同规则） |
| 命中判据 | "含该词项的分块"以**原始 `content` 的字符串包含**判定，与分词器无关 |
| 横向比较的可归因性 | 比较不同分词配置时**固定打分算法**（或用与打分无关的量：**词项可召回率**）。否则"字符级 + BM25" vs "jieba + ts_rank" 同时改了两个变量，无法归因 |
| 报告口径 | 必须报**命中数 / 词项总数**与词项清单，并声明**仅供相对比较**，不得作为质量基线或发布判据 |
| **必测的失效类** | 除常规词项外，SHALL 显式包含两类用例：① **查询经分词后词元全部被滤掉**（如「涨了吗」「5 月」）→ 走子串兜底（H1）；② **探针词项与写侧词元不同形**（如词项 `营业收入`／`增长` vs 写侧 `营业`+`收入`／`增长率`）→ 断言仍可召回（H2）。探针自带 `len>=2` 过滤与"原文字符串包含"判据，会让这两类**永远测不到**，必须显式补 |
| **查询构造也是被比较的变量** | H2 的修复让"查询构造方式"（前缀 AND / 前缀 OR / `plainto_tsquery` AND / 子串）成为与分词器**并列**的变量。比较时 SHALL 只在同一分词器内改变查询构造，或只比较词项可召回率，SHALL NOT 与分词器变更同时进行 |

**小语料下的统计力限制要明说**：176 个分块、k=30~50 时，单个词项的命中集合可能已达语料的 17%–28%，多数词项会**平凡通过**。因此探针的正确用途是**在同一语料上横向比配置**（相对判据），不是给出绝对命中率门槛。

**副产品**：这套探针同时是 **D4 中扩展方案的选型依据** —— 用它实测而不是猜。**反面教材**：`financial_rag-main` 若有这套探针，早就会发现自己的中文检索是坏的。

### D7：事务边界 —— embedding 在事务外，chunks 与文档状态同事务

```
现在（document_service.py）：
  :541 写 Chroma ─┐
  :551 更新 MySQL 状态  ├─ 三步跨三店，无事务 → 进程死在中间 = 孤儿
  :572 重建 BM25 索引  ┘

之后：
  embedding 计算（慢，网络调用，【事务外】）
    ↓
  BEGIN
    INSERT chunks（含 embedding / content_seg / tsv 由生成列算）
    UPDATE document SET status='ready', chunk_count=N, processing_state='completed'
  COMMIT
```

**为什么 embedding 必须在事务外**：它是外网调用（DashScope），耗时数百毫秒到数秒，放进事务会长时间持有连接与锁。

**但初稿的表述与现有代码不相容，必须修正**：`embedding` 目前**不是无条件预计算**的 —— 只有 `CHUNK_EVAL_ENABLED` 为真时才在 `document_service.py:509-516` 预计算并复用于两处；开关为假时 `chunk_embeddings=None`，向量由 `add_chunks` 内部产生（`vector_store/store.py:53-55`）。若照初稿把 `add_chunks` 包进 `BEGIN`，在开关关闭的配置下，DashScope 调用就会落在**事务内**。

**因此**：embedding SHALL **无条件预计算**（与 `CHUNK_EVAL_ENABLED` 解耦，开关只决定"是否额外做分块质量评估"），写入事务只接收已算好的向量。

**删除路径同样需要同事务（初稿漏了）**：

```
现在： delete_document（document_service.py:106-132）
        删分块失败 → 仅 warning（:126） → 随后照样软删文档
        → 永久孤儿分块（且 KB 软删后仍可能被全局检索扫到）

      delete_knowledge_base（app_service.py:98-116）
        软删文档 → 删 collection → 删索引 → 软删 KB，四步跨三店无事务

之后： DELETE FROM chunks WHERE doc_id = $1  与  软删文档      同事务
      DELETE FROM chunks WHERE kb_id  = $1  与  软删 KB        同事务
      删除失败 SHALL NOT 被吞掉（不得"warning 后继续软删"）
```

### D8：`VectorStore` 契约**扩展**而非重写

保持现有 11 个方法的名称与签名（`typed-data-layer` 的既有 requirement 明文要求"接口不变"），**新增**按支路取 top-k 的入口，使两路结果各自携带名次（分路可辨）。

- `similarity_search(kb_id, query, k)` 语义不变（余弦**距离**），只换后端。
- 新增的方法返回带 `dense_rank` / `sparse_rank` 的结果（在 `ChunkResult` 上增字段或返回并集）。
- **`similarity_search_all` 删除**（含其 wrapper `vector_store/__init__.py:86-107`、`search.py:75-113` 实现、以及 `tests/` 中的两条用例）。理由：唯一调用点是 `retrieval.py:100` 的 `if not kb_id` 分支，而 `rag_tools.py:135-139` 在 `kb_id` 为空时**直接返回 `[]`、根本不调检索**（docstring 明写"KB=RAG 开关硬保证"）→ 该分支在生产链路上**不可达，只被测试养着**。
  ⚠ **更正**（评审指出初稿的论据错误）：初稿称它与单表检索"语义不等价"。**这不成立** —— `search.py:75-113` 的实现是"每个 collection 以 `k` 取 top-k，合并后 `[:k]`"，而 `retrieval.py:100` 传入的 `k` 与最终 `k` 相同；此时"逐 collection top-k 的并集"必然包含全局 top-k，排序后取 k 即等于全局 top-k，与单表 `ORDER BY ... LIMIT k` **等价**。删除的正当理由只有"生产链路不可达"这一条。
- `similarity_search` 里 Chroma 的 `n_results=min(k, 100)` 是 **Chroma 的硬上限**（`search.py:44`），PG 无此限 → **保留该上限并注释来源**，避免把"能力提升"混进等价性验收。
- `similarity_search_multi` 在 `src/` 里**无调用方** → 确认后删除。
- 契约变更须同步 `docs/agents/api_contract.md` 与受影响测试断言（`CLAUDE.md` 的契约同步要求）。

### D9：在途变更处置

| change | 处置 |
|---|---|
| `bm25-index-durability`（38 任务、已 `Approve`） | **作废**。靶子（索引文件持久化 / 原子写 / 损坏自愈 / 缺失降级）在新形态下不存在。其"缺失可见"的价值并入本变更的 `observability-logging` delta |
| `retrieval-fetch-and-dedup`（33 任务、0/33） | **重定基**。它的取数口径（候选池 30 / 父块级去重 / 分数形态采样）都在应用层继续成立，只需把"候选池从哪来"换成 PG 的两路 `LIMIT`；其 delta 需在本变更之后重写一次 |
| `prompt-layering-and-domain-binding` / `skill-external-sources` / `e2e-playwright-regression` / `turn-provenance-observability` / `llm-callback-handler` | 不受影响 |

**顺序：本变更先于 `retrieval-fetch-and-dedup`。** 先改底层再改口径，只需重定基一次；反过来要在移动的靶子上写 delta。

**`TOP_K_RETRIEVAL` 默认值的三方不一致由谁收口（评审 Blocking OQ）—— 定为 `retrieval-fetch-and-dedup`。** 现状：代码已是 `30`（`settings.py:179`）、在效 spec 写 `10`（`retrieval-quality` 的 "Retrieval parameter configuration"）、该 change 声称"已先行落地 30"。**该 requirement 不在本变更的 delta 内**，故本变更**不碰**它；由 `retrieval-fetch-and-dedup` 在自己的 delta 里收口并在 proposal 中显式写成"本变更负责修正"。`tasks.md` 只做通知，不承担修正 —— 否则两方都以为对方会改，归档后仍是假陈述。

## Risks / Trade-offs

- **[词法结果必然变化，且当前无法判定好坏]** → 明确接受：本次只承诺"词项命中能力不下降"（探针可判），端到端质量推到语料到位后。**风险不会因为不写而消失，只会变成"无人知晓的回归"** —— 所以探针是必配，不是可选。
- **[词元与查询词形不一致 → 单词项查询 0 命中]**（H2，2026-09-19 实测发现）→ 缓解：查询侧加前缀通配 `:*`。残余风险：前缀匹配会带来 `增长:*` 命中 `增长率`/`增长量` 这类误召回，且无法命中"词元中间"的差异（如查询 `资产负债` 匹配不到 `资产负债率` 的前缀之外形态）。**这是本变更最需要探针量化的风险。**
- **[查询串构造与转义出错的抛错面]**（H3）→ `to_tsquery` 对含空格/操作符的输入会**抛异常**（不是静默），在 `asyncio.gather` 下影响面更大。缓解：程序侧构造 + 安全字符集剔除 + 专门守卫测试。
- **["过滤后为空"的兜底形同虚设]**（H1）→ 初稿写的"回退为不过滤"经实测**无效**（写侧已把单字从 tsv 剔除）。缓解：兜底改走原始 `content` 子串匹配，且必须有测试覆盖「涨了吗」「5 月」这类全单字查询。
- **[jieba 写入/查询口径漂移]** → 静默降召回，不报错。缓解：同一份配置集中在一处（`src/config/`）+ 一条守卫测试（写入分词结果与查询分词结果用同一函数）。
- **[176 分块的探针统计力弱]** → 只用于**选型横向对比**（相对判据），不产出绝对质量结论。这一点要写进验收文档，防止后人把探针数字当质量基线。
- **[`ts_rank` 不是 BM25]** → 命名必须诚实（`ts_rank_score`），否则会重复 `financial_rag-main` 的误导。若将来必须真 BM25：ParadeDB（无托管）或应用层自算，都是**另一次评估**。
- **[9p 文件系统]** → **已规避**：`postgres_data` 是 docker named volume（在 docker VM 内），不是 bind mount。**这条必须守住** —— 9p 上的 PG 比 BM25 脆弱得多，而我们刚在 `bm25-index-durability` 上踩过一次。
- **[dev 与 prod 的 compose 标识全同]** → 同机**不可共存**（容器名冲突，第二个 `up` 直接失败）。缓解：D1 的"不同机"前置写进 compose 注释与部署文档。
- **[prod 的 Chroma 无持久化挂载]**（1.4 顺带发现，既有缺陷）→ prod 的 `app` 只挂 `app_logs`/`chroma_onnx_cache`/`./skills`/`./agents`，**没有 `./data`** → prod 的 Chroma 落在容器可写层，容器重建即丢。本变更"删除 Chroma"会一并消除它；须在 proposal/ADR 里写明这是**既有缺陷被本变更顺带修掉**，以免被误认为新引入。
- **[双套 ORM 合并时漏字段]** → `agent`/`process` 两列只存在于运行时那套。缓解：以 `src/infra/db/models/` 为准，合并后用一条断言对照两张表的列集合。
- **[切换无并行验证]** → 数据可丢，故不设双写/影子读；但 **Chroma 数据须保留到 dense 等价性验证通过**（冻结只读、不再写入）。"数据可丢"指不需要为业务连续性保留，不是要提前删。
- **[一次性切换窗口]** → 本变更无灰度的必要（生产未上线），但切换后若 dense 等价性不达标，回滚 = 恢复旧代码 + 旧 compose；因 Chroma 数据保留，回滚是可行的。

## Migration Plan

**前置（不阻塞设计，但不做就不能开工）**

0. **RDS 侧三件事（评测 1.1/1.3；本地无法代理，**仍未完成、阻塞**）**：① 扩展清单 `SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','zhparser','pg_jieba','pg_bigm','pg_trgm','pg_search');`；② **`vector` 是否可直接 `CREATE EXTENSION`（需要什么权限/账号）** —— RDS 的扩展创建通常要求高权限账号，这条不确认会在建表时卡住；③ 已装版本 `SELECT extversion FROM pg_extension WHERE extname='vector';`（对照 pgvector 当前 **0.8.6**：HNSW 需 ≥0.5、`hnsw.iterative_scan` 需 ≥0.8；升级用 `ALTER EXTENSION vector UPDATE;`）。**本变更的机制不依赖扩展清单**（走 jieba+simple），但 `vector` 是硬依赖。仓库内**没有任何 RDS 连接配置**（`.env` 只有 `MYSQL_*`），故无法代理验证。
0b. **Chroma → PG 向量能否原样搬迁 —— ✅ 已实测通过（2026-09-19）**：5 个 collection / 176 分块的 `documents`/`metadatas`/`embeddings` **全部可读**，维度 1024，无 `None` 行。**第 2 步与 dense 等价性验收成立**，不需要降级判据。记录见 `docs/tmp/postgres-probe-2026-09-19.md`。

**步骤**

1. **建 PG 与 schema**：
   - 镜像换 `pgvector/pgvector:pg15`（pin 版本更好），大版本与 RDS 对齐
   - dev：`postgres` 去掉 langfuse profile 门、上调内存；**显式一次性创建应用 database + 最小权限账号**（既有卷不会重跑 initdb 脚本）
   - 首版迁移内含 `CREATE EXTENSION IF NOT EXISTS vector;`（幂等，dev 与 prod 都生效）
   - 合并双套 models 与双套 alembic
2. **数据搬迁（仅用于验收）**：一次性脚本从 Chroma 读出全部 176 个分块的 `documents`/`metadatas`/`embeddings`，原样写入 `chunks`。**目的是让 dense 等价性成为可判定的差分** —— 若走"重新入库"，分块与 embedding 都会变，等价性就失去依据。**前提是前置 0b 通过**；不通过则 dense 侧改用与词法相同的探针。
3. **dense 等价性验证**：≥20 条固定查询 × 单 `kb_id` 路径，top-k 重合率 ≥ 0.9（见 D6）。不达标则先查 distance 语义与 WHERE 条件，不进入下一步。
4. **词法选型与验证**：用词项命中探针（D6 的固化判据）横向比较 `字符级`(今天，作为基线) / `jieba+simple` / `pg_trgm`(本地已可用) /（若 RDS 提供）`zhparser`，**固定打分算法或只比词项可召回率**，记录命中数与词项清单。**查询构造（前缀 AND / 前缀 OR / 子串）作为与分词器并列的第二个变量单独比较**，不同时改两个变量。
5. **切代码**：`engine.py` → repos → `vector_store/`（含**删除 `similarity_search_all` 与其分支、测试**）→ 删除 `bm25_index.py` 并迁移 `rrf_fusion` → `retrieval.py` → `document_service.py`（**入库与删除两条路径事务化** + 去 BM25 重建 + embedding 无条件预计算）→ `app_service.py` 装配 → `src/main.py`（删 Chroma warmup 及其事件）→ `rag_tools.py`（`search()` 去 bm25 形参）→ 5 个 CLI 脚本。
6. **compose 改造**：dev 的 `postgres` 去 profile 门、换镜像、上调内存、`app` 加 `depends_on: postgres(service_healthy)`；退役 MySQL 服务与 Chroma/BM25 的卷。
7. **事务化验收（故障注入）**：在 `INSERT chunks` 与 `UPDATE document` 之间注入异常，确认**两者都不落库**；同法验证删除路径（删分块失败时文档 SHALL NOT 被软删）。
8. **prod 指向 RDS**：改 `docker-compose.prod.yml`，写入"不同机"前置；在 RDS 上预建应用库与最小权限账号、确认 `CREATE EXTENSION vector` 可执行；把连接预算作为实例规格输入。
9. **清理收尾**：删依赖（`chromadb` / `rank_bm25` / `aiomysql`）、删 `data/chroma_persist` 与 `data/bm25_index`、删 `deploy/chroma/Dockerfile`（未被任何 compose 引用）、`deploy/mysql/init` → `deploy/postgres/init`；更新 `code-map.md` / `api_contract.md` / `data-flow.md` / `glossary.md` / `defensive-patterns.md`；写 ADR（存储收敛 + 融合位置 + **共享实例的四条运维后果**，见 D1）。

**回滚**

- 代码回滚 = 恢复旧实现与旧 compose；Chroma 数据保留到第 3 步通过，故可回滚。
- **注意**：若已执行第 9 步（删了 `data/chroma_persist`），回滚不再可能 —— 因此第 9 步必须放在验收全部通过之后。

## Open Questions

**已在本轮修订中定案（不再开放）**

- ~~`plainto_tsquery` 的 AND 语义~~ → **实测确认是 AND**（`'营业收入' & '增长'`）。~~**决定：先用 AND 落地**~~ → **该决定已被 2026-09-19 实测推翻（H2）**：`plainto_tsquery` 的单词元查询在词形不一致时**直接 0 命中**（`增长` vs 文档词元 `增长率`；`营业收入` vs `营业`+`收入`），不是"长查询偏严"。**改定：查询侧用前缀匹配 `to_tsquery('simple','token:* & …')`，AND 与 OR 两种组合由探针比较选定。**
- ~~Chroma 能否原样读出 176 条分块的 embeddings~~ → **已实测通过（1.2）**：5 个 collection / 176 分块 / 维度 1024 / 无 `None` 行，`parent_content` 已在 metadata 中。
- ~~本地能否验证 PG 中文分词扩展~~ → **已实测（1.3）**：`pgvector/pgvector:pg15` 只提供 `vector` 0.8.6 与 `pg_trgm` 1.6，**无** `zhparser`/`pg_jieba`/`pg_bigm`/`pg_search` → 本地只能对照 `pg_trgm`。
- ~~`similarity_search` 的 `min(k, 100)` 上限~~ → **决定保留并注释来源**（Chroma 硬限），避免把能力提升混进等价性验收。
- ~~`similarity_search_multi`~~ → **决定删除**（`src/` 无调用方，确认后删）。
- ~~`similarity_search_all`~~ → **决定删除**（理由：生产链路不可达；**初稿的"语义不等价"论据已更正为不成立**，见 D8）。
- ~~`metadata` 哪些键升列~~ → **决定** `doc_id`/`chunk_index`/`chunk_total`/`source`/`page` 升列并**在读取时回填进 `metadata`**（见 D3）。
- ~~是否引入两路权重~~ → **决定不引入**（加权 RRF 是能力新增，须独立验收，见 D5）。
- ~~`TOP_K_RETRIEVAL` 三方不一致由谁收口~~ → **决定由 `retrieval-fetch-and-dedup` 收口**，本变更只通知（见 D9）。

**仍需在实施前解决**

1. **（Blocking 事实）RDS 上 `vector` 能否直接 `CREATE EXTENSION`、需要什么权限？** 以及 RDS 是否预置 `zhparser` / `pg_jieba` / `pg_bigm` / `pg_trgm`。**推荐**：扩展清单只影响探针的对照候选（本变更不依赖），但 `vector` 的创建权限是**硬前置** —— 若需高权限账号，必须在部署文档里写明由谁执行。**本地已证机制可行**（超级用户路径 `CREATE EXTENSION IF NOT EXISTS vector` 成功，`<=>` 返回余弦距离 0~2，与 Chroma 一致），未证的只有 RDS 权限模型。
2. ~~**（Blocking 事实）Chroma 能否原样读出 176 条分块的 embeddings？**~~ → **已解决（1.2 通过）**，dense 侧按迁移等价性验收，不需降级判据。
3. **`ts_rank` 还是 `ts_rank_cd`？** **推荐**：用探针实测决定（与分词配置分开测，保持可归因性）。实测量纲差异大（同一查询同一文档：`ts_rank` 0.0985 / `ts_rank_cd` 0.05）→ 横向比较必须固定打分算法。
4. **`VectorStore` 新增方法的命名与签名？** 倾向 `dense_rank` / `sparse_rank` 作为 `ChunkResult` 可选字段 + 一个返回两路并集的方法（名字待定）。**该项是契约变更**，须同步 `api_contract.md`。属实现局部，可留到编码时定。
5. **单字 token 之外是否还要过滤停用词？** 实测已确认（1.3 真实 `jieba.lcut`）：`率` **不会**被单独切出（`增长率` 成词）；被切出的单字是 `本`/`及`/`不`/`年`/`月`/`了`/`吗` 这类虚词与量词，且 `len>=2` 过滤顺带干掉空格与中文标点。**推荐**：本变更只做"长度 ≥ 2"这一条硬规则（可解释、可测试）；停用词表会引入一份需要维护的配置，等探针显示它在拖累排序再引入。
6. **prod 与 dev 是否部署在不同机器？** 两份 compose 的 `name`、`container_name`、**全部卷名**完全相同 → 同机**不可共存**（容器名冲突，第二个 `up` 直接失败；`--force-recreate` 会拆掉先起的那套）。**推荐**：不同机；若同机则必须先做命名隔离（不只是路径隔离）。这仍是一个**需要用户确认的事实**。
7. **prod 的 worker 数与连接预算**：`CLAUDE.md` 要求单 worker（进程内流式状态），`docker-compose.prod.yml:202` 实为 `--workers 4`；4 × (10+10) ≈ 80 连接 + Langfuse。**推荐**：本变更**只登记不处置**（不属存储替换），但在 ADR 里写成 RDS 规格的输入约束与既有冲突项。
