"""统一响应处理中间件 — 返回值包装 + 数据追踪日志。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式，
并对非 GET 请求记录响应体日志（统一前缀 [API]）。
健康检查和 SSE 流式响应跳过全部处理。

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
from src.core.logging import API_SKIP_FULL_LOG, LOG_MAX_BODY

# 跳过包装的路径白名单
_SKIP_PATHS = {"/api/health", "/api/chat/stream"}


async def response_processor_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """统一响应处理中间件。

    成功响应 → 读 body 包进 {"code":"SUCCESS", "data": ...} + 非 GET 日志。
    @app.exception_handler 或 Auth 已返回统一格式的 4xx → 直接透传。
    """
    path = request.url.path
    if path in _SKIP_PATHS:
        return await call_next(request)

    try:
        response: Response = await call_next(request)

        # @app.exception_handler 或 Auth 已返回统一格式 → 直接透传
        if response.status_code >= 400:
            return response

        # 读取并包装响应体
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        data = json.loads(body) if body else None

        # 数据追踪日志 — 仅非 GET 请求
        if request.method != "GET":
            if path in API_SKIP_FULL_LOG:
                # 跳过全量响应体，只记录基本路径和状态码
                logger.info(
                    "[API] {} {} | status={} | data=<skipped>",
                    request.method,
                    path,
                    response.status_code,
                )
            else:
                try:
                    data_str = str(data)
                    if len(data_str) > LOG_MAX_BODY:
                        data_str = (
                            data_str[:LOG_MAX_BODY]
                            + f"... (truncated, total={len(data_str)} chars)"
                        )
                    logger.info(
                        "[API] {} {} | status={} | data={}",
                        request.method,
                        path,
                        response.status_code,
                        data_str,
                    )
                except Exception:
                    logger.info(
                        "[API] {} {} | status={} | data=<serialization_error>",
                        request.method,
                        path,
                        response.status_code,
                    )

        return JSONResponse(
            {"code": Code.SUCCESS, "message": Code.SUCCESS_MSG, "data": data},
            status_code=response.status_code,
        )

    except Exception as e:
        # 自身异常的兜底（如读 body 时解析失败）
        logger.exception("Middleware dispatch 自身异常: %s", e)
        return JSONResponse(
            {
                "code": Code.INTERNAL_ERROR,
                "message": Code.INTERNAL_ERROR_MSG,
                "data": None,
            },
            status_code=500,
        )
