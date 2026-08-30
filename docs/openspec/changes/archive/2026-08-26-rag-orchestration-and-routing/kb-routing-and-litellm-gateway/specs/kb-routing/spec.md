## ADDED Requirements

### Requirement: 语义路由匹配知识库
当用户选择"所有知识库"时，系统 SHALL 先通过语义路由计算用户问题与每个知识库的语义相似度，选择最相关的知识库进行检索。

#### Scenario: 语义路由成功匹配
- **WHEN** 用户选择"所有知识库"并发送查询
- **THEN** 系统计算查询与每个 KB 的 name+description 的嵌入相似度
- **THEN** 相似度阈值（默认 0.82）以上的 top-2 KB 被选中
- **THEN** 只在选中的 KB 中执行检索

#### Scenario: 语义路由低置信度（LLM 兜��）
- **WHEN** 语义路由的最高相似度低于阈值
- **THEN** 系统使用 LLM 对查询进行分类，从 KB 列表中选择最相关的 KB
- **THEN** 最多选 2 个 KB 进行检索

#### Scenario: 路由完全未命中
- **WHEN** 语义路由和 LLM 兜底都无法确定相关 KB
- **THEN** 系统降级为全量检索所有知识库

#### Scenario: 非"所有知识库"模式
- **WHEN** 用户指定了具体的 kb_id
- **THEN** 路由逻辑跳过，直接检索指定 KB

### Requirement: 路由结果传递给检索节点
路由节点 SHALL 将选中的 kb_id 列表写入 AgentState，retrieve_node 据此执行限定检索。

#### Scenario: 路由结果生效
- **WHEN** kb_router_node 选中了 kb_ids = ["id_a", "id_b"]
- **THEN** retrieve_node 只在 id_a 和 id_b 对应的 collection 中执行检索
- **THEN** 不搜索其他知识库

### Requirement: 路由索引构建
系统 SHALL 使用 KB 的 name 和 description 字段构建路由依据，description 为空时只使用 name。

#### Scenario: KB 信息不全
- **WHEN** 某个 KB 的 description 为空
- **THEN** 路由仅使用该 KB 的 name 进行相似度计算
