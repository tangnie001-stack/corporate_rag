"""联网确认询问 — 经 clarify_channel 询问用户是否联网搜索补充缺失年份（原 verify_node.py 迁移）。

依赖 wait_with_abort_and_timeout（src.agents.tools.ask_tools）与进程级 pending_asks
（POST /clarify-answer 是独立请求，contextvar 不可达，经模块级注册表路由）。
"""

import asyncio

from src.agents.graph.state import AgentState
from src.agents.tools.ask_tools import wait_with_abort_and_timeout
from src.config.const import ASK_USER_TIMEOUT, MAX_VERIFY_ASK_PER_TURN
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx, pending_asks


async def _ask_web_confirm(state: AgentState, missing_years: list[int]) -> bool:
    """经 clarify_channel 询问用户是否联网，返回确认结果（async，独立计数）。

    Args:
        state: 当前图状态（读 session_id）
        missing_years: 缺失年份列表

    Returns:
        True 用户确认联网；False 拒绝/超时/槽被占（按"未确认"处理）
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return False
    # 独立计数：不计入 MAX_ASK_PER_TURN（LLM 澄清额度），每轮最多询问 1 次
    if ctx.verify_ask_count >= MAX_VERIFY_ASK_PER_TURN:
        return False
    # 单槽保护：LLM 澄清 ask_user 已挂起时放弃询问，避免覆盖其 Future
    if state.session_id in pending_asks:
        return False
    ctx.verify_ask_count += 1
    core_logging.log_event(Event.WEB_CONFIRM_ASK, missing=missing_years)
    payload = {
        "type": "ask_user",
        "questions": [
            {
                "id": "web_confirm",
                "question": (
                    f"知识库仅覆盖部分年份，缺失 {missing_years}，是否需要联网搜索补充？"
                ),
                "dimension": "free",
                "options": ["需要", "不需要"],
                "multi_select": False,
            }
        ],
    }
    loop = asyncio.get_running_loop()  # async 节点内禁止 run_until_complete
    fut = loop.create_future()
    pending_asks[state.session_id] = fut
    try:
        await ctx.clarify_channel.put(payload)
        answers = await wait_with_abort_and_timeout(
            fut, ctx.abort_signal, ASK_USER_TIMEOUT
        )
    finally:
        pending_asks.pop(state.session_id, None)
        fut.cancel()
    if not isinstance(answers, list) or not answers:
        return False
    first = answers[0]
    if not isinstance(first, dict):
        return False
    # 前端答案 selected 为数组（clarify.py 按数组消费）；对数组归一化判断，避免 list/str 恒不相等
    selected = first.get("selected") or []
    if isinstance(selected, list):
        return any(str(s) in ("需要", "需要联网") for s in selected)
    return False
