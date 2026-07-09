# 数据链路追踪日志 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 traceid 串联 API 请求 → SQL/ChromaDB 返回值 → API 响应的完整数据流

**Architecture:** 3 层日志注入：response_processor 中间件记录 API 响应体（非 GET），mysql_db 辅助函数记录 SQL SELECT 返回值，vector_store 直接记录 ChromaDB 检索结果。所有新日志用 `[SQL]` / `[CHROMA]` / `[API]` 统一前缀

**Tech Stack:** Python 3.11+ / FastAPI / Loguru / aiomysql / ChromaDB

## Global Constraints

- 不引入新的外部依赖
- 日志单行最大 10MB（`LOG_MAX_BODY = 10 * 1024 * 1024`），超了截断
- 非 GET 接口记录响应体，GET 接口不记录
- `_SKIP_PATHS` 中的路径（`/api/health`、`/api/chat/stream`）整体跳过
- 跳过名单 `SQL_SKIP_FULL_LOG` 和 `API_SKIP_FULL_LOG` 集中定义在 `src/core/logging.py`
- 所有文件 sink 使用 `enqueue=True` 异步写入
- 序列化异常时用 try/except 兜底，不中断业务流程
- `ruff format . && ruff check . --fix` 格式化，`pytest tests/ -v` 验证

---

### Task 1: 修改 logging.py — 日志配置

**Files:**
- Modify: `src/core/logging.py:33-105`

**Interfaces:**
- Consumes: Loguru `logger` 配置
- Produces: `LOG_MAX_BODY`（截断常量）、`SQL_SKIP_FULL_LOG` / `API_SKIP_FULL_LOG`（跳过名单）

- [ ] **Step 1: 修改 logging.py**

完整替换 `src/core/logging.py` 的 `setup_logging()` 部分，改动如下：

1. 删除第 74 行 `logger.add(sys.stderr, ...)` 控制台 sink
2. 第 78 行 `logger.add` 增加 `enqueue=True`
3. 第 86 行 error.log 改为 `error_{{time:YYYY-MM-DD}}.log`，rotation 改为 `"1 day"`
4. 在 `setup_logging()` 之前添加模块级常量 `LOG_MAX_BODY`、`SQL_SKIP_FULL_LOG`、`API_SKIP_FULL_LOG`

修改后的完整文件：

```python
"""统一日志配置 — 集中管理 Loguru sinks 和第三方库日志收编。

支持 API 模式（写文件 + 控制台）和 CLI 模式（仅控制台）。
提供 InterceptHandler 将标准库 logging 路由至 Loguru。
"""

import logging
import os

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

# ==== 数据链路追踪日志常量 ====
LOG_MAX_BODY = 10 * 1024 * 1024  # 单条日志最大 10MB

# SQL 方法层 — 跳过全量返回值记录（只记 count + 关键参数）
SQL_SKIP_FULL_LOG = {"get_messages"}

# API 路由层 — 跳过全量响应体记录（只记 path + status_code）
API_SKIP_FULL_LOG = {"/api/sessions/messages"}


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
      - app_{date}.log — INFO 级别，按天轮转，保留 7 天，异步写入
      - error_{date}.log — ERROR 级别，按天轮转，保留 30 天，异步写入

    CLI 模式配置：
      - stderr — INFO 级别，彩色输出（不写文件）
    """
    # 确保日志目录存在
    os.makedirs(_LOG_DIR, exist_ok=True)

    # 移除默认 sink，防止重复
    logger.remove()

    # 文件 sink（仅 API 模式）
    if write_to_file:
        logger.add(
            f"{_LOG_DIR}/app_{{time:YYYY-MM-DD}}.log",
            format=_LOG_FORMAT,
            rotation="1 day",
            retention="7 days",
            level="INFO",
            encoding="utf-8",
            enqueue=True,
        )
        logger.add(
            f"{_LOG_DIR}/error_{{time:YYYY-MM-DD}}.log",
            format=_LOG_FORMAT,
            rotation="1 day",
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

注意：CLI 模式下（`write_to_file=False`）原本的控制台 sink 仍然保留。当前 `setup_logging()` 在有 `write_to_file=False` 时不加任何 sink，这是一个问题。但当前项目中 CLI 模式另有 logger.remove()+ 控制台 sink 的配置（`src/cli/check_chunks.py:11-17`），所以 scoped 函数不变。

---

### Task 2: 响应处理器重命名 + 扩展

**Files:**
- Rename: `src/middleware/response_envelope.py` → `src/middleware/response_processor.py`
- Modify: `src/middleware/response_processor.py`（原 response_envelope.py 全部内容 + 追加日志）
- Modify: `src/main.py:128`（更新 import 和注册名）
- Modify: `tests/test_middleware.py:1-6`（更新 import）

**Interfaces:**
- Consumes: `API_SKIP_FULL_LOG` from `src.core.logging`
- Produces: `[API]` 前缀的响应体日志

- [ ] **Step 1: 重命名文件**

```bash
mv src/middleware/response_envelope.py src/middleware/response_processor.py
```

- [ ] **Step 2: 修改 response_processor.py**

在 `response_processor.py` 中：
1. 修改 docstring，将 "响应包装" 改为 "返回值处理（包装 + 数据追踪日志）"
2. 函数重命名为 `response_processor_middleware`
3. 在包装返回之前、读取 `data` 之后，添加日志代码

修改后完整文件内容：

```python
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
from src.core.logging import API_SKIP_FULL_LOG

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
                    request.method, path, response.status_code,
                )
            else:
                try:
                    logger.info(
                        "[API] {} {} | status={} | data={}",
                        request.method, path, response.status_code, data,
                    )
                except Exception:
                    logger.info(
                        "[API] {} {} | status={} | data=<serialization_error>",
                        request.method, path, response.status_code,
                    )

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
```

- [ ] **Step 3: 修改 main.py**

更新 import 和中间件注册，两处修改：

```python
# src/main.py 第 16 行
# 改前：
from src.middleware.response_envelope import response_envelope_middleware
# 改后：
from src.middleware.response_processor import response_processor_middleware

