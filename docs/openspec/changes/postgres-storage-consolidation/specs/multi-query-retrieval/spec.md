## MODIFIED Requirements

### Requirement: 多查询合并检索

多路检索结果 SHALL 由 `src/rag/fusion.py` 的 generalized N 路 RRF（`rrf_fusion_multi`）融合：任意路按排名贡献 `1/(k+rank+1)`、跨路累加后按得分降序取 top_n；同一 `id` 跨路出现时得分累加并去重。融合不引入权重参数（各等权）。

（实现现状：harness 改造后检索入口是 `retrieve_kb` 工具，每次以单条 query 调用 `retrieval.search`，其内部对 dense 与词法两路用 2 源 `rrf_fusion` 融合 —— 此前的 `retrieve_node` 节点与 `rewritten_queries` 状态字段已不存在，生产链路当前不并行发起多条改写查询。多查询变体融合能力即 `rrf_fusion_multi`，由 `tests/rag/test_fusion.py` 覆盖。）

两路检索 SHALL 同源于一个 PostgreSQL 实例（dense 走向量距离、词法走全文检索），融合 SHALL 在应用层完成。

#### Scenario: 多路结果跨路累加融合
- **WHEN** `rrf_fusion_multi` 收到多组检索结果，同一 `id` 在多组中出现
- **THEN** 该 `id` 的得分跨路累加，结果按总得分降序去重输出，多组均命中的条目排在前

#### Scenario: 融合结果受 top_n 截断
- **WHEN** 多路融合的候选条数超过 `top_n`
- **THEN** 只保留得分最高的前 `top_n` 条

#### Scenario: 融合等权、无权重参数
- **WHEN** 同一名次分别来自不同路
- **THEN** 各路贡献相同得分；`rrf_fusion_multi` 的签名不含权重参数

#### Scenario: 两路同源

- **WHEN** 对某个知识库执行一条查询的两路检索
- **THEN** 两路 SHALL 读取同一数据库实例中同一知识库的分块，SHALL NOT 分别读取两个独立存储
