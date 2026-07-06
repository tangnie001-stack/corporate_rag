# Unified API Response Format

> Design doc for standardizing all API responses with a consistent code + message + data envelope.

## Problem

Current API responses are inconsistent:
- Success: raw data directly (no wrapper, varies per endpoint)
- Error: FastAPI default `{"detail": "..."}` format (varies by exception handler)
- Frontend must parse HTTP status codes and `detail` text to determine error type
- No machine-readable error codes, making frontend error handling fragile

## Design

### Envelope

All API responses use a three-field envelope:

```json
{
    "code": "SUCCESS",
    "message": "操作成功",
    "data": [...]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Machine-readable business code. `"SUCCESS"` for success, error-specific string for failures. |
| `message` | string | Human-readable description. Frontend can display directly. |
| `data` | any | Response payload on success; `null` on error. |

### Success Response

```json
{
    "code": "SUCCESS",
    "message": "操作成功",
    "data": [{"id": "kb-1", "name": "年报"}]
}
```

List endpoints return `data` as array, single-resource endpoints return `data` as object. Both work under the same envelope.

### Error Response

```json
{
    "code": "AUTH_TOKEN_EXPIRED",
    "message": "Token 已过期，请重新登录",
    "data": null
}
```

### Health Check

`GET /api/health` keeps its current format `{"status": "ok"}` — used by external infrastructure (Docker, Nginx, load balancers), not by the frontend.

## Implementation

All response wrapping is handled by a single middleware + custom exception. Routes return raw data, never worry about the envelope.

### File Structure

```
src/middleware/
├── __init__.py         # package
├── auth.py             # auth middleware (moved from src/api/middleware.py)
└── response_envelope.py # unified response wrapper
src/infra/
└── api_error.py        # ApiError exception (new)
src/config/
└── response_codes.py   # code + message constants (new)
```

### 1. Error Exception

Create `src/infra/api_error.py`:

```python
class ApiError(Exception):
    """业务异常，由中间件捕获后统一包装为响应。"""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
