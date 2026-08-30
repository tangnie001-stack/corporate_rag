# web-search-fallback Specification

## Purpose
TBD - created by archiving change web-search-fallback. Update Purpose after archive.

## Requirements

### Requirement: search_web 工具

系统 SHALL 提供 `search_web` 工具，通过 Tavily 搜索引擎（search + extract）检索互联网实时信息，返回带来源链接的网页摘要/正文。工具接口固定为 `search_web(query, top_k)`，服务商实现可替换。

#### Scenario: 联网搜索
- **WHEN** agent 调用 `search_web(query)`
- **THEN** 返回带来源 URL 的网页结果列表，关键结果含正文内容

#### Scenario: 关键结果拉取正文
- **WHEN** search 返回结果后
- **THEN** 系统 SHALL 对相关性最高的 top-1~2 个 URL 调用 extract 拉取正文，补足摘要不足以回答的场景

#### Scenario: 结果注入引用通道
- **WHEN** search_web 返回结果
- **THEN** 结果 SHALL 追加进 `tool_contexts`，模型引用后经 format_node 产出 `kind=web` 的引用

### Requirement: web 兜底流程

KB 检索（含换词再检）仍无相关结果时，系统 SHALL 允许模型调用 `search_web` 联网补充信息，回答先说明"该问题不在当前知识库范围内"，再给出联网补充回答。

#### Scenario: KB 未命中转 web
- **WHEN** retrieve_kb 两次检索结果均为空或全部明显不相关
- **THEN** 模型 SHALL 可调用 search_web 获取信息并回答，保留网络引用

### Requirement: web 兜底控制

系统 SHALL 提供 web 兜底的控制与熔断：全局开关、每轮调用限次、搜索服务超时/失败降级。

#### Scenario: 开关关闭
- **WHEN** `WEB_SEARCH_ENABLED=false`
- **THEN** agent 不暴露 search_web 工具，KB 未命中走纯拒答路径

#### Scenario: 每轮限次
- **WHEN** 单轮对话中 search_web 调用次数达到 `WEB_SEARCH_PER_TURN_LIMIT`
- **THEN** 后续调用返回限次提示，不再发起实际搜索

#### Scenario: 搜索服务熔断
- **WHEN** Tavily 调用超时或失败
- **THEN** search_web 返回空结果，agent 走纯拒答路径，不阻塞

#### Scenario: 搜索无结果
- **WHEN** Tavily 正常返回但无相关结果
- **THEN** search_web 返回空结果，agent 走纯拒答路径
