## MODIFIED Requirements

### Requirement: 语义路由匹配知识库

跨库"所有知识库"语义路由 SHALL 废弃：会话绑定单一 KB（`kb_id`），`kb_id` 为空表示未绑定（不检索，纯对话）。系统 SHALL 不再对用户查询做知识库语义匹配，KB 解析 SHALL 下沉到 `retrieve_kb` 工具内部，直接读会话 `state.kb_id`。

分块按知识库标识作为列隶属存储（单一张分块表），SHALL NOT 以"每个知识库一个独立集合"的方式隔离。

#### Scenario: 绑定 KB 直接检索

- **WHEN** 会话绑定 `kb_id=kb_a`
- **THEN** retrieve_kb 直接按 `kb_a` 过滤分块表检索，不做语义路由

#### Scenario: 未绑定 KB 不检索

- **WHEN** 会话未绑定 KB（`kb_id` 为空）
- **THEN** retrieve_kb 返回空结果，不触发任何知识库路由

#### Scenario: 不指定知识库的检索路径不存在

- **WHEN** 检查检索入口
- **THEN** SHALL NOT 存在"跨全部知识库检索"的路径（它与单库绑定语义冲突，且在生产链路上不可达）
