"""统一响应包装中间件。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式。
健康检查和 SSE 流式响应跳过包装。

职责分工：
- 成功响应：读 body → {"code":"SUCCESS", "data": ...}
- Auth 中间件的 AppError：捕获并返回统一格式
- 自身异常兜底：dispatch 自身故障时的 fallback

@see @app.exception_handler in main.py — 处理 Router 层所有异常，dispatch 见到 status_code >= 400 直接透传。
"""

import json

from loguru import logger

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config.response_codes import Code
from src.infra.errors import AppError


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """统一响应包装中间件。

    职责：
    ① 成功响应 → 读 body 包进 data 字段
    ② Auth 中间件的 AppError → 捕获并返回统一格式
    ③ 自身异常兜底（读 body 时出错等）

    @app.exception_handler 已处理 Router 层异常，dispatch 见到 status_code >= 400 直接透传。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path == "/api/health" or path == "/api/chat/stream":
            return await call_next(request)

        try:
            response = await call_next(request)

            # @app.exception_handler 已返回统一格式 → 直接透传
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

        except AppError as e:
            # Auth 中间件的异常（Auth 在 ExceptionMiddleware 外面，@app.exception_handler 接不到）
            logger.warning("Auth 异常: {} {}", e.code, e.message)
            return JSONResponse(
                {"code": e.code, "message": e.message, "data": None},
                status_code=e.status,
            )

        except Exception as e:
            # dispatch 自身异常的兜底（如读 body 时解析失败）
            logger.exception("Middleware dispatch 自身异常: %s", e)
            return JSONResponse(
                {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
                status_code=500,
            )
