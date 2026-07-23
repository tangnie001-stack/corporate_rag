## ADDED Requirements

### Requirement: Merge tiny chunks after document chunking

Tiny chunks (tokens < 50) produced by `RecursiveCharacterTextSplitter` at tail boundaries
SHALL be automatically merged into the previous chunk after chunking completes.

#### Scenario: Normal merge of single tiny chunk
- **WHEN** a document is chunked and the last chunk has < 50 tokens
- **THEN** the tiny chunk SHALL be merged into the preceding chunk by appending its content with `\n`
- **THEN** the merged chunk's `tokens` metadata SHALL be recalculated

#### Scenario: First chunk is tiny
- **WHEN** the first chunk in the list has < 50 tokens
- **THEN** it SHALL remain as a standalone chunk (no preceding chunk to attach to)

#### Scenario: Consecutive tiny chunks
- **WHEN** multiple consecutive chunks each have < 50 tokens
- **THEN** all SHALL be accumulated into the same preceding chunk

#### Scenario: All chunks are tiny
- **WHEN** every chunk in the list has < 50 tokens
- **THEN** no merge SHALL occur (entire document content is under threshold)

### Requirement: Skip merge for QA strategy

QA strategy chunks are complete Q&A pairs; merging would corrupt semantic structure.

#### Scenario: QA strategy skip
- **WHEN** the detected chunk strategy is `qa`
- **THEN** `_merge_tiny_chunks` SHALL return the chunk list unchanged

### Requirement: Page metadata preserved after merge

The `_enrich_chunk_pages` function SHALL run before `_merge_tiny_chunks`
to ensure page metadata is assigned to original chunks before merging.

#### Scenario: Page preserved in merged chunk
- **WHEN** a tiny chunk on page 4 is merged into a preceding chunk on page 3
- **THEN** the merged chunk SHALL retain page=3 (from the preceding chunk)
