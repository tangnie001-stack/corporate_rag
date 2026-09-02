# agent-service Specification (Delta)

## MODIFIED Requirements

### Requirement: SSE event streaming

AgentService SHALL 在后台生成任务中调用 graph.astream_events() 并将 LangGraph 事件转换为携带 seq 的流式事件写入会话缓冲；SSE 订阅（实时或断点续接）从缓冲读取并推送。事件转换映射保持：`on_chain_start` 名称对应 `status` 事件，`on_chat_model_stream` token 对应 `token` 事件。

#### Scenario: astream_events 转换为带 seq 事件
- **WHEN** graph 发出 `on_chain_start` 名称为 "classify"
- **THEN** 服务 SHALL 产出 `status("classifying", "...")` 事件并写入缓冲（带递增 seq）
- **WHEN** graph 发出 `on_chain_start` 名称为 "rewrite"
- **THEN** 服务 SHALL 产出 `status("rewriting", "...")` 事件并写入缓冲
- **WHEN** graph 发出 `on_chain_start` 名称为 "retrieve"
- **THEN** 服务 SHALL 产出 `status("retrieving", "...")` 事件并写入缓冲
- **WHEN** graph 发出 `on_chain_start` 名称为 "rerank"
- **THEN** 服务 SHALL 产出 `status("reranking", "...")` 事件并写入缓冲
- **WHEN** graph 发出 `on_chat_model_stream` token
- **THEN** 服务 SHALL 产出 `token(token)` 事件并写入缓冲

### Requirement: Conversation persistence

系统 SHALL 按写入时机落库：user 消息在请求开始时**同步**经 ChatManager 写入 Redis 与 MySQL（`created_at` 为请求发起时刻，写入成功后才启动生成）；assistant 消息在 graph 执行完成时写完整到 Redis 与 MySQL（`status=complete`），取消或出错时有已产出 token 则写部分回答（`status=interrupted`，仅写 MySQL，不写入 Redis 历史），无 token 只保留 user 消息。新会话在 user 落库时同步创建。

#### Scenario: 完成后持久化完整回答
- **WHEN** graph 执行成功完成
- **THEN** 系统 SHALL 保存会话（若新）并写 user（已完成）+ assistant（`status=complete`）消息对

#### Scenario: 中止时持久化部分回答
- **WHEN** 取消或出错时已产出部分 token
- **THEN** 系统 SHALL 写 user 消息与 `status=interrupted` 的部分 assistant 消息

### Requirement: Error boundary

AgentService SHALL 将 graph 调用包裹在 try/except 中，捕获 GraphRecursionError、节点异常与 LLM 超时；异常时 SHALL 将 error 事件写入会话缓冲，若已有产出 token 则持久化部分回答（`status=interrupted`），异常不得泄漏到 SSE 订阅端之外。

#### Scenario: graph 异常写入缓冲
- **WHEN** graph.astream_events() 抛出异常
- **THEN** 系统 SHALL 将 error 事件写入缓冲，有 token 时落 `status=interrupted` 部分回答，错误不冒泡到 API 层
