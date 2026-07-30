## ADDED Requirements

### Requirement: Missing entity detection

系统 SHALL 在 classify 阶段检测查询中缺少的关键实体，并标记为 missing_entities。

#### Scenario: Year missing detected
- **WHEN** 用户输入 "营收多少" 且没有历史上下文
- **THEN** classify 输出 missing_entities 包含 type=year

#### Scenario: No missing entities
- **WHEN** 用户输入 "2024年营收多少"
- **THEN** classify 输出 missing_entities 为空

### Requirement: Clarification SSE event

当 classify 检测到 missing_entities 时，系统 SHALL 发送 SSEClarificationEvent，不发送 token/citation 事件。

#### Scenario: Clarification event sent
- **WHEN** classify 输出 missing_entities = [{"type": "year"}]
- **THEN** agent_service 发送 event: clarification，包含 question 和 suggestions
- **THEN** 流中不包含任何 token 或 citation 事件

#### Scenario: Normal flow when no clarification needed
- **WHEN** classify 输出 missing_entities 为空
- **THEN** 正常走 rewrite → retrieve → generate → format 路径
- **THEN** 发送正常 token 和 citation 事件

### Requirement: Clarification event format

SSEClarificationEvent SHALL 包含以下字段：

- type: "entity_completion" | "intent_clarification"
- question: 追问文本（如 "请问您想查询哪一年的数据？"）
- missing_entities: 缺失实体列表
- suggestions: 快捷选项列表（如 ["2023年", "2024年", "其他"]）

#### Scenario: Event structure
- **WHEN** 系统发送 clarification 事件
- **THEN** 事件包含 type、question、missing_entities、suggestions 字段

### Requirement: session_id reuse for clarification

用户回答追问后 SHALL 使用同一 session_id 发送新请求，系统从历史对话中获取上轮的实体信息。

#### Scenario: Answer after clarification
- **WHEN** 用户追问后回答 "2024年"（同 session_id）
- **THEN** classify 结合 history 推断 metric+year 已补齐
- **THEN** missing_entities 为空，走正常检索路径

### Requirement: Graph clarify routing

graph 的 `route_after_classify` 条件边 SHALL 包含 "clarify" 分支，直接路由到 END。

#### Scenario: Clarify routes to END
- **WHEN** classify 输出 missing_entities 非空
- **THEN** route_after_classify 返回 "clarify"
- **THEN** graph 直接结束，不执行 rewrite/retrieve/generate

#### Scenario: Normal route unaffected
- **WHEN** classify 输出 missing_entities 为空
- **THEN** route_after_classify 正常返回 simple/medium/complex
- **THEN** graph 走原路径

### Requirement: agent_service clarification handling

agent_service.stream_chat() SHALL 在 classify 的 CHAIN_END 事件中捕获 missing_entities，并在 loop 结束后发送 clarification 事件。

#### Scenario: Clarification captured
- **WHEN** stream_chat 收到 classify 的 CHAIN_END，输出包含 missing_entities
- **THEN** stream_chat 存储 clarification 信息
- **THEN** 循环结束后发送 SSEClarificationEvent 并提前 return
