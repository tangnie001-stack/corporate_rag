"""统一响应包装中间件。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式。
健康检查和 SSE 流式响应跳过包装。
"""

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config.response_codes import Code
from src.infra.api_error import ApiError

logger = logging.getLogger(__name__)


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """统一响应包装中间件。

    成功响应 → {"code": "SUCCESS", "message": "操作成功", "data": ...}
    ApiError 异常 → {"code": "...", "message": "...", "data": null}
    未预期异常 → {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "data": null}
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path == "/api/health" or path == "/api/chat/stream":
            return await call_next(request)

        try:
            response = await call_next(request)

            # 非 ApiError 的 4xx/5xx — 尝试从 body 中提取原始错误码
            if response.status_code >= 400:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                try:
                    err_data = json.loads(body) if body else {}
                    err_code = err_data.get("code", Code.UNKNOWN_ERROR)
                    err_msg = err_data.get("message") or err_data.get("detail") or response.reason_phrase or "请求失败"
                except (json.JSONDecodeError, AttributeError, TypeError):
                    err_code = Code.UNKNOWN_ERROR
                    err_msg = response.reason_phrase or "请求失败"
                return JSONResponse(
                    {"code": err_code, "message": err_msg, "data": None},
                    status_code=response.status_code,
                )

            # 包装成功响应 — 读取流式 body 后放入 data
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            data = json.loads(body) if body else None
            return JSONResponse(
                {"code": Code.SUCCESS, "message": Code.SUCCESS_MSG, "data": data},
                status_code=response.status_code,
            )

        except ApiError as e:
            return JSONResponse(
                {"code": e.code, "message": e.message, "data": None},
                status_code=e.status,
            )

        except Exception as e:
            logger.exception("Middleware error: %s", e)
            return JSONResponse(
                {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
                status_code=500,
            )
