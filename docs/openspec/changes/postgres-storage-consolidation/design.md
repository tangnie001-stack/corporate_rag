## Context

**当前存储形态（三套独立存储，各自可有独立故障）**

| 数据 | 载体 | 位置 |
|---|---|---|
| 关系型（7 张表） | MySQL 8.0 + SQLAlchemy 2.0 async（`mysql+aiomysql`） | `src/infra/db/` |
| 分块与向量 | ChromaDB 嵌入式 `PersistentClient`，**每知识库一个 collection**（`kb_<hex>`） | `data/chroma_persist` |
| 词法索引 | 进程内 `rank_bm25.BM25Okapi` + pickle 索引文件 | `data/bm25_index/{kb_id}/bm25.pkl` |

**检索链路**（`src/rag/retrieval.py:82-96`）：`asyncio.gather(dense, bm25)` → `rrf_fusion` → `_dedup_by_doc_id` → rerank（`qwen3-rerank`）→ 生成。融合与去重都在应用层。

**实测环境事实（2026-09-18/19）**

- 未删除知识库 **1340** 个；Chroma 有 **691** 个 collection、**176** 条向量，其中**只有 5 个 collection 含分块**。单库最大 121 分块；`b9e74e82…` 51 分块 / 31,881 字符。
- `pickle.load` 单次 **58.2 ms**（冷页缓存 191.9 ms）；`BM25Okapi` 从原文重建 **5.6 ms**；`search` 每次调用都全量重载索引（`bm25_index.py:86-90`）。
- 向量规模：天花板千文档 ≈ 2.5–4 万分块 × 1024 维 ≈ **164 MB**。
- `data/` 在 `/mnt/d`（**9p**，fsync/原子性弱）且被 gitignore。`postgres_data` 是 **docker named volume**（在 docker VM 内），**已避开 9p 坑**。
- 当前 `postgres` 服务：dev 有 `profiles: ["langfuse"]` + `mem_limit: 256m` + DB/user 均为 `langfuse`；prod **本来就没有** `profiles:`，`mem_limit: 4g`。两份 compose 的 `postgres_data` 卷名相同（`corporate_rag_postgres_data`）。
- Langfuse worker/web 通过锚点 `&langfuse-depends` 依赖 `postgres`（`service_healthy`）；**`app` 目前不依赖 postgres**。

**既有烂账（本次必须一并处理，否则会带着错误走）**

- `src/infra/db/models/`（**运行时 Repo 实际 import 这套**）与 `src/infra/db/mysql_db/models/`（**alembic `target_metadata` 指向这套**）是两套重复定义且**已不一致**：前者 `SessionModel` 有 `agent`、`MessageModel` 有 `process`，后者没有。
- 两套 alembic 目录：根 `alembic/`（`alembic.ini` 实际指向）与 `src/infra/db/mysql_db/alembic/`，两者的 `e6304ba3a9ef_init_models.py` **内容不同**。
- `ChunkData` 两处定义：`src/chunking/validator.py:9-21`（写入侧在用）与 `src/parsers/base.py:17-31`（BM25 在用）。

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

**prod 侧**：指向阿里云 RDS，不再本地起库。**前提**：prod 与 dev 不同机 —— 两份 compose 的 `postgres_data` 卷名与 `./data/*` 路径相同，同机执行会互相污染（这条要写进 compose 注释与部署文档）。

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

**业界侧印证**：`financial_rag-main`（pgvector + tsvector，与本变更同栈）、WeKnora（**有 OpenSearch 后备却仍写在 Go 里**）、Dify（连关键词打分都在 Python）、RAGFlow 的 ES 路径 —— 全是应用层融合。WeKnora 与 `financial_rag-main` 都实现了**加权 RRF**，而 Elasticsearch 原生 RRF 官方明说"各 child retriever 权重必须相等" —— **加权与运行时按租户可配，正是应用层融合长期存在的理由。**

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

**决策与理由**

