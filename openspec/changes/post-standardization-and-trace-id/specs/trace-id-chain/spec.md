## ADDED Requirements

### Requirement: Trace ID 全链路传递
系统 SHALL 提供全链路 trace ID，从前端到后端到 Langfuse 保持统一 ID，支持请求粒度的日志关联。

#### Scenario: 常规请求带 X-Trace-ID 请求头
- **WHEN** 前端发起 API 请求并设置 `X-Trace-ID` 请求头
- **THEN** 后端提取该值作为 trace_id，并在响应头 `X-Trace-ID` 中回传

#### Scenario: 前端未传 trace_id 时后端自动生成
- **WHEN** 前端发起 API 请求且不含 `X-Trace-ID` 请求头
- **THEN** 后端自动生成 UUID 作为 trace_id，并在响应头中回传

#### Scenario: SSE 请求通过 query 参数传 trace_id
- **WHEN** 前端建立 SSE 连接且 URL 中包含 `?trace_id=xxx`
- **THEN** 后端从 query 参数提取 trace_id，按正常流程处理

#### Scenario: contextvar 透传到 Langfuse
- **WHEN** 后端处理请求过程中调用 `LangfuseTracer.start_trace()`
- **THEN** 创建的 Langfuse trace 的 id 等于 HTTP 请求的 trace_id

#### Scenario: contextvar 透传到日志
- **WHEN** 后端业务代码输出日志
- **THEN** 日志中应当包含当前请求的 trace_id 字段，便于 grep 聚合

### Requirement: User ID 通过 contextvar 传递
系统 SHALL 通过 contextvars 传递当前请求的用户 ID，路由层无需从 `request.state` 手动读取。

#### Scenario: Auth 中间件设置 contextvar
- **WHEN** auth middleware 完成用户身份识别
- **THEN** 将 `user_id` 同时写入 `request.state` 和 contextvar

#### Scenario: 路由层直接读取 contextvar
- **WHEN** 路由 handler 需要当前用户 ID
- **THEN** 可通过 `current_user_id.get()` 获取，无需 `getattr(request.state, "user_id", "")`
