## ADDED Requirements

### Requirement: Parent content in LLM context
When a child chunk is retrieved, the system SHALL use its parent_content as the LLM context if available. If parent_content is null (e.g., QA strategy), the system SHALL use the chunk content directly.

#### Scenario: Parent-child retrieval
- **WHEN** a child chunk with chunk_strategy="parent_child" is retrieved
- **THEN** RAGContext.content SHALL be set to the chunk's parent_content

#### Scenario: QA retrieval
- **WHEN** a chunk with chunk_strategy="qa" is retrieved (parent_content is null)
- **THEN** RAGContext.content SHALL be set to the chunk's own content

### Requirement: Citation with parent content
The SSE citation event SHALL display the parent content snippet when available, falling back to child content.

#### Scenario: Citation for parent-child chunks
- **WHEN** a citation event is generated for a parent-child chunk
- **THEN** the snippet field SHALL contain the first 200 characters of parent_content

### Requirement: Token usage in chat history
The conversation_history table SHALL record token usage and model name for each assistant response. Token counts SHALL be extracted from the DashScope streaming response metadata (`chunk.usage_metadata`) rather than estimated from text length.

#### Scenario: Token recording from streaming response
- **WHEN** an assistant response is generated via DashScope streaming
- **THEN** prompt_tokens, completion_tokens, total_tokens SHALL be read from the final chunk's usage_metadata; model_name SHALL be read from the DashScope response model field or fall back to the configured LLM_MODEL

#### Scenario: Default values
- **WHEN** token usage data is unavailable (non-DashScope provider or streaming error)
- **THEN** the fields SHALL default to 0 (tokens) or empty string (model_name)
