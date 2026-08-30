# agent-service Specification

## Purpose
Define the AgentService that manages the LangGraph lifecycle: graph initialization, execution, SSE event conversion, and degradation handling.

## ADDED Requirements

### Requirement: Graph initialization
AgentService SHALL compile and hold the StateGraph instance. It SHALL accept RAGChain as a dependency to access vector_store, bm25, llm, and reranker.

#### Scenario: Graph compiled on init
- **WHEN** AgentService is instantiated
- **THEN** the StateGraph SHALL be compiled and ready for invocation

### Requirement: SSE event streaming
AgentService.stream_chat() SHALL invoke graph.astream_events() and convert LangGraph events to SSE event strings.

#### Scenario: astream_events converted to SSE
- **WHEN** graph emits `on_chain_start` with name "classify"
- **THEN** the service SHALL yield `sse_status("classifying", "...")`
- **WHEN** graph emits `on_chain_start` with name "rewrite"
- **THEN** the service SHALL yield `sse_status("rewriting", "...")`
- **WHEN** graph emits `on_chain_start` with name "retrieve"
- **THEN** the service SHALL yield `sse_status("retrieving", "...")`
- **WHEN** graph emits `on_chain_start` with name "rerank"
- **THEN** the service SHALL yield `sse_status("reranking", "...")`
- **WHEN** graph emits `on_chat_model_stream` with token
- **THEN** the service SHALL yield `sse_token(token)`

### Requirement: degradation SSE event
When a degradation path is triggered (grader retry exhausted or empty rerank results), AgentService SHALL yield an additional `sse_status("downgrading", "...")` event before falling back.

#### Scenario: degradation yields downgrading event
- **WHEN** the graph triggers a degradation path
- **THEN** AgentService SHALL yield `sse_status("downgrading", "...")` before the fallback pipeline executes

### Requirement: trace_id injection
AgentService SHALL inject a trace_id into the initial AgentState before graph invocation, using the session_id as a seed.

#### Scenario: trace_id present in all events
- **WHEN** any SSE event is yielded
- **THEN** the trace_id SHALL be retrievable from the state for log correlation

### Requirement: Chat history loading
AgentService SHALL load conversation history from ChatManager before graph execution and pass it to the initial AgentState as `_history`.

#### Scenario: History loaded on stream_chat start
- **WHEN** `stream_chat(kb_id, session_id, query)` is called
- **THEN** the service SHALL call `chat_manager.get_history_async(session_id)` and include the result in the initial state

### Requirement: Conversation persistence
After graph execution completes, AgentService SHALL persist the user query and assistant answer to MySQL via ChatManager.

#### Scenario: Conversation saved after streaming
- **WHEN** graph execution completes successfully
- **THEN** AgentService SHALL save the session (if new) and add user/assistant message pair to chat history

### Requirement: Error boundary
AgentService SHALL wrap graph invocation in a try/except block, catching GraphRecursionError, node exceptions, and LLM timeouts.

#### Scenario: Graph error returns SSE error
- **WHEN** graph.astream_events() raises an exception
- **THEN** AgentService SHALL yield `sse_error("...")` and `sse_done()`, preventing the exception from reaching the API layer
