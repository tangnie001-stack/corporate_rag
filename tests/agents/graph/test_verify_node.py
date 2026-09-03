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
from src.agents.graph.verify import (
    _ask_web_confirm,
    completeness_check,
    extract_years,
    faithfulness_check,
    verify_node,
)
from src.config import settings
from src.config.const import (
    MAX_VERIFY_ASK_PER_TURN,
    MAX_VERIFY_REGENERATIONS,
    VERIFY_KB_CITATION_MARKER,
)
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
        "src.agents.graph.verify.ask_confirm.wait_with_abort_and_timeout",
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
    kb_id: str = "",
    regenerations: int = 0,
) -> AgentState:
    """构造 verify_node 测试用 AgentState（kb_id="" = 未绑定 KB，态 A）。"""
    return AgentState(
        answer=answer,
        kb_id=kb_id,
        _verify_regenerations=regenerations,
    )


@pytest.mark.asyncio
async def test_verify_node_unbound_kb_passthrough(monkeypatch):
    """未绑定 KB（kb_id=""）→ 直通返回 answer，不询问/不 judge。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    state = _make_state(answer="纯对话回答")
    result = await verify_node(state)
    assert result == {"answer": "纯对话回答", "_needs_regenerate": False}


@pytest.mark.asyncio
async def test_verify_node_disabled_passthrough(monkeypatch):
    """VERIFY_ENABLED=False → 直通返回 answer，即使存在缺失年份。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", False)
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
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
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=False),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
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
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025])
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
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
async def test_verify_node_missing_regen_resets_web_quota(monkeypatch):
    """缺失年份确认联网 → regen 决策复位 search_web 配额（ctx.web_count=0）。

    ctx.web_count 是请求级累计计数：首段 search_web 已耗尽 WEB_SEARCH_PER_TURN_LIMIT
    配额，若 regen 不复位则回 agent 后工具达限返回 WEB_SEARCH_LIMIT_TEXT 不执行，
    verify 据此误判"知识库与网络均未覆盖"。regen 轮须同步归零配额（与
    _agent_iterations=0 同为"每段 regen 轮全新主循环预算"设计）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    ctx, token = _make_ctx(
        temporal_years=[2023, 2024, 2025],
        web_confirmed=True,
        web_count=settings.WEB_SEARCH_PER_TURN_LIMIT,  # 第 1 段配额已耗尽
    )
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
        result = await verify_node(state)
        assert result["_needs_regenerate"] is True
        assert result["_agent_iterations"] == 0
        assert ctx.web_count == 0  # regen 轮获得全新联网配额
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_web_exhausted_passthrough_keeps_web_quota(monkeypatch):
    """web-exhausted 标注直通（非 regen）→ 不复位 search_web 配额。

    网络穷尽（上一轮已带全缺失年份仍缺）是终止判定，不进入 regen 轮，web_count 保持
    原值；仅 regen 决策路径才复位配额，避免把"首段耗尽"误解为可无限刷新的额度。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    ctx, token = _make_ctx(
        temporal_years=[2023, 2024, 2025],
        web_confirmed=True,
        web_count=settings.WEB_SEARCH_PER_TURN_LIMIT,
    )
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
        state.messages = [
            AIMessage(
                content="",
                tool_calls=[_search_web_tool_call(["腾讯2023年报", "腾讯2025年报"])],
            )  # 上一轮已带全缺失年份仍缺 → 网络已穷尽
        ]
        result = await verify_node(state)
        assert result["_needs_regenerate"] is False
        assert "知识库与网络均未覆盖" in result["answer"]
        assert ctx.web_count == settings.WEB_SEARCH_PER_TURN_LIMIT  # 直通不复位
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_missing_fuse_exhausted_annotate(monkeypatch):
    """确认联网但修订保险丝已耗尽 → 标注"知识库与网络均未覆盖"直通，不重生成不询问。

    决策化语义：修订计数上限接管原 _agent_iterations 终止条件（防 verify→agent 无限往返）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(
            answer="2024年营收3943亿",
            kb_id="kb1",
            regenerations=MAX_VERIFY_REGENERATIONS,
        )
        result = await verify_node(state)
        assert result == {
            "answer": (
                "2024年营收3943亿\n\n"
                "> 注：知识库与网络均未覆盖 [2023, 2025]，仅 [2024] 有数据。"
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
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(side_effect=AssertionError("已确认过联网，不应再次询问")),
    )
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
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


def _search_web_tool_call(queries: list[str]) -> dict:
    """构造 search_web 工具调用 dict（仅 verify_node 单测读取，不真正执行）。"""
    return {
        "name": "search_web",
        "args": {"queries": queries},
        "id": "u1",
        "type": "tool_call",
    }


@pytest.mark.asyncio
async def test_verify_node_already_guided_partial_queries_sends_hint(monkeypatch):
    """完整指引已注入但上一轮 search_web queries 带漏 → 重申轮补发独立 hint。

    回归 hint 可达性：hint 是"还缺哪些年 + 一次带全再查"的新信息，不能因 already_guided
    被静默吞掉（否则 agent 无新指令空转烧保险丝，被误判网络未覆盖）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
        state.messages = [
            SystemMessage(
                content=(
                    "知识库缺失年份 [2023, 2025]，用户已确认联网，"
                    "请调用 search_web 工具补充这些年份的数据后再回答。"
                )
            ),
            AIMessage(
                content="", tool_calls=[_search_web_tool_call(["腾讯2023年报"])]
            ),  # 上一轮只带 2023，漏 2025
        ]
        result = await verify_node(state)
        assert result["_needs_regenerate"] is True
        assert result["_verify_regenerations"] == 1
        assert result["_agent_iterations"] == 0  # regen 轮复位主循环预算（Fix 2）
        assert len(result["messages"]) == 1  # 只补发 hint，不重复发完整指引
        hint = result["messages"][0]
        assert isinstance(hint, SystemMessage)
        assert "一次带全以下年份" in hint.content  # hint 独立消息标记短语
        assert "缺失年份 [2023, 2025]" in hint.content  # 点名仍缺年份
        assert "再调用一次 search_web" in hint.content  # 指示一次带全再查
        assert "用户已确认联网" not in hint.content  # hint 不含完整指引标记
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_already_guided_hint_deduped(monkeypatch):
    """hint 已发过 → 重申轮按短语查重命中，不再重复追加（至多发一次）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(temporal_years=[2023, 2024, 2025], web_confirmed=True)
    try:
        state = _make_state(answer="2024年营收3943亿", kb_id="kb1")
        state.messages = [
            SystemMessage(
                content=(
                    "知识库缺失年份 [2023, 2025]，用户已确认联网，"
                    "请调用 search_web 工具补充这些年份的数据后再回答。"
                )
            ),
            AIMessage(content="", tool_calls=[_search_web_tool_call(["腾讯2023年报"])]),
            SystemMessage(
                content=(
                    "缺失年份 [2023, 2025] 仍未补全：search_web 支持一次传入多个查询，"
                    "请再调用一次 search_web，"
                    "一次带全以下年份 [2023, 2025] 对应的查询后重新回答。"
                )
            ),  # 上一轮已发过 hint
        ]
        result = await verify_node(state)
        assert result["_needs_regenerate"] is True
        assert result["_verify_regenerations"] == 1
        assert result["_agent_iterations"] == 0
        assert "messages" not in result  # hint 查重命中，不再重复追加
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_complete_runs_judge(monkeypatch):
    """完整性通过 + 答案已带 [n] → 过 KB 护栏进入忠实度 judge，标记 _unsupported。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.faithfulness.faithfulness_check",
        AsyncMock(return_value=["句X"]),
    )
    _ctx, token = _make_ctx(temporal_years=[2024], tool_contexts=_make_contexts())
    try:
        state = _make_state(answer="2024年营收3943亿[1]", kb_id="kb1")
        result = await verify_node(state)
        assert result == {
            "answer": "2024年营收3943亿[1]",
            "_unsupported": ["句X"],
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_unsupported_signal_emitted(monkeypatch):
    """态 B judge 标记 unsupported → 产 unsupported 行为信号（含 kb_id/迭代/计数）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.faithfulness.faithfulness_check",
        AsyncMock(return_value=["句X"]),
    )
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal
        captured["query"] = query
        captured["iteration"] = iteration
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    _ctx, token = _make_ctx(temporal_years=[2024], tool_contexts=_make_contexts())
    try:
        state = _make_state(answer="2024年营收3943亿[1]", kb_id="kb1")
        state.query = "腾讯2024营收"
        state._agent_iterations = 3
        result = await verify_node(state)
        assert result["_unsupported"] == ["句X"]
        assert captured["signal"] == "unsupported"
        assert captured["query"] == "腾讯2024营收"
        assert captured["iteration"] == 3
        assert captured["fields"]["kb_id"] == "kb1"
        assert captured["fields"]["unsupported_count"] == 1
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_complete_judge_clean(monkeypatch):
    """完整性通过 + 答案已带 [n] → 过 KB 护栏且 judge 无标记 → 返回纯 answer。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.faithfulness.faithfulness_check",
        AsyncMock(return_value=[]),
    )
    _ctx, token = _make_ctx(temporal_years=[2024], tool_contexts=_make_contexts())
    try:
        state = _make_state(answer="2024年营收3943亿[1]", kb_id="kb1")
        result = await verify_node(state)
        assert result == {
            "answer": "2024年营收3943亿[1]",
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


def _make_web_contexts() -> list[RAGContext]:
    """构造一条 kind=web 的联网搜索上下文（模拟 search_web 结果）。"""
    return [
        RAGContext(
            content="阿里云轻量应用服务器适合个人博客",
            source="https://example.com/aliyun",
            page=0,
            doc_id="https://example.com/aliyun",
            chunk_id="https://example.com/aliyun",
            kind="web",
        )
    ]


@pytest.mark.asyncio
async def test_verify_node_unbound_web_no_citation_guides(monkeypatch):
    """未绑定 KB + 已联网检索 + 回答无 [n] 引用 → 注入标注引导并置重生成信号。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(tool_contexts=_make_web_contexts())
    try:
        state = _make_state(answer="建议选择 2核2G 配置，性价比较高")
        result = await verify_node(state)
        assert result["_needs_regenerate"] is True
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], SystemMessage)
        assert "请为联网引用标注来源编号" in result["messages"][0].content
        assert result["_agent_iterations"] == 0  # 态 A regen 轮同样复位主循环预算
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_unbound_web_with_citation_passthrough(monkeypatch):
    """未绑定 KB + 回答已带 [n] 引用 → 直通 format，不注入不重生成。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(tool_contexts=_make_web_contexts())
    try:
        state = _make_state(answer="建议选择 2核2G 配置[1]，性价比较高")
        result = await verify_node(state)
        assert result == {
            "answer": "建议选择 2核2G 配置[1]，性价比较高",
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_unbound_web_fuse_exhausted_passthrough(monkeypatch):
    """未绑定 KB + 回答无引用但修订保险丝已耗尽 → 直通不注入（防死循环）。

    语义随态 A 保险丝换源：上限判断由 _agent_iterations 改为 _verify_regenerations。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(tool_contexts=_make_web_contexts())
    try:
        state = _make_state(
            answer="建议选择 2核2G 配置",
            regenerations=MAX_VERIFY_REGENERATIONS,
        )
        result = await verify_node(state)
        assert result == {
            "answer": "建议选择 2核2G 配置",
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_unbound_web_already_guided_passthrough(monkeypatch):
    """未绑定 KB + 标注指引已注入过 → 直通不重复注入（防多轮堆积）。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(tool_contexts=_make_web_contexts())
    try:
        state = _make_state(answer="建议选择 2核2G 配置")
        state.messages = [
            SystemMessage(
                content="你刚才的回答引用了联网搜索结果，但没有标注来源编号，请为联网引用标注来源编号"
            )
        ]
        result = await verify_node(state)
        assert result == {
            "answer": "建议选择 2核2G 配置",
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_verify_node_unbound_no_web_passthrough(monkeypatch):
    """未绑定 KB + 未联网（无 web context）→ 纯对话直通，不注入。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    _ctx, token = _make_ctx(tool_contexts=[])  # ctx 存在但无 web 检索上下文
    try:
        state = _make_state(answer="纯对话回答")
        result = await verify_node(state)
        assert result == {
            "answer": "纯对话回答",
            "_needs_regenerate": False,
        }
    finally:
        current_request_ctx.reset(token)


# ── web_citation_guard（态 A 联网引用引导 regen 配额复位）──


@pytest.mark.asyncio
async def test_web_citation_guard_regen_resets_web_quota():
    """态 A web_citation_guard regen → 同步归零 search_web 配额（web_count=0）。"""
    from src.agents.graph.verify.guardrails import web_citation_guard

    state = AgentState(answer="建议选择 2核2G 配置")
    ctx = RequestContext(
        session_id="s1",
        tool_contexts=_make_web_contexts(),
        web_count=settings.WEB_SEARCH_PER_TURN_LIMIT,  # 首段配额已耗尽
    )
    decision = await web_citation_guard(state, ctx)
    assert decision is not None
    assert decision["_needs_regenerate"] is True
    assert decision["_agent_iterations"] == 0  # regen 轮复位主循环预算
    assert ctx.web_count == 0  # regen 轮复位联网配额


@pytest.mark.asyncio
async def test_web_citation_guard_passthrough_keeps_web_quota():
    """态 A 已带引用直通（None，非 regen）→ 不复位 search_web 配额。"""
    from src.agents.graph.verify.guardrails import web_citation_guard

    state = AgentState(answer="建议选择 2核2G 配置[1]，性价比较高")
    ctx = RequestContext(
        session_id="s1",
        tool_contexts=_make_web_contexts(),
        web_count=settings.WEB_SEARCH_PER_TURN_LIMIT,
    )
    decision = await web_citation_guard(state, ctx)
    assert decision is None
    assert ctx.web_count == settings.WEB_SEARCH_PER_TURN_LIMIT  # 直通不复位


# ── kb_citation_guardrail（态 B KB 强制溯源护栏）──


def _make_kb_ctx_contexts() -> list[RAGContext]:
    """构造含 kind=kb 的引用上下文（RAGContext 默认 kind 即 kb，显式标注防误读）。"""
    return [
        RAGContext(
            content="腾讯2024年营收3943亿元",
            source="a.pdf",
            page=1,
            doc_id="d1",
            chunk_id="d1:0",
            kind="kb",
        )
    ]


@pytest.mark.asyncio
async def test_kb_guardrail_guides_when_no_citation():
    """态 B 有 kb context 无 [n] → 注入 KB 溯源指引 regen（复位主循环预算不占保险丝）。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="腾讯2024年营收3943亿")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    decision = await kb_citation_guardrail(state, ctx)
    assert decision is not None
    assert decision["_needs_regenerate"] is True
    assert VERIFY_KB_CITATION_MARKER in decision["messages"][0].content
    assert decision["_agent_iterations"] == 0  # regen 轮复位主循环预算
    assert "_verify_regenerations" not in decision  # 不占完整性决策轮保险丝


@pytest.mark.asyncio
async def test_kb_guardrail_skips_when_abstention():
    """拒答/知识库未覆盖 → 不强灌引用。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="未在文档中找到相关数据")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    assert await kb_citation_guardrail(state, ctx) is None


@pytest.mark.asyncio
async def test_kb_guardrail_passes_when_cited():
    """答案带 [n] → 直接通过。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail

    state = AgentState(answer="腾讯2024年营收3943亿[1]")
    ctx = RequestContext(session_id="s1", tool_contexts=_make_kb_ctx_contexts())
    assert await kb_citation_guardrail(state, ctx) is None