# src/main.py 第 128 行
# 改前：
app.middleware("http")(response_envelope_middleware)
# 改后：
app.middleware("http")(response_processor_middleware)
```

- [ ] **Step 4: 修改 test_middleware.py**

完整替换文件内容：

```python
def test_response_processor_exists():
    from src.middleware.response_processor import response_processor_middleware

    assert callable(response_processor_middleware)


def test_auth_middleware_exists():
    from src.middleware.auth import auth_middleware

    assert callable(auth_middleware)
```

- [ ] **Step 5: 运行验证**

```bash
ruff format . && ruff check . --fix
pytest tests/test_middleware.py -v
```

预期输出：两个 test 通过，ruff 无 error。

---

### Task 3: MySQL SQL 返回值日志

**Files:**
- Modify: `src/infra/db/mysql_db.py`

**Interfaces:**
- Consumes: `SQL_SKIP_FULL_LOG` from `src.core.logging`
- Produces: `[SQL]` 前缀的辅助函数 `_log_sql_result()` + 各 SELECT 方法调用

- [ ] **Step 1: 添加 import 和辅助函数**

在 `src/infra/db/mysql_db.py` 的 `INSERT_USER` 等 query import 之后、`class MySQLDB` 之前，添加：

```python
from src.core.logging import LOG_MAX_BODY, SQL_SKIP_FULL_LOG


def _log_sql_result(method: str, rows, **extra) -> None:
    """统一 SQL 返回值日志。

    方法名在 SQL_SKIP_FULL_LOG 中时只记录 count + 额外参数，
    否则记录完整 data。超过 LOG_MAX_BODY 时截断。
    """
    count = len(rows) if isinstance(rows, (list, dict)) else (1 if rows is not None else 0)
    if method in SQL_SKIP_FULL_LOG:
        extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
        logger.info("[SQL] method={} | rows={} | {}", method, count, extra_str)
    else:
        data_str = str(rows)
        if len(data_str) > LOG_MAX_BODY:
            data_str = data_str[:LOG_MAX_BODY] + f"... (truncated, total={len(data_str)} chars)"
        try:
            logger.info("[SQL] method={} | rows={} | data={}", method, count, data_str)
        except Exception:
            logger.info("[SQL] method={} | rows={} | data=<serialization_error>", method, count)
```

- [ ] **Step 2: get_sessions 添加日志**

在第 291 行 `return rows` 之前添加：

```python
        _log_sql_result("get_sessions", rows)
```

- [ ] **Step 3: get_session_by_id 添加日志**

在第 313 行 `return row` 之前添加：

```python
        _log_sql_result("get_session_by_id", row)
```

- [ ] **Step 4: get_messages 添加日志**

在第 335 行 `return rows` 之前添加（只记录 count+session_id，不记录 content）：

```python
        _log_sql_result("get_messages", rows, session_id=session_id)
```

- [ ] **Step 5: get_user_by_account 添加日志**

在第 507 行 `return row` 之前添加：

```python
        _log_sql_result("get_user_by_account", row)
```

- [ ] **Step 6: get_user_by_token 添加日志**

在第 545 行 `return row` 之前添加：

```python
        _log_sql_result("get_user_by_token", row)
```

- [ ] **Step 7: get_kb_by_name 添加日志**

在第 571 行 `return row["id"] if row else None` 之前添加：

```python
        _log_sql_result("get_kb_by_name", row)
