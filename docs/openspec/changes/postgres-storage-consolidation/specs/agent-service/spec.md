## MODIFIED Requirements

### Requirement: Graph initialization

AgentService SHALL compile and hold the StateGraph instance. It SHALL accept RAGChain as a dependency to access vector_store, llm, and reranker.

#### Scenario: Graph compiled on init

- **WHEN** AgentService is instantiated
- **THEN** the StateGraph SHALL be compiled and ready for invocation

#### Scenario: 检索依赖来自单一存储组件

- **WHEN** AgentService 的依赖被装配
- **THEN** 它 SHALL 通过向量存储组件访问检索能力（dense 与词法两路同源于该组件背后的单一数据库），SHALL NOT 额外接收独立的词法索引组件

### Requirement: Conversation persistence

系统 SHALL 按写入时机落库：user 消息在请求开始时**同步**经 ChatManager 写入 Redis 与关系型库（`created_at` 为请求发起时刻，写入成功后才启动生成）；assistant 消息在 graph 执行完成时写完整到 Redis 与关系型库（`status=complete`），取消或出错时有已产出 token 则写部分回答（`status=interrupted`，仅写关系型库，不写入 Redis 历史），无 token 只保留 user 消息。新会话在 user 落库时同步创建。

#### Scenario: 完成后持久化完整回答
- **WHEN** graph 执行成功完成
- **THEN** 系统 SHALL 保存会话（若新）并写 user（已完成）+ assistant（`status=complete`）消息对

#### Scenario: 中止时持久化部分回答
- **WHEN** 取消或出错时已产出部分 token
- **THEN** 系统 SHALL 写 user 消息与 `status=interrupted` 的部分 assistant 消息
