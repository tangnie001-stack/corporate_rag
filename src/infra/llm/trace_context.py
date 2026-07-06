"""请求级上下文变量 — 通过 contextvars 实现 per-request 数据传递。

提供 current_trace_id 和 current_user_id 两个 ContextVar，
分别在 TraceID 中间件和 Auth 中间件中设置，供 LangfuseTracer、
日志过滤等下游模块自动读取，无需显式传参。
"""

from contextvars import ContextVar

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
