## ADDED Requirements

### Requirement: Retrieval returns parent_content
Retrieved chunk SHALL include `parent_content` field in the result metadata. When `chunk_strategy` is `parent_child` or `table_preserving`, the `parent_content` SHALL contain the full parent chunk text. When `chunk_strategy` is `qa`, `parent_content` SHALL be null.

#### Scenario: Parent-child chunk metadata
- **WHEN** a chunk with chunk_strategy="parent_child" is retrieved
- **THEN** its metadata SHALL include parent_content with the full parent text

#### Scenario: QA chunk metadata
- **WHEN** a chunk with chunk_strategy="qa" is retrieved
- **THEN** parent_content SHALL be null in its metadata

## MODIFIED Requirements

### Requirement: Cross-document aggregation
The system SHALL aggregate context from up to TOP_K_RERANK chunks across documents. Each chunk's context SHALL use parent_content if available, otherwise use chunk content directly.

#### Scenario: Cross-document query with parent context
- **WHEN** user asks a question whose answer spans multiple documents
- **THEN** the response SHALL include information from all relevant documents, with each chunk's parent_content (or content if parent is null) used as LLM context; citations SHALL trace back to each source document
