# kb-routing Specification

## Purpose
TBD - created by archiving change kb-routing-and-litellm-gateway. Update Purpose after archive.
## Requirements
### Requirement: 语义路由匹配知识库

跨库"所有知识库"语义路由 SHALL 废弃：会话绑定单一 KB（`kb_id`），`kb_id` 为空表示未绑定（不检索，纯对话）。系统 SHALL 不再对用户查询做知识库语义匹配，KB 解析 SHALL 下沉到 `retrieve_kb` 工具内部，直接读会话 `state.kb_id`。

#### Scenario: 绑定 KB 直接检索

- **WHEN** 会话绑定 `kb_id=kb_a`
- **THEN** retrieve_kb 直接在 kb_a 的 collection 检索，不做语义路由

#### Scenario: 未绑定 KB 不检索

- **WHEN** 会话未绑定 KB（`kb_id` 为空）
- **THEN** retrieve_kb 返回空结果，不触发任何知识库路由

### Requirement: 路由结果传递给检索节点

系统 SHALL 不再存在独立的路由节点：`kb_router` 图节点 SHALL 被移除，图的入口直接为 agent 节点；`AgentState._resolved_kb_ids` 字段 SHALL 退役，检索目标直接从 `state.kb_id` 读取。

#### Scenario: 图入口直连 agent

- **WHEN** 编译 agent 图
- **THEN** 图不含 kb_router 节点，入口节点为 agent
