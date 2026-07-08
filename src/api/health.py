"""健康检查端点。"""

from fastapi import APIRouter

from src.api.model.response import HealthResponse

router = APIRouter()


@router.get("/health")
async def health_check() -> HealthResponse:
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        HealthResponse: {"status": "ok"}
    """
    return HealthResponse(status="ok")
