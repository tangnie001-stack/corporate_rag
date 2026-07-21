## Why

当前项目 API 同时使用 GET / POST / DELETE 三种 HTTP 方法，前端对接时需区分方法语义，且部分中间件/代理对非 POST 方法支持不统一。同时请求链中缺少统一的 trace ID，导致问题排查时无法跨前端-后端-Langfuse 串联日志。

## What Changes

- **BREAKING**: 所有非 SSE、非健康检查的接口改为 POST 方法，路径参数移入请求 body
- 列表接口统一 `List` 后缀，删除接口统一 `Delete` 后缀
- 新增 TraceID 中间件，在请求入口生成/提取 trace_id，通过 `contextvars` 注入全链路
- `user_id` 也通过 `contextvars` 传递，减少各路由 `getattr(request.state, ...)` 重复代码
- 前端 `api.js` 自动生成 trace_id 并注入 `X-Trace-ID` 请求头
- SSE 接口通过 `?trace_id=xxx` query 参数传递 trace_id
- Langfuse trace 复用 HTTP trace_id，实现前端到 Langfuse 的全链路贯通
- 更新接口契约文档 `docs/api-contract.md` 和 `docs/api_contract.md`

## Capabilities

### New Capabilities
- `trace-id-chain`: 全链路 trace ID 生成与传递，支持前端→后端→Langfuse 统一追踪
- `api-post-standardization`: API 接口统一 POST 方法的规范与实现

### Modified Capabilities
<!-- 不涉及已有 spec 的能力变化，仅实现层改造 -->

## Impact

- **后端路由**: `auth.py`, `knowledge_base.py`, `documents.py`, `sessions.py` — 方法声明、路径、参数模型全部重构
- **中间件**: 新增 `trace_id.py`，修改 `auth.py`（+1 行 contextvar 设置）
- **基础设施**: 新增 `trace_context.py`（contextvars），修改 `langfuse_tracing.py`（读取外部 trace_id）
- **应用入口**: `main.py` 注册新中间件
- **前端**: `api.js` 改方法/路径/body，`chat.js` SSE URL 加 trace_id，`login.html` login 改为 JSON body
- **文档**: 两份接口契约文档全面更新
