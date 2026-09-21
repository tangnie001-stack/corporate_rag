"""`/xxx` 前缀读时清洗：历史与当前 query 在组装 prompt 时剥掉已注册前缀。"""

from unittest.mock import MagicMock

from src.agents.graph.agent_node import _initial_messages
from src.agents.graph.state import AgentState
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _call_initial_messages(state: AgentState, known: set[str]):
    """设置 RequestContext 后调用 _initial_messages，返回组装后的消息列表。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = "基础段"
    pm.get_user_template.side_effect = lambda context="", query="": f"Q:{query}"

    ctx = RequestContext(session_id="s1")
    ctx.known_skill_names = known
    ctx.persona = ""
    ctx.has_skills = False
    token = current_request_ctx.set(ctx)
    try:
        return _initial_messages(state, pm, frozenset())
    finally:
        current_request_ctx.reset(token)


def test_history_and_query_prefix_cleaned():
    """历史里的已注册前缀行与当前 query 都被剥掉前缀。"""
    state = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="/finance-analyst 腾讯2024",
        _history=[
            ChatMessage(role="user", content="/finance-analyst 旧任务"),
            ChatMessage(role="assistant", content="旧回答"),
        ],
    )
    messages = _call_initial_messages(state, {"finance-analyst"})

    contents = [m.content for m in messages]
    assert "Q:腾讯2024" in contents
    assert "旧任务" in contents
    assert "旧回答" in contents
    assert "/finance-analyst 旧任务" not in contents


def test_unknown_prefix_preserved():
    """不在 known_skill_names 里的 /ghost 前缀原样保留。"""
    state = AgentState(
        session_id="s1",
        kb_id="kb1",
        query="/ghost 任务",
        _history=[ChatMessage(role="user", content="/ghost xxx")],
    )
    messages = _call_initial_messages(state, {"finance-analyst"})

    contents = [m.content for m in messages]
    assert "Q:/ghost 任务" in contents
    assert "/ghost xxx" in contents
