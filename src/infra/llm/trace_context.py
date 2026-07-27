"""请求级上下文变量 — 通过 contextvars 实现 per-request 数据传递。

提供 current_trace_id、current_user_id、current_tracer 三个 ContextVar，
分别在 TraceID 中间件、Auth 中间件、traced 装饰器中设置，供下游模块
自动读取，无需显式传参。
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.llm.langfuse_tracing import LangfuseTracer

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_tracer: ContextVar["LangfuseTracer | None"] = ContextVar("current_tracer", default=None)
