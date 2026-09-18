## MODIFIED Requirements

### Requirement: Graph initialization

AgentService SHALL compile and hold the StateGraph instance. It SHALL accept RAGChain as a dependency to access vector_store, llm, and reranker.

#### Scenario: Graph compiled on init

- **WHEN** AgentService is instantiated
- **THEN** the StateGraph SHALL be compiled and ready for invocation

#### Scenario: 检索依赖来自单一存储组件

- **WHEN** AgentService 的依赖被装配
- **THEN** 它 SHALL 通过向量存储组件访问检索能力（dense 与词法两路同源于该组件背后的单一数据库），SHALL NOT 额外接收独立的词法索引组件
