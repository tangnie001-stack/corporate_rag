## ADDED Requirements

### Requirement: RAG pipeline stage logging
The system SHALL log each stage of the RAG pipeline (检索/重排序/生成) at INFO level, including stage-specific metrics and timing.

#### Scenario: SSE streaming path logs complete lifecycle
- **WHEN** a user sends a query via the SSE streaming endpoint
- **THEN** the system SHALL log: request entry with query/session/kb_id, search completion with results count, rerank completion with context count and top score, generation completion with token usage and timings, total request duration

#### Scenario: Synchronous path logs complete lifecycle
- **WHEN** a request is processed through the synchronous `chat_with_citations` path
- **THEN** the system SHALL log the same lifecycle information as the SSE path, with stage timings

#### Scenario: Conversation persistence confirmation
- **WHEN** a session is successfully persisted to MySQL after a chat request
- **THEN** the system SHALL log a confirmation with session_id, kb_id, and source count

### Requirement: Retrieval mode and results logging
The system SHALL log the search mode (hybrid vs dense) along with retrieval results.

#### Scenario: Search mode differentiation
- **WHEN** a search is performed
- **THEN** the system SHALL include the search mode (hybrid/dense) in the INFO log

### Requirement: Rerank completion logging
The system SHALL log rerank completion including input/output counts and top relevance score.

#### Scenario: Successful rerank
- **WHEN** reranker completes successfully
- **THEN** the system SHALL log the number of input items, number of output contexts, and the top relevance score

### Requirement: Generation completion logging
The system SHALL log generation completion including total latency, token usage, and character count.

#### Scenario: Generation complete
- **WHEN** LLM stream generation completes
- **THEN** the system SHALL log: total generation latency (ms), prompt tokens, completion tokens, total tokens, and output character count
