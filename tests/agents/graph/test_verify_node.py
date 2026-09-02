"""测试验证循环节点 — extract_years / completeness_check / faithfulness_check。

faithfulness_check 的 judge LLM 通过 monkeypatch mock src.models.get_llm
（FakeJudgeLLM），不构造真实 RAGAS_LLM_MODEL，不发真实网络/API 调用。
"""

import asyncio
from contextvars import Token
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from src.agents.graph.state import AgentState
from src.agents.graph.verify_node import (
    _ask_web_confirm,
    completeness_check,
    extract_years,
    faithfulness_check,
    verify_node,
)
from src.config.const import MAX_VERIFY_ASK_PER_TURN
from src.infra.llm.request_context import (
    RequestContext,
    current_request_ctx,
    pending_asks,
)
from src.rag.context import RAGContext


def test_extract_years():
    """应提取答案中所有 4 位年份，无年份时返回空集合。"""
    assert extract_years("2024年营收 3943 亿，2023 年 3000 亿") == {2023, 2024}
    assert extract_years("近三年持续增长") == set()


def test_completeness_check():
    """应返回要求年份中答案未覆盖的缺失年份（升序）。"""
    assert completeness_check([2023, 2024, 2025], "2024年营收3943亿") == [2023, 2025]
    assert completeness_check([2024], "2024年营收3943亿") == []


class FakeJudgeLLM:
    """极简 fake judge LLM：ainvoke 返回固定 judge 输出（AIMessage）。

    返回 AIMessage 而非 dict，因为 faithfulness_check 通过
    getattr(resp, "content", None) 读取响应文本（属性访问）。
    """

    async def ainvoke(self, messages, **kwargs):
        """返回带固定 JSON 内容的 AIMessage，等价于真实模型响应。"""
        return AIMessage(content='{"unsupported": ["句X"]}')


def _make_contexts() -> list[RAGContext]:
    """构造一条含 content 的引用上下文。"""
    return [
        RAGContext(
            content="2024年营收3943亿",
            source="a.pdf",
            page=1,
            doc_id="d1",
            chunk_id="d1:0",
        )
    ]


@pytest.mark.asyncio
async def test_faithfulness_check_returns_unsupported(monkeypatch):
    """应返回 judge 标出的无支撑句子清单。"""
    monkeypatch.setattr("src.models.get_llm", lambda *args, **kwargs: FakeJudgeLLM())
    result = await faithfulness_check("答案", _make_contexts())
    assert result == ["句X"]


@pytest.mark.asyncio
async def test_faithfulness_check_empty_contexts(monkeypatch):
    """contexts 为空时应直接返回空清单，不调用 get_llm。"""

    def fail_if_called(*args, **kwargs):
        raise AssertionError("contexts 为空时不应构造 judge LLM")

    monkeypatch.setattr("src.models.get_llm", fail_if_called)
    result = await faithfulness_check("答案", [])
    assert result == []


# ── _ask_web_confirm ──


def _make_ctx(
    session_id: str = "s1", **overrides
) -> tuple[RequestContext, Token[RequestContext | None]]:
    """构造并 set 到 contextvar 的 RequestContext，返回 (ctx, token)。"""
    ctx = RequestContext(session_id=session_id, **overrides)
    token = current_request_ctx.set(ctx)
    return ctx, token


def _mock_wait(monkeypatch, return_value):
    """mock verify_node 模块内的 wait_with_abort_and_timeout，避免真实等待。"""
    monkeypatch.setattr(
        "src.agents.graph.verify_node.wait_with_abort_and_timeout",
        AsyncMock(return_value=return_value),
    )


@pytest.mark.asyncio
async def test_ask_web_confirm_confirmed(monkeypatch):
    """用户选择"需要"且未超限/槽空 → 返回 True，独立计数自增。"""
    ctx, token = _make_ctx()
    try:
        _mock_wait(monkeypatch, [{"id": "web_confirm", "selected": ["需要"]}])
        result = await _ask_web_confirm(AgentState(session_id="s1"), [2023, 2025])
        assert result is True
        assert ctx.verify_ask_count == 1
        # 问题已推送进 clarify_channel，且挂起槽已清理
        payload = ctx.clarify_channel.get_nowait()
        assert payload["type"] == "ask_user"
        assert payload["questions"][0]["id"] == "web_confirm"
        assert "s1" not in pending_asks
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_ask_web_confirm_selected_list_confirmed(monkeypatch):
    """前端答案 selected 为数组且含"需要" → 返回 True（真实答案形状回归，防 list/str 失配）。"""
    _ctx, token = _make_ctx()
    try:
        _mock_wait(
            monkeypatch,
            [{"id": "web_confirm", "selected": ["需要"]}],
        )
        result = await _ask_web_confirm(AgentState(session_id="s1"), [2023])
        assert result is True
        assert "s1" not in pending_asks
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ret",
    ["超时文本", [], [{"id": "web_confirm", "selected": ["不需要"]}]],
)
async def test_ask_web_confirm_not_confirmed(monkeypatch, ret):
    """答案非列表/空列表/明确拒绝 → 按"未确认"返回 False。"""
    _ctx, token = _make_ctx()
    try:
        _mock_wait(monkeypatch, ret)
        result = await _ask_web_confirm(AgentState(session_id="s1"), [2023])
        assert result is False
        assert "s1" not in pending_asks
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_ask_web_confirm_count_limit(monkeypatch):
    """verify_ask_count 达每轮上限 → 直接返回 False，不推送问题。"""
    ctx, token = _make_ctx(verify_ask_count=MAX_VERIFY_ASK_PER_TURN)
    try:
        _mock_wait(monkeypatch, [{"id": "web_confirm", "selected": ["需要"]}])
        result = await _ask_web_confirm(AgentState(session_id="s1"), [2023])
        assert result is False
        assert ctx.verify_ask_count == MAX_VERIFY_ASK_PER_TURN  # 计数未再自增
        assert ctx.clarify_channel.empty()  # 超限后未推送问题
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_ask_web_confirm_slot_occupied(monkeypatch):
    """pending_asks 已被 LLM 澄清 ask_user 占用 → 放弃询问返回 False。"""
    fut = asyncio.get_running_loop().create_future()
    pending_asks["s1"] = fut
    ctx, token = _make_ctx()
    try:
        _mock_wait(monkeypatch, [{"id": "web_confirm", "selected": ["需要"]}])
        result = await _ask_web_confirm(AgentState(session_id="s1"), [2023])
        assert result is False
        assert ctx.verify_ask_count == 0  # 槽被占不计次
    finally:
        pending_asks.pop("s1", None)
        fut.cancel()
        current_request_ctx.reset(token)


