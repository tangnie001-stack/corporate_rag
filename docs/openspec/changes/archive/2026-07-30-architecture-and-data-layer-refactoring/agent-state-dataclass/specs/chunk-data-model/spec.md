## ADDED Requirements

### Requirement: ChunkData SHALL be the standard chunker return type

The system SHALL define chunker output as `list[ChunkData]` instead of `list[dict]`. The `BaseChunker.chunk()` method SHALL declare `-> list[ChunkData]`.

#### Scenario: parent_child chunker returns ChunkData list

- **WHEN** `ParentChildChunker.chunk()` is called
- **THEN** it SHALL return `list[ChunkData]`
- **AND** each item SHALL have `.content` and `.metadata` attributes

#### Scenario: Consumers access chunk fields via attribute

- **WHEN** a consumer reads chunk data from `document_service.py`
- **THEN** it SHALL use `chunk.content` and `chunk.metadata` instead of `chunk["content"]` and `chunk["metadata"]`

### Requirement: ChunkData SHALL be validated with existing validator

The `validate_chunks()` function SHALL continue to work unchanged, as it already accepts `list[ChunkData]`.

#### Scenario: validate_chunks accepts ChunkData

- **WHEN** `validate_chunks(chunks)` is called with `list[ChunkData]`
- **THEN** it SHALL process `chunk.content` and `chunk.tokens` correctly
- **AND** produce the same `ChunkQualityReport` as before
