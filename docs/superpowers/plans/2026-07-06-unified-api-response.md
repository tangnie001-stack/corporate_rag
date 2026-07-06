# Unified API Response Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize all API responses into `{"code", "message", "data"}` envelope via middleware. Routes return raw data, middleware handles wrapping.

**Architecture:** A single `ResponseEnvelopeMiddleware` catches all responses. Success responses are wrapped with `code: "SUCCESS"`. Errors use a custom `ApiError` exception carrying a business error code. Auth middleware raises `ApiError` instead of returning `JSONResponse` directly. Frontend reads `body.code` for all API responses.

**Tech Stack:** Python 3.11+ / FastAPI / Starlette / JavaScript

## Global Constraints

- Health check (`GET /api/health`) keeps its current format — not wrapped
- SSE streaming (`/api/chat/stream`) is skipped by the middleware — not wrapped
- Middleware registration order: CORSMiddleware → ResponseEnvelopeMiddleware → auth_middleware
- All middleware lives in `src/middleware/`
- `Code` class in `src/config/response_codes.py` uses `_MSG` suffix for message constants

---
### Task 1: Response Code Constants

**Files:**
- Create: `src/config/response_codes.py`

**Interfaces:**
- Produces: `Code.SUCCESS`, `Code.SUCCESS_MSG`, `Code.AUTH_REQUIRED`, `Code.AUTH_REQUIRED_MSG`, etc.
- Produces: All error codes and their messages as class attributes

- [ ] **Step 1: Write the failing test**

```python
# tests/test_response_codes.py
from src.config.response_codes import Code


def test_success_constants():
    assert Code.SUCCESS == "SUCCESS"
    assert Code.SUCCESS_MSG == "操作成功"


def test_error_codes_have_messages():
    """每个错误码都有对应的 _MSG 常量。"""
    codes = [attr for attr in dir(Code) if attr.isupper() and not attr.endswith("_MSG")]
    for code in codes:
        msg_attr = f"{code}_MSG"
        assert hasattr(Code, msg_attr), f"{code} missing {msg_attr}"
        assert getattr(Code, msg_attr), f"{code} has empty message"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_response_codes.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.config.response_codes'`

- [ ] **Step 3: Create `src/config/response_codes.py`**

```python
"""API 响应码和消息常量。

所有错误码用大写字符串，对应的消息用 `{CODE}_MSG` 命名。
添加新错误码时必须同时添加 `_MSG`。"""


class Code:
    # 通用
    SUCCESS = "SUCCESS"
    SUCCESS_MSG = "操作成功"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INTERNAL_ERROR_MSG = "服务器内部错误"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    UNKNOWN_ERROR_MSG = "未知错误"
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
    FILE_DUPLICATE_MSG = "文件已存在，请勿重复上传"

    # 文档
    DOC_PROCESSING_FAILED = "DOC_PROCESSING_FAILED"
    DOC_PROCESSING_FAILED_MSG = "文档处理失败"

    # 会话
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_NOT_FOUND_MSG = "会话不存在"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_response_codes.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/config/response_codes.py tests/test_response_codes.py
git commit -m "feat(api): add response code constants with Code class"
```
---

### Task 2: ApiError Exception

**Files:**
- Create: `src/infra/api_error.py`
- Test: `tests/test_api_error.py`

**Interfaces:**
- Produces: `ApiError(code, message, status=400)` — raised by middleware and routes, caught by `ResponseEnvelopeMiddleware`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_error.py
import pytest
from src.infra.api_error import ApiError


def test_api_error_requires_code_and_message():
    err = ApiError("TEST_ERROR", "测试错误")
    assert err.code == "TEST_ERROR"
    assert err.message == "测试错误"
    assert err.status == 400


def test_api_error_custom_status():
    err = ApiError("NOT_FOUND", "不存在", 404)
    assert err.status == 404


def test_api_error_is_exception():
    assert issubclass(ApiError, Exception)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_api_error.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.infra.api_error'`

