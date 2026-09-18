## Why

**1. 三套存储 = 三个独立故障域，失败不原子 —— 这是根本病根。**
关系型在 MySQL、分块与向量在 Chroma（嵌入式，落 `data/chroma_persist`）、词法索引在进程内 `rank_bm25` 且落 `data/bm25_index`。三者可各自半死：BM25 挂了而 MySQL 完好，于是系统一边静默降级一边照打 `hybrid done` —— `trace_c54ce259` 那条缺陷活了半个月无人发现，就是这么来的。**根治办法不是给 BM25 再加观测与自愈**（那正是 `bm25-index-durability` 的 38 个任务），**而是让三者在同一个故障域内**：存储要么整体可用，要么整体不可用，不存在"半活"状态可供伪装。

**2. 托管化。** 生产 MySQL/Redis 已是阿里云托管高可用，真单点只剩向量库与文件存储。Chroma 没有托管形态，pgvector 有 → 收敛到 RDS PostgreSQL 能让「最后一个可托管的派生单点」交给云托管。

**3. 分块与文档状态没有事务。** `document_service.py:541` 写 Chroma、`:551` 更新 MySQL 状态、`:572` 重建 BM25，三步跨三店。进程死在中间就产生「有分块、文档未 ready」的孤儿。

**4. 顺带清掉四类既有缺陷**：① collection-per-KB + 读路径 `get_or_create` 造就了 691 个 collection / 只有 5 个含分块；② `src/infra/db/models/` 与 `src/infra/db/mysql_db/models/` 是**两套重复且已不一致**的 ORM 定义（前者有 `agent`/`process`，后者没有）；③ 两套 alembic 目录内容不同 —— **实际生效的是根 `alembic/`（`alembic.ini:8` → 根 `env.py:9` 的 `from src.infra.db.models import *`），它只有一条首版迁移，而未被指向的 `src/infra/db/mysql_db/alembic/` 反而多出两条 feedback 迁移**（即生效链缺失这两条）；④ `ChunkData` 两处定义。

**5. 参照项目不可照搬。** 同领域的 `financial_rag-main` 用的正是 pgvector + tsvector + 应用层 RRF，其**融合的组织形态值得学**；但它的稀疏侧是 `to_tsvector('simple', content)` + `ts_rank`，字段却叫 `bm25_score` —— **`simple` 对中文不分词、`ts_rank` 也不是 BM25**，该实现在中文上基本失效（`530.sql:585-586`、`search_service.py:629`）。本变更只取其形态，不取其实现。

## What Changes

- **关系型存储 MySQL → PostgreSQL**（7 张表：`users` / `knowledge_base` / `document` / `sessions` / `conversation_history` / `eval_report` / `feedback`），驱动 `aiomysql` → `asyncpg`。
- **分块与向量 Chroma → PostgreSQL + pgvector**：新建 `chunks` 表（含 `embedding vector(1024)` 与 `kb_id` 外键）。**不再是每个知识库一个 collection**，空 collection 这一类缺陷结构性消失。
- **词法检索：进程内 `rank_bm25` + pickle 索引文件 → PostgreSQL 全文检索**（`tsvector` 生成列 + GIN 索引）。中文分词由 **Python 侧 jieba 预分词**保证（写入与查询必须同源），因此**不依赖任何 PG 分词扩展**。
- **融合仍留在应用层。** pgvector 官方不提供任何融合能力（README 仅一句 "You can use Reciprocal Rank Fusion or a cross-encoder to combine results"），ParadeDB `pg_search` 到 0.25.9 仍把 Native Hybrid Search 标为 coming soon。所谓"下推"在 Postgres 上等于把手写 RRF 搬进 SQL 字符串并自担 tiebreaker 确定性 —— 收益为零、代价明确。两路并行取数 + 应用层 RRF + 应用层去重，与 `financial_rag-main`、WeKnora、Dify、RAGFlow-ES 路径同构。
- **不再需要词法索引文件的持久化、原子写、损坏自愈与降级观测** —— 这些问题的载体消失。
- **BREAKING**：`VectorStore` 契约扩展取数入口（新增按支路取 top-k 的方法）；`bm25_index.py` 删除（其纯函数 `rrf_fusion` / `rrf_fusion_multi` 保留并迁移）；`ChunkResult` 增加分路排名字段使融合前来源可辨。
- **依赖增减**：删 `chromadb`、`rank_bm25`、`aiomysql`；加 `asyncpg`、`pgvector`；`jieba`（已在 `pyproject.toml:38` 声明但全局零调用）**终于接线**。
- **部署**：dev 的 `postgres` 服务去掉 `profiles: ["langfuse"]`（它不能再挂在 Langfuse profile 下——应用现在依赖它）、扩容、增建应用 database，`app` 增加 `depends_on: postgres(service_healthy)`；prod 指向阿里云 RDS PostgreSQL（一个实例两个 database：应用 + Langfuse）。数据目录继续用 docker named volume（**不绑 `/mnt/d`**，9p 的 fsync/原子性弱）。
- **作废 change `bm25-index-durability`**：其 38 个任务的靶子（索引文件的持久化、原子写、损坏自愈、缺失/不可读降级）在新形态下不存在。
- **与在途 change `retrieval-fetch-and-dedup` 重叠**：两者都改 `retrieval-quality` / `retrieval-judgment` / `observability-logging`。详见下方顺序声明。

