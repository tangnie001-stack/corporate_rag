## ADDED Requirements

### Requirement: Langfuse self-hosted tracing
The system SHALL include a self-hosted Langfuse instance for LLM observability.
- SHALL run Langfuse server in a Docker container
- SHALL use PostgreSQL as Langfuse's backing database
- SHALL expose Langfuse UI on port 3000
- SHALL require manual API key creation on first startup

#### Scenario: Collect traces
- **WHEN** a user asks a question in chat
- **THEN** Langfuse captures a trace with spans for retrieval, reranking, and LLM generation

#### Scenario: Graceful degradation
- **WHEN** Langfuse is unavailable
- **THEN** the chat functionality continues working without interruption
- **THEN** errors are logged but not propagated to the user

### Requirement: CallbackHandler integration
The RAGChain SHALL initialize a Langfuse CallbackHandler on startup.
- SHALL be configurable via LANGFUSE_ENABLE setting
- SHALL pass handler to llm.stream() calls for automatic LLM trace capture
- SHALL handle initialization failures gracefully (log warning, continue without tracing)

### Requirement: @observe decorator
The RAGChain SHALL use @observe decorator for method-level tracing.
- SHALL decorate chat_with_citations() as the root trace
- SHALL decorate _rerank_results() as a child span
- SHALL automatically correlate with CallbackHandler traces
