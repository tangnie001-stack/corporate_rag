# PostgreSQL 存储收敛 —— §1 前置核查实测记录

> 2026-09-19 执行。对应 `docs/openspec/changes/postgres-storage-consolidation/tasks.md` §1（1.1–1.4）。
> 目的：把"设计时的推断"换成"实测事实"，并暴露需要回改设计的项。
> 本文件是**实测证据**，不承载设计决策；决策见 `design.md`。

## 1.2 Chroma 能否原样读出全部分块 embeddings —— ✅ 通过

探针：`chromadb.PersistentClient` 只读打开 `data/chroma_persist` 的副本（不碰原目录），
`get_collection(name).get(include=["documents","metadatas","embeddings"])`。chromadb **1.5.9**。

| 项 | 实测值 |
|---|---|
| collection 总数 | **691** |
| 空 collection | **686** |
| 含分块的 collection | **5** |
| 分块总数 | **176** |
| `get_collection` 失败 | **0** |
| embeddings 可读 | **全部可读**，无 `None` 行 |
| 向量维度 | **1024**（= DashScope text-embedding-v3） |
| `documents` / `metadatas` | 全部可读 |

含分块的 5 个 collection：

| collection | count |
|---|---|
| `kb_2f85ec3c77f343d88b0e3e77d001c86c` | 1 |
| `kb_9498856e74aa45d8bb38feeea068bff6` | 1 |
| `kb_b9e74e820e0a4bad8472304446e54f5c` | 51 |
| `kb_095efa161649406d8e18d2f4b5bcc3bd` | 2 |
| `kb_ea84fb7235a941f9b64bcf4f5fa4b7f2` | 121 |

**结论**：数据搬迁（tasks 8.1）与 dense 迁移等价性验收（tasks 8.2）**成立**，不需要降级为词法式探针判据。

**附带事实（与契约相关）**

- id 格式确认是 `{doc_id}:{chunk_index}`（如 `38e82f97-…:0`），`store.py:44` 的格式保持不变。
- metadata 键集合（两种形态）：
  - 通用：`chunk_index` / `chunk_strategy` / `chunk_total` / `doc_id` / `page` / `parent_content` / `source`（+ 业务侧 `company` / `report_period` / `sec_code`）
  - 富集后追加：`block_type` / `heading_path` / `currency` / `quarter` / `report_type` / `year`
  - **`parent_content` 已在库里** —— 与 `retrieval-fetch-and-dedup` 的"父块级去重"直接相关，`metadata jsonb` 必须原样承载它，不得只保留契约 5 键。
- 契约 5 键（`doc_id`/`chunk_index`/`chunk_total`/`source`/`page`）**在全部 5 个 collection 中均存在**，升列后回填的来源数据完整。

## 1.3 扩展清单与 pgvector 版本 —— 本地已得，RDS 待办

**本地（`pgvector/pgvector:pg15`，docker）**

- PostgreSQL **15.19**（Debian 15.19-1.pgdg12+2）
- pgvector `default_version` = **0.8.6**，`CREATE EXTENSION vector` 后 `extversion` = **0.8.6**
- `pg_available_extensions` 中与本变更相关者**只有两个**：`vector` 0.8.6、`pg_trgm` 1.6
  → **无** `zhparser` / `pg_jieba` / `pg_bigm` / `pg_search`
- 推论：**本地 dev 无法验证任何 PG 分词扩展**。若探针要对照 `zhparser`/`pg_bigm`，只能在 RDS 上做；`pg_trgm` 是本地唯一可对照项。

**RDS（待用户执行）**

```sql
SELECT name, default_version FROM pg_available_extensions
 WHERE name IN ('vector','zhparser','pg_jieba','pg_bigm','pg_trgm','pg_search');

SELECT extversion FROM pg_extension WHERE extname='vector';
```

