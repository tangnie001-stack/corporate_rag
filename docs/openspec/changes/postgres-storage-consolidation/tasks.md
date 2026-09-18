## 1. 前置核查（不阻塞设计，但不做不能开工）

- [ ] 1.1 **RDS 上 `vector` 扩展能否创建、需要什么权限/账号**（RDS 的 `CREATE EXTENSION` 通常要求高权限账号）。**这是硬前置** —— 不确认会在建表时卡住；若需专门账号，部署文档要写明由谁执行
- [ ] 1.2 **实测 Chroma 能否原样读出全部分块的 embeddings**（`collection.get(include=["documents","metadatas","embeddings"])`）。**这是第 1 件要做的事**：它决定数据搬迁（第 8 节）与 dense 等价性验收是否成立。不可读则 dense 侧降级为与词法相同的探针判据
- [ ] 1.3 RDS 扩展清单与 pgvector 版本：`SELECT name, default_version FROM pg_available_extensions WHERE name IN ('vector','zhparser','pg_jieba','pg_bigm','pg_trgm','pg_search');` 与 `SELECT extversion FROM pg_extension WHERE extname='vector';`。**本变更不依赖前四项**（走应用侧分词），但结果决定探针的对照候选；`vector` 版本对照当前 **0.8.6**（HNSW 需 ≥0.5、`hnsw.iterative_scan` 需 ≥0.8）
- [ ] 1.4 确认 prod 与 dev 是否部署在不同机器（两份 compose 的 `postgres_data` 卷名与 `./data/*` 路径相同，同机必然互污）。若同机，先做路径隔离

## 2. schema 与迁移

- [ ] 2.1 建 `chunks` 表：`id` / `kb_id`（FK）/ `doc_id` / `chunk_index` / `chunk_total` / `content` / `content_seg` / `tsv`（`GENERATED ... AS to_tsvector('simple', content_seg) STORED`）/ `embedding vector(1024)` / `source` / `page` / `metadata jsonb`；唯一约束 `(kb_id, doc_id, chunk_index)`
- [ ] 2.2 建索引：`(kb_id)` btree、`tsv` GIN、`(doc_id)` btree。**先不建 HNSW**（164 MB / 4 万分块下精确扫描足够且召回精确）
- [ ] 2.3 **迁移内加 `CREATE EXTENSION IF NOT EXISTS vector;`**（放在 alembic 首版迁移，**不放** `deploy/postgres/init/` —— initdb 脚本只在数据目录首次初始化时执行，既有卷不重跑、托管实例不参与）
- [ ] 2.4 7 张关系表迁 PostgreSQL：去 `MEDIUMTEXT`（`models/chat.py:6,44`）、去 MySQL 方言类型
- [ ] 2.5 **合并双套 ORM 模型为一套**（以 `src/infra/db/models/` 为准，它含 `agent`/`process` 两列）；合并后加断言：对比两套表的列集合，确认无遗漏
- [ ] 2.6 **合并双套 alembic 目录**（根 `alembic/` 与 `src/infra/db/mysql_db/alembic/`，二者 `e634...` 首版内容不同），`alembic.ini` 与 `target_metadata` 指向同一套
- [ ] 2.7 **统一 `ChunkData` 为一份定义**：以 `src/chunking/validator.py:9-21`（含 `tokens`）为唯一类型，`src/parsers/base.py:17-31` 改为引用它；`chunk_id` 字段在 `bm25_index.py` 删除后确认无消费方则去掉。⚠ 该目标初稿只写在 proposal/design 里，tasks 无对应动作（评审 F8）
- [ ] 2.8 `deploy/mysql/init/001_schema.sql` → `deploy/postgres/init/`；删 `deploy/mysql/`

## 3. 引擎与 Repo

- [ ] 3.1 `src/infra/db/engine.py:16-27` DSN 换 `postgresql+asyncpg://`；评估连接池（**连接预算是共享的**，见 10.5）
- [ ] 3.2 5 个 Repo 的幂等写入改为 `INSERT ... ON CONFLICT DO UPDATE`（现在靠捕获 `IntegrityError` 模拟，`chat_repo.py:25-44`、`kb_repo.py:20-50`）
- [ ] 3.3 跑通 `tests/infra/db/test_mysql_db.py`（改名或原位改造），修方言相关断言

## 4. 向量存储换后端（保持 11 个方法签名）

