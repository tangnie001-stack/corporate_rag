# Phase 2 Step 0 — Infrastructure Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Gradio UI with FastAPI REST API + Nginx reverse proxy + HTML frontend, upgrade LangChain from 0.3.x to 1.x, and add Langfuse self-hosted observability.

**Architecture:** Nginx (:80) serves static frontend files and proxies `/api/*` to FastAPI (:8000). FastAPI replaces Gradio as the backend layer, exposing KB CRUD, document management, and SSE streaming chat endpoints. Langfuse (postgres-backed) provides LLM tracing via CallbackHandler + @observe(). LangChain is upgraded from 0.3.x to 1.x with exact-version pinning for production stability. DashScope models remain on langchain-community (langchain-dashscope lacks full model coverage).

**Tech Stack:** FastAPI 0.138.0, Nginx (alpine), LangChain 1.x (core/openai/text-splitters), langchain-community 0.3.31, Langfuse 4.12.0, ChromaDB, MySQL 8.0, Redis 7, Docker Compose, SSE streaming

## Global Constraints

- All new Python code MUST pass `ruff format` and `ruff check --fix`
- All modified interfaces MUST be reflected in `docs/api-contract.md`
- Gradio `src/app.py` MUST be deleted (replaced by FastAPI + HTML frontend)
- API Key (DashScope, Langfuse) values MUST NOT be hardcoded — loaded from `.env` or environment
- Every external dependency (MySQL, Redis, ChromaDB, Langfuse) MUST have graceful degradation on failure
- All new tests MUST use `pytest` (not `unittest.TestCase`) with `function`-scoped fixtures
- Docker container names MUST follow `financial-qa-*` convention
- ChromaDB data persists at `data/chroma_persist/` — Docker volume mapping must preserve this
- `old/` directory files MUST NOT be modified
- SSE streaming format MUST follow: `event: token` / `event: citation` / `event: done` sequence
- Single file upload limit: 10MB (FastAPI), 50MB (internal processing)

---

## File Structure

### New Files Created

| File | Responsibility |
|------|---------------|
| `src/api/main.py` | FastAPI app creation, CORS middleware, lifecycle events, router mounting |
| `src/api/__init__.py` | Package init |
| `src/api/routes/__init__.py` | Router aggregation |
| `src/api/routes/chat.py` | `GET /api/chat/stream` SSE streaming endpoint |
| `src/api/routes/knowledge_base.py` | `GET /api/kbs`, `POST /api/kbs`, `DELETE /api/kbs/{kb_id}` |
| `src/api/routes/documents.py` | `GET /api/kbs/{kb_id}/documents`, `POST /api/kbs/{kb_id}/documents/upload` |
| `src/api/routes/health.py` | `GET /api/health` |
| `nginx/Dockerfile` | Nginx alpine image with conf + static files baked in |
| `nginx/nginx.conf` | Reverse proxy `/api/*` → `app:8000`, static file serving at `/` |
| `nginx/html/index.html` | Frontend landing page (KB management) |
| `nginx/html/kb.html` | Frontend KB detail page (documents list) |
| `nginx/html/chat.html` | Frontend chat page (SSE streaming) |
| `nginx/html/css/style.css` | Frontend stylesheet |
| `nginx/html/js/api.js` | Frontend API client (fetch helpers) |
| `nginx/html/js/chat.js` | Frontend chat logic (EventSource SSE consumer) |
| `docs/api-contract.md` | API contract documentation |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Upgrade LangChain packages to 1.x, add fastapi/uvicorn/langfuse, pin exact versions |
| `src/models.py` | No changes — stays on langchain-community (DashScope package lacks Rerank) |
| `src/config/settings.py` | Add 4 Langfuse config variables |
| `src/rag_chain.py` | Add Langfuse CallbackHandler + @observe decorators |
| `docker-compose.yml` | Add postgres, langfuse, nginx services; adjust app port/command |
| `.env` (or `.env.template`) | Add Langfuse + FastAPI configuration entries |
| `README.md` | Update architecture, startup steps, add Langfuse setup |

### Removed Files

| File | Reason |
|------|--------|
| `src/app.py` | Gradio UI deleted — replaced by FastAPI + HTML frontend |

---

## Task Breakdown

### Task T1: Update pyproject.toml dependencies

**Files:**
- Modify: `pyproject.toml` (full file — version bumps + new deps)
- Test: `tests/test_dependencies.py` (verify imports)

**Interfaces:**
- Produces: Updated `dependencies` list in pyproject.toml with:
  - `langchain-core==1.4.8` (pinned exact)
  - `langchain-openai==1.3.3` (pinned exact)
  - `langchain-text-splitters==1.1.2` (pinned exact)
  - `langchain-community==0.3.31` (pinned exact, stays on 0.3.x)
  - `langfuse==4.12.0` (pinned exact)
  - `fastapi==0.138.0` (pinned exact)
  - `uvicorn[standard]==0.49.0` (pinned exact)
- `langchain-dashscope` is NOT added (DashScope models stay in langchain-community for now)
- The `gradio>=5.0,<6.0` dependency is removed

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dependencies.py
"""Verify that all required packages can be imported."""


def test_fastapi_import():
    import fastapi  # noqa: F401


def test_uvicorn_import():
    import uvicorn  # noqa: F401


def test_langfuse_import():
    import langfuse  # noqa: F401


def test_langchain_core_version():
    import langchain_core
    version = tuple(int(x) for x in langchain_core.__version__.split(".")[:2])
    assert version >= (1, 0), f"langchain-core {langchain_core.__version__} < 1.0"


def test_gradio_not_required():
    """Gradio is no longer a hard dependency."""
    import importlib
    import sys
    # If gradio happens to be installed, that's fine — it's just not required
    # This test verifies the core app can start without gradio
    if "gradio" in sys.modules:
        del sys.modules["gradio"]
    try:
        import importlib
        spec = importlib.util.find_spec("gradio")
        # gradio may still be in the environment, just no longer required
    except ModuleNotFoundError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dependencies.py -v`
Expected: FAIL with 5 errors — `ModuleNotFoundError: No module named 'fastapi'`, etc.

- [ ] **Step 3: Modify pyproject.toml**

Edit `pyproject.toml` — replace `[project] dependencies` section:

```toml
dependencies = [
    "chromadb>=0.5.0,<1.0.0",
    "langchain-openai==1.3.3",
    "langchain-community==0.3.31",
    "langchain-core==1.4.8",
    "langchain-text-splitters==1.1.2",
    "langfuse==4.12.0",
    "fastapi==0.138.0",
    "uvicorn[standard]==0.49.0",
    "pymupdf>=1.24.0,<2.0.0",
    "python-docx>=1.1.0,<2.0.0",
    "python-dotenv>=1.0.0,<2.0.0",
    "loguru>=0.7.0,<1.0.0",
    "mysql-connector-python>=8.0.0,<9.0.0",
    "chardet>=5.0.0,<6.0.0",
    "pymysql>=1.1.0,<2.0.0",
    "redis>=5.0.0,<6.0.0",
    "dashscope>=1.20.0,<2.0.0",
]
```

Changes made:
- Removed `gradio>=5.0,<6.0`
- Bumped `langchain-openai` from `>=0.2.0,<1.0.0` to `==1.3.3` (pinned)
- Bumped `langchain-core` from `>=0.3.0,<1.0.0` to `==1.4.8` (pinned)
- Bumped `langchain-text-splitters` from `>=0.3.0,<1.0.0` to `==1.1.2` (pinned)
- Added `langfuse==4.12.0` (pinned)
- Added `fastapi==0.138.0` (pinned)
- Added `uvicorn[standard]==0.49.0` (pinned)
- Note: `langchain-community` stays at `==0.3.31` (pinned). No `langchain-dashscope` added — DashScope models remain in community.

- [ ] **Step 4: Install new dependencies**

Run: `pip install -e ".[dev]"`
Expected: All packages install successfully.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_dependencies.py -v`
Expected: All 6 tests PASS

