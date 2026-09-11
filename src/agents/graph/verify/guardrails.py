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

from src.agents.graph.state import AgentState
from src.config.const import (
    EXPERT_ANALYSIS_MARKER,
    MAX_VERIFY_REGENERATIONS,
    VERIFY_CITATION_MARKER,
    VERIFY_KB_CITATION_MARKER,
    SSEInteractionTexts,
)
from src.config.prompts import (
    VERIFY_CITATION_GUIDANCE_PROMPT,
    VERIFY_KB_CITATION_GUIDANCE_PROMPT,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import RequestContext


def _answer_has_citation(answer: str) -> bool:
    """判断回答文本是否含 [n] 引用标记（format_node 依赖 [n] 提取 citations）。"""
    return re.search(r"\[\d+\]", answer) is not None


def _has_web_context(state: AgentState) -> bool:
    """判断本轮是否调用了 search_web 并拿到了联网上下文（state 材料含 kind=web）。

    Args:
        state: 当前图状态（材料来自 AgentState 承载的本轮材料池）

    Returns:
        True 存在 kind=web 的检索上下文
    """
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_WEB
        for c in state.tool_contexts
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

    判据材料（是否联网）读 AgentState 承载的本轮材料池；ctx 仅用于 regen 轮复位
    search_web 请求级配额（web_count）。

    Args:
        state: 当前图状态（材料来自 AgentState 承载的本轮材料池）
        ctx: 请求上下文（仅用于复位 web_count 配额）

    Returns:
        None 通过（未联网/已带引用/已达保险丝上限/已引导过）；
        注入指引的 regen 决策 dict
    """
    answer = state.answer or ""
    if (
        not _has_web_context(state)
        or _answer_has_citation(answer)
        or state._verify_regenerations >= MAX_VERIFY_REGENERATIONS
        or _citation_guidance_already_injected(state)
    ):
        return None
    core_logging.log_event(Event.CITATION_GUIDE, kind="web", answer_len=len(answer))
    guidance = SystemMessage(
        content=VERIFY_CITATION_GUIDANCE_PROMPT.format(marker=VERIFY_CITATION_MARKER)
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
        "_delegate_used": False,  # regen=全新 5 轮预算，不复位则 delegate 放宽 +2 会放大每段 regen 上限
    }


def _has_kb_context(state: AgentState) -> bool:
    """判断本轮是否检索了知识库（state 材料含 kind=kb）。

    Args:
        state: 当前图状态（材料来自 AgentState 承载的本轮材料池）

    Returns:
        True 存在 kind=kb 的检索上下文
    """
    return any(
        getattr(c, "kind", None) == SSEInteractionTexts.CITATION_KIND_KB
        for c in state.tool_contexts
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


async def kb_citation_guardrail(state: AgentState) -> dict | None:
    """态 B KB 答案强制溯源：有 kb context 但答案无 [n] → 注入指引重生成一次。

    判据材料（是否检索 KB）读 AgentState 承载的本轮材料池。排除拒答/知识库未覆盖
    （答案无 KB 事实可标引用）、专家分析观点（含 EXPERT_ANALYSIS_MARKER 的分析 fork
    答案视为不需溯源的观点表述，M7）与已引导过（防与模型"判断无关"冲突，不重复灌
    第二次）。不占 verify 修订保险丝：本护栏靠 already_guided 至多触发一次，共享保险丝
    会饿死完整性决策轮的重生成额度，两条 regen 路径计数保持独立。

    Args:
        state: 当前图状态（材料来自 AgentState 承载的本轮材料池）

    Returns:
        None 通过（无 kb context / 已带引用 / 拒答或未覆盖 / 专家分析观点 / 已引导过）；
        注入指引的 regen 决策 dict（含 _agent_iterations=0 复位主循环预算、
        _delegate_used=False 复位 delegate 放宽，防 +2 放大每段 regen 上限）
    """
    answer = state.answer or ""
    if (
        not _has_kb_context(state)
        or _answer_has_citation(answer)
        or _is_abstention_or_kb_uncovered(answer)
        or EXPERT_ANALYSIS_MARKER in answer  # 专家分析观点豁免（design D9 / M7）
    ):
        return None
    already_guided = any(
        isinstance(m, SystemMessage) and VERIFY_KB_CITATION_MARKER in (m.content or "")
        for m in state.messages
    )
    if already_guided:
        # 已引导过仍无引用：不强灌第二次（避免与模型"判断无关"冲突）
        return None
    core_logging.log_event(Event.CITATION_GUIDE, kind="kb", answer_len=len(answer))
    guidance = SystemMessage(
        content=VERIFY_KB_CITATION_GUIDANCE_PROMPT.format(
            marker=VERIFY_KB_CITATION_MARKER
        )
    )
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
        "_agent_iterations": 0,  # regen 轮复位主循环预算，route_agent 不吞本轮补标
        "_delegate_used": False,  # regen=全新 5 轮预算，不复位则 delegate 放宽 +2 会放大每段 regen 上限
    }
