"""健康检查与前端配置端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_app_service
from src.api.model.response import HealthResponse, AppConfigResponse
from src.services.app_service import AppService

router = APIRouter()


@router.get("/health")
async def health_check() -> HealthResponse:
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        HealthResponse: {"status": "ok"}
    """
    return HealthResponse(status="ok")


@router.post("/config")
async def app_config(svc: AppService = Depends(get_app_service)) -> AppConfigResponse:
    """前端配置 — 返回前端需要的系统参数。

    Returns:
        AppConfigResponse: 含 max_upload_size 等前端配置
    """
    return AppConfigResponse(max_upload_size=svc.settings.MAX_FILE_SIZE)