- **`doc_id` / `chunk_index` / `chunk_total` / `source` / `page` 升为列**（不留在 jsonb 里）：这五个键是**契约字段**（`store.py:47-51`、`rag_tools.py:171-176` 读 `source`/`page`/`doc_id`），升列后可被约束与索引，且 jsonb 只承载 chunker 的自定义键。
- **`id` 格式保持不变**：`ChunkResult.id` 被 `RagContext.chunk_id`（`rag_tools.py:174`）与引用渲染消费，改格式会波及 citation。
- **不建 HNSW**：164 MB / 4 万分块规模下，`kb_id` btree + 精确扫描是几十毫秒级且**召回精确**，顺带避开 pgvector 与 Chroma 共有的"近似索引 + WHERE 过滤导致返回不足 k 条"的坑。若将来规模或延迟要求变化，再加 HNSW 是纯增量（`CREATE INDEX` 不改 schema）。
- **`upsert` 语义**：Chroma 的 `collection.add` 遇重复 id 抛错（无 upsert），PG 侧用 `INSERT ... ON CONFLICT (kb_id, doc_id, chunk_index) DO UPDATE` —— 重复入库不再依赖捕获异常。

### D4：中文词法检索 —— jieba 预分词 + `to_tsvector('simple')`，**不依赖任何 PG 扩展**

**问题**：PG 默认的 tsvector 分词器**不对中文分词**（连续 CJK 会被当作单个 token）。这正是 `financial_rag-main` 的稀疏检索在中文上失效的原因（`530.sql:585-586` 是 `to_tsvector('simple', content)`，查询用 `plainto_tsquery('simple', ...)`）。**诚实标注：该分词器行为未在本机实测**（拉取 PG 镜像时 Docker Hub 不通），属 PG 已知特性，若需钉死应在 RDS 上跑一条 `to_tsvector` 验证。

**方案**：

```
写入： content ──jieba.lcut──▶ " ".join(tokens) ──▶ content_seg
        tsv = to_tsvector('simple', content_seg)     ← 空格已切出词边界，
                                                       `simple` 只需按非字母数字切分
查询： query ──jieba.lcut──▶ plainto_tsquery('simple', " ".join(tokens))
```

**为什么可行**：`simple` 配置**永远可用**（不依赖云厂商扩展）。真正的依赖只有 Python 侧的 `jieba` —— 它**已声明在 `pyproject.toml:38`**，但**全仓库零调用**（`src/`、`tests/` 的 `.py` 里 grep 无命中；唯一用途是 `financial_rag-main` 拿它做实体提取）。本变更让这个僵尸依赖终于接线。

**为什么不用扩展**（备选与否决理由）：

| 方案 | 中文能力 | 需求 | 否决理由 |
|---|---|---|---|
| `zhparser` / `pg_jieba` | 真分词，最接近"中文 BM25" | 扩展需云厂商预装 | **可用性未确认**；不作为前置依赖（若 RDS 提供，见 Open Questions） |
| `pg_bigm` | bigram，中日韩常用 | 同上 | 同上 |
| `pg_trgm` | 子串/近似，无需分词器 | 官方镜像自带，基本必有 | 可用，但语义是 `similarity()` 三元组，与"词项加权"不同；**作为探针的对照候选之一**，不作首选 |
| ParadeDB `pg_search` | 真 BM25（tantivy） | **无托管** | 破"能用托管就用托管"原则 |

**硬约束（必须做成守卫测试）**：写入侧与查询侧**必须用同一份 jieba 配置与词典**。不一致不会报错，只会静默降召回 —— 这是本方案唯一的新失效模式。

**命名纪律**：`ts_rank` / `ts_rank_cd` **不是 BM25**（是 cover-density 排名）。字段名**不得**沿用 `bm25_score` 的叫法（那是 `financial_rag-main` 的命名错误），用 `ts_rank_score` 或 `lexical_score`。

