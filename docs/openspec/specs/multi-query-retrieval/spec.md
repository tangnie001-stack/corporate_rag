# multi-query-retrieval Specification

## Purpose
TBD - created by archiving change query-rewrite-and-graph-simplification. Update Purpose after archive.
## Requirements
### Requirement: 多查询合并检索

系统 SHALL 让 `retrieve_node` 并行（`asyncio.gather`）遍历 `rewritten_queries` 列表逐条执行 dense + BM25 混合检索，检索查询列表为"改写结果 + 原始查询"（双路径，原 query 必须保留在列表中），并将各查询结果用 generalized N 路 RRF（现有 `rrf_fusion` 仅支持 2 源）融合合并、去重后送入 rerank。`rewritten_queries` 为空时回退到仅用原始查询检索。

#### Scenario: medium 单改写查询 + 原 query 双路径
- **WHEN** medium 改写输出 `standalone_query`（如"腾讯2024年毛利率是多少"）
- **THEN** 检索并行执行 `["腾讯2024年毛利率是多少", "毛利率呢"]` 两条查询，RRF 合并去重后送入 rerank

#### Scenario: complex 子查询 + 原 query 多路径
- **WHEN** complex 改写输出 2-4 条子查询
- **THEN** 检索并行执行全部子查询加原始查询，逐条检索后 N 路 RRF 合并去重

#### Scenario: 改写为空时回退原查询
- **WHEN** `rewritten_queries` 为空列表
- **THEN** 仅用原始查询执行一次检索

### Requirement: rerank 相对截断（无绝对阈值）

系统 SHALL 对合并后的检索结果执行 rerank 精排，并**只取前 `TOP_K_RERANK` 条相对结果，不应用绝对分数阈值过滤**（删除 `RERANK_MIN_SCORE` 判定）。打分查询按路由区分：medium 使用原始用户查询打分；complex 使用逐条子查询打分（每条子查询对合并池打分，取各自 top 合并）。

#### Scenario: medium 用原查询打分取 top-N
- **WHEN** 路由为 medium，双路径检索完成合并
- **THEN** rerank 使用原始用户查询打分，取前 `TOP_K_RERANK` 条作为上下文，不因分数低于 0.3 而丢弃

#### Scenario: complex 逐子查询打分
- **WHEN** 路由为 complex
- **THEN** 每条子查询对合并候选池打分，各取 top 合并去重后作为上下文，保证每个对比侧面都被召回

#### Scenario: 低分相关 chunk 不被绝对阈值拦截
- **WHEN** 相关 chunk 的 rerank 分数低于 0.3（如表述鸿沟导致的 0.19）
- **THEN** 该 chunk 仍按相对排序进入 top-N 上下文，交由生成阶段 LLM 语义判断
