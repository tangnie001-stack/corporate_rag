## 1. 前置核查（不阻塞设计，但不做不能开工）

- [ ] 1.1 在 RDS 上跑扩展清单与版本：`SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','zhparser','pg_jieba','pg_bigm','pg_trgm','pg_search');` 并记录 `pgvector` 版本。**本变更不依赖结果**（走应用侧分词），但结果决定词项命中探针的对照候选（design Open Question #1）
- [ ] 1.2 **实测 Chroma 能否原样读出全部分块的 embeddings**（`collection.get(include=["documents","metadatas","embeddings"])`）。**这条决定 dense 迁移等价性验收是否成立**；若不能原样读出，改用与词法相同的探针覆盖两路（design Open Question #4）
- [ ] 1.3 确认 prod 与 dev 是否部署在不同机器（两份 compose 的 `postgres_data` 卷名与 `./data/*` 路径相同，同机必然互污）。若同机，先做路径隔离（design Open Question #9）

## 2. schema 与迁移

- [ ] 2.1 建 `chunks` 表：`id` / `kb_id`（FK）/ `doc_id` / `chunk_index` / `chunk_total` / `content` / `content_seg` / `tsv`（`GENERATED ... AS to_tsvector('simple', content_seg) STORED`）/ `embedding vector(1024)` / `source` / `page` / `metadata jsonb`；唯一约束 `(kb_id, doc_id, chunk_index)`
- [ ] 2.2 建索引：`(kb_id)` btree、`tsv` GIN、`(doc_id)` btree。**先不建 HNSW**（164 MB / 4 万分块下精确扫描足够且召回精确，design D3）
- [ ] 2.3 7 张关系表迁 PostgreSQL：去 `MEDIUMTEXT`（`models/chat.py:6,44`）、去 MySQL 方言类型；`meta_info` 等 JSON 字符串字段可保持 `Text` 不变
- [ ] 2.4 **合并双套 ORM 模型为一套**（`src/infra/db/models/` 为准，它含 `agent`/`process` 两列）；合并后加一条断言：对比两套表的列集合，确认无遗漏
- [ ] 2.5 **合并双套 alembic 目录**（根 `alembic/` 与 `src/infra/db/mysql_db/alembic/`，二者 `e6304ba3a9ef` 内容不同），`alembic.ini` 与 `target_metadata` 指向同一套；重写首版迁移
- [ ] 2.6 `deploy/mysql/init/001_schema.sql` → `deploy/postgres/init/`（含创建应用 database 与账号）；删 `deploy/mysql/`

## 3. 引擎与 Repo

- [ ] 3.1 `src/infra/db/engine.py:16-27` DSN 换 `postgresql+asyncpg://`；评估连接池参数（`pool_size=10/max_overflow=10/pool_recycle=3600` 是否仍合适）
- [ ] 3.2 5 个 Repo 的幂等写入改为 `INSERT ... ON CONFLICT DO UPDATE`（现在靠捕获 `IntegrityError` 模拟，`chat_repo.py:25-44`、`kb_repo.py:20-50`）
- [ ] 3.3 跑通 `tests/infra/db/test_mysql_db.py`（重命名为 test_relational_db 或等价），修断言中的方言相关部分

## 4. 向量存储换后端（保持 11 个方法签名）

- [ ] 4.1 `vector_store/` 各模块换 pgvector：`search.py` 的 `similarity_search` 用 `ORDER BY embedding <=> :q LIMIT k`（**返回余弦距离，语义与 Chroma 的 distance 一致**，见 `rag_tools.py:168`）；`store.py` 写入改 ORM/`INSERT ON CONFLICT`；`client.py` 的 collection 生命周期与 `hnsw:*` metadata 参数退役
- [ ] 4.2 `ChunkResult` 增分路排名字段、`bm25_score` 改名 `lexical_score`（与引擎无关的诚实命名，design D4）；同步 `typed-data-layer` 与受影响测试
- [ ] 4.3 新增两路取数入口，使 dense 与词法各自取 top-k 并携带名次（design D8）；方法命名见 Open Question #5
- [ ] 4.4 决定 `similarity_search` 的 `min(k, 100)` 上限：**建议保留并注释来源**（Chroma 硬限，`search.py:44`），避免把"能力提升"混进等价性验收（Open Question #6）
- [ ] 4.5 `similarity_search_multi`（`src/` 无调用方）确认后删除（Open Question #7）
- [ ] 4.6 跑 `tests/infra/db/test_vector_store.py`（需真 PG，改为集成测试）
- [ ] 4.7 同步 `docs/agents/api_contract.md`（契约变更）

