# graceful-degradation Specification

## Purpose
Define the three-level degradation strategy that ensures the system always returns a response when higher-level paths fail.

## ADDED Requirements

### Requirement: Three-level degradation chain
The system SHALL implement a three-level degradation chain: Agentic RAG → Enhanced RAG → Naive RAG → error message.

#### Scenario: Grader retries exhausted falls back to Enhanced RAG
- **WHEN** the grader loop reaches max retries (2)
- **THEN** the graph SHALL fall back to the medium path (rerank → generate) using whatever retrieval results are available

#### Scenario: Empty rerank results fall back to Naive RAG
- **WHEN** the rerank node produces zero contexts (empty list)
- **THEN** the generate_node SHALL call `build_simple_prompt()` to produce a direct LLM answer without context

#### Scenario: LLM failure returns error message
- **WHEN** the LLM call in generate_node fails (after retries)
- **THEN** the agent_service SHALL yield a SSE error event with "暂时无法回答，请稍后再试"

### Requirement: Degradation tracking
The system SHALL track degradation state in AgentState via `downgraded: bool` and `downgrade_reason: str` fields, logged for observability.

#### Scenario: Degradation reason logged
- **WHEN** any degradation path is triggered
- **THEN** the state's downgraded flag SHALL be set and the reason logged with trace_id