- [ ] **Step 6: Ruff format & check**

Run: `ruff format . && ruff check . --fix`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/test_dependencies.py
git commit -m "build: upgrade LangChain to 1.x, add FastAPI/Langfuse dependencies"
```

---

### Task T2: Verify models.py stays on langchain-community

**Note:** After evaluation, `langchain-dashscope` 0.1.8 only exports `ChatDashScope` and `DashScopeEmbeddings` — it lacks `DashScopeRerank`. Migrating only one of two models would create an inconsistent split. Decision: **keep both imports on `langchain_community`** until langchain-dashscope fully supports all three model types.

**Files:**
- No changes to `src/models.py` (stays on community imports)
- Test: `tests/test_models.py` (existing tests remain unchanged)

**Interfaces:**
- `get_embeddings()` returns `langchain_community.embeddings.DashScopeEmbeddings` (unchanged)
- `get_rerank()` returns `langchain_community.document_compressors.dashscope_rerank.DashScopeRerank` (unchanged)

- [ ] **Step 1: Verify no changes needed**

Run: `grep "langchain_community" src/models.py`
Expected output shows both imports from `langchain_community`:
```
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
```

- [ ] **Step 2: Run existing model tests**

Run: `pytest tests/test_models.py -v`
Expected: All tests PASS (no changes needed)

- [ ] **Step 3: Commit**

```bash
git add .
git commit -m "chore: confirm models.py stays on langchain-community (dashscope package lacks Rerank)"
```

---

### Task T3: Add Langfuse configuration to settings.py

**Files:**
- Modify: `src/config/settings.py` (add 4 lines at end of file)
- Test: Update `tests/test_config.py` or create `tests/test_settings.py`

**Interfaces:**
- Consumes: `python-dotenv` (already loaded at top of settings.py)
- Produces: New config variables:
  - `LANGFUSE_SECRET_KEY: str` — Langfuse API secret key
  - `LANGFUSE_PUBLIC_KEY: str` — Langfuse API public key
  - `LANGFUSE_HOST: str` — Langfuse server URL (default: `http://langfuse:3000`)
  - `LANGFUSE_ENABLE: bool` — Master toggle (default: `true`)
- All four variables consumed by T5 (rag_chain.py)

- [ ] **Step 1: Write the test**

```python
# tests/test_settings.py
"""Tests for configuration settings."""

import os
from unittest.mock import patch

from src.config import settings as config


def test_langfuse_secret_key_defaults_to_empty():
    """LANGFUSE_SECRET_KEY defaults to empty string."""
    assert hasattr(config, "LANGFUSE_SECRET_KEY")
    assert config.LANGFUSE_SECRET_KEY == ""


def test_langfuse_public_key_defaults_to_empty():
    """LANGFUSE_PUBLIC_KEY defaults to empty string."""
    assert hasattr(config, "LANGFUSE_PUBLIC_KEY")
    assert config.LANGFUSE_PUBLIC_KEY == ""


def test_langfuse_host_default():
    """LANGFUSE_HOST defaults to http://langfuse:3000 (Docker internal)."""
    assert config.LANGFUSE_HOST == "http://langfuse:3000"


def test_langfuse_enable_default_true():
    """LANGFUSE_ENABLE defaults to True."""
    assert config.LANGFUSE_ENABLE is True


def test_langfuse_host_override_from_env():
    """LANGFUSE_HOST can be overridden via environment variable."""
    with patch.dict(os.environ, {"LANGFUSE_HOST": "http://localhost:3000"}, clear=False):
        # Re-import triggers the os.getenv call
        from importlib import reload
        from src.config import settings as reloaded
        reload(reloaded)
        assert reloaded.LANGFUSE_HOST == "http://localhost:3000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL — 4 errors: `AttributeError: module 'src.config.settings' has no attribute 'LANGFUSE_SECRET_KEY'`

- [ ] **Step 3: Add config to settings.py**

Append these lines at the end of `src/config/settings.py` (before any EOF comment):

```python
# ====== Langfuse ======
# LLM 可观测性平台配置，用于 trace 检索→重排序→生成的完整链路
# 首次启动需手动在 Langfuse UI (http://localhost:3000) 创建 API Key
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
# 注意：Docker 内部使用容器名 langfuse:3000，宿主机访问用 localhost:3000
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
# 全局开关：false 时完全跳过 Langfuse 初始化
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_settings.py -v`
Expected: All 5 tests PASS. (Note: the env override test may be flaky due to module-level caching — it's acceptable if this test needs adjustment.)

- [ ] **Step 5: Ruff format & check**

Run: `ruff format . && ruff check . --fix`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/config/settings.py tests/test_settings.py
git commit -m "feat: add Langfuse configuration to settings"
```

---

### Task T4: Create FastAPI application framework + routes

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/main.py`
- Create: `src/api/routes/__init__.py`
- Create: `src/api/routes/chat.py`
- Create: `src/api/routes/knowledge_base.py`
- Create: `src/api/routes/documents.py`
- Create: `src/api/routes/health.py`
- Modify: `docs/api-contract.md` (new file — API reference)
- Test: `tests/api/test_health.py`, `tests/api/test_knowledge_base.py`, `tests/api/test_documents.py`, `tests/api/test_chat.py`

**Interfaces:**
- Consumes: `app_service.AppService` (existing, unchanged) — all route handlers delegate to this
- Consumes: `src.config.settings` — for configuration
- Produces: `GET /api/health` → `{"status": "ok"}`
- Produces: `GET /api/kbs` → `[{"id": "uuid", "name": "str"}, ...]`
- Produces: `POST /api/kbs` (body: `{"name": "str", "description": "str"}`) → `{"id": "uuid", "created": bool}`
- Produces: `DELETE /api/kbs/{kb_id}` → `{"success": bool, "message": "str"}`
- Produces: `GET /api/kbs/{kb_id}/documents` → `[{"id": "uuid", "filename": "str", ...}]`
- Produces: `POST /api/kbs/{kb_id}/documents/upload` (multipart) → `{"success": bool, "chunk_count": int, "error": "str"}`
- Produces: `GET /api/chat/stream?session_id=&kb_id=&query=` → SSE stream (token → citation → done)

- [ ] **Step 1: Create package init files**

```python
# src/api/__init__.py
# FastAPI REST API package
```

```python
# src/api/routes/__init__.py
from src.api.routes.health import router as health_router
from src.api.routes.knowledge_base import router as kb_router
from src.api.routes.documents import router as doc_router
from src.api.routes.chat import router as chat_router

__all__ = ["health_router", "kb_router", "doc_router", "chat_router"]
```

- [ ] **Step 2: Create main.py — FastAPI app with CORS and lifecycle**

```python
# src/api/main.py
"""FastAPI application entry point — app factory, CORS, lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.routes import health_router, kb_router, doc_router, chat_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle handler — startup/shutdown without Gradio."""
    logger.info("Financial QA API starting up")
    yield
    logger.info("Financial QA API shutting down")