- [ ] 4.1 `vector_store/` 换 pgvector：`search.py` 的 `similarity_search` 用 `ORDER BY embedding <=> :q LIMIT k`（**余弦距离**，语义与 Chroma 的 distance 一致，`rag_tools.py:168` 依赖 `score = 1 - distance`）；`store.py` 写入改 ORM/`ON CONFLICT`；`client.py` 的 collection 生命周期与 `hnsw:*` metadata 参数退役
- [ ] 4.2 **`ChunkResult.metadata` 回填契约**：读取时由**列值 + jsonb 平铺合并**（冲突以列为准），至少含 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page`。⚠ 不回填会让 `_dedup_by_doc_id`（读 `metadata["doc_id"]`，`retrieval.py:53`）**静默失效**、引用与来源变空、实体透传归零（评审 F1）
- [ ] 4.3 **先写会失败的测试**：写入一个分块后读回，断言 `metadata` 五个契约键齐全，且去重、`RAGContext.source/page`、`entities` 三处取值非空
- [ ] 4.4 `ChunkResult` 增分路排名字段；`bm25_score` 改名 `lexical_score`（与引擎无关的诚实命名）
- [ ] 4.5 新增两路取数入口，使 dense 与词法各自取 top-k 并携带名次（方法名见 Open Question #4）
- [ ] 4.6 **删除 `similarity_search_all`**：wrapper（`vector_store/__init__.py:86-107`）、实现（`search.py:75-113`）、`retrieval.py:100` 的 `if not kb_id` 分支、`tests/infra/db/test_vector_store.py:110-137` 与 `tests/rag/test_retrieval.py:73-83` 两处用例。依据：生产链路不可达（`rag_tools.py:135-139` 在 kb_id 空时直接返回 `[]`），且与集合式存储**语义不等价**
- [ ] 4.7 `similarity_search` 的 `min(k, 100)` 上限**保留并注释来源**（Chroma 硬限，`search.py:44`），避免把能力提升混进等价性验收
- [ ] 4.8 `similarity_search_multi` 确认无调用方后删除
- [ ] 4.9 跑 `tests/infra/db/test_vector_store.py`（真 PG，集成测试）；同步 `docs/agents/api_contract.md`

## 5. 词法检索（应用侧分词）

- [ ] 5.1 分词入口**集中到一处**（建议 `src/config/` 或 `src/rag/` 单一函数），写入侧与查询侧**必须调用同一个**
- [ ] 5.2 **过滤单字词项**（长度 ≥ 2 才纳入检索文本与查询条件）。依据（实测）：jieba 会把「营业收入同比增长率保持稳定」切出独立的「率」，单字词项 df 极高会淹没精确词项排序
- [ ] 5.3 **pin `jieba` 精确版本**（不用 `>=`）。理由：`tsv` 是 `content_seg` 的 `STORED` 生成列，**分词结果落库即固化**；jieba 升级后存量与新查询侧不一致 → 静默降召回，而"同进程内两函数比较"的守卫测试抓不到
- [ ] 5.4 写入侧：`content` → 分词（过滤单字）→ `content_seg`
- [ ] 5.5 查询侧：`query` → 同一分词 → 查询条件。**`ts_rank` vs `ts_rank_cd` 用探针实测选定**；`plainto_tsquery` 的 AND 语义已实测确认，作为基线，OR 组合作为对照项
- [ ] 5.6 **先写会失败的守卫测试**：同一段文本经写入侧与查询侧分词后结果相等
- [ ] 5.7 字段命名纪律：**不得**把 `ts_rank` 的结果命名成 `bm25_score`（参照项目的命名错误）
- [ ] 5.8 记录"分词器/词典变更须触发 `content_seg` 全量重写"为迁移检查项（不变量，不能靠人工记忆）

## 6. 融合与删除 bm25_index

- [ ] 6.1 `rrf_fusion` / `rrf_fusion_multi`（`src/infra/search/bm25_index.py:120-181`）**原样迁移**到独立模块（建议 `src/rag/fusion.py`），仅改 import。**SHALL NOT 引入两路权重**（加权 RRF 是能力新增，须独立验收）
- [ ] 6.2 `src/rag/retrieval.py` 两路调用改用新入口；融合与 `_dedup_by_doc_id` 逻辑**保持不变**
- [ ] 6.3 **删除 `src/infra/search/bm25_index.py`**
- [ ] 6.4 `src/services/document_service.py` 删除 BM25 重建调用与 `_rebuild_kb_index`（`:134-150`）
- [ ] 6.5 `src/services/app_service.py` 装配调整（不再构造词法索引组件）
- [ ] 6.6 **`src/main.py` 删除 Chroma warmup**（`VectorStore().list_collections()`，`:61-67`）及其事件。⚠ 初稿遗漏；留着的后果是每次启动打一条 warning 而非报错（被 try/except 包住），更隐蔽（评审 F9）
- [ ] 6.7 **`src/agents/tools/rag_tools.py:136` 的 `retrieval.search()` 去掉 `bm25` 形参**（初稿遗漏）
- [ ] 6.8 CLI：删 `src/cli/rebuild_bm25.py`；`replay_trace.py:133` / `check_abstain.py:195` / `eval_ragas.py:537` 的构造替换；`eval_ragas.py:512` 的 `get_or_create_collection(kb_id).count()` 改为只读计数

## 7. 事务边界（入库与删除两条路径）

- [ ] 7.1 **embedding 改为无条件预计算**，与 `CHUNK_EVAL_ENABLED` 解耦（该开关只决定"是否额外做分块质量评估"）。⚠ 现行只有开关为真时才预计算，开关为假时向量由 `add_chunks` 内部产生（`store.py:53-55`）→ 照初稿把写入包进事务会让 DashScope 调用落在**事务内**（评审 F3）
- [ ] 7.2 `document_service.py` 重排：embedding 在事务外；`INSERT chunks` 与 `UPDATE document SET status='ready', chunk_count=N` **同事务**
- [ ] 7.3 **删除路径纳入同事务**：`delete_document`（`:106-132`）的删分块与软删文档同事务、`delete_knowledge_base`（`app_service.py:98-116`）的删分块与软删 KB 同事务；**删除失败 SHALL NOT 被吞掉**（现状是"仅 warning 后照样软删"→ 永久孤儿分块，评审 F4）
- [ ] 7.4 **先写会失败的故障注入测试**：① 在 `INSERT chunks` 与 `UPDATE document` 之间抛异常 → 两者都不落库；② 删分块失败时文档 SHALL NOT 被软删

## 8. 验收（两套判据，判据已固化）

- [ ] 8.1 一次性搬迁脚本：从 Chroma 读出全部分块（含 embeddings）原样写入 `chunks`。**前提是 1.2 通过**；不可读则 dense 侧改用与词法相同的探针
- [ ] 8.2 **dense 迁移等价性**：≥20 条固定查询（覆盖中文 / 数值 / 时间三类）× **单 `kb_id` 路径**，top-k 重合率 **≥ 0.9**；未达标先查 distance 语义与 WHERE 条件，**不进入后续步骤**
- [ ] 8.3 **词法词项命中探针**：词项从**原始 `content`** 抽取（不用 `content_seg`/tsquery 自判，避免自我循环）；按 `2 ≤ df ≤ 0.1 × 分块数` 筛选、长度 ≥ 2；命中以**原始正文字符串包含**判定
- [ ] 8.4 用同一套探针横向比较候选配置（字符级基线 / jieba+simple / 若可用的扩展方案），**固定打分算法**或只比词项可召回率；记录命中数 / 词项总数 / 词项清单
- [ ] 8.5 验收记录中显式写出统计力限制（176 分块、k=30~50 时单词项命中集合可能已达语料 17%–28%，多数平凡通过 → **仅供相对比较**）
- [ ] 8.6 **显式登记遗留**：端到端答案质量（RAGAS）在语料到位后独立评估
- [ ] 8.7 **保留 Chroma 数据到 8.2 通过**（冻结只读、不再写入）—— 否则回滚不可能

## 9. compose 与部署

- [ ] 9.1 `docker-compose.yml` 的 `postgres` **去掉 `profiles: ["langfuse"]`**（应用现在依赖它）、**镜像换 `pgvector/pgvector:pg15`**（pin 版本更好；`postgres:15-alpine` 不自带 pgvector）、上调 `mem_limit`（现 256m 偏紧）
- [ ] 9.2 **显式创建应用 database 与最小权限账号**（dev）：`postgres` 镜像只在数据目录**首次初始化**时执行 initdb 脚本，既有卷不会重跑 → 必须一次性 `CREATE DATABASE`（或删卷重建并挂载 `./deploy/postgres/init`）。⚠ 仅"改文件路径"不足以完成（评审 F2）
- [ ] 9.3 `docker-compose.yml` 的 `app` 增加 `depends_on: postgres(service_healthy)`
- [ ] 9.4 ⚠ **PG 数据目录继续用 docker named volume，绝不绑 `/mnt/d`**（9p 的 fsync/原子性弱，PG 比 BM25 脆弱得多）
- [ ] 9.5 退役 MySQL 服务与 `mysql_data` 卷；退役 Chroma 持久化目录挂载与 ONNX 缓存卷；退役 BM25 索引目录挂载
- [ ] 9.6 `docker-compose.prod.yml` 改为指向阿里云 RDS（不再本地起库）；**prod 侧在 RDS 预建应用库 + 最小权限账号**；写入"prod 与 dev 不同机"前置到 compose 注释与部署文档
- [ ] 9.7 本地 PG **大版本与 RDS 对齐**（否则本地绿、上线行为不同）

## 10. 依赖、配置与清理

- [ ] 10.1 删依赖 `chromadb` / `rank_bm25` / `aiomysql`；加 `asyncpg` / `pgvector`；**`jieba` pin 精确版本并接线**（现声明在 `pyproject.toml:38` 但全局零调用）
- [ ] 10.2 删 `data/chroma_persist`、`data/bm25_index`；删 `deploy/chroma/Dockerfile`（未被任何 compose 引用）
- [ ] 10.3 删 `scripts/rebuild_kb_data.py`（Chroma 专有）或改写；重写 `tests/reset_data.py`（三合一重置 → 单库重置）
- [ ] 10.4 新增配置项归位到 `src/config/`（分块表名、`TOP_K_*`、融合参数），不散落在业务代码
- [ ] 10.5 **把连接预算写成 RDS 规格的输入约束**：prod 实为 `--workers 4` × (`pool_size=10 + max_overflow=10`) ≈ 80 连接，加 Langfuse 与其 worker 可能触及 `max_connections`。**本变更只登记不处置**（worker 数问题另案，与 `CLAUDE.md` 的单 worker 规则冲突）

## 11. 测试与门禁

- [ ] 11.1 跑 `pytest tests/ -v`（含重写后的存储侧测试）、`ruff check .`、`pyright src/`
- [ ] 11.2 `tests/services/test_app_service.py` 的 5 处 Repo + VectorStore patch 边界同步
- [ ] 11.3 `tests/rag/test_retrieval.py` 的 mock 边界调整（融合仍可纯函数测试；删除 `similarity_search_all` 用例）

## 12. 文档与收尾

- [ ] 12.1 `docs/agents/code-map.md`、`api_contract.md`（契约：`ChunkResult.metadata` 回填 + 分路字段 + 删除的方法）、`data-flow.md`、`glossary.md`（`lexical_score` / 两路取数 / RRF / 应用侧分词）
- [ ] 12.2 `docs/agents/defensive-patterns.md` 登记新缺陷类别：**"派生数据与源数据分属不同存储 → 失败非原子、可互相伪装"**（含本次消除的两种形态：空集合、孤儿分块）与**"列化的契约字段未回填进 metadata → 静默失效"**
- [ ] 12.3 新增 ADR：存储收敛到 PostgreSQL + 融合留在应用层（两条论据分开记录，含 pgvector 无原生融合的一手依据）；**并显式记录共享实例的四条运维后果为接受项**：备份/PITR 是实例级、连接数是共享预算、大版本升级是实例级维护窗口、独立账号需额外创建步骤
- [ ] 12.4 作废登记：`bm25-index-durability` 标记为被本变更取代（靶子消失）；其"缺失可见"的价值已并入 `observability-logging` delta
- [ ] 12.5 通知 `retrieval-fetch-and-dedup` 重定基：取数口径与父块级去重继续成立，只需把"候选池从哪来"换成 PG 两路 `LIMIT`；**并明确 `TOP_K_RETRIEVAL` 默认值的三方不一致（代码 30 / 在效 spec 10）由该 change 收口** —— 本变更只通知，不承担修正
- [ ] 12.6 `docs/agents/requirements_pool.md` 关闭/更新 F-15（每请求全量重载索引）—— 载体消失
- [ ] 12.7 `openspec validate postgres-storage-consolidation --strict` 通过并归档
