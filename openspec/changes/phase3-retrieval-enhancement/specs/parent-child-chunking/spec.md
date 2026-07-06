# parent-child-chunking Specification

## ADDED Requirements

### Requirement: Parent-Child hierarchical chunking
The system SHALL implement a two-level chunking strategy: parent chunks (~1024 tokens) and child chunks (~256 tokens). Each child chunk SHALL carry its parent's full content in metadata for retrieval-time context.

Chunking SHALL use `RecursiveCharacterTextSplitter` with Chinese-friendly separators: `\n\n → \n → 。→ . → space`.

#### Scenario: Parent chunk larger than child
- **WHEN** a document is chunked using ParentChildChunker
- **THEN** parent chunks (1024 tokens) SHALL be larger than child chunks (256 tokens), and every child SHALL carry `parent_content` in its metadata

#### Scenario: Child chunk metadata includes parent context
- **WHEN** a child chunk is created
- **THEN** its metadata SHALL include `parent_content` (full parent text), `parent_chunk_id`, `child_index`, `parent_index`, `doc_id`, and `tokens`
