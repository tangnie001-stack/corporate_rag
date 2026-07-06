## ADDED Requirements

### Requirement: Parser block_type metadata
PDF and DOCX parsers SHALL add `block_type` metadata to each chunk during parsing. Chunks originating from tables SHALL have `block_type="table"`; all other chunks SHALL have `block_type="text"`. This enables downstream chunkers to identify table boundaries.

#### Scenario: Table block detection in PDF parser
- **WHEN** PyMuPDFParser detects a table via `page.find_tables()`
- **THEN** the parsed chunk SHALL have metadata.block_type="table"

#### Scenario: Text block detection in DOCX parser
- **WHEN** DocxParser processes a paragraph that is not inside a table
- **THEN** the parsed chunk SHALL have metadata.block_type="text"

### Requirement: ChunkRouter for chunking strategy
The system SHALL implement a ChunkRouter that selects the chunking strategy based on document structure features after parsing. The router SHALL check features in order: question density > table detection > fallback to default.

#### Scenario: Strategy selection order
- **WHEN** a document is parsed and its features are analyzed
- **THEN** the router SHALL check: (1) question sentence ratio > 20% → "qa" strategy; (2) any chunk with block_type="table" → "table_preserving" strategy; (3) otherwise → "parent_child" strategy

### Requirement: QA chunking strategy
The system SHALL implement a QA chunker for documents with high question density (FAQ, Q&A collections). QA chunks SHALL be single-level (no parent-child), preserving question-answer pairs without splitting.

#### Scenario: QA document detection
- **WHEN** a document has > 20% of sentences ending with question marks ("?" or "？")
- **THEN** system SHALL use QAChunker with chunk_strategy="qa"

#### Scenario: QA chunk structure
- **WHEN** QAChunker processes a Q&A pair
- **THEN** each chunk SHALL contain a complete question-answer pair, with parent_content set to null, tokens counted, and chunk_strategy="qa"

### Requirement: Table-preserving chunking strategy
The system SHALL implement a table-preserving chunker for documents containing tables. The chunker SHALL NOT split content across table boundaries, ensuring tables remain intact within a single chunk.

#### Scenario: Table-preserving document detection
- **WHEN** parsed document contains any chunk with block_type="table"
- **THEN** system SHALL use TablePreservingChunker with chunk_strategy="table_preserving"

#### Scenario: Table boundary protection
- **WHEN** TablePreservingChunker processes a document with a table
- **THEN** the table SHALL NOT be split across multiple chunks; table content SHALL be contained entirely within one parent chunk

### Requirement: Parent-child chunking strategy (default)
The system SHALL keep ParentChildChunker as the default fallback strategy. Parent chunks at ~1024 tokens, child chunks at ~256 tokens, overlap 25 tokens. Each child chunk SHALL carry parent_content in its metadata.

#### Scenario: Default fallback
- **WHEN** a document does not match QA or table-preserving detection rules
- **THEN** system SHALL use ParentChildChunker with chunk_strategy="parent_child"

#### Scenario: Parent-child metadata
- **WHEN** a child chunk is created by ParentChildChunker
- **THEN** its metadata SHALL include parent_content (full parent text), tokens, heading_path, chunk_strategy="parent_child"

### Requirement: Heading path injection
The system SHALL inject heading path context into chunk content. The heading path SHALL be derived from document structure (chapter/section titles). The text SHALL be prefixed with `【{last_2_levels}】{original_content}`.

#### Scenario: Heading prefix in content
- **WHEN** a chunk is created from a section with heading_path "2024年 > 利润表 > 主要项目"
- **THEN** the chunk content SHALL be prefixed as "【利润表 > 主要项目】original content..."

### Requirement: Chunk metadata completeness
The system SHALL store complete metadata for each chunk in ChromaDB. The `add_chunks()` method SHALL NOT discard any metadata fields passed from the chunker.

#### Scenario: Metadata preservation
- **WHEN** a chunker returns metadata including chunk_strategy, heading_path, parent_content, tokens, entities
- **THEN** add_chunks() SHALL store all these fields in ChromaDB, plus auto-generated chunk_index, chunk_total, source, page, doc_id

### Requirement: chunk_strategy field
MySQL document table and ChromaDB chunk metadata SHALL both include a `chunk_strategy` field.

#### Scenario: Document-level strategy
- **WHEN** a document is processed
- **THEN** MySQL document.chunk_strategy SHALL be set to the selected strategy

#### Scenario: Chunk-level strategy
- **WHEN** a chunk is stored in ChromaDB
- **THEN** each chunk's metadata SHALL include chunk_strategy

### Requirement: Processing state machine
The document processing pipeline SHALL update processing_state and processing_progress in the MySQL document table.

#### Scenario: State transitions
- **WHEN** document is being parsed
- **THEN** processing_state="extracting", progress=30
- **WHEN** document is being chunked
- **THEN** processing_state="chunking", progress=50
- **WHEN** chunks are being indexed to ChromaDB
- **THEN** processing_state="indexing", progress=70, processing_message="向量化中 {current}/{total}"
- **WHEN** processing completes successfully
- **THEN** status="ready", progress=100
- **WHEN** processing fails
- **THEN** status="failed", error_msg set with failure reason