- [ ] **Step 3: Create `src/infra/api_error.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_api_error.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infra/api_error.py tests/test_api_error.py
git commit -m "feat(api): add ApiError exception class"
```
---

### Task 3: Middleware — Auth Move & Response Envelope

**Files:**
- Create: `src/middleware/__init__.py`
- Create: `src/middleware/auth.py` (move from `src/api/middleware.py`)
- Create: `src/middleware/response_envelope.py`
- Delete: `src/api/middleware.py`

**Interfaces:**
- Consumes: `Code` class (Task 1), `ApiError` class (Task 2)
- Produces: `ResponseEnvelopeMiddleware` — wraps all non-excluded responses
- Produces: `auth_middleware` — raises `ApiError` instead of returning `JSONResponse`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_middleware.py
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from src.middleware.response_envelope import ResponseEnvelopeMiddleware


def test_response_envelope_is_middleware():
    assert issubclass(ResponseEnvelopeMiddleware, BaseHTTPMiddleware)


def test_auth_middleware_exists():
    from src.middleware.auth import auth_middleware
    assert callable(auth_middleware)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_middleware.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `src/middleware/__init__.py`**

```python
"""HTTP 中间件包 — 认证、响应包装等请求管道的处理。"""
```

- [ ] **Step 4: Create `src/middleware/response_envelope.py`**

```python
"""统一响应包装中间件。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式。
健康检查和 SSE 流式响应跳过包装。
"""

import json

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.config.response_codes import Code
from src.infra.api_error import ApiError


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """统一响应包装中间件。

    成功响应 → {"code": "SUCCESS", "message": "操作成功", "data": ...}
    ApiError 异常 → {"code": "...", "message": "...", "data": null}
    未预期异常 → {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "data": null}
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 跳过健康检查和 SSE 流式端点
        path = request.url.path
        if path == "/api/health" or path == "/api/chat/stream":
            return await call_next(request)

        try:
            response = await call_next(request)

            # 非 ApiError 的 4xx/5xx（如 FastAPI 默认 405 Not Allowed）
            if response.status_code >= 400:
                body = response.body
                detail = None
                if body:
                    try:
                        detail = json.loads(body).get("detail")
                    except (json.JSONDecodeError, AttributeError):
                        pass
                return JSONResponse(
                    {"code": Code.UNKNOWN_ERROR, "message": detail or response.reason_phrase or "请求失败", "data": None},
                    status_code=response.status_code,
                )

            # 包装成功响应: 解析原 body 放入 data 字段
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

        except Exception:
            return JSONResponse(
                {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
                status_code=500,
            )
```

- [ ] **Step 5: Create `src/middleware/auth.py` — move from `src/api/middleware.py`, replace JSONResponse with ApiError**

```python
"""认证中间件 — 从 Cookie 中读取 token 验证身份。

优先校验 token Cookie（登录用户），
fallback 为 user_id Cookie（匿名用户，仅用于 chat/sessions）。
"""

import uuid as uuid_mod
from fastapi import Request, Response
from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.api_error import ApiError
from src.infra.user_auth import UserAuth

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # 认证端点无需鉴权
    if path.startswith("/api/auth/") or path == "/api/health":
        return await call_next(request)

    # 知识库端点：必须登录
    if path.startswith("/api/kbs"):
        token = request.cookies.get("token")
        if not token:
            raise ApiError(Code.AUTH_REQUIRED, Code.AUTH_REQUIRED_MSG, 401)
        uid = await UserAuth.get_user_id_from_token_async(
            _get_service().redis_client, token
        )
        if not uid:
            raise ApiError(Code.AUTH_TOKEN_EXPIRED, Code.AUTH_TOKEN_EXPIRED_MSG, 401)
        request.state.user_id = uid
        return await call_next(request)

    # Chat / Sessions：优先 token，fallback 匿名 user_id
    if path.startswith("/api/chat/") or path.startswith("/api/sessions/"):
        token = request.cookies.get("token")
        if token:
            uid = await UserAuth.get_user_id_from_token_async(
                _get_service().redis_client, token
            )
            if uid:
                request.state.user_id = uid
                return await call_next(request)
        uid = request.cookies.get("user_id")
        if not uid:
            uid = str(uuid_mod.uuid4())
        request.state.user_id = uid
        resp: Response = await call_next(request)
        if not request.cookies.get("user_id"):
            resp.set_cookie(
                "user_id", uid, max_age=31536000, path="/", samesite="lax"
            )
        return resp

    return await call_next(request)
```

