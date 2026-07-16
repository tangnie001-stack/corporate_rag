"""健康检查与前端配置端点。"""

from fastapi import APIRouter

from src.api.model.response import HealthResponse, AppConfigResponse
from src.config import MAX_FILE_SIZE

router = APIRouter()


@router.get("/health")
async def health_check() -> HealthResponse:
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        HealthResponse: {"status": "ok"}
    """
    return HealthResponse(status="ok")


@router.post("/config")
async def app_config() -> AppConfigResponse:
    """前端配置 — 返回前端需要的系统参数。

    Returns:
        AppConfigResponse: 含 max_upload_size 等前端配置
    """
    return AppConfigResponse(max_upload_size=MAX_FILE_SIZE)
