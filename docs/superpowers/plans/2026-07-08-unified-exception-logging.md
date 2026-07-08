# 统一异常与日志体系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立统一的日志配置、异常层次体系和重试策略，确保所有异常路径记录完整 traceback，异常响应格式统一。

**Architecture:** 三步走：① Loguru 配置收拢到 `src/core/logging.py`，API/CLI 双模式；② 新增 AppError 异常层次，增强 `@app.exception_handler`，简化 `ResponseEnvelopeMiddleware`，Auth 改为 raise；③ 统一三套重试策略到增强版 `with_retry` 装饰器。

**Tech Stack:** Python 3.12+ / FastAPI / Loguru / Starlette

## Global Constraints

- 不引入新的外部依赖
- 不改变现有 API 响应格式（`{"code", "message", "data"}`）
- 所有修改后 `pytest tests/ -v` 全部通过
- 所有修改后 `ruff check .` 无错误
- 旧的 `src/infra/api_error.py` 删除，不留别名

---

## File Structure

### Files to Create
- `src/core/logging.py` — `setup_logging()` + `InterceptHandler`，集中 Loguru 配置
- `src/infra/errors.py` — `AppError / BusinessError / AuthError / ValidationError / SystemError` 异常层次

### Files to Modify
- `src/api/main.py` — 移除行 57-79 的内联 Loguru 配置，改为调用 setup_logging；增强 @app.exception_handler
- `src/middleware/response_envelope.py` — 简化 dispatch：只包装成功响应 + Auth 的 except AppError + 自身兜底
- `src/middleware/auth.py` — 3 处 return JSONResponse → raise AuthError
- `src/models.py` — with_retry 增强 retryable_exceptions 参数 ；重试耗尽加 logger.exception
- `src/rag_chain.py` — 替换 rerank 和 LLM 内联重试为 with_retry + 降级
- `src/api/routes/chat.py` — 替换持久化内联重试为 with_retry
- `src/chat_manager.py` — 3 处无日志 except 补 logger.warning
- `src/cli/check_retrieval.py` — print → logger
- `src/cli/eval_ragas.py` — 添加 setup_logging 调用

### Files to Delete
- `src/infra/api_error.py` — 被 src/infra/errors.py 替代

---

### Task 1: 创建 src/core/logging.py 统一日志模块

**Files:**
- Create: `src/core/logging.py`
- Modify: `src/api/main.py:57-79`
- Test: 手动验证（日志输出到文件/控制台）

**Interfaces:**
- Produces: `setup_logging(write_to_file: bool = True, configure_trace_id: bool = False) -> None`
- Produces: `InterceptHandler(logging.Handler)` — 收编标准库日志到 Loguru

- [ ] **Step 1: 创建 src/core/logging.py**

