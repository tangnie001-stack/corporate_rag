"""FastAPI 依赖注入 — 集中管理 API 层的共享依赖。"""

from src.services.app_service import AppService

_service: AppService | None = None


async def get_app_service() -> AppService:
    """FastAPI 依赖：提供 AppService 单例。

    延迟初始化：首次调用时创建实例，后续复用。
    避免模块导入阶段产生网络或数据库连接。
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service
