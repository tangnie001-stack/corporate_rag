## Context

项目为纯静态前端（HTML + JS + Tailwind CSS）配合 FastAPI 后端。当前 API 接口混合使用 GET / POST / DELETE 方法，且请求链中缺少统一的 trace ID。前端通过原生 `fetch` 和 `EventSource`（SSE）与后端通信，认证走 Cookie。Langfuse 已集成但 trace ID 仅在 RAGChain 内部生成，与 HTTP 请求隔离。

## Goals / Non-Goals

**Goals:**
- 所有非 SSE、非健康检查接口改为 POST，路径参数统一放入请求 body
- 每个带参数的端点独立 Pydantic RequestBody 类，类型安全
- 全链路 trace ID：前端生成 → HTTP header/query → 后端中间件 → contextvar → Langfuse / 日志
- user_id 同样通过 contextvar 传递，路由层无需 `getattr(request.state, ...)`
- 更新接口契约文档

**Non-Goals:**
- 不涉及业务逻辑变更
- 不改变现有响应格式（仍为 code/message/data）
- 不引入新的前端框架或依赖

## Decisions

### 1. 路径设计：列表用 `List` 后缀，删除用 `Delete` 后缀
- 避免 POST `/api/kbs` 同时承担 list 和 create 两种语义
- 删除操作 ID 通过 body 传递，不再放在 URL 路径中

### 2. 请求体模型：每个端点独立 Pydantic class
- 替代方案（通用大 class）会导致类型不安全、自动文档不精确
- 独立 class 每端点多 3-5 行，但 schema 精确、互不影响

### 3. Trace ID 传递：contextvars 而非显式传参
- 替代方案（request 传参）需要 LangfuseTracer 持有 request 引用，侵入业务代码
- contextvar 在 asyncio 中 per-request 隔离，业务代码零改动
- 同时用 `request.state` 保留一份，给中间件和异常处理场景用

### 4. Trace ID 中间件位置：CORS 之后、ResponseEnvelope 之前
- 最早生成 trace_id，后续所有中间件和路由都能访问
- 响应头在 `call_next` 返回后统一设置，覆盖正常/异常/SSE 全部场景

### 5. Login 接口：Form → JSON body
- 前端 `login.html` 配合改为 `Content-Type: application/json`
- 与项目其他 JSON 接口保持一致

### 6. Langfuse 集成：使用 SDK 的 `id` 参数
- `langfuse.trace(id=trace_id)` 显式接受自定义 ID，已通过 SDK signature 验证

## Risks / Trade-offs

- **contextvar 在后台任务中的行为**：`asyncio.create_task()` 创建时继承当前上下文快照，`_process_document_task` 能正确拿到请求时的 trace_id — 预期行为
- **SSE 无法设自定义请求头**：`EventSource` 限制，通过 `?trace_id=xxx` query 参数传递，中间件统一从 header → query → uuid4 兜底处理
- **响应头在 auth middleware 快路径无效**：auth middleware 直接 `return JSONResponse(...)` 不走后续中间件，但 TraceID 中间件在最外层，`call_next` 返回后统一设 header 可覆盖此场景
