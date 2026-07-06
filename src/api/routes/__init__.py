from src.api.routes.health import router as health_router
from src.api.routes.knowledge_base import router as kb_router
from src.api.routes.documents import router as doc_router
from src.api.routes.chat import router as chat_router
from src.api.routes.sessions import router as sessions_router

__all__ = ["health_router", "kb_router", "doc_router", "chat_router", "sessions_router"]