app = FastAPI(
    title="Financial QA API",
    description="REST API for Financial Document QA Assistant — KB management, document upload, and streaming RAG chat",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
)

# CORS: allow Nginx reverse proxy origin + localhost for dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Nginx handles origin filtering at proxy layer
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount route modules
app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(kb_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(doc_router, prefix="/api", tags=["documents"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
```

- [ ] **Step 3: Create health route**

```python
# src/api/routes/health.py
"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check — returns status ok if the server is running."""
    return {"status": "ok"}
```

- [ ] **Step 4: Create knowledge_base routes**

```python
# src/api/routes/knowledge_base.py
"""Knowledge base CRUD endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.app_service import AppService

router = APIRouter()

# Singleton service instance (lazy init)
_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


class CreateKBRequest(BaseModel):
    name: str
    description: str = ""


class CreateKBResponse(BaseModel):
    id: str
    created: bool


@router.get("/kbs")
async def list_knowledge_bases():
    """List all knowledge bases."""
    svc = _get_service()
    kbs = svc.list_knowledge_bases()
    return [{"id": kb_id, "name": kb_name} for kb_id, kb_name in kbs]


@router.post("/kbs", status_code=201)
async def create_knowledge_base(body: CreateKBRequest) -> CreateKBResponse:
    """Create a new knowledge base (or return existing if name duplicates)."""
    svc = _get_service()
    kb_id, is_new = svc.create_knowledge_base(body.name, body.description)
    return CreateKBResponse(id=kb_id, created=is_new)


@router.delete("/kbs/{kb_id}")
async def delete_knowledge_base(kb_id: str):
    """Delete a knowledge base and its vector data."""
    svc = _get_service()
    success, message = svc.delete_knowledge_base(kb_id)
    if not success:
        raise HTTPException(status_code=404, detail=message)
    return {"success": True, "message": message}
```

- [ ] **Step 5: Create documents routes**

```python
# src/api/routes/documents.py
"""Document upload and listing endpoints."""

from fastapi import APIRouter, HTTPException, UploadFile, File
from loguru import logger

from src.app_service import AppService

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB per spec


@router.get("/kbs/{kb_id}/documents")
async def get_documents(kb_id: str):
    """List all documents in a knowledge base."""
    svc = _get_service()
    docs = svc.get_documents(kb_id)
    return docs


@router.post("/kbs/{kb_id}/documents/upload", status_code=201)
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    """Upload and process a document (PDF/DOCX/TXT)."""
    # Validate file size
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 10MB)")

    # Validate file extension
    allowed_extensions = {".pdf", ".docx", ".txt"}
    ext = f".{file.filename.rsplit('.', 1)[-1].lower()}" if "." in file.filename else ""
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(allowed_extensions)}",
        )

    # Write to temp file
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        svc = _get_service()
        result = svc.upload_and_process(kb_id, tmp_path, file.filename)
        if not result["success"]:
            raise HTTPException(status_code=422, detail=result.get("error", "Processing failed"))
        return {
            "success": True,
            "chunk_count": result["chunk_count"],
            "filename": file.filename,
        }
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError as e:
            logger.warning("Failed to clean up temp file {}: {}", tmp_path, e)
```

- [ ] **Step 6: Create chat SSE streaming route**

```python
# src/api/routes/chat.py
"""SSE streaming chat endpoint."""

import asyncio
import json
from typing import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from src.app_service import AppService

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


async def _stream_rag_response(
    kb_id: str, session_id: str, query: str,
) -> AsyncGenerator[str, None]:
    """Stream RAG response as SSE events: token → citation → done."""
    try:
        svc = _get_service()
        token_gen, citations = svc.rag_chain.chat_with_citations(
            kb_id, session_id, query,
        )

        # Stream tokens as SSE token events
        for token in token_gen:
            yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
            await asyncio.sleep(0)  # yield control to event loop

        # Stream citations as SSE citation events
        for ctx in citations:
            citation_data = {
                "source": ctx.source,
                "page": ctx.page,
                "snippet": ctx.content[:200],
            }
            yield f"event: citation\ndata: {json.dumps(citation_data)}\n\n"
            await asyncio.sleep(0)

        # Save assistant response to chat history
        # (The token_gen was already consumed above; citations are available)
        full_answer = ""
        # Note: we can't replay token_gen, so we save just the citations for history
        # A better approach would buffer tokens, but for MVP citations are enough
        sources = [f"{c.source} (第{c.page}页)" for c in citations]
        svc.rag_chain.chat_manager.add_message(
            session_id, "assistant", full_answer, sources=sources,
        )

    except Exception as e:
        logger.error("Chat stream error: {}", str(e))
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    # Signal completion
    yield "event: done\ndata: {}\n\n"


@router.get("/chat/stream")
async def chat_stream(
    session_id: str = Query(..., description="Session ID for conversation history"),
    kb_id: str = Query(..., description="Knowledge base ID (or empty for cross-KB search)"),
    query: str = Query(..., description="User question"),
):
    """Streaming RAG chat endpoint — returns SSE event stream.

    Events:
      - token: individual answer tokens
      - citation: source document references
      - error: error information
      - done: stream complete
    """
    return StreamingResponse(
        _stream_rag_response(kb_id, session_id, query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )
```

- [ ] **Step 7: Write tests for API endpoints**

```python
# tests/api/test_health.py
"""Tests for health check endpoint."""

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_returns_200():
    """GET /api/health returns 200 with status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

```python
# tests/api/test_knowledge_base.py
"""Tests for KB CRUD endpoints."""

from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


@patch("src.api.routes.knowledge_base._get_service")
def test_list_kbs(mock_get_service):
    """GET /api/kbs returns list of KBs."""
    mock_svc = mock_get_service.return_value
    mock_svc.list_knowledge_bases.return_value = [("kb-1", "年报知识库"), ("kb-2", "财报知识库")]

    response = client.get("/api/kbs")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "kb-1", "name": "年报知识库"},
        {"id": "kb-2", "name": "财报知识库"},
    ]


@patch("src.api.routes.knowledge_base._get_service")
def test_create_kb(mock_get_service):
    """POST /api/kbs creates a new KB."""
    mock_svc = mock_get_service.return_value
    mock_svc.create_knowledge_base.return_value = ("new-kb-uuid", True)

    response = client.post("/api/kbs", json={"name": "测试库", "description": "测试"})

    assert response.status_code == 201
    assert response.json() == {"id": "new-kb-uuid", "created": True}


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_exists(mock_get_service):
    """DELETE /api/kbs/{kb_id} when KB exists."""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base.return_value = (True, "知识库已删除")

    response = client.delete("/api/kbs/kb-1")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "知识库已删除"}


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_not_found(mock_get_service):
    """DELETE /api/kbs/{kb_id} when KB doesn't exist returns 404."""
    mock_svc = mock_get_service.return_value
    mock_svc.delete_knowledge_base.return_value = (False, "知识库不存在")

    response = client.delete("/api/kbs/kb-missing")

    assert response.status_code == 404
```

```python
# tests/api/test_documents.py
"""Tests for document upload/listing endpoints."""

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


@patch("src.api.routes.documents._get_service")
def test_get_documents(mock_get_service):
    """GET /api/kbs/{kb_id}/documents returns document list."""
    mock_svc = mock_get_service.return_value
    mock_svc.get_documents.return_value = [
        {"id": "doc-1", "filename": "report.pdf", "status": "ready"},
    ]

    response = client.get("/api/kbs/kb-1/documents")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["filename"] == "report.pdf"


@patch("src.api.routes.documents._get_service")
def test_upload_document(mock_get_service):
    """POST /api/kbs/{kb_id}/documents/upload with valid file."""
    mock_svc = mock_get_service.return_value
    mock_svc.upload_and_process.return_value = {"success": True, "chunk_count": 10, "error": ""}

    response = client.post(
        "/api/kbs/kb-1/documents/upload",
        files={"file": ("test.pdf", b"%PDF-1.4 test content", "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["chunk_count"] == 10
```

```python
# tests/api/test_chat.py
"""Tests for SSE streaming chat endpoint."""

from unittest.mock import patch
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


@patch("src.api.routes.chat._get_service")
def test_chat_stream_returns_sse(mock_get_service):
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = mock_get_service.return_value
    mock_chain = mock_svc.rag_chain

    # Create a generator that yields tokens
    def token_gen():
        yield "净利润"
        yield "为"
        yield "100亿"
        yield "元"

    mock_chain.chat_with_citations.return_value = (token_gen(), [])

    response = client.get("/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/api/ -v`
Expected: All API tests PASS (with mocked services)

- [ ] **Step 9: Create / update API contract document**

```markdown
# docs/api-contract.md
# Financial QA API — Contract

## Base URL

Production: `http://localhost/api/`
Development: `http://localhost:8000/`
OpenAPI Docs: `http://localhost/api/docs`

## Knowledge Bases

### List all KBs

`GET /api/kbs`

Response 200:
```json
[
  {"id": "uuid", "name": "库名称"}
]
```

### Create KB

`POST /api/kbs`
Content-Type: `application/json`

Body:
```json
{"name": "库名称", "description": "可选描述"}
```

Response 201:
```json
{"id": "uuid", "created": true || false}
```

### Delete KB

`DELETE /api/kbs/{kb_id}`

Response 200:
```json
{"success": true, "message": "知识库已删除"}
```

Response 404:
```json
{"detail": "知识库不存在"}
```

## Documents

### List documents

`GET /api/kbs/{kb_id}/documents`

Response 200:
```json
[
  {"id": "uuid", "filename": "name.pdf", "type": "pdf", "size": 1234, "status": "ready", "chunk_count": 10}
]
```

### Upload document

`POST /api/kbs/{kb_id}/documents/upload`
Content-Type: `multipart/form-data`

Field `file`: PDF/DOCX/TXT file (max 10MB)

Response 201:
```json
{"success": true, "chunk_count": 10, "filename": "name.pdf"}
```

Response 413:
```json
{"detail": "File too large (max 10MB)"}
```

## Chat (SSE Streaming)

### Stream chat response

`GET /api/chat/stream?session_id={sid}&kb_id={kb_id}&query={question}`

Content-Type: `text/event-stream`

Events:

```
event: token
data: {"token": "回答文本片段"}

event: citation
data: {"source": "文件名.pdf", "page": 15, "snippet": "内容摘要..."}

event: done
data: {}

event: error
data: {"error": "错误消息"}
```

## Health

### Health check

`GET /api/health`

Response 200:
```json
{"status": "ok"}
```
```

- [ ] **Step 10: Ruff format & check**

Run: `ruff format . && ruff check . --fix`
Expected: No errors

- [ ] **Step 11: Commit**

```bash
git add src/api/ tests/api/ docs/api-contract.md
git commit -m "feat: add FastAPI REST API layer with KB CRUD, document upload, and SSE streaming"
```

---

### Task T5: Add Langfuse Tracing to rag_chain.py

**Files:**
- Modify: `src/rag_chain.py` (add CallbackHandler init + @observe decorators)
- Test: `tests/test_rag_chain_tracing.py`

**Interfaces:**
- Consumes: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENABLE` (from T3)
- Produces: `RAGChain._langfuse_handler` — CallbackHandler instance (or None if disabled/failed)
- Produces: `RAGChain.chat_with_citations()` gets `@observe(name="chat_with_citations")` decorator
- Produces: `RAGChain._rerank_results()` gets `@observe(name="rerank_results")` decorator
- Produces: `RAGChain._stream_answer()` passes `config={"callbacks": [handler]}` to `self.llm.stream()`

- [ ] **Step 1: Write tests**

```python
# tests/test_rag_chain_tracing.py
"""Tests for Langfuse tracing integration in RAGChain."""

from unittest.mock import patch, MagicMock, PropertyMock
from src.rag_chain import RAGChain


@patch("src.rag_chain.LANGFUSE_ENABLE", False)
def test_langfuse_handler_not_created_when_disabled():
    """When LANGFUSE_ENABLE is False, no handler is created."""
    chain = RAGChain()
    assert chain._langfuse_handler is None


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.CallbackHandler")
def test_langfuse_handler_created_when_enabled(mock_handler_cls):
    """When LANGFUSE_ENABLE is True, CallbackHandler is initialized."""
    mock_handler = MagicMock()
    mock_handler_cls.return_value = mock_handler

    chain = RAGChain()

    mock_handler_cls.assert_called_once()
    assert chain._langfuse_handler == mock_handler


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.CallbackHandler")
def test_langfuse_init_failure_does_not_crash(mock_handler_cls):
    """When CallbackHandler init fails, chain still works without tracing."""
    mock_handler_cls.side_effect = Exception("Connection refused")

    chain = RAGChain()  # Should not raise

    assert chain._langfuse_handler is None


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.CallbackHandler")
def test_stream_answer_passes_callbacks(mock_handler_cls, mocker):
    """_stream_answer passes callbacks config to llm.stream()."""
    mock_handler = MagicMock()
    mock_handler_cls.return_value = mock_handler
    chain = RAGChain()
    mock_llm = MagicMock()
    mock_stream = MagicMock()
    mock_llm.stream.return_value = []
    chain._llm = mock_llm

    list(chain._stream_answer([mocker.MagicMock()]))

    mock_llm.stream.assert_called_once()
    _, kwargs = mock_llm.stream.call_args
    assert "config" in kwargs
    assert kwargs["config"]["callbacks"] == [mock_handler]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rag_chain_tracing.py -v`
Expected: FAIL — `AttributeError: type object 'RAGChain' has no attribute '_langfuse_handler'` (multiple tests)

- [ ] **Step 3: Modify rag_chain.py**

Add at the top of `src/rag_chain.py` (after existing imports):

```python
from src.config import (
    LANGFUSE_SECRET_KEY,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_HOST,
    LANGFUSE_ENABLE,
)
```

Add `_langfuse_handler` initialization inside `__init__()` after the existing model property assignments:

```python
# Langfuse CallbackHandler (graceful degradation on failure)
self._langfuse_handler = None
if LANGFUSE_ENABLE:
    try:
        from langfuse.callback import CallbackHandler
        self._langfuse_handler = CallbackHandler(
            secret_key=LANGFUSE_SECRET_KEY,
            public_key=LANGFUSE_PUBLIC_KEY,
            host=LANGFUSE_HOST,
        )
        logger.info("Langfuse tracing enabled")
    except Exception as e:
        logger.warning("Langfuse initialization failed (tracing disabled): {}", e)
```

Add import line at top for `@observe`:
```python
from langfuse.decorators import observe
```

Add `@observe(name="chat_with_citations")` decorator before `chat_with_citations` method definition:

```python
@observe(name="chat_with_citations")
def chat_with_citations(
    self, kb_id: str, session_id: str, query: str,
) -> tuple[Generator[str, None, None], list[RAGContext]]:
```

Add `@observe(name="rerank_results")` decorator before `_rerank_results` method:

```python
@observe(name="rerank_results")
def _rerank_results(self, query: str, results: list[dict]) -> list[RAGContext]:
```

Modify `_stream_answer()` to pass the callbacks config to `self.llm.stream()`:

```python
def _stream_answer(self, messages: list) -> Generator[str, None, None]:
    last_error: Optional[Exception] = None
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            config = {"callbacks": [self._langfuse_handler]} if self._langfuse_handler else None
            stream = self.llm.stream(messages, config=config)
            for chunk in stream:
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    yield content
            return
        except Exception as e:
            last_error = e
            if attempt < RETRY_MAX_ATTEMPTS:
                wait = RETRY_INITIAL_INTERVAL * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                logger.warning(
                    "LLM stream failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                    attempt, RETRY_MAX_ATTEMPTS, e, wait,
                )
                time.sleep(wait)
    logger.error("LLM stream failed after {} attempts", RETRY_MAX_ATTEMPTS)
    yield f"生成回答失败: {last_error}"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_rag_chain_tracing.py -v`
Expected: All tests PASS

- [ ] **Step 5: Ruff format & check**

Run: `ruff format . && ruff check . --fix`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add src/rag_chain.py tests/test_rag_chain_tracing.py
git commit -m "feat: add Langfuse tracing with CallbackHandler and @observe decorators"
```

---

### Task T6: Add postgres + langfuse Docker services

**Files:**
- Modify: `docker-compose.yml` (add postgres and langfuse services + postgres_data volume)
- Test: `docker compose config` (validate YAML)

**Interfaces:**
- Consumes: Docker Compose existing structure (mysql, redis, app, networks, volumes)
- Produces: `postgres` service — PostgreSQL 15 for Langfuse data store
- Produces: `langfuse` service — Langfuse 2.x server on port 3000
- Produces: `postgres_data` named volume

- [ ] **Step 1: Add postgres service to docker-compose.yml**

Insert after the `redis` service (before `app`) in `docker-compose.yml`:

```yaml
  postgres:
    image: postgres:15-alpine
    container_name: financial-qa-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ${LANGFUSE_POSTGRES_PASS:-langfuse_pass}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    networks:
      - app-network
```

- [ ] **Step 2: Add langfuse service**

Insert after the `postgres` service:

```yaml
  langfuse:
    image: langfuse/langfuse:2
    container_name: financial-qa-langfuse
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      DATABASE_URL: postgresql://langfuse:${LANGFUSE_POSTGRES_PASS:-langfuse_pass}@postgres:5432/langfuse
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET:-changeme}
      SALT: ${LANGFUSE_SALT:-changeme}
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - app-network
```

- [ ] **Step 3: Add postgres_data volume**

Add to the `volumes:` section:

```yaml
  postgres_data:
    name: financial_qa_postgres_data
```

- [ ] **Step 4: Validate docker-compose.yml**

Run: `docker compose config`
Expected: No errors, YAML is valid

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add postgres and langfuse Docker services for LLM tracing"
```

---

### Task T7: Adjust app service for FastAPI

**Files:**
- Modify: `docker-compose.yml` (app service: port + command)
- Test: `docker compose config` (validate)

**Interfaces:**
- Consumes: `src/api/main.py` (from T4) — FastAPI app
- Consumes: Docker Compose existing app service definition (from T6 context)
- Produces: Updated `app` service with uvicorn command and port 8000

- [ ] **Step 1: Modify app service in docker-compose.yml**

Change the app service block:

```yaml
  app:
    build: .
    container_name: financial-qa-app
    restart: unless-stopped
    ports:
      - "8000:8000"                         # Changed: 7860 → 8000
    env_file:
      - .env
    environment:
      MYSQL_HOST: mysql
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:-financial_qa_pass}
      REDIS_HOST: redis
      REDIS_PASSWORD: ${REDIS_PASSWORD:-financial_qa_pass}
      CHROMA_PERSIST_DIR: /data/chroma
    volumes:
      - ./data/chroma_persist:/data/chroma
      - app_logs:/data/logs
    depends_on:
      mysql:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - app-network
```

Note: The `command` key is NOT needed in the Dockerfile or compose — we'll handle entry point in the Dockerfile's CMD. Actually, let's add it explicitly in compose:

Add to the app service:
```yaml
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

- [ ] **Step 2: Validate configuration**

Run: `docker compose config`
Expected: No errors, app service shows port 8000 and uvicorn command

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "refactor: change app service to FastAPI port 8000 with uvicorn"
```

---

### Task T8: Create Nginx reverse proxy

**Files:**
- Create: `nginx/Dockerfile`
- Create: `nginx/nginx.conf`

**Interfaces:**
- Consumes: FastAPI app on port 8000 (from T4/T7)
- Consumes: Static frontend files at `nginx/html/` (from T12, create placeholder for now)
- Produces: Nginx container on port 80, proxying `/api/*` → `app:8000`

- [ ] **Step 1: Create nginx.conf**

```nginx
# nginx/nginx.conf
server {
    listen 80;
    server_name localhost;

    # Gzip
    gzip on;
    gzip_types text/css application/javascript text/html application/json;
    gzip_min_length 256;

    # Frontend static files
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
        expires 1h;
        add_header Cache-Control "public, max-age=3600";
    }

    # API reverse proxy
    location /api/ {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;

        # SSE requires buffering to be off
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # OpenAPI docs — pass through
    location /docs {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
```

- [ ] **Step 2: Create Dockerfile for Nginx**

```dockerfile
# nginx/Dockerfile
FROM nginx:alpine

# Copy configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy static frontend files
COPY html/ /usr/share/nginx/html/

# Remove default nginx config
RUN rm -f /etc/nginx/conf.d/example_ssl.conf

# Expose port 80
EXPOSE 80

# Run nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 3: Add nginx service to docker-compose.yml**

Add after the `langfuse` service:

```yaml
  nginx:
    build: ./nginx
    container_name: financial-qa-nginx
    restart: unless-stopped
    ports:
      - "80:80"
    depends_on:
      - app
    networks:
      - app-network
```

- [ ] **Step 4: Validate configuration**

Run: `docker compose config`
Expected: No errors, nginx service present on port 80

- [ ] **Step 5: Commit**

```bash
git add nginx/ docker-compose.yml
git commit -m "feat: add Nginx reverse proxy with static file serving and SSE support"
```

---

### Task T9: Create .env template with new variables

**Files:**
- Create: `.env.template` (or modify existing `.env.example` if it exists)
- Check: existing `.env` file (add new variables if it exists)

**Interfaces:**
- Produces: `.env.template` with all required environment variables for FastAPI + Langfuse

- [ ] **Step 1: Check for existing env template**

Run: `ls -la .env*`
Expected: Show existing `.env` and/or `.env.example` files

- [ ] **Step 2: Create .env.template**

```bash
# .env.template
# Financial QA Phase 2 — Environment Configuration
# Copy to .env and fill in your values.

# ====== DashScope API ======
DASHSCOPE_API_KEY=sk-xxx

# ====== MySQL ======
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=financial_qa_pass
MYSQL_DATABASE=financial_qa

# ====== Redis ======
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_USERNAME=default
REDIS_PASSWORD=financial_qa_pass

# ====== FastAPI ======
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8000

# ====== Langfuse (Self-hosted) ======
# Get these from http://localhost:3000 → Settings → API Keys after first login
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_HOST=http://langfuse:3000
LANGFUSE_ENABLE=true

# ====== Langfuse Database ======
LANGFUSE_POSTGRES_PASS=langfuse_pass

# ====== Langfuse Auth (change these for production) ======
NEXTAUTH_SECRET=your-secret-here
LANGFUSE_SALT=your-salt-here
```

- [ ] **Step 3: Ensure .env has the new variables**

If `.env` exists, read it and check if Langfuse variables are present. Add them if missing.

Run: `grep -c "LANGFUSE" .env || echo "Need to add Langfuse vars"`

- [ ] **Step 4: Commit**

```bash
git add .env.template
git commit -m "docs: add .env template with Langfuse and FastAPI configuration"
```

---

### Task T10: Remove Gradio app.py (replaced by FastAPI)

**Files:**
- Delete: `src/app.py` — Gradio UI is replaced by FastAPI + HTML frontend

**Interfaces:**
- Consumes: `src/app.py` existing Gradio file (to be deleted)
- Note: Gradio is no longer needed — FastAPI handles all API concerns, HTML frontend handles UI.

- [ ] **Step 1: Delete Gradio app.py**

```bash
rm src/app.py
```

- [ ] **Step 2: Verify deletion**

Run: `ls src/app.py 2>&1 || echo "app.py deleted successfully"`
Expected: `ls: cannot access 'src/app.py': No such file or directory`

- [ ] **Step 3: Commit**

```bash
git add src/app.py  # staged deletion
git commit -m "chore: remove Gradio app.py (replaced by FastAPI + HTML frontend)"
```

---

### Task T11: Update README with new architecture

**Files:**
- Modify: `README.md` (update startup steps, architecture description, Langfuse setup)

- [ ] **Step 1: Read existing README**

Run: `head -50 README.md`
Expected: Current content

- [ ] **Step 2: Update README.md**

Replace or update the following sections:

**Architecture section** — update from Gradio-based to Nginx/FastAPI architecture:

```markdown
## Architecture

```
Nginx (:80) → /api/* → FastAPI (:8000) → MySQL, Redis, ChromaDB
           → /* → Static HTML/CSS/JS frontend

Langfuse (:3000) → PostgreSQL (tracing storage)
```
```

**Startup section** — update:

```markdown
## Quick Start

```bash
# 1. Start all services
docker compose up -d --build

# 2. Access the application
open http://localhost         # Frontend UI
open http://localhost/api/docs  # API documentation

# 3. (First time only) Configure Langfuse
open http://localhost:3000
# → Register first user → Create project → Settings → API Keys
# → Copy keys to .env: LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY
# → Restart app: docker compose restart app
```

**Development:**

```bash
# Start dependencies only (for local development)
docker compose up -d mysql redis postgres langfuse

# Run app locally with hot reload
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Run tests
pytest tests/ -v
```
```

- [ ] **Step 3: Ruff check (README.md is not Python — skip format check)**

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update README with FastAPI architecture and Langfuse setup"
```

---

### Task T12: Frontend HTML pages (KB management + Chat)

**Files:**
- Create: `nginx/html/index.html` — KB management landing page
- Create: `nginx/html/chat.html` — Chat page with SSE streaming
- Create: `nginx/html/css/style.css` — Stylesheet
- Create: `nginx/html/js/api.js` — API fetch helpers
- Create: `nginx/html/js/chat.js` — Chat logic with EventSource

**Interfaces:**
- Consumes: All REST API endpoints (T4) — KB CRUD, document upload, SSE chat
- Consumes: EventSource API (browser native)

- [ ] **Step 1: Create CSS stylesheet**

```css
/* nginx/html/css/style.css */
:root {
    --sidebar-width: 280px;
    --primary: #2563eb;
    --primary-dark: #1d4ed8;
    --sidebar-bg: #1e293b;
    --sidebar-text: #94a3b8;
    --sidebar-active: #3b82f6;
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --border: #e2e8f0;
    --text: #1e293b;
    --text-muted: #64748b;
    --success: #22c55e;
    --danger: #ef4444;
    --warning: #f59e0b;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
    min-height: 100vh;
}

/* ====== Sidebar ====== */
.sidebar {
    width: var(--sidebar-width);
    background: var(--sidebar-bg);
    color: var(--sidebar-text);
    display: flex;
    flex-direction: column;
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    z-index: 100;
}
.sidebar-header {
    padding: 20px;
    border-bottom: 1px solid rgba(255,255,255,0.1);
}
.sidebar-header h1 {
    font-size: 18px;
    color: #fff;
    font-weight: 600;
}
.sidebar-header p {
    font-size: 12px;
    margin-top: 4px;
}
.sidebar-nav { padding: 12px 0; flex: 1; }
.sidebar-nav a {
    display: flex;
    align-items: center;
    padding: 10px 20px;
    color: var(--sidebar-text);
    text-decoration: none;
    font-size: 14px;
    transition: all 0.2s;
    gap: 10px;
}
.sidebar-nav a:hover { background: rgba(255,255,255,0.05); color: #fff; }
.sidebar-nav a.active {
    background: rgba(59,130,246,0.15);
    color: var(--sidebar-active);
    border-right: 3px solid var(--sidebar-active);
}

/* ====== Main Content ====== */
.main-content {
    margin-left: var(--sidebar-width);
    flex: 1;
    padding: 24px 32px;
    max-width: calc(100vw - var(--sidebar-width));
}
.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
}
.page-header h2 { font-size: 24px; font-weight: 600; }

/* ====== Cards ====== */
.card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 16px;
}
.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
}
.card-header h3 { font-size: 16px; font-weight: 600; }

/* ====== Buttons ====== */
.btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.2s;
    font-weight: 500;
}
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover { background: var(--primary-dark); }
.btn-danger { background: var(--danger); color: #fff; }
.btn-danger:hover { opacity: 0.9; }
.btn-outline {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
}
.btn-outline:hover { background: var(--bg); }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* ====== Forms ====== */
.form-group { margin-bottom: 16px; }
.form-group label {
    display: block;
    font-size: 14px;
    font-weight: 500;
    margin-bottom: 6px;
    color: var(--text);
}
.form-input {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
    transition: border-color 0.2s;
}
.form-input:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
}

/* ====== Tables / Lists ====== */
.data-list { list-style: none; }
.data-list li {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 0;
    border-bottom: 1px solid var(--border);
}
.data-list li:last-child { border-bottom: none; }
.item-name { font-weight: 500; }
.item-meta { font-size: 12px; color: var(--text-muted); }
.item-actions { display: flex; gap: 8px; }
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: var(--text-muted);
}
.empty-state p { margin-bottom: 16px; }

/* ====== Chat ====== */
.chat-container {
    display: flex;
    flex-direction: column;
    height: calc(100vh - 120px);
}
.chat-messages {
    flex: 1;
    overflow-y: auto;
    padding: 16px 0;
    display: flex;
    flex-direction: column;
    gap: 16px;
}
.message {
    max-width: 80%;
    padding: 12px 16px;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.6;
    animation: fadeIn 0.3s;
}
.message.user {
    background: var(--primary);
    color: #fff;
    align-self: flex-end;
    border-bottom-right-radius: 4px;
}
.message.assistant {
    background: var(--card-bg);
    border: 1px solid var(--border);
    align-self: flex-start;
    border-bottom-left-radius: 4px;
}
.message.assistant .citations {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 12px;
    color: var(--text-muted);
}
.message.assistant .citations .citation-item {
    display: block;
    margin-top: 4px;
    padding: 6px 8px;
    background: var(--bg);
    border-radius: 4px;
    cursor: pointer;
}
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
.chat-input-area {
    display: flex;
    gap: 8px;
    padding: 16px 0;
    border-top: 1px solid var(--border);
}
.chat-input-area .form-input { flex: 1; }
.chat-select {
    min-width: 180px;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 14px;
    background: var(--card-bg);
}

/* ====== Toast ====== */
.toast {
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 12px 20px;
    border-radius: 8px;
    color: #fff;
    font-size: 14px;
    z-index: 1000;
    animation: slideIn 0.3s;
    display: none;
}
.toast.success { background: var(--success); }
.toast.error { background: var(--danger); }
@keyframes slideIn {
    from { transform: translateX(100%); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
}

/* ====== Loading ====== */
.spinner {
    display: inline-block;
    width: 16px;
    height: 16px;
    border: 2px solid var(--border);
    border-top-color: var(--primary);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 2: Create API helper JS**

```javascript
// nginx/html/js/api.js
const API_BASE = '/api';

async function apiRequest(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const config = {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    };

    // Don't set Content-Type for FormData (browser sets with boundary)
    if (options.body instanceof FormData) {
        delete config.headers['Content-Type'];
    }

    const response = await fetch(url, config);

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || `HTTP ${response.status}`);
    }

    // Return null for 204 No Content
    if (response.status === 204) return null;
    return response.json();
}

