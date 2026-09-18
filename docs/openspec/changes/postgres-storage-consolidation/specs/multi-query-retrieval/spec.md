## MODIFIED Requirements

### Requirement: 多查询合并检索

系统 SHALL 让 `retrieve_node` 并行（`asyncio.gather`）遍历 `rewritten_queries` 列表逐条执行 dense + 词法两路检索，检索查询列表为"改写结果 + 原始查询"（双路径，原 query 必须保留在列表中），并将各查询结果用 generalized N 路 RRF（现有 `rrf_fusion` 仅支持 2 源）融合合并、去重后送入 rerank。`rewritten_queries` 为空时回退到仅用原始查询检索。

两路检索 SHALL 同源于一个 PostgreSQL 实例（dense 走向量距离、词法走全文检索），融合 SHALL 在应用层完成。

#### Scenario: medium 单改写查询 + 原 query 双路径
- **WHEN** medium 改写输出 `standalone_query`（如"腾讯2024年毛利率是多少"）
- **THEN** 检索并行执行 `["腾讯2024年毛利率是多少", "毛利率呢"]` 两条查询，RRF 合并去重后送入 rerank

#### Scenario: complex 子查询 + 原 query 多路径
- **WHEN** complex 改写输出 2-4 条子查询
- **THEN** 检索并行执行全部子查询加原始查询，逐条检索后 N 路 RRF 合并去重

#### Scenario: 改写为空时回退原查询
- **WHEN** `rewritten_queries` 为空列表
- **THEN** 仅用原始查询执行一次检索

#### Scenario: 两路同源

- **WHEN** 对某个知识库执行一条查询的两路检索
- **THEN** 两路 SHALL 读取同一数据库实例中同一知识库的分块，SHALL NOT 分别读取两个独立存储
