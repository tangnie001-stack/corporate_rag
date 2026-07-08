"""统一响应包装中间件。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式。
健康检查和 SSE 流式响应跳过包装。

Router 层异常已由 @app.exception_handler 统一处理（返回统一格式的 JSONResponse），
Auth 中间件异常通过 return JSONResponse 直接返回（不 raise 以避免 BaseHTTPMiddleware 二次 raise）。
status_code >= 400 的响应直接透传。
"""

import json
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from src.config.response_codes import Code

# 跳过包装的路径白名单
_SKIP_PATHS = {"/api/health", "/api/chat/stream"}


async def response_envelope_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """统一响应包装中间件。

    成功响应 → 读 body 包进 {"code":"SUCCESS", "data": ...}。
    @app.exception_handler 或 Auth 已返回统一格式的 4xx → 直接透传。
    """
    path = request.url.path
    if path in _SKIP_PATHS:
        return await call_next(request)

    try:
        response = await call_next(request)

        # @app.exception_handler 或 Auth 已返回统一格式 → 直接透传
        if response.status_code >= 400:
            return response

        # 包装成功响应 — 读取流式 body 后放入 data
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        data = json.loads(body) if body else None
        return JSONResponse(
            {"code": Code.SUCCESS, "message": Code.SUCCESS_MSG, "data": data},
            status_code=response.status_code,
        )

    except Exception as e:
        # 自身异常的兜底（如读 body 时解析失败）
        logger.exception("Middleware dispatch 自身异常: %s", e)
        return JSONResponse(
            {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
            status_code=500,
        )
