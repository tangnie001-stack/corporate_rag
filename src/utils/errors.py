"""应用异常层次体系。

异常类型按错误性质分类（BusinessError / AuthError / SystemError），
不按业务模块细分。模块归属信息由 Code 枚举前缀（DOC_* / FILE_* / KB_*）承载。

层次结构：
  AppError (基类)
  ├── BusinessError  — 业务规则冲突 (400)
  ├── AuthError      — 认证授权 (401/403)
  ├── ValidationError — 参数校验 (422)
  ├── SystemError    — 基础设施故障 (503)
  └── AppError       — 未知异常兜底 (500)
"""


class AppError(Exception):
    """应用异常基类。

    Attributes:
        code: 业务错误码（如 AUTH_REQUIRED、DOC_NOT_FOUND）
        message: 人类可读的错误描述
        status: HTTP 状态码
    """

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


class BusinessError(AppError):
    """业务规则冲突 — 如用户名已存在、文档状态不允许删除。"""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(code, message, status)


class AuthError(AppError):
    """认证授权失败 — 如 token 过期、密码错误。"""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(code, message, status)


class ValidationError(AppError):
    """参数校验失败 — 由 Pydantic 校验触发。"""

    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(code, message, status)


class SystemError(AppError):
    """基础设施故障 — 如数据库连接失败、第三方 API 超时。"""

    def __init__(self, code: str, message: str, status: int = 503) -> None:
        super().__init__(code, message, status)