```python
"""统一日志配置 — 集中管理 Loguru sinks 和第三方库日志收编。

支持 API 模式（写文件 + 控制台）和 CLI 模式（仅控制台）。
提供 InterceptHandler 将标准库 logging 路由至 Loguru。
"""

import logging
import os
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    """将标准库 logging 无缝路由到 Loguru。

    用于收编 uvicorn、fastapi 等三方库的日志，
    确保所有日志通过 Loguru 统一管道输出。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[trace_id]:36} | {name}:{function}:{line} - {message}"
_LOG_DIR = os.getenv("LOG_DIR", "logs")


def _setup_trace_id_patcher() -> None:
    """配置 Loguru patcher，自动注入当前请求的 trace_id。

    从 trace_context 模块的 ContextVar 中读取当前 trace_id，
    写入每一条日志记录的 extra 字段。
    仅在 API 进程（有 HTTP 请求上下文）中启用。
    """
    from src.infra.llm.trace_context import current_trace_id as _trace_var

    def _patcher(record):
        record["extra"]["trace_id"] = _trace_var.get() or ""

    logger.configure(extra={"trace_id": ""}, patcher=_patcher)


def setup_logging(write_to_file: bool = True, configure_trace_id: bool = False) -> None:
    """初始化 Loguru 日志配置。

    Args:
        write_to_file: 是否写入文件（API 模式 True，CLI 模式 False）
        configure_trace_id: 是否注入 trace_id patcher（API 模式 True，CLI 模式 False）

    API 模式配置：
      - app_{date}.log — INFO 级别，按天轮转，保留 7 天
      - error.log     — ERROR 级别，100MB 轮转，保留 30 天，异步写入
      - stderr        — INFO 级别，彩色输出

    CLI 模式配置：
      - stderr — INFO 级别，彩色输出（不写文件）
    """
    # 确保日志目录存在
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 移除默认 sink，防止重复
    logger.remove()

    # 控制台 sink（API 和 CLI 共用）
    logger.add(sys.stderr, format=_LOG_FORMAT, level="INFO", colorize=True)

    # 文件 sink（仅 API 模式）
    if write_to_file:
        logger.add(
            f"{_LOG_DIR}/app_{{time:YYYY-MM-DD}}.log",
            format=_LOG_FORMAT,
            rotation="1 day",
            retention="7 days",
            level="INFO",
            encoding="utf-8",
        )
        logger.add(
            f"{_LOG_DIR}/error.log",
            format=_LOG_FORMAT,
            rotation="100 MB",
            retention="30 days",
            level="ERROR",
            encoding="utf-8",
            enqueue=True,
        )

    # 收编标准库日志到 Loguru
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        _log = logging.getLogger(name)
        _log.handlers = [InterceptHandler()]
        _log.propagate = False

    # trace_id patcher（仅 API 模式）
    if configure_trace_id:
        _setup_trace_id_patcher()
```

- [ ] **Step 2: 修改 main.py — 替换内联 Loguru 配置为调用 setup_logging**

将 main.py 中第 57-79 行的内联 Loguru 配置替换为：

```python
from src.core.logging import setup_logging

# ...（app 创建后，lines 56-79 替换为:）

# 配置 Loguru — 收拢到统一模块
setup_logging(write_to_file=True, configure_trace_id=True)
```

删除的代码：
- 第 57-62 行 `def _trace_id_patcher` 和 `logger.configure(...)`（已收入 logging.py）
- 第 64-67 行 `_LOG_FORMAT`、`_LOG_DIR`、`os.makedirs`（已收入 logging.py）
- 第 70-79 行 `logger.remove()`、`logger.add(...)`（已收入 logging.py）

- [ ] **Step 3: 修改 CLI 入口 — check_retrieval.py**

在文件顶部 `from loguru import logger` 之后，添加：

```python
from src.core.logging import setup_logging

setup_logging(write_to_file=False)
```

将第 57 行处 `print(f"Error: {e}")` 替换为 `logger.error("Error: {}", e)`。

- [ ] **Step 4: 修改 CLI 入口 — eval_ragas.py**

在文件顶部 `from loguru import logger` 之后，添加：

```python
from src.core.logging import setup_logging

setup_logging(write_to_file=False)
```

- [ ] **Step 5: 补 chat_manager.py 无日志 except 块**

找到 `chat_manager.py` 中三处无日志的 `except Exception`（行 167、318、323），在每个块中添加 `logger.warning`：

行 167 处（Redis 健康检查失败）：
```python
except Exception:
    logger.warning("Redis ping 失败，持续使用 InMemory 模式")
    self._in_memory = True
```

行 318 处：
```python
except Exception:
    logger.warning("Redis 同步连接失败，尝试重连")
```

行 323 处：
```python
except Exception:
    logger.warning("Redis 初始化失败，切换到 InMemory 模式")
    self._redis = None
    self._in_memory = True
```