```

### 2. Response Code Constants

File: `src/config/response_codes.py`

```python
class Code:
    # 通用
    SUCCESS = "SUCCESS"
    SUCCESS_MSG = "操作成功"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INTERNAL_ERROR_MSG = "服务器内部错误"
    NOT_FOUND = "NOT_FOUND"
    NOT_FOUND_MSG = "资源不存在"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    METHOD_NOT_ALLOWED_MSG = "请求方法不允许"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VALIDATION_ERROR_MSG = "参数校验失败"

    # 认证
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_REQUIRED_MSG = "请先登录"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_EXPIRED_MSG = "Token 已过期，请重新登录"
    AUTH_WRONG_PASSWORD = "AUTH_WRONG_PASSWORD"
    AUTH_WRONG_PASSWORD_MSG = "密码错误"
    AUTH_ACCOUNT_EXISTS = "AUTH_ACCOUNT_EXISTS"
    AUTH_ACCOUNT_EXISTS_MSG = "账号已存在"

    # 知识库
    KB_NOT_FOUND = "KB_NOT_FOUND"
    KB_NOT_FOUND_MSG = "知识库不存在"
    KB_DELETE_FAILED = "KB_DELETE_FAILED"
    KB_DELETE_FAILED_MSG = "知识库删除失败"

    # 文件
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TOO_LARGE_MSG = "文件超过 10MB 上限"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED"
    FILE_TYPE_UNSUPPORTED_MSG = "不支持的文件类型"
    FILE_UPLOAD_FAILED = "FILE_UPLOAD_FAILED"
    FILE_UPLOAD_FAILED_MSG = "文件上传到存储服务失败"
    FILE_DUPLICATE = "FILE_DUPLICATE"
    FILE_DUPLICATE_MSG = "文件已存在"

    # 文档
    DOC_PROCESSING_FAILED = "DOC_PROCESSING_FAILED"
    DOC_PROCESSING_FAILED_MSG = "文档处理失败"

    # 会话
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_NOT_FOUND_MSG = "会话不存在"
```

### 3. Unified Middleware

Create `src/middleware/response_envelope.py`:

```python
import json
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from starlette.requests import Request
from src.config.response_codes import Code
from src.infra.api_error import ApiError


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """统一响应包装中间件。

    成功响应 → {"code": "SUCCESS", "message": "操作成功", "data": ...}
    ApiError 异常 → {"code": "...", "message": "...", "data": null}
    未预期异常 → {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "data": null}
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 健康检查不包装
        if request.url.path == "/api/health":
            return await call_next(request)

        try:
            response = await call_next(request)

            # 已经通过中间件抛出的 ApiError 会在 except 中处理
            if response.status_code >= 400:
                # 非 ApiError 的 4xx/5xx（如 Nginx 返回的）
                return JSONResponse(
                    {"code": Code.UNKNOWN_ERROR, "message": response.reason_phrase or "请求失败", "data": None},
                    status_code=response.status_code,
                )

            # 包装成功响应
            body = response.body
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
            return JSONResponse(
                {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
                status_code=500,
            )
```

### 4. Register in main.py

```python
from src.middleware.response_envelope import ResponseEnvelopeMiddleware
from src.middleware.auth import auth_middleware

app.add_middleware(ResponseEnvelopeMiddleware)
app.middleware("http")(auth_middleware)
```

Remove any existing `@app.exception_handler(Exception)` — the middleware handles all cases.

### 5. Move Auth Middleware to src/middleware/

Move `src/api/middleware.py` → `src/middleware/auth.py`. Update all imports.

Auth middleware no longer returns `JSONResponse(...)` directly. Instead it raises `ApiError`:

```python
# src/api/middleware.py
from src.infra.api_error import ApiError
from src.config.response_codes import Code

async def auth_middleware(request, call_next):
    path = request.url.path
    if path.startswith("/api/auth/") or path == "/api/health":
        return await call_next(request)

    if path.startswith("/api/kbs"):
        token = request.cookies.get("token")
        if not token:
            raise ApiError(Code.AUTH_REQUIRED, Code.AUTH_REQUIRED_MSG, 401)
        uid = await UserAuth.get_user_id_from_token_async(...)
        if not uid:
            raise ApiError(Code.AUTH_TOKEN_EXPIRED, Code.AUTH_TOKEN_EXPIRED_MSG, 401)
        request.state.user_id = uid
        return await call_next(request)
```

### 6. Update Route Error Handling

Routes that currently raise HTTPException(...) should switch to raising `ApiError(...)`:

```python
# Before:
raise HTTPException(status_code=404, detail="知识库不存在")

# After:
raise ApiError(Code.KB_NOT_FOUND, Code.KB_NOT_FOUND_MSG, 404)
```

### 7. Frontend: Unauthorized Handling

The frontend `apiRequest()` helper in `js/api.js` checks `data.code === "AUTH_REQUIRED"` or `"AUTH_TOKEN_EXPIRED"` and redirects to login without showing an error toast.

```javascript
async function apiRequest(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const config = {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    };
    if (options.body instanceof FormData) {
        if (config.headers) delete config.headers['Content-Type'];
    }
    const response = await fetch(url, config);
    const body = await response.json().catch(() => null);
    if (!body) throw new Error('请求失败');
    if (body.code === "AUTH_REQUIRED" || body.code === "AUTH_TOKEN_EXPIRED") {
        location.href = '/login.html';
        return;
    }
    if (body.code !== "SUCCESS") {
        throw new Error(body.message || '请求失败');
    }
    return body.data;
}
```

Also update `checkAuth()` in `index.html` to check `data.code`:

```javascript
(async function checkAuth() {
    try {
        const r = await fetch('/api/auth/verify');
        const d = await r.json();
        if (d.code !== "SUCCESS" || !d.data?.valid) {
            updateUserArea(null);
        } else {
            updateUserArea(d.data.user_id);
        }
    } catch (e) {
        updateUserArea(null);
    }
})();
```

### 8. API 契约文档更新

`docs/api_contract.md` 中所有接口返回格式统一改为新的信封格式。主要改动：

**成功响应示例：**
```diff
- [{"id":"uuid","name":"库名称","doc_count":0}]
+ {"code":"SUCCESS","message":"操作成功","data":[{"id":"uuid","name":"库名称","doc_count":0}]}
```

**错误响应示例：**
```diff
- {"detail":"知识库不存在"}
+ {"code":"KB_NOT_FOUND","message":"知识库不存在","data":null}
```

### 9. 完整调用方覆盖

| 文件 | 函数 | 改动 |
|------|------|------|
| `js/api.js` | `apiRequest()` | 核心改动：返回 `body.data`，按 `body.code` 判断错误 |
| `index.html` | `checkAuth()` | 改为解析 `data.code` 而非 HTTP 状态码 |
| `index.html` | 其他 API 使用 | 全部经过 `apiRequest()`，不需要额外改动 |
| `chat.js` | SSE `handleLogout()` | 直接 `fetch('/api/auth/logout')`，需更新错误处理 |
| `chat.js` | SSE `EventSource` | 不经过 `apiRequest`，不需要改 |

## Migration Strategy

1. Create `src/config/response_codes.py` (no dependency on other code)
2. Create `src/infra/response_envelope.py` with `ApiResponse` helper + exception handlers
3. Register exception handlers in `src/api/main.py`
4. Add `ResponseEnvelopeMiddleware` middleware
5. Update `js/api.js` to use `data.code` for error detection
6. Update `js/chat.js` SSE error handling to use new error codes
