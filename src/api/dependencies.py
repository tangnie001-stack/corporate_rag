"""FastAPI 依赖注入 — 转发到 services 层的共享依赖。"""

from src.services.app_service import AppService, get_app_service

__all__ = ["AppService", "get_app_service"]
