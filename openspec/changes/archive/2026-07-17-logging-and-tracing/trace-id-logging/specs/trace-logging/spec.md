## ADDED Requirements

### Requirement: Loguru format with trace_id
The system SHALL configure loguru to output trace_id in every log line.
- SHALL call `logger.remove()` to remove the default sink
- SHALL add a file sink with format containing `{extra[trace_id]}`
- SHALL use `LOG_DIR` environment variable (default "logs") to determine log directory
- SHALL auto-create the log directory if it does not exist
- SHALL set log level to INFO to exclude DEBUG noise
- SHALL rotate log files daily, retain for 7 days

#### Scenario: Log contains trace_id
- **WHEN** a request is processed and logged
- **THEN** the log line includes the trace_id from the current ContextVar

#### Scenario: Log directory is created automatically
- **WHEN** the application starts and the log directory doesn't exist
- **THEN** the directory is created automatically before the first log write

### Requirement: stdlib logging files use loguru
The system SHALL replace `logging.getLogger(__name__)` with `from loguru import logger` in 3 files that currently bypass the trace_id patcher:
- `src/middleware/response_envelope.py`
- `src/infra/llm/langfuse_tracing.py`
- `src/infra/llm/prompt_manager.py`

#### Scenario: response_envelope logs have trace_id
- **WHEN** response_envelope logs an error
- **THEN** the log line includes trace_id from loguru's patcher

### Requirement: MySQL operation logging
The system SHALL log all key database operations with trace_id context.
- SHALL log get_document with doc_id and found status
- SHALL log soft_delete_document with doc_id and rows_affected
- SHALL log soft_delete_documents_by_kb with kb_id and rows_affected
- SHALL log soft_delete_kb with kb_id and found status
- SHALL log get_documents with kb_id and result count
- SHALL log update_document_status with doc_id, status, chunk_count
- SHALL log add_document with doc_id, kb_id, filename, status

#### Scenario: Document status change is logged
- **WHEN** a document's status is updated
- **THEN** the log shows "SQL update_document_status: doc_id=xxx status=ready chunk_count=42"

### Requirement: MinIO operation logging
The system SHALL log MinIO file operations immediately after they complete.
- SHALL log file upload with key and size
- SHALL log file download with key and size

#### Scenario: File upload is logged
- **WHEN** a file is uploaded to MinIO
- **THEN** the log shows "MinIO upload: key=documents/xxx size=1024000"

### Requirement: ChromaDB operation logging
The system SHALL log ChromaDB operations with trace_id context.
- SHALL log delete_collection with kb_id
- SHALL log add_chunks with kb_id, doc_id, chunk count
- SHALL log delete_document with kb_id, doc_id, deleted count
- SHALL log similarity_search with kb_id, query length, result count

#### Scenario: ChromaDB collection deletion is logged
- **WHEN** a ChromaDB collection is deleted
- **THEN** the log shows "ChromaDB delete_collection: kb_id=xxx"

### Requirement: RAG chain phase logging
The system SHALL log key RAG pipeline phases for latency and result tracking.
- SHALL log search results count per kb_id
- SHALL log rerank input/output count
- SHALL log first token latency in milliseconds

#### Scenario: RAG search results are logged
- **WHEN** a RAG search completes
- **THEN** the log shows "RAG search: kb_id=xxx query_len=yy results=zz"

### Requirement: Background task trace_id
The system SHALL capture and log trace_id at the start of async background document processing tasks.
- SHALL read `current_trace_id.get()` at the start of `_process_document_task`

#### Scenario: Background task has trace_id
- **WHEN** a document processing task starts
- **THEN** the first log line includes the trace_id from the upload request that created the task
