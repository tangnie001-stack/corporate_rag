# ADR-0005：混合检索的融合留在应用层

- **Status**：Accepted
- **Date**：2026-09-20
- **Deciders**：用户（决策）；Claude（调研与实测）

## 背景与问题

**现状**：混合检索的两路取数（dense / 词法）在 PostgreSQL 内完成，融合与去重在应用层 —— 两路结果各自取 top-k，应用层做 RRF 融合，再做去重，最后 rerank 生成。

**触发问题**：存储收敛后，两路取数已落在同一个 PostgreSQL 实例里。既然在同一个库内，是否应该把融合"下推"进 SQL，用数据库一次性返回已融合的 top-k？

**一手证据**（完整调研见 `docs/tmp/deep-research-hybrid-fusion-location.md`）：

- **pgvector 官方不提供任何融合能力**：其 README「Hybrid Search」节全文只有一句 "You can use Reciprocal Rank Fusion or a cross-encoder to combine results" —— 告知可行，不提供实现。
- **ParadeDB `pg_search` 到 0.25.9（2026-09-11）仍把 `Native Hybrid Search` 标为 `coming soon`**；`|||` 是 BM25 的 match disjunction 算子，不是融合算子。
- 反观 10 个被调研的系统里 **8 个有原生融合**（ES 8.16+ / OpenSearch 2.19+ / Milvus / Weaviate / Qdrant / Azure / Vespa / MongoDB），官方一律推荐库内融合 —— **但 pgvector 恰是"没有"的那一个**。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| ① | **融合留在应用层**（现状形态，改后端） | 两次往返取数（现状）；无法借用引擎的融合优化 | 融合是纯函数，确定、可测、可复现；换存储不动融合 |
| ② | RRF 写进 SQL（手写 CTE + `ROW_NUMBER()` + `1.0/(60+r)`） | 必须自担 tiebreaker 确定性；绑上专有语法；换实现即重写 | 往返由 2 次变 1 次；去重下推省传输量 |
| ③ | 换用有原生融合的引擎（Milvus / OpenSearch） | 检索栈从"一个托管 PG"退回"PG + 另一个检索引擎" | 融合由引擎负责 |
| ④ | ParadeDB `pg_search` 原生 hybrid | 无托管形态 | 库内真 BM25 + 原生 hybrid |

## 决策

**选 ①。** 融合保持在应用层；并明确**两路等权、不引入权重**。

## 理由

**最关键的一行：在 Postgres 生态上"下推融合"并不存在可下推的引擎能力，它的实质是把 RRF 公式手写进 SQL 字符串并自担确定性 —— 代价明确、收益为零。**

必须把两条论据分开，它们论证的是不同的东西：

- **论据 ①（约束，本项目适用）：Postgres 生态缺乏可用的融合能力。** pgvector 本体不提供；ParadeDB 到 0.25.9 仍标 `coming soon`；其余可选项要么无托管（破"能用托管就用托管"原则），要么需要 RDS 预装扩展（可用性未确认）。**因此方案 ② 的实质不是"利用引擎能力"，而是"把手写 RRF 搬进 SQL"**：ParadeDB 官方警告必须加 tiebreaker，否则同一条查询两次运行可能返回不同候选 —— 这与现有纯 Python `sorted` 的确定性需逐条对齐，否则引入不可复现的漂移；而 2 次往返变 1 次、去重下推省下的传输量（`k=30` 下 60 行 vs 50 行）**无关痛痒**。
- **论据 ②（业界取向，只用来说明"应用层融合不是落后"）：即便引擎提供原生融合，需要加权与多租户可配的产品仍会选择应用层。** 证据：**WeKnora 有 OpenSearch 后端（其 2.19 已有原生 RRF），却把加权 RRF 写在 Go 里**（`internal/application/service/knowledgebase_search_fusion.go:84-125`，配置项 `RRFVectorWeight=0.5` / `RRFKeywordWeight=0.3`，per-tenant 可配）；`financial_rag-main`（pgvector + tsvector，与本变更同栈）在 Python 里做**按 domain 加权**的 RRF；**Elasticsearch 官方明说其原生 RRF 的各 child retriever 权重必须相等** —— 加权能力正是原生方案的缺口。

> **WeKnora 属论据 ②，不能用来支持论据 ①。** 它是"有原生能力却不用"，不是"没有原生能力"；把它当作"PG 无融合"的佐证是类比错位。

- **否决 ③**：会把选型从"收敛到一个托管 PG"推回"PG + 另一个检索引擎"，与本变更的首要目标（收敛、少一个自建状态组件）冲突。
- **不引入权重**：现行 `rrf_fusion` 没有权重参数；加权重会改变融合输出，从而污染 dense 迁移等价性的验证框架。加权 RRF 是**能力新增**（参照项目 WeKnora / `financial_rag-main` 都有），应作为独立变更并配自己的验收。

## 后果

**正面**：

- 融合与去重是应用层的纯函数，确定、可测、可复现；换存储后端不动融合逻辑。
- 精度调节手段（RRF / 去重 / rerank）集中在应用层，不必为它们付 SQL 复杂度。

**负面 / 接受的代价**：

- 两次数据库往返取数（现状不变）。
- 无法使用引擎侧的融合优化（Postgres 侧本就没有）。
- 下游若需要加权 / 多租户权重，必须另开变更。

**不解决的问题**（避免后人误以为本 ADR 管了它）：

- 不改检索取数口径、去重策略、rerank 模型与阈值 —— 那是另一条变更（`retrieval-fetch-and-dedup`）的范围。
- 不做引擎替换（Milvus / OpenSearch）—— 若将来"融合由引擎负责"成为硬需求，是一条需要**重开评估**的路，不是本变更可顺手兼容的。

## 复查触发条件

- **加权 / 多租户权重成为硬需求时** —— 这时方案 ① 的"等权"前提失效，需要重新评估是否独立实现加权 RRF（无论落在应用层还是引擎侧）。
- **pgvector 或 ParadeDB 发布可用的原生融合、且有托管形态时** —— 方案 ② 的约束前提（生态无能力）被打破，应重测"下推"的收益。
