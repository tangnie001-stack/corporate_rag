"""输出护栏校验器 — 联网引用标注引导（态 A）；KB 强制溯源（态 B）。

态 A web_citation_guard：保险丝用 verify 修订计数判断（state._verify_regenerations
< MAX_VERIFY_REGENERATIONS），调过 search_web 但答案无 [n] 引用且未达上限时注入一次
标注指引驱动重生成。
态 B kb_citation_guardrail：检索到 KB context 但答案无 [n] 且非拒答/知识库未覆盖时注入
一次 KB 溯源指引驱动重生成。只复位主循环预算（_agent_iterations=0），不占 verify 修订
保险丝（与完整性决策轮计数独立）；已引导过仍无引用则不强灌第二次。
"""

import re

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.config.const import (
    MAX_VERIFY_REGENERATIONS,
    VERIFY_CITATION_MARKER,
    VERIFY_KB_CITATION_MARKER,
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
    if ctx is not None:
        # regen 轮同步归零 search_web 请求级配额（web_count）：ctx.web_count 跨 verify
        # regen 段累积会让 regen 轮的 search_web 达限返回 WEB_SEARCH_LIMIT_TEXT 不执行，
        # 与 _agent_iterations=0 同属"每段 regen 轮全新主循环预算"设计。
        ctx.web_count = 0
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
        "_verify_regenerations": state._verify_regenerations + 1,
        "_agent_iterations": 0,  # regen 轮复位主循环预算，route_agent 不吞本轮的 [n] 补标工具调用
    }


def _has_kb_context(ctx) -> bool:
    """判断本轮是否检索了知识库（tool_contexts 含 kind=kb）。

    Args:
        ctx: 当前请求上下文（可能为 None）

    Returns:
        True 存在 kind=kb 的检索上下文
    """
    if ctx is None:
        return False
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_KB
        for c in ctx.tool_contexts
    )


def _is_abstention_or_kb_uncovered(answer: str) -> bool:
    """判断回答是否拒答/表达知识库未覆盖（此时无 KB 事实可标引用，不强灌）。

    Args:
        answer: 答案文本

    Returns:
        True 命中拒答或知识库未覆盖措辞
    """
    markers = ("未在文档中找到", "知识库未覆盖", "不在当前知识库范围")
    return any(m in answer for m in markers)


async def kb_citation_guardrail(
    state: AgentState, ctx: RequestContext | None
) -> dict | None:
    """态 B KB 答案强制溯源：有 kb context 但答案无 [n] → 注入指引重生成一次。

    排除拒答/知识库未覆盖（答案无 KB 事实可标引用）与已引导过（防与模型"判断无关"
    冲突，不重复灌第二次）。不占 verify 修订保险丝：本护栏靠 already_guided 至多触发
    一次，共享保险丝会饿死完整性决策轮的重生成额度，两条 regen 路径计数保持独立。

    Args:
        state: 当前图状态
        ctx: 请求上下文

    Returns:
        None 通过（无 kb context / 已带引用 / 拒答或未覆盖 / 已引导过）；
        注入指引的 regen 决策 dict（含 _agent_iterations=0 复位主循环预算）
    """
    answer = state.answer or ""
    if (
        not _has_kb_context(ctx)
        or _answer_has_citation(answer)
        or _is_abstention_or_kb_uncovered(answer)
    ):
        return None
    already_guided = any(
        isinstance(m, SystemMessage) and VERIFY_KB_CITATION_MARKER in (m.content or "")
        for m in state.messages
    )
    if already_guided:
        # 已引导过仍无引用：不强灌第二次（避免与模型"判断无关"冲突）
        return None
    logger.info(
        "verify kb-citation guide session_id={} answer_len={}",
        state.session_id,
        len(answer),
    )
    guidance = SystemMessage(
        content=(
            f"你刚才的回答基于知识库检索结果，但没有标注来源编号，"
            f"{VERIFY_KB_CITATION_MARKER}，请在引用来源的对应句末补上 [n] 编号"
            "（编号须与检索返回的来源列表一致）后重新回答。"
        )
    )
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
        "_agent_iterations": 0,  # regen 轮复位主循环预算，route_agent 不吞本轮补标
    }