- [ ] **Step 6: 验证改动**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
python -c "from src.core.logging import setup_logging; setup_logging(write_to_file=True, configure_trace_id=False); print('OK')"
# 预期：控制台输出 "OK"，logs/ 目录下无文件（setup_logging 时还没创建日志，但目录存在）
```

```bash
ruff check .
# 预期：无错误
```

- [ ] **Step 7: Commit**

```bash
git add src/core/logging.py src/api/main.py src/cli/check_retrieval.py src/cli/eval_ragas.py src/chat_manager.py
git commit -m "feat: 统一日志配置 — setup_logging 收拢 Loguru，API/CLI 双模式"
```

---

### Task 2: 创建 src/infra/errors.py 异常层次

**Files:**
- Create: `src/infra/errors.py`
- Delete: `src/infra/api_error.py`

**Interfaces:**
- Produces: `AppError(Exception)` — 基类，属性 `code: str`, `message: str`, `status: int`
- Produces: `BusinessError(AppError)` — 业务规则冲突，默认 status=400
- Produces: `AuthError(AppError)` — 认证授权，默认 status=401
- Produces: `ValidationError(AppError)` — 参数校验，默认 status=422
- Produces: `SystemError(AppError)` — 基础设施故障，默认 status=503

- [ ] **Step 1: 创建 src/infra/errors.py**

```python
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
```

- [ ] **Step 2: 删除旧的 src/infra/api_error.py**

```bash
git rm src/infra/api_error.py
```

- [ ] **Step 3: 验证**

```bash
ruff check .
# 预期：无错误
```

```bash
python -c "from src.infra.errors import AppError, BusinessError, AuthError, SystemError; print('OK')"
# 预期：OK
```

- [ ] **Step 4: Commit**

```bash
git add src/infra/errors.py
git rm src/infra/api_error.py
git commit -m "feat: 新增 AppError 异常层次体系，删除旧 api_error.py"
```

---

### Task 3: 全局替换 raise ApiError → raise BusinessError/AuthError

**Files:**
- Modify: `src/app_service.py` — 4 处
- Modify: `src/api/routes/sessions.py` — 2 处
- Modify: `src/api/routes/documents.py` — 3 处
- Modify: `src/api/routes/knowledge_base.py` — 1 处
- Modify: `src/api/routes/auth.py` — 1 处
- Modify: `src/middleware/response_envelope.py` — 1 处 `except ApiError` → `except AppError`
- Modify: `src/config/response_codes.py` — 无改动
- Modify: `src/infra/llm/trace_context.py` — 无改动

- [ ] **Step 1: 全局搜索所有 raise ApiError 位置**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag && grep -rn "raise ApiError" src/ --include="*.py"
```

确认输出包含以下 12 处：

| 文件 | 行 | 当前代码 | 改为 |
|------|---|---------|------|
| `app_service.py:153` | `raise ApiError(Code.DOC_NOT_FOUND, ...)` | `raise BusinessError(Code.DOC_NOT_FOUND, ...)` |
| `app_service.py:157` | `raise ApiError(Code.DOC_DELETE_NOT_ALLOWED, ...)` | `raise BusinessError(Code.DOC_DELETE_NOT_ALLOWED, ...)` |
| `app_service.py:165` | `raise ApiError(Code.DOC_STATUS_CONFLICT, ...)` | `raise BusinessError(Code.DOC_STATUS_CONFLICT, ...)` |
| `app_service.py:184` | `raise ApiError(Code.DOC_NOT_FOUND, ...)` | `raise BusinessError(Code.DOC_NOT_FOUND, ...)` |
| `sessions.py:100` | `raise ApiError(Code.SESSION_NOT_FOUND, ...)` | `raise BusinessError(Code.SESSION_NOT_FOUND, ...)` |
| `sessions.py:145` | `raise ApiError(Code.SESSION_NOT_FOUND, ...)` | `raise BusinessError(Code.SESSION_NOT_FOUND, ...)` |
| `documents.py:134` | `raise ApiError(Code.FILE_TOO_LARGE, ...)` | `raise BusinessError(Code.FILE_TOO_LARGE, ...)` |
| `documents.py:145` | `raise ApiError(Code.FILE_TYPE_UNSUPPORTED, ...)` | `raise BusinessError(Code.FILE_TYPE_UNSUPPORTED, ...)` |
| `documents.py:173` | `raise ApiError(Code.FILE_UPLOAD_FAILED, ...)` | `raise SystemError(Code.FILE_UPLOAD_FAILED, ...)` |
| `knowledge_base.py:110` | `raise ApiError(Code.KB_NOT_FOUND, ...)` | `raise BusinessError(Code.KB_NOT_FOUND, ...)` |
| `auth.py:39` | `raise ApiError(Code.AUTH_WRONG_PASSWORD, ...)` | `raise AuthError(Code.AUTH_WRONG_PASSWORD, ...)` |
| `response_envelope.py:62` | `except ApiError as e` | `except AppError as e` |

