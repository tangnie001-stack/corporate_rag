"""输出护栏校验器 — 联网引用标注引导（态 A）；KB 强制溯源在态 B 接入。

态 A 保险丝用 verify 修订计数判断（state._verify_regenerations < MAX_VERIFY_REGENERATIONS）：
调过 search_web 但答案无 [n] 引用且未达保险丝上限时，注入一次标注指引驱动重生成。
"""

import re

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.config.const import (
    MAX_VERIFY_REGENERATIONS,
    VERIFY_CITATION_MARKER,
    SSEInteractionTexts,
)
from src.infra.llm.request_context import RequestContext


def _answer_has_citation(answer: str) -> bool:
    """判断回答文本是否含 [n] 引用标记（format_node 依赖 [n] 提取 citations）。"""
    return re.search(r"\[\d+\]", answer) is not None


def _has_web_context(ctx) -> bool:
    """判断本轮是否调用了 search_web 并拿到了联网上下文（tool_contexts 含 kind=web）。

    Args:
        ctx: 当前请求上下文（可能为 None）

    Returns:
        True 存在 kind=web 的检索上下文
    """
    if ctx is None:
        return False
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_WEB
        for c in ctx.tool_contexts
    )


def _citation_guidance_already_injected(state: AgentState) -> bool:
    """查重：联网引用标注指引 SystemMessage 是否已注入过。

    Args:
        state: 当前图状态（遍历 messages 找含 VERIFY_CITATION_MARKER 的 SystemMessage）

    Returns:
        True 已注入过（避免每轮重复追加同一条指引）
    """
    return any(
        isinstance(m, SystemMessage) and VERIFY_CITATION_MARKER in (m.content or "")
        for m in state.messages
    )


async def web_citation_guard(
    state: AgentState, ctx: RequestContext | None
) -> dict | None:
    """态 A 联网引用引导：调过 search_web 但答案无 [n] → 注入指引重生成一次。

    Args:
        state: 当前图状态
        ctx: 请求上下文

    Returns:
        None 通过（未联网/已带引用/已达保险丝上限/已引导过）；
        注入指引的 regen 决策 dict
    """
    answer = state.answer or ""
    if (
        not _has_web_context(ctx)
        or _answer_has_citation(answer)
        or state._verify_regenerations >= MAX_VERIFY_REGENERATIONS
        or _citation_guidance_already_injected(state)
    ):
        return None
    logger.info(
        "verify web-citation guide session_id={} answer_len={}",
        state.session_id,
        len(answer),
    )
    guidance = SystemMessage(
        content=(
            f"你刚才的回答引用了联网搜索结果，但没有标注来源编号，"
            f"{VERIFY_CITATION_MARKER}，请在引用来源的对应句末补上 [n] 编号"
            "（编号须与搜索结果返回的来源列表一致）后重新回答。"
        )
    )
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
        "_verify_regenerations": state._verify_regenerations + 1,
    }
