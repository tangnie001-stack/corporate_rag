"""历史里的注入标记行 → 独立 HumanMessage，位于 system 段之后、普通历史之前。"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.graph.agent_node import _initial_messages
from src.agents.graph.state import AgentState
from src.config.const import SKILL_INJECTION_PREFIX
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.request_context import RequestContext, current_request_ctx


def test_injected_history_becomes_separate_human_message(monkeypatch):
    """注入行抽成独立 HumanMessage 且排在普通历史之前；普通历史不受影响。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = "基础段"
    pm.get_system_prompt.return_value = "基础段"
    pm.get_user_template.return_value = "用户模板"

    state = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="腾讯2024",
        _history=[
            ChatMessage(role="user", content=SKILL_INJECTION_PREFIX + "\n方法论正文"),
            ChatMessage(role="user", content="上一轮问题"),
            ChatMessage(role="assistant", content="上一轮回答"),
        ],
    )
    ctx = RequestContext(session_id="s1")
    ctx.known_skill_names = set()
    ctx.persona = ""
    ctx.has_skills = False
    token = current_request_ctx.set(ctx)
    try:
        messages = _initial_messages(state, pm)
    finally:
        current_request_ctx.reset(token)

    types = [type(m) for m in messages]
    assert types.index(SystemMessage) == 0
    injected_idx = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, HumanMessage) and SKILL_INJECTION_PREFIX in m.content
    )
    prev_user_idx = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, HumanMessage) and m.content == "上一轮问题"
    )
    assert injected_idx < prev_user_idx  # 注入在普通历史之前
    injected_msg = messages[injected_idx]
    assert isinstance(injected_msg, HumanMessage)
    assert isinstance(injected_msg.content, str)
    assert injected_msg.content.startswith(SKILL_INJECTION_PREFIX)
    # 普通历史保持原样（AI 回复映射为 AIMessage）
    assert any(isinstance(m, AIMessage) for m in messages)