### D5：删除 `bm25_index.py`；`rrf_fusion` 保留并迁移

- `bm25_index.py` 的**索引生命周期**（`build_index` / `rebuild_from_results` / `delete_index` / pickle 读写 / 索引文件路径）**全部删除** —— 其载体（独立索引文件）不复存在。
- `rrf_fusion` / `rrf_fusion_multi`（`bm25_index.py:120-181`）是**纯函数**，**原样迁移**到独立模块（建议 `src/rag/fusion.py`），其测试与调用点只需改 import。
- `document_service.py:572` 的"每文档入库后全量重建词法索引"**删除** —— 该 O(n) 重建与随之而来的失败静默（`:145-150`）一并消失。

### D6：两套验收 —— 因为分词口径变了，两路不能共用同一判据

**背景**：本次同时发生两件事 ——（a）存储替换（**不应改变行为**）、（b）词法侧算法替换（**必然改变行为**：char-unigram `rank_bm25` → jieba 词项 + `ts_rank`）。把两者混在一个判据里，整个变更就变成不可验证的。

| 支路 | 判据 | 为什么在 176 分块上可判定 |
|---|---|---|
| **dense** | **迁移等价性**：同一份语料、同一批 query，pgvector 的 top-k 与 Chroma 的 top-k 高度重合（差异只来自浮点与索引近似） | 差分测量：语料与查询不变，只有存储换了 → 噪声抵消 |
| **sparse** | **词项命中探针**：取语料里实际出现的 N 个有区分度中文词项，断言"含该词项的 chunk"进 top-k；并**横向比较** `字符级`(今天) / `jieba+simple` / `pg_trgm` (若有扩展再加 `zhparser`) | 每个词项是**二值命中**判断，不是连续质量分 —— 二值量的方差远小于均值型指标 |
| **端到端答案（RAGAS）** | **明确不在本次验收内**，登记为"语料到位后再做" | 176 分块上 RAGAS 的方差会盖过信号 |

**探针集从语料自身派生**，不需要外部标注 —— 这是它能在小语料上成立的原因。它也正好打在今天最弱的地方：字符级 unigram 会把「资产负债率」拆成 6 个单字，任何含「资」或「产」的 chunk 都被命中，精确词项反而被淹没。

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

### D8：`VectorStore` 契约**扩展**而非重写

保持现有 11 个方法的名称与签名（`typed-data-layer` 的既有 requirement 明文要求"接口不变"），**新增**按支路取 top-k 的入口，使两路结果各自携带名次（分路可辨）。

- `similarity_search(kb_id, query, k)` 语义不变（余弦**距离**），只换后端。
- 新增的方法返回带 `dense_rank` / `sparse_rank` 的结果（在 `ChunkResult` 上增字段或返回并集）。
- `similarity_search` 里 Chroma 的 `n_results=min(k, 100)` 是**Chroma 的硬上限**，PG 无此限 → 见 Open Questions。
- `similarity_search_multi` 在 `src/` 里**无调用方** → 见 Open Questions。
- 契约变更须同步 `docs/agents/api_contract.md` 与受影响测试断言（`CLAUDE.md` 的契约同步要求）。

### D9：在途变更处置

| change | 处置 |
|---|---|
| `bm25-index-durability`（38 任务、已 `Approve`） | **作废**。靶子（索引文件持久化 / 原子写 / 损坏自愈 / 缺失降级）在新形态下不存在。其"缺失可见"的价值并入本变更的 `observability-logging` delta |
| `retrieval-fetch-and-dedup`（33 任务、0/33） | **重定基**。它的取数口径（候选池 30 / 父块级去重 / 分数形态采样）都在应用层继续成立，只需把"候选池从哪来"换成 PG 的两路 `LIMIT`；其 delta 需在本变更之后重写一次 |
| `prompt-layering-and-domain-binding` / `skill-external-sources` / `e2e-playwright-regression` / `turn-provenance-observability` / `llm-callback-handler` | 不受影响 |

