"""请求级上下文变量 — 通过 contextvars 实现 per-request 数据传递。

提供 current_trace_id、current_user_id、current_session_id、current_channel
四个 ContextVar，分别在 TraceID 中间件、Auth 中间件、各渠道入口
（站点 API / 企微 handler）与 chat 生成任务入口中设置，供下游模块自动读取，
无需显式传参。

current_channel 取值域见 `src/config/const.py` 的 `Channel`；未设置时为空串，
日志 patcher 会以 `CHANNEL_DEFAULT`（`none`）占位。
"""

from contextvars import ContextVar

current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
current_channel: ContextVar[str] = ContextVar("current_channel", default="")
