## ADDED Requirements

### Requirement: Query complexity routing

系统 SHALL 将用户查询分类为 simple / medium / complex 三种路由级别。

- simple: 问候、感谢、单一事实查询，无需检索或直接 LLM 回答
- medium: 需要单次 RAG 检索的事实性问题
- complex: 需要多步推理、对比或因果分析的查询

#### Scenario: Simple greeting classified as simple
- **WHEN** 用户输入 "你好" 或 "谢谢"
- **THEN** classify_node 输出 route = "simple"

#### Scenario: Medium query classified as medium
- **WHEN** 用户输入 "2024年营收是多少"
- **THEN** classify_node 输出 route = "medium"

#### Scenario: Complex query classified as complex
- **WHEN** 用户输入 "对比2023年和2024年的营收变化"
- **THEN** classify_node 输出 route = "complex"

### Requirement: L0 greeting intercept

系统 SHALL 在 Tier 0 直接拦截问候/长度过短的查询，不经过 L1/L2/L3，直接返回 simple。

#### Scenario: Greeting intercepted
- **WHEN** 用户输入 "你好"、"hi"、"hello"、"谢谢"
- **THEN** QueryRouter 直接返回 "simple"，不调用 LLM

#### Scenario: Short query intercepted
- **WHEN** 用户输入 ≤2 个中文字符的查询
- **THEN** QueryRouter 直接返回 "simple"

### Requirement: Multi-layer routing with LLM fallback

系统 SHALL 在 Tier 1（正则规则）和 Tier 2（复杂度评分）之后，通过 LLM 做最终路由决策。

#### Scenario: LLM overrides complexity score
- **WHEN** Tier 2 复杂度评分给出 low score，但 LLM 判断需要多步分析
- **THEN** classify_node 输出以 LLM 决策为准

### Requirement: Consistency with graph conditional edges

classify_node 输出的 route SHALL 映射到 workflow.py 的条件边分支。

#### Scenario: Simple route bypasses rewrite
- **WHEN** classify_node 输出 route = "simple"
- **THEN** graph 条件边路由到 retrieve，跳过 rewrite

#### Scenario: Medium route goes to rewrite
- **WHEN** classify_node 输出 route = "medium"
- **THEN** graph 条件边路由到 rewrite

#### Scenario: Complex route goes to rewrite
- **WHEN** classify_node 输出 route = "complex"
- **THEN** graph 条件边路由到 rewrite

### Requirement: Removal of duplicate classify_query

`retrieval.py` 中的 `classify_query()` SHALL 被删除，`rewrite_query()` SHALL 接收来自 classify_node 的 intent_route 参数。

#### Scenario: rewrite_query uses passed route
- **WHEN** rewrite_query 被调用时传入了 intent_route
- **THEN** rewrite_query 使用传入的 route，不再内部分类

#### Scenario: classify_query no longer exists
- **WHEN** 搜索 classify_query 引用
- **THEN** 只有 query_router.py 包含分类逻辑
