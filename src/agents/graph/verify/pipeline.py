"""校验器管道执行器 — 遍历校验器列表，短路返回首个决策。"""

from collections.abc import Awaitable, Callable

from src.agents.graph.state import AgentState
from src.infra.llm.request_context import RequestContext

# 校验器协议：async (state, ctx) -> dict | None
#   None = 通过，继续下一校验器
#   dict = 决策（含 _needs_regenerate / messages / answer / _unsupported 等），短路返回
VerifyCheck = Callable[[AgentState, RequestContext | None], Awaitable[dict | None]]


async def run_pipeline(
    pipeline: list[VerifyCheck], state: AgentState, ctx: RequestContext | None
) -> dict:
    """遍历管道，首个返回非 None 的校验器短路。

    Args:
        pipeline: 校验器列表（有序）
        state: 当前图状态
        ctx: 请求上下文（可能 None）

    Returns:
        校验决策 dict；全部通过时返回直通决策 {"answer": ..., "_needs_regenerate": False}
    """
    for check in pipeline:
        decision = await check(state, ctx)
        if decision is not None:
            return decision
    return {"answer": state.answer or "", "_needs_regenerate": False}
