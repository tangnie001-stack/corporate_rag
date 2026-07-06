# sse-status Specification

## ADDED Requirements

### Requirement: Emit SSE status events during RAG pipeline
The chat streaming endpoint SHALL emit SSE 'status' events at the start of each pipeline stage: retrieving, reranking, and generating. Each status event SHALL include a stage identifier and a human-readable Chinese message.

#### Scenario: Status events emitted in order
- **WHEN** a RAG chat request starts processing
- **THEN** the SSE stream SHALL emit status events in order: retrieving → reranking → generating, with appropriate Chinese messages

#### Scenario: Status event has correct format
- **WHEN** a status event is emitted
- **THEN** the event SHALL have the format `event: status\ndata: {"stage":"...","message":"..."}\n\n`

### Requirement: Preserve existing SSE event types
The streaming endpoint SHALL continue to emit the existing token, citation, error, and done events. Status events are additive and SHALL NOT alter the format of other event types.

#### Scenario: Token events unchanged
- **WHEN** a token is emitted during the generating stage
- **THEN** the token SHALL have the existing format `event: token\ndata: {"token":"..."}\n\n`
