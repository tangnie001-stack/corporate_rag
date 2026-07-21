# chunk-validation Specification

## ADDED Requirements

### Requirement: Validate chunk quality on ingestion
The document processing pipeline SHALL validate chunk quality before indexing into the vector store. The validation SHALL detect and report both tiny chunks (too few tokens) and garbled chunks (excessive Unicode replacement characters).

The system SHALL use a heuristic token count (~2 characters per Chinese token) for validation, without invoking a full tokenizer.

#### Scenario: Tiny chunk detection
- **WHEN** a chunk contains fewer than 50 heuristic tokens
- **THEN** the system SHALL log a warning and record the chunk index in the quality report

#### Scenario: Garbled chunk detection
- **WHEN** a chunk contains more than 5% Unicode replacement characters (U+FFFD)
- **THEN** the system SHALL log a warning and record the chunk index in the quality report

#### Scenario: Normal chunk passes validation
- **WHEN** a chunk has >= 50 tokens and < 5% garbled characters
- **THEN** the system SHALL accept the chunk without generating any warning