注意 `documents.py:173` 是文件上传失败，属于基础设施故障 → 用 `SystemError`。
`auth.py:39` 是密码错误 → 用 `AuthError`。
其余都是业务逻辑冲突 → 用 `BusinessError`。

- [ ] **Step 2: 逐文件替换**

对每个文件：
1. 修改文件顶部的 import：`from src.infra.api_error import ApiError` → `from src.infra.errors import BusinessError, AuthError, SystemError`（按需导入）
2. 修改所有 `raise ApiError(...)` 为对应的 `raise BusinessError(...)` / `raise AuthError(...)` / `raise SystemError(...)`
3. 修改 `response_envelope.py:62` 的 `except ApiError` → `import AppError` + `except AppError`

- [ ] **Step 3: 验证**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
ruff check . --fix
ruff check .
# 预期：无错误（可能有一些未使用的 import 警告，ruff --fix 会自动清理）
```

```bash
pytest tests/ -v
# 预期：全部通过
```

- [ ] **Step 4: Commit**

```bash
git add src/app_service.py src/api/routes/sessions.py src/api/routes/documents.py src/api/routes/knowledge_base.py src/api/routes/auth.py src/middleware/response_envelope.py
git commit -m "refactor: 全局替换 ApiError → BusinessError/AuthError/SystemError"
```

---

### Task 4: 增强全局 exception_handler + 简化 dispatch

**Files:**
- Modify: `src/api/main.py` — lines 82-107 增强 handler
- Modify: `src/middleware/response_envelope.py` — 重写 dispatch

**Interfaces:**
- Consumes: `AppError` / `BusinessError` / `AuthError` / `SystemError` from `src.infra.errors`

- [ ] **Step 1: 增强 main.py 的 @app.exception_handler**

将 main.py 第 82-107 行的两个 handler 替换为：

```python
from src.infra.errors import AppError