版本对照基线：pgvector 当前 **0.8.6**；HNSW 需 ≥0.5，`hnsw.iterative_scan` 需 ≥0.8。若 RDS 已装则可直接 `ALTER EXTENSION vector UPDATE;` 升级。

## 1.1 `vector` 扩展能否创建 —— 机制已证，**权限未证（阻塞）**

本地非 RDS 的超级用户路径：

```
CREATE EXTENSION IF NOT EXISTS vector;   -- CREATE EXTENSION（成功）
SELECT extversion FROM pg_extension WHERE extname='vector';   -- 0.8.6
```

**`<=>` 与 Chroma 余弦距离语义一致性（关键契约，已证）**

| 输入对 | `<=>` 返回值 |
|---|---|
| 正交 `[1,0,0]` vs `[0,1,0]` | 1 |
| 相同 `[1,0,0]` vs `[1,0,0]` | 0 |
| 相反 `[1,0,0]` vs `[-1,0,0]` | 2 |

→ 值域 0~2，与 Chroma cosine distance 一致；`rag_tools.py:168` / `retrieval.py:157-158` 的
`score = 1 - distance` 契约**无需修改即成立**。

**RDS 侧仍未确认**：`CREATE EXTENSION` 通常要求高权限账号。仓库内**没有任何 RDS 连接配置**
（`.env` 只有 `MYSQL_*` 与该实例相关的 `LANGFUSE_POSTGRES_PASS`），故无法代理验证。

## 1.4 prod 与 dev 是否不同机 —— 比"互污"更硬的结论

两份 compose 的以下标识**完全相同**：

| 项 | dev | prod |
|---|---|---|
| project name | `corporate_rag` | `corporate_rag` |
| postgres 容器名 | `corporate-rag-postgres` | `corporate-rag-postgres` |
| 卷名 | `corporate_rag_postgres_data` | `corporate_rag_postgres_data` |
| 其余卷名（mysql/redis/clickhouse/minio/app_logs/chroma_onnx_cache） | 全部同名 | 全部同名 |

→ 同机执行不是"数据互相污染"，而是**容器名冲突，第二个 `up` 直接失败**
（`--force-recreate` 还会把先起的那套拆掉）。设计里"同机必然互污"的表述**偏轻**，应改为"同机不可共存"。

**附带发现（既有缺陷，非本变更引入）**：prod 的 `app` **没有** `./data` 挂载（dev 有
`./data/chroma_persist` 与 `./data/ragas`，见 `docker-compose.yml:244-245`）。prod 仅挂
`app_logs` / `chroma_onnx_cache` / `./skills` / `./agents` → **prod 的 Chroma 数据落在容器可写层，容器重建即丢**。
本变更的"删除 Chroma"会一并消除它；建议同时登记为需求池条目以免被误认为新引入的问题。

## D3 DDL 与 upsert —— ✅ 可直接建（本地验证）

`tasks.md` 2.1/2.2 的建表语句在 pgvector 15 上**原样通过**：

```
CREATE TABLE chunks ( … tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content_seg)) STORED,
                      embedding vector(1024), metadata jsonb NOT NULL DEFAULT '{}', … );
CREATE INDEX … USING GIN (tsv);   -- 三处索引均成功
```

`INSERT … ON CONFLICT (kb_id, doc_id, chunk_index) DO UPDATE` 成功，且：
**STORED 生成列在 UPDATE 后自动重算**（`tsv` 随 `content_seg` 变化）→ `tasks.md` 3.2 的 upsert 改写与
D3 的生成列设计均成立，无需应用侧重算 `tsv`。

## D4 中文词法检索 —— ⚠ 实测暴露两处硬缺陷，须回改设计

### 真实 `jieba.lcut` 输出（jieba **0.42.1**）

