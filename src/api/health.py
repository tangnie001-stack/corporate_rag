"""健康检查与前端配置端点。"""

from __future__ import annotations

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


def _get_service() -> _ConfigService:
    """获取配置服务实例。

    Returns:
        _ConfigService: 配置服务实例
    """
    return _ConfigService()


class _ConfigService:
    """内部配置服务，封装系统配置读取。"""

    async def get_max_upload_size(self) -> int:
        """获取上传大小限制。

        Returns:
            int: 单文件上传上限（字节）
        """
        return MAX_FILE_SIZE


@router.post("/config")
async def app_config() -> AppConfigResponse:
    """前端配置 — 返回前端需要的系统参数。

    Returns:
        AppConfigResponse: 含 max_upload_size 等前端配置
    """
    svc = _get_service()
    max_size = await svc.get_max_upload_size()
    return AppConfigResponse(max_upload_size=max_size)