## Capabilities

> ⚠ **本变更的 capability 面很宽（14 个），这是"换了系统底层存储"的必然结果**：多条既有 requirement 在正文里**点名了将要消失的组件**（`MySQL` / `ChromaDB` / `BM25Index` / `bm25_index.py` / `collection`），不改就会在归档后变成假陈述。其中 **1 个新建、6 个承载真实行为变化、7 个纯命名同步**。
>
> **本轮修订补入了 2 个先前遗漏的 capability（`kb-routing`、`database-migrations`），并在 3 个已有 delta 里补上了遗漏的 requirement**（评审 F7 指出：初稿声称"12 个面已覆盖"但集合不完整 —— 数量不是问题，**完整性**才是）。

### New Capabilities

- `hybrid-retrieval`: 混合检索的取数与融合契约 —— 同一 PostgreSQL 内的两路取数（dense / 词法）各自取 top-k、融合在应用层、结果携带分路排名、写入与查询的分词口径同源（含版本漂移不变量）、融合参数（`k` / `top_n`）可配。**（新建）**

### Modified Capabilities

**承载真实行为变化（6）**

- `retrieval-quality`（ADDED）: 新增「迁移等价性与词项命中探针」要求 —— 两路各有一套可判定的验收判据；并把端到端质量评估显式移出本次范围。
- `retrieval-quality`（MODIFIED）: 「Rerank context passthrough」中的 `ChromaDB chunk metadata` 改为 `chunks` 表。
- `observability-logging`（ADDED）: 新增「稀疏支路贡献可见」要求 —— 融合后仍须能分辨某结果来自哪一路。
- `observability-logging`（MODIFIED）: 「生成层可观测（LLM 摘要事件）」正文要求"经 MySQL 会话表获取"，须随引擎改名（初稿遗漏）。
- `database-orm`（MODIFIED）: 「ORM 模型定义」的 `MySQL 表` → PostgreSQL，并写入「模型与迁移脚本的**单一事实源**」（消除两套模型、两套 alembic）；「搜索类型搬迁」的引用方列表移除 `bm25_index.py`。
- `typed-data-layer`（MODIFIED）: 「检索结果统一类型」的链路名（`ChromaDB / BM25` → 同一 PostgreSQL 的两路），`ChunkResult` 增加分路排名字段与 **`metadata` 回填契约**；「MySQL 实体类型」→ 关系型实体类型；**另补「mysql_db.py 拆为 Repo」与「ChatManager 改用 ChatRepo」两条**（正文点名 `MySQLDB` 类，初稿遗漏）。该 capability 的「api/documents.py 走 service」**不改** —— 其正文不含引擎/组件命名，本变更不影响它。
- `architecture-tidy`（MODIFIED）: 「AppService 直接持有全局依赖」不再持有 `BM25Index`（该组件退役）。
- `database-migrations`（MODIFIED）: 「第一版迁移」写死了 6 张表与 `scripts/clean_all_data.py`，本变更新增 `chunks` 表并换引擎（含 `CREATE EXTENSION IF NOT EXISTS vector`），该 requirement 必须同步。

**纯命名同步（7，行为不变，只是组件名不再成立）**

- `agent-service`（MODIFIED）: 除「Graph initialization」的依赖列表去掉 `bm25` 外，**补上「Conversation persistence」**（该 requirement 正文写"写入 Redis 与 MySQL"，初稿漏了它）。
- `multi-query-retrieval`（MODIFIED）: 「多查询合并检索」的 `dense + BM25 混合检索` 改为 `dense + 词法两路检索`。
- `chunk-entity-enrichment`（MODIFIED）: 「Entity injection into chunk metadata」的 `before storing in ChromaDB` / `in MySQL` 改为分块表与关系型表。
- `model-config`（MODIFIED）: 「Embedding 创建」的维度一致性对照对象从 `ChromaDB collection` 改为 `chunks.embedding` 列。
- `request-abort`（MODIFIED）: 「abort 后清理与孤儿消息处理」中的 `MySQL` → PostgreSQL（落库时机与语义不变）。
- `streaming-run`（MODIFIED）: 「任务状态查询」中的 `MySQL 存在 assistant 消息` → PostgreSQL（判定逻辑不变）。
- `kb-routing`（MODIFIED）: 「语义路由匹配知识库」的 scenario 写"直接在 kb_a 的 **collection** 检索"——collection 概念消失，必须改（初稿遗漏）。
- （`retrieval-judgment` **不在其列**：其「检索结果去重」要求只规定"按 doc_id 去重、位置在 RRF 融合后 rerank 前"，不涉及存储，本变更不改变它。初稿曾误列此条，已更正。）

