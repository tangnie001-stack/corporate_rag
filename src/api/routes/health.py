"""健康检查端点。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check():
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        dict: {"status": "ok"}
    """
    return {"status": "ok"}