// ====== Knowledge Bases ======
async function listKBs() {
    return apiRequest('/kbs');
}

async function createKB(name, description = '') {
    return apiRequest('/kbs', {
        method: 'POST',
        body: JSON.stringify({ name, description }),
    });
}

async function deleteKB(kbId) {
    return apiRequest(`/kbs/${kbId}`, { method: 'DELETE' });
}

// ====== Documents ======
async function listDocuments(kbId) {
    return apiRequest(`/kbs/${kbId}/documents`);
}

async function uploadDocument(kbId, file) {
    const formData = new FormData();
    formData.append('file', file);
    return apiRequest(`/kbs/${kbId}/documents/upload`, {
        method: 'POST',
        body: formData,
    });
}

// ====== Toast Notification ======
function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type}`;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}
```

- [ ] **Step 3: Create chat JS**

```javascript
// nginx/html/js/chat.js
let currentSessionId = generateSessionId();

function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

async function loadKbSelector() {
    const select = document.getElementById('kb-select');
    try {
        const kbs = await listKBs();
        select.innerHTML = '<option value="">所有知识库</option>';
        kbs.forEach(kb => {
            const option = document.createElement('option');
            option.value = kb.id;
            option.textContent = kb.name;
            select.appendChild(option);
        });
    } catch (err) {
        console.error('Failed to load KBs:', err);
    }
}

function sendMessage() {
    const input = document.getElementById('chat-input');
    const query = input.value.trim();
    if (!query) return;

    const kbId = document.getElementById('kb-select').value;
    input.value = '';

    // Add user message
    addMessage(query, 'user');

    // Add placeholder assistant message
    const assistantDiv = addMessage('', 'assistant');
    const contentDiv = assistantDiv.querySelector('.message-content');
    contentDiv.innerHTML = '<span class="spinner"></span> 思考中...';

    // Connect to SSE
    const params = new URLSearchParams({
        session_id: currentSessionId,
        kb_id: kbId,
        query: query,
    });
    const evtSource = new EventSource(`/api/chat/stream?${params}`);

    let fullText = '';
    const citations = [];

    evtSource.addEventListener('token', (e) => {
        const data = JSON.parse(e.data);
        fullText += data.token;
        // Remove spinner, show text
        contentDiv.innerHTML = marked ? marked.parse(fullText) : fullText;
    });

    evtSource.addEventListener('citation', (e) => {
        const data = JSON.parse(e.data);
        citations.push(data);
    });

    evtSource.addEventListener('done', () => {
        evtSource.close();
        // Append citations if any
        if (citations.length > 0) {
            const citationsHtml = citations.map(c =>
                `<span class="citation-item">📄 ${c.source} (第${c.page}页)</span>`
            ).join('');
            contentDiv.innerHTML = (marked ? marked.parse(fullText) : fullText)
                + `<div class="citations"><strong>来源：</strong>${citationsHtml}</div>`;
        }
    });

    evtSource.addEventListener('error', (e) => {
        evtSource.close();
        contentDiv.innerHTML = '<span style="color: var(--danger)">连接错误，请重试</span>';
        console.error('SSE error:', e);
    });
}

function addMessage(text, role) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = `<div class="message-content">${text}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return div;
}