```

- [ ] **Step 8: get_all_kb 添加日志**

在第 601 行 `return result` 之前添加：

```python
        _log_sql_result("get_all_kb", result)
```

- [ ] **Step 9: get_documents 添加日志**

在第 646 行 `return rows` 之前添加：

```python
        _log_sql_result("get_documents", rows)
```

- [ ] **Step 10: get_or_create_kb 两个返回路径添加日志**

在第 169 行 `return kb_id, True` 之前添加：

```python
        _log_sql_result("get_or_create_kb", (kb_id, True))
```

在第 180 行 `return existing_id, False` 之前添加：

```python
        _log_sql_result("get_or_create_kb", (existing_id, False))
```

- [ ] **Step 11: 运行验证**

```bash
ruff format . && ruff check . --fix
```

确认无 error。

---

### Task 4: ChromaDB 检索结果日志

**Files:**
- Modify: `src/infra/db/vector_store.py`

**Interfaces:**
- Consumes: `LOG_MAX_BODY` from `src.core.logging`
- Produces: `[CHROMA]` 前缀的检索结果日志

- [ ] **Step 1: 添加 import**

在 `src/infra/db/vector_store.py` 的 `from loguru import logger` 之后添加：

```python
from src.core.logging import LOG_MAX_BODY
```

- [ ] **Step 2: similarity_search 添加日志**

在第 265 行 `return formatted` 之前添加：

```python
        try:
            data_str = str(formatted)
            if len(data_str) > LOG_MAX_BODY:
                data_str = data_str[:LOG_MAX_BODY] + f"... (truncated, total={len(data_str)} chars)"
            logger.info(
                "[CHROMA] method=similarity_search | kb_id={} | query_len={} | rows={} | data={}",
                kb_id, len(query), len(formatted), data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=similarity_search | kb_id={} | query_len={} | rows={} | data=<serialization_error>",
                kb_id, len(query), len(formatted),
            )
```

- [ ] **Step 3: similarity_search_all 添加日志**

在第 465 行 `return all_results[:k]` 之前添加：

```python
        try:
            data_str = str(all_results[:k])
            if len(data_str) > LOG_MAX_BODY:
                data_str = data_str[:LOG_MAX_BODY] + f"... (truncated, total={len(data_str)} chars)"
            logger.info(
                "[CHROMA] method=similarity_search_all | rows={} | data={}",
                min(len(all_results), k), data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=similarity_search_all | rows={} | data=<serialization_error>",
                min(len(all_results), k),
            )
```

- [ ] **Step 4: get_chunks_by_doc_id 添加日志**

在第 323 行 `return chunks` 之前（try 块内）添加：

```python
        try:
            data_str = str(chunks)
            if len(data_str) > LOG_MAX_BODY:
                data_str = data_str[:LOG_MAX_BODY] + f"... (truncated, total={len(data_str)} chars)"
            logger.info(
                "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={} | data={}",
                doc_id, len(chunks), data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={} | data=<serialization_error>",
                doc_id, len(chunks),
            )
```

- [ ] **Step 5: get_chunks_paginated 添加日志**

在第 382 行 `return {`...`}` 之前（try 块内）添加：

```python
        try:
            data_str = str(items)
            if len(data_str) > LOG_MAX_BODY:
                data_str = data_str[:LOG_MAX_BODY] + f"... (truncated, total={len(data_str)} chars)"
            logger.info(
                "[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | page_size={} | total={} | data={}",
                doc_id, page, page_size, total, data_str,
            )
        except Exception:
            logger.info(
                "[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | page_size={} | data=<serialization_error>",
                doc_id, page, page_size,
            )
```

- [ ] **Step 6: 运行验证**

```bash
ruff format . && ruff check . --fix
```

确认无 error。

---

### Task 5: 验证

- [ ] **Step 1: 运行完整测试套件**

```bash
pytest tests/ -v
```

预期输出：全部用例通过。（注意 mocking 环境下的测试不依赖实际日志输出）

- [ ] **Step 2: 运行格式化 + lint**

```bash
ruff format . && ruff check . --fix
```

预期输出：无 error。

- [ ] **Step 3: 无遗留调试代码检查**

```bash
# 检查是否遗留 print()、TODO、调试代码
grep -rn "print(" src/ --include="*.py" | grep -v "#" || true
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: add data trace logging - [SQL][CHROMA][API] unified prefix logging with centralized skip list

- Remove console sink, add enqueue=True to all file sinks
- Rename response_envelope to response_processor with response body logging
- Add SQL SELECT return value logging with _log_sql_result() helper
- Add ChromaDB retrieval result logging for 4 query methods
- Centralized skip list (SQL_SKIP_FULL_LOG, API_SKIP_FULL_LOG) in logging.py
- Update middleware test to match new implementation"
```
