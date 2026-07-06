## 1. Response Code Constants & ApiError

- [ ] 1.1 Create `src/config/response_codes.py` with `Code` class (SUCCESS, AUTH*, KB*, FILE*, DOC*, SESSION*)
- [ ] 1.2 Create `src/infra/api_error.py` with `ApiError(code, message, status=400)` exception
- [ ] 1.3 Tests for Code class and ApiError

## 2. Middleware & Auth

- [ ] 2.1 Create `src/middleware/response_envelope.py` — `ResponseEnvelopeMiddleware`
- [ ] 2.2 Move `src/api/middleware.py` → `src/middleware/auth.py`, replace `JSONResponse` with envelope format
- [ ] 2.3 Register middleware in `src/api/main.py` (CORS → ResponseEnvelope → auth)
- [ ] 2.4 Register exception handlers for `HTTPException` (404→NOT_FOUND) and `RequestValidationError` (422→VALIDATION_ERROR)

## 3. Routes — HTTPException → ApiError

- [ ] 3.1 `knowledge_base.py`: `raise HTTPException(404)` → `raise ApiError(Code.KB_NOT_FOUND, ...)`
- [ ] 3.2 `sessions.py`: `raise HTTPException(404)` → `raise ApiError(Code.SESSION_NOT_FOUND, ...)`
- [ ] 3.3 `auth.py`: `raise HTTPException(401)` → `raise ApiError(Code.AUTH_WRONG_PASSWORD, ...)`
- [ ] 3.4 `documents.py`: `raise HTTPException(413/400/500)` → `ApiError` with correct codes

## 4. Frontend — apiRequest & checkAuth

- [ ] 4.1 Rewrite `apiRequest()` to return `body.data` on SUCCESS, throw on errors
- [ ] 4.2 Update `chat.html` `checkAuth()`: `d.code === 'SUCCESS' && d.data?.valid`
- [ ] 4.3 Update `index.html` `checkAuth()`: same
- [ ] 4.4 Update `login.html`: `d.data?.token || d.token`
- [ ] 4.5 Update `index.html` `loadKBs()` catch: `AUTH_REQUIRED` → redirect login
- [ ] 4.6 Update `index.html` `renderChunkPage()`: `body.data` → `data.items`
- [ ] 4.7 Update `index.html` upload polling: `body.data` → `data.status`
- [ ] 4.8 Version bump: `api.js?v=2` → `v=10`, add `chat.js?v=1`

## 5. doc_count Fix

- [ ] 5.1 Update `queries.py`: SQL add `LEFT JOIN document` + `COUNT(d.id) AS doc_count`
- [ ] 5.2 Update `mysql_db.py`: return `[{"id","name","doc_count"}]` instead of `[(id,name)]`
- [ ] 5.3 Update `app_service.py` and `knowledge_base.py` to use new format

## 6. Chunk field rename

- [ ] 6.1 `documents.py`: `chunk_strategy` → `block_type`
- [ ] 6.2 `index.html` chunk card badge: `chunk_strategy` → `block_type` (table→表格, text→文本)

## 7. Deployment Fix

- [ ] 7.1 `docker-compose.yml`: add `./data/chroma_persist:/app/data/chroma_persist` volume mount

## 8. infra Reorganization

- [ ] 8.1 Create subdirectories: `db/`, `chunking/`, `chunking/strategies/`, `search/`, `llm/`, `auth/`
- [ ] 8.2 Move files and update internal imports
- [ ] 8.3 Update all external `from src.infra.*` imports
- [ ] 8.4 Update test file imports