**顺序：本变更先于 `retrieval-fetch-and-dedup`。** 先改底层再改口径，只需重定基一次；反过来要在移动的靶子上写 delta。

## Risks / Trade-offs

- **[词法结果必然变化，且当前无法判定好坏]** → 明确接受：本次只承诺"词项命中能力不下降"（探针可判），端到端质量推到语料到位后。**风险不会因为不写而消失，只会变成"无人知晓的回归"** —— 所以探针是必配，不是可选。
- **[jieba 写入/查询口径漂移]** → 静默降召回，不报错。缓解：同一份配置集中在一处（`src/config/`）+ 一条守卫测试（写入分词结果与查询分词结果用同一函数）。
- **[176 分块的探针统计力弱]** → 只用于**选型横向对比**（相对判据），不产出绝对质量结论。这一点要写进验收文档，防止后人把探针数字当质量基线。
- **[`ts_rank` 不是 BM25]** → 命名必须诚实（`ts_rank_score`），否则会重复 `financial_rag-main` 的误导。若将来必须真 BM25：ParadeDB（无托管）或应用层自算，都是**另一次评估**。
- **[9p 文件系统]** → **已规避**：`postgres_data` 是 docker named volume（在 docker VM 内），不是 bind mount。**这条必须守住** —— 9p 上的 PG 比 BM25 脆弱得多，而我们刚在 `bm25-index-durability` 上踩过一次。
- **[dev 与 prod 共用卷名/路径]** → 同机执行会互相污染。缓解：D1 的"不同机"前置写进 compose 注释与部署文档。
- **[双套 ORM 合并时漏字段]** → `agent`/`process` 两列只存在于运行时那套。缓解：以 `src/infra/db/models/` 为准，合并后用一条断言对照两张表的列集合。
- **[切换无并行验证]** → 数据可丢，故不设双写/影子读；但 **Chroma 数据须保留到 dense 等价性验证通过**（冻结只读、不再写入）。"数据可丢"指不需要为业务连续性保留，不是要提前删。
- **[一次性切换窗口]** → 本变更无灰度的必要（生产未上线），但切换后若 dense 等价性不达标，回滚 = 恢复旧代码 + 旧 compose；因 Chroma 数据保留，回滚是可行的。

## Migration Plan

**前置（不阻塞设计，但不做就不能开工）**

0. **RDS 扩展清单**：`SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','zhparser','pg_jieba','pg_bigm','pg_trgm');` 同时确认 pgvector 版本。**本变更不依赖结果**（D4 走 jieba+simple），但结果决定探针的对照候选与后续优化空间。

**步骤**

1. **建 PG 与 schema**：本地起 `postgres`（named volume、去掉 langfuse profile、上调内存、增建应用 database）；写首版迁移；合并双套 models 与双套 alembic。
2. **数据搬迁（仅用于验收）**：一次性脚本从 Chroma 读出全部 176 个分块的 `documents`/`metadatas`/`embeddings`，原样写入 `chunks`。**目的是让 dense 等价性成为可判定的差分** —— 若走"重新入库"，分块与 embedding 都会变，等价性就失去依据。
   - ⚠ 待确认：Chroma 能否原样读出 embeddings（见 Open Questions #4）。若不能，dense 侧退化为"用探针同时覆盖两路"。
