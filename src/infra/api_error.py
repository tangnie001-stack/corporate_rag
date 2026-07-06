"""API 业务异常 — 由中间件捕获后统一包装为响应。"""


class ApiError(Exception):
    """业务异常，携带业务码、人类可读消息和 HTTP 状态码。

    Args:
        code: 业务错误码（如 AUTH_REQUIRED、NOT_FOUND）
        message: 人类可读的错误描述
        status: HTTP 状态码，默认 400
    """

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
