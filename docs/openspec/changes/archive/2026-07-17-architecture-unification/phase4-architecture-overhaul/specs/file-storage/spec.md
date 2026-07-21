## ADDED Requirements

### Requirement: MinIO file storage
The system SHALL store uploaded files in MinIO instead of local temp files. The object key SHALL follow the pattern `documents/{user_id}/{kb_id}/{doc_id}/{filename}`.

#### Scenario: Upload file to MinIO
- **WHEN** a file is uploaded via POST /kbs/{kb_id}/documents/upload
- **THEN** the file SHALL be streamed to MinIO bucket "documents" at path `documents/{user_id}/{kb_id}/{doc_id}/{filename}` before any database record is created

### Requirement: Write order (MinIO first, MySQL second)
The system SHALL write the file to MinIO first. Only if MinIO write succeeds SHALL the system INSERT a MySQL document record. If MinIO write fails, the system SHALL return an error without creating any MySQL record.

#### Scenario: Successful upload
- **WHEN** MinIO write succeeds
- **THEN** MySQL INSERT with status=processing, processing_state=extracting, file_path set to MinIO object key； API returns 202 with doc_id

#### Scenario: MinIO write failure
- **WHEN** MinIO write fails
- **THEN** API returns 5xx error, no MySQL document record is created

### Requirement: Frontend upload loading indicator
The frontend SHALL display a modal loading box with text "正在同步上传中..." during the upload-to-MinIO phase.

#### Scenario: Upload loading UI
- **WHEN** user clicks upload button
- **THEN** frontend shows a modal overlay with spinner and "正在同步上传中..." text, blocking further interaction until upload completes or fails

### Requirement: File download from MinIO
The system SHALL support downloading original files from MinIO for background processing (parsing).

#### Scenario: Download for processing
- **WHEN** the background task starts processing a document
- **THEN** the system downloads the file from MinIO using the stored file_path

### Requirement: MD5 deduplication
The system SHALL compute the MD5 hash of uploaded files. Before inserting a new document record, the system SHALL check if a document with the same hash already exists in the same knowledge base.

#### Scenario: Duplicate file
- **WHEN** user uploads a file with the same MD5 hash as an existing document in the same knowledge base
- **THEN** the system SHALL return the existing doc_id and not create a duplicate
