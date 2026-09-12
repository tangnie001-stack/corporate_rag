# sse-tool-detail Specification

## Purpose
TBD - created by archiving change sse-tool-detail. Update Purpose after archive.
## Requirements
### Requirement: 工具状态事件携带调用明细

系统 SHALL 在 SSE 状态事件（`SSEStatusEvent`）的可选 `detail` 字段中携带工具调用要点：`retrieve_kb` 携带检索 query（及非默认 top_k），`search_web` 携带查询列表；detail 仅用于前端展示，不改变事件类型体系与缓冲回放契约。

#### Scenario: 检索工具状态带查询

- **WHEN** agent 调用 `retrieve_kb(query="腾讯2024年报 业绩")`
- **THEN** 前端收到 `SSEStatusEvent`，其 `detail` 含检索 query，等待界面展示"正在检索: query=腾讯2024年报 业绩"

#### Scenario: 联网搜索状态带查询列表

- **WHEN** agent 调用 `search_web(queries=[...])`
- **THEN** 前端收到 status 事件，`detail` 含查询列表，等待界面展示联网搜索对象

#### Scenario: 无 detail 向后兼容

- **WHEN** 工具无入参可展示或 detail 为空
- **THEN** 状态事件照常发出，前端按无 detail 逻辑渲染（与原行为一致）

### Requirement: detail 长度约束

系统 SHALL 对 detail 中的 query 做长度截断（如 40 字符），防止过长检索词撑爆状态事件行。

#### Scenario: 长查询截断

- **WHEN** 检索 query 超过 40 字符
- **THEN** detail 中 query 截断为 40 字符，不影响 message/stage 字段
