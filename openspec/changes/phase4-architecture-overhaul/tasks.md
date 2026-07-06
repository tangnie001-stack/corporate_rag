## 1. Database Schema

- [ ] 1.1 Update `deploy/mysql/init/001_schema.sql`: add users table, add user_id to knowledge_base/document/sessions, add new fields to document, add token fields to conversation_history
- [ ] 1.2 Update `src/config/queries.py`: all INSERT/SELECT/UPDATE/DELETE statements for new fields
- [ ] 1.3 Update `src/infra/mysql_db.py`: CRUD methods with new parameters

## 2. User Authentication

- [ ] 2.1 Create `src/infra/user_auth.py`: token generation (UUID), Redis store/lookup, sha256 password hashing, anonymous user_id generation
- [ ] 2.2 Create `src/api/routes/auth.py`: POST /api/auth/login (auto-register + login), GET /api/auth/verify, POST /api/auth/logout
- [ ] 2.3 Create `src/api/middleware.py`: token validation for /api/kbs/*; anonymous user_id Cookie management (generate on first visit, read `token` > `user_id` Cookie priority)
- [ ] 2.4 Create `nginx/html/login.html`: login page with account/password inputs and login button
- [ ] 2.5 Update `nginx/html/index.html`: on load, call /api/auth/verify, redirect to login.html if invalid
- [ ] 2.6 No frontend changes needed for chat auth — Cookie is auto-sent by browser; backend handles anonymous user_id generation

## 3. MinIO File Storage

- [ ] 3.1 Add `minio` dependency to `pyproject.toml`
- [ ] 3.2 Create `src/infra/file_store.py`: MinIO client with upload/download/delete methods, path builder `documents/{user_id}/{kb_id}/{doc_id}/{filename}`
- [ ] 3.3 Update `src/api/routes/documents.py`: upload flow changed to MinIO-first, then MySQL INSERT; MD5 dedup check; frontend loading response

## 4. Chunking Strategy

- [ ] 4.1 Add `block_type` metadata to parser output: pymupdf_parser.py and docx_parser.py set metadata.block_type="text" / "table" on each chunk
- [ ] 4.2 Refactor `src/infra/chunk_enhancer.py`: create BaseChunker abstract class, implement ParentChildChunker/QAChunker/TablePreservingChunker
- [ ] 4.3 Implement ChunkRouter: detect document features (question density from sentence ratio, table from block_type), route to appropriate chunker
- [ ] 4.4 Implement heading_path extraction from parsed document structure, inject prefix into chunk content (Phase 1: extract from DOCX heading styles; PDF/TXT heading_path left empty)
- [ ] 4.5 Update `src/infra/vector_store.py`: add_chunks preserves all metadata fields, stop discarding parent_content/tokens
- [ ] 4.6 Wire ChunkRouter into upload pipeline in documents.py

## 5. Retrieval Enhancement

- [ ] 5.1 Update `src/rag_chain.py`: RAGContext reads parent_content, format_context uses parent content for LLM; capture token usage from DashScope streaming response (chunk.usage_metadata) instead of heuristic estimation
- [ ] 5.2 Update `src/api/routes/chat.py`: citation event uses parent_content snippet, pass real token usage and model_name to add_message
- [ ] 5.3 Update `src/chat_manager.py`: add_message accepts token/model parameters, update INSERT

## 6. Frontend Adaptations

- [ ] 6.1 Update index.html: chunk preview shows chunk_strategy badge, displays parent_content when available
- [ ] 6.2 Update index.html: upload loading modal "正在同步上传中..." during MinIO transfer
- [ ] 6.3 Update chat source display: show parent_content snippet when available

## 7. Integration & Verification

- [ ] 7.1 Rebuild Docker images (app + nginx), restart stack
- [ ] 7.2 Verify user auth flow: register → login → token in cookie → KB page loads
- [ ] 7.3 Verify anonymous chat flow: no token → chat works as anonymous user
- [ ] 7.4 Verify file upload flow: file → MinIO → MySQL record → background process → ready
- [ ] 7.5 Verify chunk strategies: upload Q&A doc → chunk_strategy="qa"; upload table doc → chunk_strategy="table_preserving"; upload plain doc → chunk_strategy="parent_child"
- [ ] 7.6 Verify parent_content delivery: search → citation shows parent snippet, LLM receives parent content
- [ ] 7.7 Verify token recording: after chat response, conversation_history has token counts and model_name
- [ ] 7.8 Verify paginated chunks API: /chunks returns {items, total, page, page_size}
- [ ] 7.9 `pytest tests/ -v` all pass, `ruff check .` clean
