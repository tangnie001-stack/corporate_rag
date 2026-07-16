# async-upload Specification

## ADDED Requirements

### Requirement: Async document upload with 202
The document upload endpoint SHALL return HTTP 202 Accepted immediately after validating the file, before processing begins. The response SHALL include the document ID and initial status "parsing".

#### Scenario: Valid file returns 202
- **WHEN** a valid file (pdf/docx/txt under 10MB) is uploaded
- **THEN** the endpoint SHALL return 202 with `{"doc_id":"...", "status":"parsing", "filename":"..."}`

#### Scenario: File too large returns 413
- **WHEN** a file exceeds 10MB
- **THEN** the endpoint SHALL return HTTP 413

#### Scenario: Unsupported file type returns 400
- **WHEN** a file has an unsupported extension
- **THEN** the endpoint SHALL return HTTP 400

### Requirement: Background document processing
After accepting the upload, the system SHALL process the document in the background through stages: parsing, chunking, indexing, and ready/failed. Status SHALL be persisted in MySQL.

#### Scenario: Document status updates during processing
- **WHEN** processing progresses through each stage
- **THEN** the document status SHALL update to "parsing" → "chunking" → "indexing" → "ready"

### Requirement: Document status polling endpoint
The system SHALL provide a `GET /api/kbs/{kb_id}/documents/{doc_id}/status` endpoint returning the current processing status and progress percentage.

#### Scenario: Poll status for processed document
- **WHEN** the status endpoint is polled after processing completes
- **THEN** it SHALL return `{"status": "ready", "progress": 100, "chunk_count": N}`

#### Scenario: Poll status for non-existent document
- **WHEN** the status endpoint is polled for a non-existent document ID
- **THEN** it SHALL return `{"status": "not_found", "progress": 0}`

### Requirement: Chunk preview endpoint
The system SHALL provide a `GET /api/kbs/{kb_id}/documents/{doc_id}/chunks` endpoint returning the first 500 characters of each chunk for preview.

#### Scenario: Preview processed document chunks
- **WHEN** the chunks endpoint is called for a processed document
- **THEN** it SHALL return a list of chunks with content (truncated to 500 chars), page, tokens, and char_count
