"""直出轮确认门（design D18）——规则检测子代理的"需确认"信号并复用澄清链路问用户。

fork 子代理不持有 ask_user（FORK_FORBIDDEN_TOOLS 硬保证），故"需确认"由正文
marker 表达；本模块只做规则检测与"问一句"，编排（是否重跑、如何标注）由
`skill_direct` 节点负责，保证本模块可纯测。
"""

import asyncio

from src.agents.tools.ask_tools import wait_with_abort_and_timeout
from src.config.const import (
    ASK_USER_TIMEOUT,
    FORK_CONFIRM_MARKER,
    SSEInteractionTexts,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx, pending_asks


def detect_confirm_request(answer: str) -> str:
    """检测子代理返回的"需确认"信号。

    Args:
        answer: 子代理聚合文本

    Returns:
        命中行首 marker 时返回其后的提问文本（去空白）；未命中返回空串
    """
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(FORK_CONFIRM_MARKER):
            return stripped[len(FORK_CONFIRM_MARKER) :].strip()
    return ""


async def ask_confirm_question(question: str, session_id: str) -> str | None:
    """经澄清链路向用户提问并等待答复。

    Args:
        question: 子代理提出的确认问题
        session_id: 会话 ID（pending_asks 单槽键）

    Returns:
        用户的答复文本；ctx 缺失 / 超时 / 澄清槽被占 / 答复不可解析 → None
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return None
    if session_id in pending_asks:
        # 单槽保护：LLM 澄清或联网确认已挂起时放弃本次确认（按未确认处理）
        return None
    core_logging.log_event(Event.FORK_CONFIRM_ASKED, session_id=session_id)
    payload = {
        "type": "ask_user",
        "questions": [
            {
                "id": "fork_confirm",
                "question": SSEInteractionTexts.CONFIRM_QUESTION_TMPL.format(
                    question=question
                ),
                "dimension": "free",
                "options": [],
                "multi_select": False,
            }
        ],
    }
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_asks[session_id] = fut
    try:
        await ctx.clarify_channel.put(payload)
        answers = await wait_with_abort_and_timeout(
            fut, ctx.abort_signal, ASK_USER_TIMEOUT
        )
    finally:
        pending_asks.pop(session_id, None)
        fut.cancel()
    # 请求取消（abort）时 wait_with_abort_and_timeout 抛 CancelledError，**必须原样透传**
    # （不要 catch），由直出/委派的取消路径收尾；超时则返回文案哨兵，在此按"未确认"处理
    # ——与 ask_confirm._ask_web_confirm 的判定口径保持一致。
    if not isinstance(answers, list) or not answers:
        core_logging.log_event(
            Event.FORK_CONFIRM_UNCONFIRMED, session_id=session_id, reason="no_answer"
        )
        return None
    first = answers[0]
    if not isinstance(first, dict):
        return None
    text = first.get("text")
    if not isinstance(text, str):
        text = ""
    if not text.strip():
        # clarify.py 的既有消费形状是 selected 数组（自由问答也归一化到该字段）
        selected = first.get("selected")
        if isinstance(selected, list) and selected:
            text = " ".join(str(item) for item in selected)
    if not text.strip():
        core_logging.log_event(
            Event.FORK_CONFIRM_UNCONFIRMED, session_id=session_id, reason="empty"
        )
        return None
    return text.strip()
