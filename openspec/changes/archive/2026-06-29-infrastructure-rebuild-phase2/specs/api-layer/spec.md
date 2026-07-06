## ADDED Requirements

### Requirement: FastAPI application server
The system SHALL provide a FastAPI-based REST API server replacing the Gradio UI layer.
- SHALL create FastAPI app instance with title "Financial QA API"
- SHALL enable CORS middleware for Nginx reverse proxy origin
- SHALL serve on port 8000
- SHALL include OpenAPI documentation at /docs

#### Scenario: Health check endpoint
- **WHEN** user sends GET /api/health
- **THEN** the server responds with 200 OK and JSON status

### Requirement: Knowledge base CRUD API
The system SHALL expose knowledge base management endpoints.
- SHALL provide GET /api/kbs returning list of (kb_id, kb_name)
- SHALL provide POST /api/kbs accepting JSON body {name, description?}
- SHALL provide DELETE /api/kbs/{kb_id} removing KB and its vectors

#### Scenario: List knowledge bases
- **WHEN** user sends GET /api/kbs
- **THEN** response contains array of {id, name} objects

### Requirement: Document management API
The system SHALL expose document upload and listing endpoints.
- SHALL provide GET /api/kbs/{kb_id}/documents returning document list
- SHALL provide POST /api/kbs/{kb_id}/documents/upload accepting multipart file
- SHALL limit single file size to 10MB

#### Scenario: Upload document
- **WHEN** user sends POST /api/kbs/{kb_id}/documents/upload with a PDF file
- **THEN** the document is parsed, vectorized, and response includes chunk_count

### Requirement: SSE streaming chat
The system SHALL expose a server-sent events endpoint for streaming chat responses.
- SHALL provide GET /api/chat/stream with query params: session_id, kb_id, query
- SHALL stream tokens as SSE `token` events
- SHALL stream citations as SSE `citation` events
- SHALL terminate with SSE `done` event
- SHALL use app_service.rag_chain.chat_with_citations() as the backend

#### Scenario: Stream chat response
- **WHEN** user connects to GET /api/chat/stream with a financial question
- **THEN** the client receives a stream of token events followed by citation events
- **THEN** the stream terminates with a done event
