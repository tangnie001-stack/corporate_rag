"""摘要段注入的单测：位置、让位声明、聚合预算上限、无摘要时零影响。"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.graph import agent_node
from src.agents.graph.agent_node import (
    _SUMMARY_TAG,
    _compose_summary_message,
    _split_initial_messages,
)
from src.agents.graph.state import AgentState
from src.config.const import HISTORY_TOKEN_BUDGET
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.token_count import count_tokens


def test_compose_summary_message_has_tag_and_yield_clause():
    """摘要段带标签与让位声明（事实以本轮检索为准 + 不确定时请用户复述）。"""
    message = _compose_summary_message("## 用户目标\n看年报")
    assert isinstance(message, HumanMessage)
    assert _SUMMARY_TAG in message.content
    assert "以本轮检索结果为准" in message.content
    assert "请用户复述" in message.content


def test_compose_summary_message_strips_citation_numbers():
    """注入前剥离 [数字] 编号（防 format 阶段映射串号）。"""
    message = _compose_summary_message("见 [1] 与 [2]")
    assert "[1]" not in message.content
    assert "[2]" not in message.content


def test_summary_message_inserted_after_last_system_message():
    """摘要段插到最后一个 SystemMessage 之后、普通历史之前。"""
    messages = [
        SystemMessage(content="SYS-1"),
        SystemMessage(content="SYS-2"),
        HumanMessage(content="旧问题"),
        HumanMessage(content="当前问题"),
    ]
    out = agent_node._insert_after_last_system(
        messages, [_compose_summary_message("摘要")]
    )
    assert isinstance(out[2], HumanMessage)
    assert _SUMMARY_TAG in out[2].content
    assert out[3].content == "旧问题"


def _call_initial_messages(state: AgentState):
    """设置 RequestContext 后调用 `_split_initial_messages`，返回 (system, 非 system) 两半。"""
    pm = MagicMock()
    pm.get_user_template.side_effect = lambda context="", query="": f"Q:{query}"

    ctx = RequestContext(session_id="s1")
    ctx.known_skill_names = set()
    ctx.persona = ""
    ctx.has_skills = False
    token = current_request_ctx.set(ctx)
    try:
        return _split_initial_messages(state, pm, frozenset())
    finally:
        current_request_ctx.reset(token)


def test_seed_point_injects_summary_into_non_system_half():
    """真实 seed 点：摘要段进非 system 半段（带标签 + 让位声明），不进 system 半段且先于普通历史。"""
    state = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="当前问题",
        _history=[
            ChatMessage(role="user", content="旧问题"),
            ChatMessage(role="assistant", content="旧回答"),
        ],
        _summary="## 用户目标\n看年报",
    )
    system_half, rest_half = _call_initial_messages(state)

    summary_msgs = [
        m
        for m in rest_half
        if isinstance(m, HumanMessage) and _SUMMARY_TAG in m.content
    ]
    assert len(summary_msgs) == 1
    assert "以本轮检索结果为准" in summary_msgs[0].content
    assert "请用户复述" in summary_msgs[0].content
    # 摘要段只出现在非 system 半段，绝不在 system 半段
    assert not any(_SUMMARY_TAG in m.content for m in system_half)
    # 排在普通历史之前
    old_idx = next(i for i, m in enumerate(rest_half) if m.content == "旧问题")
    assert rest_half.index(summary_msgs[0]) < old_idx


def test_seed_point_no_summary_is_zero_impact():
    """`_summary` 为空串 ⇒ 返回结果与无摘要基线逐条一致（零影响）。"""
    history = [
        ChatMessage(role="user", content="旧问题"),
        ChatMessage(role="assistant", content="旧回答"),
    ]
    baseline = AgentState(
        session_id="s1", kb_id="kb1", query="当前问题", _history=list(history)
    )
    empty_summary = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="当前问题",
        _history=list(history),
        _summary="",
    )

    base_sys, base_rest = _call_initial_messages(baseline)
    empty_sys, empty_rest = _call_initial_messages(empty_summary)

    assert [(type(m).__name__, m.content) for m in base_sys] == [
        (type(m).__name__, m.content) for m in empty_sys
    ]
    assert [(type(m).__name__, m.content) for m in base_rest] == [
        (type(m).__name__, m.content) for m in empty_rest
    ]
    assert not any(_SUMMARY_TAG in m.content for m in empty_rest)


def _degraded_count_tokens(text: str) -> int:
    """encoder 不可用时的降级口径（与 token_count 一致）：len//2，非空至少 1。

    单个汉字 `len("摘") // 2 == 0`，正是旧实现「永不截断、静默突破预算」的成因；
    本替身用于显式复现该口径。
    """
    if not text:
        return 0
    return max(1, len(text) // 2)


@pytest.mark.parametrize(
    "counter",
    [count_tokens, _degraded_count_tokens],
    ids=["real", "degraded"],
)
def test_summary_and_tail_fit_budget(monkeypatch, counter):
    """聚合预算：摘要与尾部合计超 `HISTORY_TOKEN_BUDGET` 时摘要被真实缩短（两口径都断言）。"""
    from src.agents.graph.agent_node import _fit_summary_to_budget

    monkeypatch.setattr(agent_node, "count_tokens", counter)

    tail = [HumanMessage(content="尾" * 3000)]
    # 摘要远大于「预算 − 尾部 − 固定开销」的余量，两种口径下都必须触发截断
    # （否则该用例恒真/恒假、测不到东西）
    over = "摘" * 40000
    fitted = _fit_summary_to_budget(over, tail)

    tail_tokens = sum(counter(str(m.content)) for m in tail)
    # 缩短确实发生（真口径与降级口径下都成立）
    assert len(fitted) < len(over)
    # 摘要 + 尾部仍不超预算（固定开销已从 allowance 中扣除）
    assert counter(fitted) + tail_tokens <= HISTORY_TOKEN_BUDGET