# ── verify_node ──


def _make_state(
    answer: str = "",
    resolved: list[str] | None = None,
    iterations: int = 0,
    max_iter: int = 5,
) -> AgentState:
    """构造 verify_node 测试用 AgentState。"""
    return AgentState(
        answer=answer,
        _resolved_kb_ids=resolved,
        _agent_iterations=iterations,
        _max_agent_iterations=max_iter,
    )


@pytest.mark.asyncio
async def test_verify_node_unbound_kb_passthrough(monkeypatch):
    """未绑定 KB（_resolved_kb_ids 为空）→ 直通返回 answer，不询问/不 judge。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    state = _make_state(answer="纯对话回答", resolved=[])
    result = await verify_node(state)
    assert result == {"answer": "纯对话回答", "_needs_regenerate": False}


@pytest.mark.asyncio
async def test_verify_node_disabled_passthrough(monkeypatch):
    """VERIFY_ENABLED=False → 直通返回 answer，即使存在缺失年份。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", False)
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        result = await verify_node(state)
        assert result == {
            "answer": "2024年营收3943亿",
            "_needs_regenerate": False,
        }  # 无缺失标注
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_missing_user_rejects_annotate(monkeypatch):
    """缺失年份 + 用户拒绝联网 → 标注缺失后直通，不重生成。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(return_value=False),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        result = await verify_node(state)
        assert result == {
            "answer": (
                "2024年营收3943亿\n\n"
                "> 注：知识库仅覆盖 [2024]，缺失 [2023, 2025] 未联网补充。"
            ),
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_missing_confirmed_regenerates(monkeypatch):
    """缺失年份 + 用户确认联网 → 注入 SystemMessage 并置 _needs_regenerate。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        result = await verify_node(state)
        assert result["answer"] == "2024年营收3943亿"
        assert result["_needs_regenerate"] is True
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], SystemMessage)
        assert "缺失年份 [2023, 2025]" in result["messages"][0].content
        assert ctx.web_confirmed is True  # 本轮已确认，后续缺失不再询问
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_missing_iteration_limit_annotate(monkeypatch):
    """确认联网但 _agent_iterations 已超限 → 标注缺失直通，不重生成不询问。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(
            answer="2024年营收3943亿", resolved=["kb1"], iterations=5, max_iter=5
        )
        result = await verify_node(state)
        assert result == {
            "answer": (
                "2024年营收3943亿\n\n"
                "> 注：知识库仅覆盖 [2024]，缺失 [2023, 2025] 未联网补充。"
            ),
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_missing_confirmed_already_guided(monkeypatch):
    """缺失年份 + 联网指引已注入过 → 只置重生成信号，不再重复追加 SystemMessage。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        state.messages = [
            SystemMessage(
                content="知识库缺失年份 [2023, 2025]，用户已确认联网，请调用 search_web 工具补充。"
            )
        ]
        result = await verify_node(state)
        assert result["_needs_regenerate"] is True
        assert "messages" not in result  # 不再追加第二条指引
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_complete_runs_judge(monkeypatch):
    """完整性通过 → 跑忠实度 judge，标记 _unsupported。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node.faithfulness_check",
        AsyncMock(return_value=["句X"]),
    )
    _ctx, token = _make_ctx(temporal_years=[2024], tool_contexts=_make_contexts())
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        result = await verify_node(state)
        assert result == {
            "answer": "2024年营收3943亿",
            "_unsupported": ["句X"],
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_complete_judge_clean(monkeypatch):
    """完整性通过且 judge 无标记 → 返回纯 answer。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node.faithfulness_check",
        AsyncMock(return_value=[]),
    )
    _ctx, token = _make_ctx(temporal_years=[2024], tool_contexts=_make_contexts())
    try:
        state = _make_state(answer="2024年营收3943亿", resolved=["kb1"])
        result = await verify_node(state)
        assert result == {"answer": "2024年营收3943亿", "_needs_regenerate": False}
    finally:
        current_request_ctx.reset(token)
