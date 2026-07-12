# FastAPI REST API 包 — 提供 RAG 系统的 HTTP 端点

from src.api.health import router as health_router
from src.api.knowledge_base import router as kb_router
from src.api.documents import router as doc_router
from src.api.chat import router as chat_router
from src.api.sessions import router as sessions_router
from src.api.kb_eval import router as kb_eval_router

__all__ = [
    "health_router", "kb_router", "doc_router", "chat_router",
    "sessions_router", "kb_eval_router",
]