3. **dense 等价性验证**：固定 query set，比对两库 top-k 重合率。不达标则先查 distance 语义与过滤条件，不进入下一步。
4. **词法选型与验证**：用词项命中探针横向比较 `字符级`(今天，作为基线) / `jieba+simple` /（若可用）`pg_trgm` / `zhparser`，选定配置并记录数字。
5. **切代码**：`engine.py` → repos → `vector_store/` → 删除 `bm25_index.py` 并迁移 `rrf_fusion` → `retrieval.py` → `document_service.py`（事务化 + 去 BM25 重建）→ `app_service.py` 装配 → 5 个 CLI 脚本。
6. **compose 改造**：dev 的 `postgres` 去 profile 门、上调内存、`app` 加 `depends_on: postgres(service_healthy)`；退役 MySQL 服务与 Chroma/BM25 的卷。
7. **事务化验收（故障注入）**：在 `INSERT chunks` 与 `UPDATE document` 之间注入异常，确认**两者都不落库**（无孤儿）。
8. **prod 指向 RDS**：改 `docker-compose.prod.yml`，写入"不同机"前置；确认 RDS 上 pgvector 可用与迁移账号权限。
9. **清理收尾**：删依赖（`chromadb` / `rank_bm25` / `aiomysql`）、删 `data/chroma_persist` 与 `data/bm25_index`、删 `deploy/chroma/Dockerfile`（未被任何 compose 引用）、`deploy/mysql/init` → `deploy/postgres/init`；更新 `code-map.md` / `api_contract.md` / `data-flow.md` / `glossary.md` / `defensive-patterns.md`；写 ADR（存储收敛 + 融合位置 + 共享实例的故障域代价）。

**回滚**

- 代码回滚 = 恢复旧实现与旧 compose；Chroma 数据保留到第 3 步通过，故可回滚。
- **注意**：若已执行第 9 步（删了 `data/chroma_persist`），回滚不再可能 —— 因此第 9 步必须放在验收全部通过之后。

## Open Questions

1. **RDS 是否提供 `zhparser` / `pg_jieba` / `pg_bigm` / `pg_trgm`？** 若有，是否改用引擎侧分词？**建议：本变更不依赖**（D4 的 jieba 路线无条件可用），把"引擎侧分词"作为**独立后续优化**，用同一套探针决定是否值得切换。理由：把不阻塞的决策提前，只会增加本变更的变数。
2. **`ts_rank` 还是 `ts_rank_cd`？查询用 `plainto_tsquery`（AND）还是 OR 组合？** AND 语义在长查询下召回偏严。**建议：用探针实测决定，不靠推理**（这正是探针的第一个用途）。
3. **dense 是否建 HNSW？** **建议：先不建**（164 MB / 4 万分块下精确扫描足够且召回精确）。需要 pgvector ≥ 0.5 才能建 HNSW；`hnsw.iterative_scan` 需 ≥ 0.8。此项不影响 schema，可后加。
4. **Chroma 能否原样读出 176 条分块的 embeddings？**（`collection.get(include=["embeddings"])`）**这条决定第 2 步的可行性，进而决定 dense 等价性验收是否成立。** 若不能原样读出，dense 侧改用与 sparse 相同的探针（两路都靠"词项/语义命中"判），等价性验收降级为"命中率对比"。
5. **`VectorStore` 新增方法的命名与签名？** 建议 `dense_rank` / `sparse_rank` 作为 `ChunkResult` 的可选字段，新增一个返回两路并集的方法（名字待定，倾向 `retrieve_branches` 或 `hybrid_candidates`）。该项是**契约变更**，须同步 `api_contract.md`。
6. **`similarity_search` 的 `min(k, 100)` 上限要不要保留？** Chroma 的硬限（`search.py:44`），PG 无此限。保留 = 行为等价；去掉 = 能力提升但改变边界条件。**建议保留并注释来源**，避免把"能力提升"混进"迁移等价性"验收。
7. **`similarity_search_multi`（`src/` 无调用方）删除还是保留？** 建议删除，并在 tasks 里注明"确认无调用方后删"。
8. **`metadata` 中哪些键升列？** 建议 `doc_id`/`chunk_index`/`chunk_total`/`source`/`page` 升列（D3），其余留 jsonb。需确认 chunker 产出的键集合没有遗漏会被 SQL 过滤的键。
9. **prod 与 dev 是否部署在不同机器？** 两份 compose 的卷名与 `./data/*` 路径相同 → 同机必然互相污染。若确实同机，必须先做路径隔离（如 `./data/prod/...`）。
