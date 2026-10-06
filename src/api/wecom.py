"""企微智能机器人回调路由（GET 验证 URL / POST 收消息）。

极薄：只判 enabled 并把原始 query/body 透传给驱动；不声明类型化参数、
不使用 Pydantic —— 避免框架自动校验把 400/403 改写成 422/500 统一信封。
"""

from fastapi import APIRouter, Request
from starlette.responses import Response

from src.config import settings
from src.config.const import WECOM_CALLBACK_PATH
from src.services import wecom_service

router = APIRouter()


@router.get(WECOM_CALLBACK_PATH)
async def wecom_verify(request: Request) -> Response:
    """URL 有效性验证：验签 + 解密 echostr，返回明文。"""
    if not settings.WECOM_BOT_ENABLED:
        return Response(status_code=404)
    return wecom_service.get_driver().verify(request.url.query)


@router.post(WECOM_CALLBACK_PATH)
async def wecom_receive(request: Request) -> Response:
    """接收消息/事件回调。"""
    if not settings.WECOM_BOT_ENABLED:
        return Response(status_code=404)
    return await wecom_service.get_driver().handle_message(
        request.url.query, await request.body()
    )