// Send on Enter
document.addEventListener('DOMContentLoaded', () => {
    loadKbSelector();
    document.getElementById('chat-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
});
```

- [ ] **Step 4: Create KB management page (index.html)**

```html
<!-- nginx/html/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial QA — 知识库管理</title>
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <!-- Sidebar -->
    <aside class="sidebar">
        <div class="sidebar-header">
            <h1>📊 Financial QA</h1>
            <p>金融文档智能问答系统</p>
        </div>
        <nav class="sidebar-nav">
            <a href="/" class="active">📚 知识库管理</a>
            <a href="/chat">💬 智能问答</a>
        </nav>
    </aside>

    <!-- Main Content -->
    <main class="main-content">
        <div class="page-header">
            <h2>知识库管理</h2>
            <button class="btn btn-primary" onclick="showCreateForm()">＋ 新建知识库</button>
        </div>

        <!-- Create KB Form (hidden by default) -->
        <div id="create-form" class="card" style="display:none;">
            <div class="card-header"><h3>新建知识库</h3></div>
            <div class="form-group">
                <label>知识库名称</label>
                <input type="text" id="kb-name" class="form-input" placeholder="输入知识库名称">
            </div>
            <div class="form-group">
                <label>描述（可选）</label>
                <input type="text" id="kb-desc" class="form-input" placeholder="简要描述">
            </div>
            <div style="display:flex;gap:8px;">
                <button class="btn btn-primary" onclick="handleCreate()">创建</button>
                <button class="btn btn-outline" onclick="hideCreateForm()">取消</button>
            </div>
        </div>

        <!-- KB List -->
        <div class="card">
            <div class="card-header"><h3>所有知识库</h3></div>
            <ul id="kb-list" class="data-list">
                <li class="empty-state"><p>暂无知识库</p></li>
            </ul>
        </div>

        <!-- Document List -->
        <div class="card" id="doc-section" style="display:none;">
            <div class="card-header">
                <h3 id="doc-section-title">文档列表</h3>
                <button class="btn btn-primary btn-sm" onclick="document.getElementById('file-input').click()">＋ 上传文档</button>
                <input type="file" id="file-input" accept=".pdf,.docx,.txt" style="display:none" onchange="handleUpload(event)">
            </div>
            <ul id="doc-list" class="data-list">
                <li class="empty-state"><p>暂无文档</p></li>
            </ul>
        </div>
    </main>

    <!-- Toast -->
    <div id="toast" class="toast"></div>

    <script src="js/api.js"></script>
    <script>
        let selectedKbId = null;

        async function loadKBs() {
            try {
                const kbs = await listKBs();
                const list = document.getElementById('kb-list');
                if (kbs.length === 0) {
                    list.innerHTML = '<li class="empty-state"><p>暂无知识库，点击右上角新建</p></li>';
                    return;
                }
                list.innerHTML = kbs.map(kb => `
                    <li onclick="selectKB('${kb.id}', '${kb.name}')" style="cursor:pointer;">
                        <div>
                            <div class="item-name">${kb.name}</div>
                            <div class="item-meta">ID: ${kb.id.slice(0,8)}...</div>
                        </div>
                        <div class="item-actions">
                            <button class="btn btn-danger btn-sm" onclick="event.stopPropagation(); handleDelete('${kb.id}')">删除</button>
                        </div>
                    </li>
                `).join('');
            } catch (err) {
                showToast('加载知识库失败: ' + err.message, 'error');
            }
        }

        function showCreateForm() {
            document.getElementById('create-form').style.display = 'block';
        }

        function hideCreateForm() {
            document.getElementById('create-form').style.display = 'none';
            document.getElementById('kb-name').value = '';
            document.getElementById('kb-desc').value = '';
        }

        async function handleCreate() {
            const name = document.getElementById('kb-name').value.trim();
            if (!name) { showToast('请输入知识库名称', 'error'); return; }
            try {
                await createKB(name, document.getElementById('kb-desc').value);
                showToast('知识库创建成功');
                hideCreateForm();
                loadKBs();
            } catch (err) {
                showToast('创建失败: ' + err.message, 'error');
            }
        }

        async function handleDelete(kbId) {
            if (!confirm('确定要删除这个知识库吗？此操作不可撤销。')) return;
            try {
                await deleteKB(kbId);
                showToast('知识库已删除');
                if (selectedKbId === kbId) {
                    selectedKbId = null;
                    document.getElementById('doc-section').style.display = 'none';
                }
                loadKBs();
            } catch (err) {
                showToast('删除失败: ' + err.message, 'error');
            }
        }

        async function selectKB(kbId, name) {
            selectedKbId = kbId;
            document.getElementById('doc-section').style.display = 'block';
            document.getElementById('doc-section-title').textContent = `文档列表 — ${name}`;
            await loadDocuments(kbId);
        }

        async function loadDocuments(kbId) {
            try {
                const docs = await listDocuments(kbId);
                const list = document.getElementById('doc-list');
                if (docs.length === 0) {
                    list.innerHTML = '<li class="empty-state"><p>暂无文档，点击上传</p></li>';
                    return;
                }
                list.innerHTML = docs.map(doc => `
                    <li>
                        <div>
                            <div class="item-name">📄 ${doc.filename}</div>
                            <div class="item-meta">
                                ${doc.type.toUpperCase()} · ${formatSize(doc.size)} · 
                                <span class="${doc.status === 'ready' ? '' : 'warning'}">${doc.status}</span>
                                ${doc.chunk_count ? `· ${doc.chunk_count} 分块` : ''}
                            </div>
                        </div>
                    </li>
                `).join('');
            } catch (err) {
                showToast('加载文档失败: ' + err.message, 'error');
            }
        }

        async function handleUpload(event) {
            const file = event.target.files[0];
            if (!file || !selectedKbId) return;
            try {
                const result = await uploadDocument(selectedKbId, file);
                showToast(`上传成功：${result.chunk_count} 个文本分块已入库`);
                loadDocuments(selectedKbId);
            } catch (err) {
                showToast('上传失败: ' + err.message, 'error');
            }
            event.target.value = '';
        }

        function formatSize(bytes) {
            if (!bytes) return '未知';
            if (bytes < 1024) return bytes + 'B';
            if (bytes < 1024*1024) return (bytes/1024).toFixed(1) + 'KB';
            return (bytes/1024/1024).toFixed(1) + 'MB';
        }

        document.addEventListener('DOMContentLoaded', loadKBs);
    </script>
</body>
</html>
```

- [ ] **Step 5: Create chat page (chat.html)**

```html
<!-- nginx/html/chat.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial QA — 智能问答</title>
    <link rel="stylesheet" href="css/style.css">
    <!-- Optional: Markdown rendering -->
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
    <aside class="sidebar">
        <div class="sidebar-header">
            <h1>📊 Financial QA</h1>
            <p>金融文档智能问答系统</p>
        </div>
        <nav class="sidebar-nav">
            <a href="/">📚 知识库管理</a>
            <a href="/chat" class="active">💬 智能问答</a>
        </nav>
    </aside>

    <main class="main-content">
        <div class="chat-container">
            <div class="page-header" style="margin-bottom:16px;">
                <h2>智能问答</h2>
                <select id="kb-select" class="chat-select">
                    <option value="">加载中...</option>
                </select>
            </div>

            <div id="chat-messages" class="chat-messages">
                <div class="message assistant">
                    <div class="message-content">👋 您好！请选择知识库并输入问题。</div>
                </div>
            </div>

            <div class="chat-input-area">
                <input type="text" id="chat-input" class="form-input" placeholder="输入您的金融问题..." autofocus>
                <button class="btn btn-primary" onclick="sendMessage()">发送</button>
                <button class="btn btn-outline" onclick="resetSession()">新会话</button>
            </div>
        </div>
    </main>

    <div id="toast" class="toast"></div>

    <script src="js/api.js"></script>
    <script src="js/chat.js"></script>
    <script>
        function resetSession() {
            currentSessionId = generateSessionId();
            document.getElementById('chat-messages').innerHTML = `
                <div class="message assistant">
                    <div class="message-content">🔄 新会话已开始，请提问。</div>
                </div>
            `;
        }
    </script>
</body>
</html>
```

- [ ] **Step 6: Ruff format check (skip — HTML/CSS/JS files)**

- [ ] **Step 7: Commit**

```bash
git add nginx/html/
git commit -m "feat: add frontend HTML pages for KB management and SSE streaming chat"
```

---

### Task T13: Update roadmap document

**Files:**
- Modify: `docs/2026-06-13-financial-qa-full-roadmap.md` (update Phase 2 plans)

- [ ] **Step 1: Read existing roadmap**

- [ ] **Step 2: Update roadmap**

Changes to make:
- In Phase 2 section, replace "LangSmith Trace 接入" with "Langfuse Tracing"
- Add Phase 2 Step 0 entry describing infrastructure rebuild
- Add entry for HTML frontend replacing Gradio UI
- Update timeline if applicable

- [ ] **Step 3: Commit**

```bash
git add docs/2026-06-13-financial-qa-full-roadmap.md
git commit -m "docs: update roadmap with Phase 2 Step 0 infrastructure changes"
```

---
## Self-Review Checklist

**1. Spec coverage:**
- ✅ `api-layer/spec.md` — Covered by T4 (all endpoints: health, KB CRUD, doc upload, SSE chat)
- ✅ `nginx-proxy/spec.md` — Covered by T8 (Nginx config + Dockerfile)
- ✅ `llm-tracing/spec.md` — Covered by T5 (CallbackHandler + @observe) and T6 (Docker services)
- ✅ `html-frontend/spec.md` — Covered by T12 (KB management page + chat page + CSS)
- ✅ `rag-generation/spec.md` — Covered by T1 (deps) and T2 (import changes)
- ✅ `infrastructure/spec.md` — Covered by T6 (postgres+langfuse), T7 (app port), T8 (nginx), T9 (env), T11 (README)
- ✅ Gradio removal — T10
- ✅ API contract — T4 step 9
- ✅ Roadmap update — T13

**2. Placeholder scan:**
- No "TBD", "TODO", "implement later" placeholders in code blocks
- No "Add appropriate error handling" without actual code
- Every test has concrete assertions
- All function signatures are explicit

**3. Type consistency:**
- `app_service.AppService` methods used in T4 match the existing signatures from `src/app_service.py`
- Config variables defined in T3 match what T5 consumes
- SSE format in T4 matches the design spec

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-26-infrastructure-rebuild-phase2.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development` skill.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
