"""健康检查端点。"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str  # 服务状态，固定 "ok"


@router.get("/health")
async def health_check() -> HealthResponse:
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        HealthResponse: {"status": "ok"}
    """
    return HealthResponse(status="ok")
