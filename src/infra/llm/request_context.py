"""Per-request 上下文 — 通过 contextvar 传递给 agent 循环内的工具与节点。

工具闭包在 AgentService 初始化时构建、跨请求共享，无法持有 per-request 对象；
graph 在同一 asyncio task 执行，contextvar 自动传播到工具与 async 节点。
因此用 ContextVar 承载单次 /chat/stream 请求的共享对象集合（RequestContext），
请求入口 set、请求结束 reset，工具/节点经 current_request_ctx.get() 读取。

注意：工具收集的上下文统一挂在 current_request_ctx.get().tool_contexts 上
（retrieve_kb 与 search_web 共享，全局递增编号），
不单独定义独立的 current_tool_contexts ContextVar —— ContextVar(default=list())
的 default 在模块加载时求值一次，所有请求会共享同一个 list（并发污染陷阱）。
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field

from src.rag.context import RAGContext


@dataclass
class RequestContext:
    """单次 /chat/stream 请求的共享对象集合，由请求入口创建并 set 到 current_request_ctx。"""

    session_id: str  # 会话 ID（请求/URL 参数来源），范围：整个请求生命周期，用途：路由到 registry 挂起澄清
    clarify_channel: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )  # 澄清事件/SSE 事件通道，范围：请求内共享，用途：工具请求澄清时投递事件
    abort_signal: asyncio.Event = field(
        default_factory=asyncio.Event
    )  # 断连/取消信号，范围：请求内共享，用途：客户端断开或取消时置位以中断 agent 循环
    registry: dict = field(
        default_factory=dict
    )  # [已弃用] session_id -> asyncio.Future（挂起澄清），范围：请求内共享；POST/SSE 是独立请求，contextvar 不跨请求，请改用模块级 pending_asks（保留字段避免破坏既有引用）
    tool_contexts: list[RAGContext] = field(
        default_factory=list
    )  # retrieve_kb / search_web 共享累积上下文，范围：请求内累积，用途：按编号顺序拼装引用（编号顺序即引用顺序）
    ask_count: int = (
        0  # 澄清提问计数，范围：请求内累积，用途：限制单次请求的澄清轮数上限
    )
    web_count: int = (
        0  # search_web 调用计数，范围：请求内累积，用途：限制单轮联网搜索次数上限
    )


current_request_ctx: ContextVar[RequestContext | None] = ContextVar(
    "current_request_ctx", default=None
)
"""当前请求共享对象；工具/节点经此读取 queue/abort/tool_contexts/ask_count/web_count。"""

pending_asks: dict[str, asyncio.Future] = {}
"""进程级挂起澄清注册表（session_id -> asyncio.Future）。

ask_user 工具登记挂起 Future，POST /clarify-answer 按 session_id 解析；
与 RequestContext.registry 不同，它是进程级共享的——POST 与 SSE 是独立
HTTP 请求，contextvar 不跨请求传播，per-request dict 无法被 POST 端访问。
"""
