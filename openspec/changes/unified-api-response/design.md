## Context

当前 API 响应格式不统一：成功返回裸数据，失败返回 `{"detail": "..."}`（FastAPI 默认）或 `{"message": "..."}`（自定义）。前端需依赖 HTTP 状态码 + 字符串匹配判断错误类型。

## Goals / Non-Goals

**Goals:**
- 所有非 SSE API 端点返回统一 `{"code", "message", "data"}` 信封
- 前端统一用 `body.code` 判断业务结果
- 机器可读的错误码，前端按 code 做分支处理（如 `AUTH_REQUIRED` 跳登录）
- 健康检查 `/api/health` 保持原格式（外部基础设施使用）

**Non-Goals:**
- 不改 SSE 流式端点（`/api/chat/stream`）
- 不改后端业务逻辑，只改响应包装方式
- 不引入新的 API 版本号

## Decisions

1. **中间件包装，非路由层**：用 `ResponseEnvelopeMiddleware` 统一包裹所有路由返回。路由照旧返回裸 dict/Model，中间件自动加信封。无需改每个路由方法。
2. **自定义异常 + 中间件捕获**：`ApiError(code, message, status)` 由中间件拦截后格式化为错误信封。路由/中间件直接 raise 即可。
3. **auth 中间件直接用 JSONResponse**：`app.middleware("http")` 不在 BaseHTTPMiddleware 链内，raise 的异常不被 ResponseEnvelopeMiddleware 捕获，所以 auth 中间件直接 `return JSONResponse(...)` 带信封。
4. **异常处理器兜底**：FastAPI 内置的 `HTTPException`（404/405）和 `RequestValidationError`（422）注册 `@app.exception_handler` 直接返回信封格式。
5. **前端 `apiRequest()` 统一解信封**：核心辅助函数统一解析 `body.code`，业务代码保持使用 `apiRequest` 即可。
6. **`doc_count` 用 SQL LEFT JOIN**：不再硬编码 0，改为 `LEFT JOIN document` + `COUNT`。

## Risks / Trade-offs

- `response.body` 在 BaseHTTPMiddleware 的 `call_next` 返回的是 `_StreamingResponse`，需要通过 `async for chunk in response.body_iterator` 读取，不能直接 `response.body`。
- `app.middleware("http")` 注册的中间件（auth）抛出异常不走 `dispatch()` 的 try/except，需要单独处理。