# 异常处理器 — 所有 Router 层异常在此集中处理
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    from starlette.responses import JSONResponse

    # BusinessError 等已知业务异常用 warning 级别
    # SystemError 等基础设施异常用 exception 级别（含完整 traceback）
    if exc.status >= 500:
        logger.exception("基础设施异常: {} {}", exc.code, exc.message)
    else:
        logger.warning("业务异常: {} {}", exc.code, exc.message)

    return JSONResponse(
        {"code": exc.code, "message": exc.message, "data": None},
        status_code=exc.status,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    from starlette.responses import JSONResponse

    logger.exception("HTTP 异常: {} {}", exc.status_code, exc.detail)
    code = Code.NOT_FOUND if exc.status_code == 404 else Code.UNKNOWN_ERROR
    msg = exc.detail or (
        Code.NOT_FOUND_MSG if exc.status_code == 404 else Code.UNKNOWN_ERROR_MSG
    )
    return JSONResponse(
        {"code": code, "message": msg, "data": None}, status_code=exc.status_code
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    from starlette.responses import JSONResponse

    logger.exception("参数校验异常: {}", exc.errors())
    return JSONResponse(
        {
            "code": Code.VALIDATION_ERROR,
            "message": Code.VALIDATION_ERROR_MSG,
            "data": None,
        },
        status_code=422,
    )


@app.exception_handler(Exception)
async def unknown_exception_handler(request: Request, exc: Exception):
    from starlette.responses import JSONResponse

    logger.exception("未处理的系统异常: {} {}", request.method, request.url)
    return JSONResponse(
        {"code": Code.INTERNAL_ERROR, "message": Code.INTERNAL_ERROR_MSG, "data": None},
        status_code=500,
    )
```

注意删除原有的 `@app.exception_handler(StarletteHTTPException)` 和 `@app.exception_handler(RequestValidationError)` 的定义（行 82-107）。

- [ ] **Step 2: 重写 ResponseEnvelopeMiddleware.dispatch()**

```python
"""统一响应包装中间件。

将路由返回的原始数据包装为 {"code", "message", "data"} 格式。
健康检查和 SSE 流式响应跳过包装。
异常处理由 @app.exception_handler 集中处理，此处仅处理成功包装和 Auth 层的异常。
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
```

- [ ] **Step 3: 修改 auth.py — return JSONResponse → raise AuthError**

找到 `auth.py:38` 和 `auth.py:47` 两处，改为：

```python
# auth.py 顶部添加 import
from src.infra.errors import AuthError

# 行 38-40（token 为空）
if not token:
    raise AuthError(Code.AUTH_REQUIRED, Code.AUTH_REQUIRED_MSG, status=401)

# 行 47-49（token 无效）
if not uid:
    raise AuthError(Code.AUTH_TOKEN_EXPIRED, Code.AUTH_TOKEN_EXPIRED_MSG, status=401)
```

删除原有的 `from fastapi.responses import JSONResponse` 导入（如果不再使用）。

- [ ] **Step 4: 在 @app.exception_handler 中留 Prometheus 注释位**

在 `app_error_handler` 和 `unknown_exception_handler` 的 handler 函数体中，在返回语句之前添加：

```python
    # TODO: ARMS Prometheus 接入后在此处打 exception_total.inc()
```

- [ ] **Step 5: models.py 重试耗尽处加 logger.exception**

在 `models.py:90` 处，将：

```python
logger.error("{} failed after {} attempts", func.__name__, max_attempts)
```

改为：

```python
logger.exception("{} failed after {} attempts: {}", func.__name__, max_attempts, last_error)
```

- [ ] **Step 6: 验证**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
ruff check . --fix
ruff check .
pytest tests/ -v
```

- [ ] **Step 7: Commit**

```bash
git add src/api/main.py src/middleware/response_envelope.py src/middleware/auth.py src/models.py
git commit -m "feat: 增强全局异常处理 + Auth 规范化 + 简化 dispatch"
```

---

### Task 5: 重试策略统一

**Files:**
- Modify: `src/models.py` — with_retry 增强 retryable_exceptions
- Modify: `src/rag_chain.py` — 替换 rerank 和 LLM 内联重试
- Modify: `src/api/routes/chat.py` — 替换持久化内联重试

**Interfaces:**
- Consumes: `with_retry` from `src.models`

- [ ] **Step 1: 增强 with_retry 装饰器**

将 `models.py` 中 `with_retry` 函数的签名从：

```python
def with_retry(
    func: F = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    initial_interval: float = RETRY_INITIAL_INTERVAL,
    backoff: float = RETRY_BACKOFF_FACTOR,
) -> Callable:
```

改为：

```python
def with_retry(
    func: F = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    initial_interval: float = RETRY_INITIAL_INTERVAL,
    backoff: float = RETRY_BACKOFF_FACTOR,
    retryable_exceptions: tuple = (Exception,),
) -> Callable:
```

并在 `wrapper` 函数中将 `except Exception as e` 改为精确匹配：

```python
except retryable_exceptions as e:
```

其他逻辑不变（指数退避、re-raise）。保留不使用参数时的默认行为（`(Exception,)` → 兼容旧调用方）。

- [ ] **Step 2: 替换 rag_chain.py rerank 内联重试**

将行 468-499 的内联重试替换为：

```python
from src.models import with_retry

# 替换行 470-499（for attempt...else: 结构）
try:
    reranked = with_retry(self.reranker.rerank, max_attempts=RETRY_MAX_ATTEMPTS)(
        query, docs
    )
except Exception as e:
    logger.warning(
        "Rerank failed after {} attempts (using raw order): {}",
        RETRY_MAX_ATTEMPTS,
        e,
    )
    reranked = [
        {"index": i, "relevance_score": r.get("distance", 0)}
        for i, r in enumerate(results)
    ]
```

- [ ] **Step 3: 替换 rag_chain.py LLM 内联重试**

将行 650-697 的内联重试替换为：

```python
# 替换 for attempt... + try/except 结构
try:
    with_retry(_run_stream, max_attempts=RETRY_MAX_ATTEMPTS)(messages, model_config)
except Exception as e:
    logger.error("LLM stream failed after {} attempts", RETRY_MAX_ATTEMPTS)
    error_msg = f"生成回答失败: {e}"
    full_output = error_msg
    tracer.end_generation(gen_id, trace_id, output=error_msg)
    yield error_msg
    return
```

注意：LLM 重试代码中 `_run_stream` 是内部辅助函数，需要确认其是否可提取。如果 LLM 重试块内有复杂的上下文管理（tracer、token 追踪等），需要调整提取方式。具体做法是将重试保护的代码（`self.llm.stream(...)` 和 `async for` 循环）抽取为一个内部函数 `_run_llm_stream`，然后用 `with_retry` 包装。

- [ ] **Step 4: 替换 chat.py 持久化内联重试**

将 `chat.py:388-399` 的内联重试（线性退避 0.5s, 1s, 1.5s）替换为：

```python
from src.models import with_retry

# 替换行 388-399 的 for + try/except 结构
try:
    with_retry(_persist_conversation, max_attempts=3, initial_interval=0.5, backoff=2.0)(
        db, session_id, session_cache, messages_to_persist, file_ids,
    )
except Exception as e:
    logger.warning("持久化会话失败: {}", e)
```

- [ ] **Step 5: 验证**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
ruff check . --fix
ruff check .
pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/rag_chain.py src/api/routes/chat.py
git commit -m "refactor: 统一重试策略 — with_retry 增强 + 收编内联重试"
```

---

### Task 6: 最终清理与验证

**Files:**
- Confirm: `BACKLOG.md`
- Confirm: `CLAUDE-RULES.md`

- [ ] **Step 1: 确认 BACKLOG.md 中存在 Prometheus 待定项**

```bash
grep -q "Prometheus" BACKLOG.md && echo "已记录"
```

- [ ] **Step 2: 确认 CLAUDE-RULES.md 存在**

```bash
grep -q "异常处理三模式" CLAUDE-RULES.md && echo "架构规约已记录"
```

- [ ] **Step 3: 完整自检**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag

# 1. 测试全部通过
pytest tests/ -v

# 2. 无 ruff 错误
ruff check .

# 3. 无遗留旧导入
grep -rn "from src.infra.api_error import" src/ --include="*.py" && echo "WARNING: 旧导入残留" || echo "无旧导入残留"

# 4. 无遗留 print/TODO（Prometheus 注释除外）
grep -rn "print(" src/ --include="*.py" | grep -v "print\|#\|\.pyc" && echo "WARNING: 有 print 残留" || echo "无 print 残留"
```

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "chore: 最终清理 — 确认 BACKLOG + CLAUDE-RULES"
```