| 原文 | `lcut`（未过滤） | 过滤 `len>=2` 后 |
|---|---|---|
| `营业收入同比增长率保持稳定` | `['营业','收入','同比','增长率','保持稳定']` | `['营业','收入','同比','增长率','保持稳定']` |
| `公司资产负债率上升，研发费用 5 月增加` | `['公司','资产负债率','上升','，','研发','费用',' ','5',' ','月','增加']` | `['公司','资产负债率','上升','研发','费用','增加']` |
| `腾讯控股2024年全年营收6603亿元，净利润1941亿元` | `['腾讯','控股','2024','年','全年','营收','6603','亿元','，','净利润','1941','亿元']` | `['腾讯','控股','2024','全年','营收','6603','亿元','净利润','1941','亿元']` |
| `本公司董事会及全体董事保证本公告内容不存在任何虚假记载` | `['本','公司','董事会','及','全体','董事','保证','本','公告','内容','不','存在','任何','虚假','记载']` | `['公司','董事会','全体','董事','保证','公告','内容','存在','任何','虚假','记载']` |

**顺带确认（支持 OQ#5 的结论）**：`len>=2` 过滤**同时干掉了空格与中文标点**（`' '`、`'，'` 均为 len 1）
→ 不需要额外维护停用词表，本变更只做"长度 ≥ 2"这一条硬规则即可。

**另一处需注意**：`营业收入` **不是** jieba 词元（被切成 `营业`+`收入`）；`资产负债率` 是词元。
即 `design.md`/`tasks.md` 中把 `营业收入` 当作词项的举例**在真实 tsv 中不存在**。

### 硬缺陷 H1：「查询侧过滤后为空 → 回退为不过滤」**是无效兜底**

实测（写入侧按 `len>=2` 过滤后）：

| 查询 | 结果 |
|---|---|
| `plainto_tsquery('simple','月')` | **0 命中** |
| `plainto_tsquery('simple','涨')` | **0 命中** |
| `content LIKE '%月%'` | **1 命中** |
| `plainto_tsquery('simple','')` | 0 命中（NOTICE: text-search query doesn't contain lexemes） |

**原因**：写入侧已把单字从 `content_seg` 剔除，**tsv 里根本没有单字 lexeme**。
因此查询侧"不过滤"得到的 `涨 & 了 & 吗` 依然搜不到任何东西 —— 兜底形同虚设。

**回改方向**：兜底必须落到**原始 `content` 的子串匹配**（如 `content LIKE '%' || :q || '%'`，
与 tasks 8.3 的命中判据同口径），或字符 bigram。**"不过滤"这一支必须从 design/spec/tasks 中删除**，
否则会留下一条"写了但无效"的缓解措施（评审 I4 只发现了失效模式，未发现缓解措施本身无效）。

### 硬缺陷 H2：词形不一致 → **即使单词项查询也会 0 命中**

| 查询 | 结果 | 说明 |
|---|---|---|
| `plainto_tsquery('simple','增长')` | **0 命中** | 文档 tsv 里是 `增长率` |
| `plainto_tsquery('simple','营业收入')` | **0 命中** | 文档 tsv 里是 `营业` + `收入` |
| `plainto_tsquery('simple','营业')` | 1 命中 | 恰好等于词元 |
| `to_tsquery('simple','增长:*')` | **1 命中** | ✅ 前缀匹配救回 |
| `to_tsquery('simple','营业 & 收入:*')` | **1 命中** | ✅ |

**影响**：这比 `design.md` 担心的"AND 语义使长查询召回偏严"**严重一个量级** ——
不是"长查询偏严"，而是"**用户输入的自然词与文档词元不相等就完全搜不到**"。
`营业`/`收入` 这类切分尤甚（几乎任何含"营业收入"的查询都会命中不了含"营业收入同比增长率"的文档）。

**回改方向**：查询构造须用**前缀匹配**（对每个词元加 `:*`），或用 `|` 组合降为 OR；
`plainto_tsquery` 的 AND 语义只能作为**基线对照项**，不能作为落地形态。这一改动落在
`tasks.md` 5.5 与 OQ#3（`ts_rank` vs `ts_rank_cd` 之外，新增了"查询构造方式"这一未列入的变量）。