## 5. 词法检索（应用侧分词）

- [ ] 5.1 分词入口**集中到一处**（建议 `src/config/` 或 `src/rag/` 的单一函数），写入侧与查询侧**必须调用同一个**（design D4 硬约束）
- [ ] 5.2 写入侧：`content` → 分词 → `content_seg`（`tsv` 由生成列自动维护）
- [ ] 5.3 查询侧：`query` → 同一分词 → 词法查询条件；**`ts_rank` vs `ts_rank_cd`、`plainto_tsquery`(AND) vs OR 组合，用探针实测选定，不靠推理**（Open Question #2）
- [ ] 5.4 **先写会失败的守卫测试**：同一段文本经写入侧与查询侧分词后结果相等（口径漂移是静默降召回，必须被测试拦住）
- [ ] 5.5 字段命名纪律：**不得**把 `ts_rank` 的结果命名成 `bm25_score`（那是参照项目的命名错误，design D4）

## 6. 融合与删除 bm25_index

- [ ] 6.1 `rrf_fusion` / `rrf_fusion_multi`（`src/infra/search/bm25_index.py:120-181`）**原样迁移**到独立模块（建议 `src/rag/fusion.py`），仅改 import
- [ ] 6.2 `src/rag/retrieval.py` 的两路调用改为新的取数入口；融合与 `_dedup_by_doc_id` 逻辑**保持不变**
- [ ] 6.3 **删除 `src/infra/search/bm25_index.py`**（索引生命周期、pickle 读写、索引路径全部退役）
- [ ] 6.4 `src/services/document_service.py` 删除 BM25 重建调用（`:572`）；`_rebuild_kb_index`（`:134-150`）整体删除
- [ ] 6.5 `src/services/app_service.py` 装配调整（不再构造词法索引组件）
- [ ] 6.6 CLI：删 `src/cli/rebuild_bm25.py`；`replay_trace.py:133` / `check_abstain.py:195` / `eval_ragas.py:537` 的构造替换；`eval_ragas.py:512` 的 `get_or_create_collection(kb_id).count()` 改为只读计数

## 7. 事务边界

- [ ] 7.1 `document_service.py` 重排：embedding 计算**在事务外**；`INSERT chunks` 与 `UPDATE document SET status='ready', chunk_count=N` **在同一事务**内提交（design D7）
- [ ] 7.2 **先写会失败的故障注入测试**：在 `INSERT chunks` 与 `UPDATE document` 之间抛异常，断言**两者都不落库**（无孤儿）

## 8. 验收（两套判据，design D6）

- [ ] 8.1 一次性搬迁脚本：从 Chroma 读出全部分块（含 embeddings）原样写入 `chunks`。**目的只是让 dense 等价性成为可判定的差分**（若走重新入库，分块与向量都会变，等价性失去依据）
- [ ] 8.2 **dense 迁移等价性**：固定 query set，比对替换前后 top-k 重合率，记录数字；未达标先查 distance 语义与过滤条件，**不进入后续步骤**
- [ ] 8.3 **词法词项命中探针**：从语料自身派生一组有区分度中文词项，逐个查询并断言含该词项的分块进入 top-k
- [ ] 8.4 用同一套探针**横向比较**候选配置（字符级作为基线 / 应用侧分词 / 若 RDS 可用的扩展方案），按实测选定并记录
- [ ] 8.5 **显式登记遗留**：端到端答案质量（RAGAS）在语料到位后独立评估 —— 当前 176 分块上指标方差会盖过信号
- [ ] 8.6 **保留 Chroma 数据到 8.2 通过**（冻结只读、不再写入）—— 否则回滚不可能

