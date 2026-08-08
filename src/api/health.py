"""健康检查与前端配置端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_app_service
from src.api.model.response import AppConfigResponse, HealthResponse
from src.api.schema import ResponseModel
from src.services.app_service import AppService

router = APIRouter()


@router.get("/health", response_model=ResponseModel)
async def health_check():
    """基本健康检查 — 服务运行中返回 status ok。

    Returns:
        ResponseModel: data 含 status
    """
    return ResponseModel(data=HealthResponse(status="ok"))


@router.post("/config", response_model=ResponseModel)
async def app_config(svc: AppService = Depends(get_app_service)):
    """前端配置 — 返回前端需要的系统参数。

    Returns:
        ResponseModel: data 含 max_upload_size 等前端配置
    """
    return ResponseModel(
        data=AppConfigResponse(max_upload_size=svc.settings.MAX_FILE_SIZE)
    )