- [ ] **Step 6: Delete old auth middleware**

```bash
rm src/api/middleware.py
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_middleware.py -v
ruff check src/middleware/
```

Expected: 2 passed, no ruff errors.

- [ ] **Step 8: Commit**

```bash
git add src/middleware/ tests/test_middleware.py
git rm src/api/middleware.py
git commit -m "feat(middleware): add ResponseEnvelopeMiddleware, move auth to src/middleware/"
```
---

### Task 4: Update Routes — Replace HTTPException with ApiError

**Files:**
- Modify: `src/api/routes/knowledge_base.py`
- Modify: `src/api/routes/sessions.py`
- Modify: `src/api/routes/auth.py`
- Modify: `src/api/routes/documents.py`

**Interfaces:**
- Consumes: `ApiError` class (Task 2), `Code` class (Task 1)
- Destroys: All `raise HTTPException(...)` calls replaced with `raise ApiError(...)`

- [ ] **Step 1: Search for all HTTPException usages**

```bash
grep -rn "raise HTTPException" src/api/routes/
```

- [ ] **Step 2: Replace in each route file**

**`src/api/routes/knowledge_base.py`:**

```python
# Remove import:
from fastapi import APIRouter, HTTPException, Request

# Add import:
from src.config.response_codes import Code
from src.infra.api_error import ApiError

# Replace all raise HTTPException(...) with:
raise ApiError(Code.KB_NOT_FOUND, Code.KB_NOT_FOUND_MSG, 404)
```

**`src/api/routes/sessions.py`:** Search for `HTTPException` and replace each with corresponding `ApiError(Code.SESSION_NOT_FOUND, ...)`.

**`src/api/routes/auth.py`:** Replace `HTTPException` with `ApiError(Code.AUTH_WRONG_PASSWORD, ...)` and `ApiError(Code.AUTH_ACCOUNT_EXISTS, ...)`.

**`src/api/routes/documents.py`:** Replace `HTTPException` with corresponding file/KB error codes (413 → `FILE_TOO_LARGE`, 400 → `FILE_TYPE_UNSUPPORTED`, 500 → `FILE_UPLOAD_FAILED`, 404 → `KB_NOT_FOUND`).

- [ ] **Step 3: Remove unused `HTTPException` imports**

```bash
grep -rn "HTTPException" src/api/routes/
# If no more usages, remove from import lines
```

- [ ] **Step 4: Run ruff check**

```bash
ruff check src/api/routes/
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/
git commit -m "refactor(routes): replace HTTPException with ApiError for unified error codes"
```
---

### Task 5: Update main.py — Register Middleware

**Files:**
- Modify: `src/api/main.py`

**Interfaces:**
- Consumes: `ResponseEnvelopeMiddleware` (Task 3), `auth_middleware` from new path (Task 3)

- [ ] **Step 1: Update imports and registration in `src/api/main.py`**

Replace:
```python
from src.api.middleware import auth_middleware
```
With:
```python
from src.middleware.auth import auth_middleware
from src.middleware.response_envelope import ResponseEnvelopeMiddleware
```

Add after the CORS middleware line:
```python
# 统一响应包装（在 CORS 内层、auth 外层）
app.add_middleware(ResponseEnvelopeMiddleware)
```

Remove any existing `@app.exception_handler(Exception)` if present.