## 9. compose 与部署

- [ ] 9.1 `docker-compose.yml` 的 `postgres` 服务**去掉 `profiles: ["langfuse"]`**（应用现在依赖它），上调 `mem_limit`（现 256m 偏紧），增建应用 database（一个服务、两个 database，与生产的单实例对齐）
- [ ] 9.2 `docker-compose.yml` 的 `app` 增加 `depends_on: postgres(service_healthy)`
- [ ] 9.3 ⚠ **PG 数据目录继续用 docker named volume，绝不绑 `/mnt/d`**（9p 的 fsync/原子性弱，PG 比 BM25 脆弱得多）
- [ ] 9.4 退役 MySQL 服务与 `mysql_data` 卷；退役 Chroma 持久化目录挂载与 ONNX 缓存卷；退役 BM25 索引目录挂载
- [ ] 9.5 `docker-compose.prod.yml` 改为指向阿里云 RDS（不再本地起库）；写入"prod 与 dev 不同机"前置到 compose 注释与部署文档
- [ ] 9.6 测试机本地 PG 的**大版本与 RDS 对齐**（否则本地绿、上线行为不同）

## 10. 依赖与清理

- [ ] 10.1 删依赖 `chromadb` / `rank_bm25` / `aiomysql`；加 `asyncpg` / `pgvector`
- [ ] 10.2 **`jieba` 终于接线**（已声明在 `pyproject.toml:38` 但全局零调用）
- [ ] 10.3 删 `data/chroma_persist`、`data/bm25_index`；删 `deploy/chroma/Dockerfile`（未被任何 compose 引用）
- [ ] 10.4 删 `scripts/rebuild_kb_data.py`（Chroma 专有）或改写；重写 `tests/reset_data.py`（三合一重置 → 单库重置）

## 11. 测试与门禁

- [ ] 11.1 跑 `pytest tests/ -v`（含重写后的存储侧测试）、`ruff check .`、`pyright src/`
- [ ] 11.2 `tests/services/test_app_service.py` 的 5 处 Repo + VectorStore patch 边界同步
- [ ] 11.3 `tests/rag/test_retrieval.py` 的 mock 边界调整（融合仍可纯函数测试）

## 12. 文档与收尾

- [ ] 12.1 `docs/agents/code-map.md`（代码结构唯一归属）、`api_contract.md`（契约）、`data-flow.md`、`glossary.md`（词法检索与融合术语：`lexical_score` / 两路取数 / RRF）
- [ ] 12.2 `docs/agents/defensive-patterns.md` 登记新缺陷类别：**"派生数据与源数据分属不同存储 → 失败非原子、可互相伪装"**（含本次消除的两种形态：空集合、孤儿分块）
- [ ] 12.3 新增 ADR：存储收敛到 PostgreSQL（含"共享实例的故障域代价"为显式接受项）+ 融合留在应用层（含 pgvector 无原生融合的一手依据）
- [ ] 12.4 作废登记：`bm25-index-durability` 标记为被本变更取代（其靶子消失）；其"缺失可见"的价值已并入 `observability-logging` delta
- [ ] 12.5 通知 `retrieval-fetch-and-dedup` 重定基：其取数口径与父块级去重继续成立，只需把"候选池从哪来"换成 PG 两路 `LIMIT`；并解决 `retrieval-quality` 的 `TOP_K_RETRIEVAL` 默认值（在效 spec 仍写 10）由哪一方收口
- [ ] 12.6 `docs/agents/requirements_pool.md` 关闭/更新 F-15（每请求全量重载索引）—— 该缺陷载体消失
- [ ] 12.7 `openspec validate postgres-storage-consolidation --strict` 通过并归档