### 硬缺陷 H3：改前缀匹配会引入 `tsquery` 解析面（H2 的修复自带新风险）

`plainto_tsquery` 对任意用户输入是**安全**的（自行分词、`&` 连接、不解析操作符）；
`to_tsquery` 要求**预格式化语法**，实测：

| 输入 | `to_tsquery` | `plainto_tsquery` |
|---|---|---|
| `C&C` | `'c' & 'c'`（静默） | `'c' & 'c'` |
| `a:` | `'a'`（**静默丢弃** `:`） | 不适用 |
| `研发费用 5 月`（含空格） | **ERROR: syntax error in tsquery** | `'研发费用' & '5' & '月'` |

→ 若为 H2 改用 `to_tsquery` 拼前缀，**查询串必须在程序侧构造并逐词元转义**：
含 `&` / `|` / `!` / `<->` / `(` / `)` / `:` 的词元会被当作操作符解析，含空格则**抛异常**。
查询文本来自用户 → 这条路径若不做转义，词法路会**抛错**而不是静默降召回，
在 `asyncio.gather(dense, bm25)` 下影响面更大。

**回改方向**：程序侧构造 tsquery —— 每个词元先按安全字符集（CJK/字母/数字/下划线）剔除，
再输出 `token:*`，以 ` & ` 连接；剔除后为空则整体降级为 `content` 子串匹配（与 H1 同一兜底）。
**这一构造点必须与分词一样进守卫测试**（它是新增的、可静默降召回/抛错的第二个入口）。

### 次要实测

- `ts_rank` = 0.0985 vs `ts_rank_cd` = 0.05（同一文档同一查询）→ 量纲不同，
  横向比较必须**固定打分算法**（design D6 已要求），否则不可归因。
- 向量列与 `tsv` 同表共存、各自排序均正常（`ORDER BY embedding <=> :q LIMIT k` 返回 0.0049/0.9004/1.0）。
  两路取数（tasks 4.5）在单表上可直接实现。

## 汇总：本次实测对变更文档的回改清单

| # | 落点 | 改动 |
|---|---|---|
| R1 | `design.md` D4 / `tasks.md` 5.2 | 删除"回退为不过滤"；改为回退到原始 `content` 子串匹配（H1） |
| R2 | `design.md` D4 / `tasks.md` 5.5 / OQ#3 | 查询构造由 `plainto_tsquery`(AND) 改为**前缀匹配**或 OR；AND 降为对照项（H2） |
| R3 | `design.md` D4 实测段 / `tasks.md` 5.2 | 用本次真实 `jieba.lcut` 输出替换人工构造示例；记录 `营业收入` 被切分这一事实 |
| R4 | `design.md` D1 / `tasks.md` 1.4 | "同机互污"→"同机**不可共存**"（容器名/项目名/卷名全同） |
| R5 | `tasks.md` 1.2 / 8.1 / 8.2 | 标记为已通过；补记 `parent_content` 须随 jsonb 原样搬迁（与 `retrieval-fetch-and-dedup` 的接口） |
| R6 | `tasks.md` 8.3 / `design.md` D6 | 探针的对照候选须去掉本地不可得的扩展；本地只余 `pg_trgm`，`zhparser`/`pg_bigm` 仅 RDS 可测 |
| R7 | `design.md` D6 / `tasks.md` 8.3 | 探针须补"被切碎词项"用例（H2 是**单词项**失效，不只是查询侧过滤为空才失效） |
| R8 | `design.md` D4 / `tasks.md` 5.5 | 新增"查询串构造与转义"为独立实现点 + 守卫测试；不得直接把词元拼进 `to_tsquery`（H3） |
| R9 | `design.md` D4 硬约束段 | 原"唯一的新失效模式"表述不成立：除写入/查询分词不一致外，还新增了**查询串转义**这一失效模式（H3） |