**未列入 Capabilities 的一处**（避免把实现细节当契约）：
- `chunk-data-model`：其现有 requirement 已要求 `ChunkData` 是唯一标准类型，仓库里存在两份定义属于**未满足既有 requirement**，本变更修正它而非修改它（但**必须有 task** —— 初稿只在 proposal/design 里声明了目标，tasks 里没有对应动作，评审 F8）。

## Impact

**代码**

- `src/infra/db/engine.py` — DSN 与驱动（`mysql+aiomysql` → `postgresql+asyncpg`）、连接池
- `src/infra/db/models/` + `src/infra/db/mysql_db/models/` — 合并为一套；去 `MEDIUMTEXT` 与 MySQL 方言类型
- `src/infra/db/mysql_db/*_repo.py`（5 个）— 引擎无关，主要改动是幂等写入与 JSON 字段
- `src/infra/db/vector_store/`（6 模块）— 后端换 pgvector；`client.py` 的 collection 生命周期与 HNSW metadata 参数退役
- `src/infra/search/bm25_index.py` — 删除；`rrf_fusion` / `rrf_fusion_multi` 迁移到独立模块
- `src/rag/retrieval.py` — 两路取数与融合调用点调整；**删除不可达的 `if not kb_id` 分支**（其调用方 `rag_tools.py:135-139` 在 kb_id 为空时直接返回 `[]`）
- `src/agents/tools/rag_tools.py` — `retrieval.search()` 去掉 `bm25` 形参（初稿遗漏）
- `src/main.py` — 删除 Chroma warmup（`VectorStore().list_collections()`，`:61-67`）及其事件（初稿遗漏；留着的后果是每次启动打一条 warning 而非报错，更隐蔽）
- `src/services/document_service.py` — 入库与删除两条路径事务化；去掉 BM25 重建调用；embedding 改为无条件预计算
- `src/services/app_service.py` — 组件装配（BM25 实例退役）
- `src/chunking/validator.py` + `src/parsers/base.py` — 统一 `ChunkData` 为一份定义（初稿只在 Context 声明了目标，tasks 里没有对应动作，评审 F8）
- `src/cli/` — `rebuild_bm25.py` 删除；`replay_trace.py` / `check_abstain.py` / `eval_ragas.py` 的 BM25 装配替换
- `alembic/` + `src/infra/db/mysql_db/alembic/` — 合并为一套并重写首版迁移
- `deploy/mysql/init/001_schema.sql` → `deploy/postgres/init/`（含应用 database 创建）

**部署与运维**

- `docker-compose.yml` — `postgres` 去 `profiles: ["langfuse"]`、扩容、增建应用库；`app` 依赖 `postgres`；MySQL 服务退役
- `docker-compose.prod.yml` — 同上，并改为指向阿里云 RDS（不再本地起库）
- Chroma 与 BM25 的持久化目录、ONNX 缓存卷、`deploy/chroma/Dockerfile` 退役

**测试**

- `tests/infra/db/test_mysql_db.py`、`tests/infra/db/test_vector_store.py`、`tests/infra/search/test_bm25_index.py`、`tests/reset_data.py`（三合一重置 → 单库重置）重写；`tests/rag/test_retrieval.py` 的 mock 边界调整

**文档**

- `docs/agents/code-map.md`（代码结构唯一归属）、`docs/agents/api_contract.md`（`VectorStore` 契约）、`docs/agents/data-flow.md`、`docs/agents/glossary.md`（词法检索与融合术语）、`docs/agents/defensive-patterns.md`（"派生数据与源数据不同库导致失败非原子"这一类）、新增 ADR（存储收敛与融合位置）

**⚠ 顺序声明（与在途变更的关系）**

本变更与 `retrieval-fetch-and-dedup`（已批准、0/33）在**同一批 capability 上重叠**：两者都改 `retrieval-quality`、`retrieval-judgment`、`observability-logging`。

- **建议本变更先行。** 理由：本变更换掉的是两路取数的**存储与实现**，而 `retrieval-fetch-and-dedup` 调的是**取数口径与去重策略**（它写的是「候选池 50 / 父块级去重 / 分数形态采样」）。先改底层再改口径，只需把后者的 delta 重定基一次；反过来则要在一个移动的靶子上写本变更的 delta。
- 若两者并行，`retrieval-quality` 的「Retrieval parameter configuration」（在效 spec 仍写 `TOP_K_RETRIEVAL` 默认 10）会出现三方不一致 —— 必须由其中一个 change 收口。
- `bm25-index-durability` 本变更落地后**正式作废**；其观测相关条目若仍有价值，并入本变更的 `observability-logging` delta，不另立。

**明确不在本变更范围**

- prompt 载体与分段模型（另见 `prompt-layering-and-domain-binding`）
- 技能来源获取（另见 `skill-external-sources`）
- Langfuse 的服务端版本选择（v2 与"仅 PG"的决议另有评估，不在本变更）
- 端到端答案质量（RAGAS）的回归判定 —— 当前语料仅 176 个分块 / 5 个知识库，指标方差会盖过信号，评估推到语料填充之后
- BM25 的算法调参与 rerank 的模型选择