Final middleware section should look like:
```python
# 中间件注册顺序（请求从外到内）：
# CORS → ResponseEnvelope → auth → router
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ResponseEnvelopeMiddleware)
app.middleware("http")(auth_middleware)
```

- [ ] **Step 2: Verify imports**

```bash
python3 -c "from src.api.main import app; print('OK')"
```

Expected: "OK"

- [ ] **Step 3: Commit**

```bash
git add src/api/main.py
git commit -m "feat(api): register ResponseEnvelopeMiddleware in main.py"
```
---

### Task 6: Update Frontend — New Response Format

**Files:**
- Modify: `nginx/html/js/api.js`
- Modify: `nginx/html/js/chat.js`
- Modify: `nginx/html/index.html`

**Interfaces:**
- Consumes: All API responses now have `{code, message, data}` envelope

- [ ] **Step 1: Update `js/api.js` — rewrite `apiRequest`**

Replace the function:
```javascript
async function apiRequest(path, options = {}) {
    const url = `${API_BASE}${path}`;
    const config = {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    };

    // Don't set Content-Type for FormData (browser sets with boundary)
    if (options.body instanceof FormData) {
        if (config.headers) delete config.headers['Content-Type'];
    }

    const response = await fetch(url, config);
    const body = await response.json().catch(() => null);

    // 网络错误或非 JSON 响应
    if (!body) {
        throw new Error('请求失败');
    }

    // 未登录/auth 过期 → 直接跳转登录页（不弹错误提示）
    if (body.code === 'AUTH_REQUIRED' || body.code === 'AUTH_TOKEN_EXPIRED') {
        location.href = '/login.html?redirect=' + encodeURIComponent(location.pathname);
        return;
    }

    // 业务错误
    if (body.code !== 'SUCCESS') {
        throw new Error(body.message || '请求失败');
    }

    // 成功 — 返回 data 部分
    return body.data;
}
```

Remove the old `if (!response.ok)` block and `if (response.status === 204) return null;`.

- [ ] **Step 2: Update `index.html` — checkAuth wrapper**

Replace the `checkAuth` function:
```javascript
(async function checkAuth() {
    try {
        const r = await fetch('/api/auth/verify');
        const d = await r.json();
        if (d.code !== 'SUCCESS' || !d.data?.valid) {
            updateUserArea(null);
        } else {
            updateUserArea(d.data.user_id || '');
        }
    } catch (e) {
        updateUserArea(null);
    }
})();
```

- [ ] **Step 3: Verify frontend changes**

```bash
ruff check nginx/html/ --extensions=.js 2>/dev/null || echo "ruff skipped (JS files)"
```

- [ ] **Step 4: Commit**

```bash
git add nginx/html/js/api.js nginx/html/index.html
git commit -m "feat(frontend): adapt apiRequest and checkAuth to new response envelope"
```
---

### Task 7: Update API Contract Documentation

**Files:**
- Modify: `docs/api_contract.md`

**Interfaces:**
- All API responses now wrapped in `{code, message, data}` envelope

- [ ] **Step 1: Update each endpoint's response format in `docs/api_contract.md`**

For each endpoint in section 2 (Routes ↔ AppService), wrap the example response with the envelope:

```markdown
### 2.1.1 `GET /api/kbs → list[dict]`

列出所有知识库。

```json
{"code": "SUCCESS", "message": "操作成功", "data": [
  {"id": "uuid", "name": "库名称", "doc_count": 0}
]}
```

Error example:

```diff
- {"detail": "知识库不存在"}
+ {"code": "KB_NOT_FOUND", "message": "知识库不存在", "data": null}
```

Continue for all endpoints in section 2 (2.1.1 through 2.4.3).

- [ ] **Step 2: Skip health check and SSE stream (they don't use envelope)**

- [ ] **Step 3: Verify the document renders correctly**

```bash
head -50 docs/api_contract.md
```

- [ ] **Step 4: Commit**

```bash
git add docs/api_contract.md
git commit -m "docs: update API contract with unified response format"
```
---

