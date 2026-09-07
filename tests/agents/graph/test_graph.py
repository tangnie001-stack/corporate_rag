"""Tests for LangGraph node functions and graph topology."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import tool

from src.agents.graph.nodes import format_node
from src.agents.graph.state import AgentState
from src.agents.graph.workflow import build_graph
from src.config import settings
from src.config.const import SSEInteractionTexts
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


def test_graph_topology():
    """图结构断言：agent 循环 → verify → format，不含固定流水线节点。"""
    graph = build_graph(
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    nodes = graph.get_graph().nodes
    # LangGraph 内部哨兵节点 __start__/__end__ 不属于业务节点，断言前剔除
    node_names = set(nodes) - {"__start__", "__end__"}
    assert node_names == {
        "agent",
        "tools",
        "agent_finalize",
        "verify",
        "format",
    }
    # 固定流水线节点已删除
    for removed in (
        "kb_router",
        "classify",
        "rewrite",
        "retrieve",
        "rerank",
        "generate",
    ):
        assert removed not in node_names


def test_format_node_only_keeps_cited_sources():
    """format_node 应只保留回答中实际引用的来源，并带原始编号。"""
    state = AgentState(
        answer="腾讯2024年营收3943亿元[1]，灿坤2019年营收见[3]",
        tool_contexts=[
            RAGContext(
                content="腾讯2024年报内容",
                source="腾讯.pdf",
                page=5,
                doc_id="d1",
                chunk_id="c1",
                score=0.9,
            ),
            RAGContext(
                content="灿坤内容A",
                source="灿坤.pdf",
                page=1,
                doc_id="d2",
                chunk_id="c2",
                score=0.8,
            ),
            RAGContext(
                content="灿坤2019年报内容",
                source="灿坤.pdf",
                page=10,
                doc_id="d2",
                chunk_id="c3",
                score=0.7,
            ),
            RAGContext(
                content="无关内容",
                source="其他.pdf",
                page=1,
                doc_id="d3",
                chunk_id="c4",
                score=0.6,
            ),
        ],
    )
    result = format_node(state)
    citations = result["citations"]
    assert len(citations) == 2  # [1] 腾讯.pdf:5 和 [3] 灿坤.pdf:10
    assert citations[0]["index"] == 1
    assert citations[0]["source"] == "腾讯.pdf"
    assert citations[1]["index"] == 3
    assert citations[1]["source"] == "灿坤.pdf"


def test_format_node_snippet_targets_relevant_passage():
    """snippet 应截取 chunk 中与回答最相关的片段，而非固定前 200 字符。"""
    # 前缀为股东持股等无关内容，营收句子确保落在第 200 字符之后
    prefix = (
        "注：截至本报告期末，东软集团股份有限公司回购专用证券账户持有公司股份18,225,976股，"
        "占公司总股本的1.5142%，未纳入前10名股东持股情况中列示。\n"
        "持股5%以上股东、前10名股东及前10名无限售流通股股东参与转融通业务出借股份情况，"
        "□适用√不适用。\n"
        "前10名股东及前10名无限售流通股股东因转融通出借/归还原因导致较上期发生变化，"
        "□适用√不适用。\n"
        "根据《上海证券交易所股票上市规则》相关规定，公司应当披露报告期内的其他重要事项。\n"
    )
    revenue_sentence = "报告期内，公司实现营业收入184,980万元，同比增长1.06%。"
    content = prefix + revenue_sentence
    assert len(prefix) > 200  # 前置条件：营收句子确实在 200 字符之后

    state = AgentState(
        answer="2025年第一季度公司实现营业收入184,980万元，同比增长1.06% [1]。",
        tool_contexts=[
            RAGContext(
                content=content,
                source="neusoft_2025_q1.pdf",
                page=3,
                doc_id="d1",
                chunk_id="c1",
                score=0.9,
            ),
        ],
    )
    result = format_node(state)
    snippet = result["citations"][0]["snippet"]
    assert "营业收入184,980万元" in snippet
    assert "同比增长1.06%" in snippet


def test_format_node_ignores_invalid_index():
    """超出范围的引用编号应被忽略。"""
    state = AgentState(
        answer="内容[9]",  # 只有 1 个 context，编号 9 非法
        tool_contexts=[
            RAGContext(
                content="内容",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="c1",
                score=0.9,
            ),
        ],
    )
    result = format_node(state)
    assert result["citations"] == []


def test_format_node_keeps_citations_when_marker_and_ref():
    """web 兜底回答混入拒答语但带 [n] 引用时，引用不被误删，kind=web。"""
    state = AgentState(
        answer="未在文档中找到该信息，该问题不在当前知识库范围内，网络结果[1]",
        tool_contexts=[
            RAGContext(
                content="网页内容",
                source="https://example.com",
                page=0,
                doc_id="u1",
                chunk_id="u1",
                kind="web",
            ),
        ],
    )
    result = format_node(state)
    citations = result["citations"]
    assert len(citations) == 1
    assert citations[0]["kind"] == "web"
    assert citations[0]["source"] == "https://example.com"


def test_format_node_citation_kind_default_kb():
    """知识库引用的 kind 默认 kb。"""
    state = AgentState(
        answer="营收184,980万元[1]",
        tool_contexts=[
            RAGContext(
                content="报告期内营业收入184,980万元",
                source="neusoft_2025_q1.pdf",
                page=3,
                doc_id="d1",
                chunk_id="c1",
            ),
        ],
    )
    result = format_node(state)
    assert result["citations"][0]["kind"] == "kb"


def test_format_node_empty_when_abstention():
    """回答含拒答语时 citations 应为空。"""
    state = AgentState(
        answer="未在文档中找到相关数据。",
        tool_contexts=[
            RAGContext(
                content="内容",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="c1",
                score=0.5,
            ),
        ],
    )
    result = format_node(state)
    assert result["citations"] == []


# ── 编译图集成测试：verify 回环与终止（回归 Critical #1 / Important #3）──


class SequenceChatModel:
    """极简 fake LLM：按调用顺序消费固定 AIMessage 序列，bind_tools 原样返回自身。"""

    def __init__(self, responses: list[AIMessage]) -> None:
        """记录固定响应序列。"""
        self.responses = list(responses)
        self.tools = None

    def bind_tools(self, tools):
        """绑定工具：fake 直接返回自身。"""
        self.tools = tools
        return self

    async def astream(self, messages, **kwargs):
        """按序 yield 一条固定响应（单块），忽略 extra_body 等额外参数。"""
        yield self.responses.pop(0)


class StubPromptManager:
    """极简 PromptManager stub：只提供 build_prompt 需要的两个方法。"""

    def get_system_prompt(self):
        """返回固定系统指令。"""
        return "system prompt"

    def get_user_template(self, context="", query=""):
        """返回含 query 的用户模板。"""
        return f"user template: {query}"


@tool("search_web")
async def fake_search_web(queries: list[str], top_k: int = 5) -> str:
    """联网搜索（测试桩）：返回含 2023/2025 年份数据的固定文本，不发真实请求。"""
    return "2023年营收3000亿。2025年营收4500亿。"


def _search_web_call(queries: list[str] | None = None) -> dict:
    """构造 ToolNode 可执行的 search_web 工具调用 dict（queries 形如真实工具签名）。

    Args:
        queries: search_web 的 queries 参数；None 时用不含年份的通用查询
            （模拟 agent 带漏年份/未按指引逐轮给出缺失年份查询）
    """
    if queries is None:
        queries = ["这几年营收"]
    return {
        "name": "search_web",
        "args": {"queries": queries},
        "id": "c1",
        "type": "tool_call",
    }


def _build_test_graph(llm, tools=None) -> object:
    """编译测试图：默认注入 fake search_web 工具（tools 可传 spy 覆盖），其余依赖全 MagicMock。"""
    if tools is None:
        tools = [fake_search_web]
    return build_graph(
        MagicMock(),  # vector_store
        None,  # bm25
        llm,  # agent LLM（fake，按序响应）
        MagicMock(),  # reranker
        StubPromptManager(),
        tools=tools,
    )


def _make_verify_ctx():
    """构造并 set 已确认联网的 RequestContext，返回 (ctx, token)。"""
    ctx = RequestContext(
        session_id="s1",
        temporal_years=[2023, 2024, 2025],
        web_confirmed=True,  # 模拟用户已确认联网，避免走 clarify_channel 询问
    )
    return ctx, current_request_ctx.set(ctx)


async def _run_graph(graph, initial_state):
    """运行编译图（updates 模式），返回 (节点顺序, 最后 verify 更新, 累计 messages, verify 更新列表)。

    stream_mode="updates" 逐节点 yield {节点名: 节点返回 dict}；
    累计 messages 按追加顺序拼接（agent 首轮含初始 system/user，后续只含新增消息）；
    verify 更新列表保留每次 verify 节点的返回 dict（供断言修订保险丝计数逐轮自增）。
    """
    node_order: list[str] = []
    last_verify: dict | None = None
    verify_updates: list[dict] = []
    all_messages: list = []
    async for update in graph.astream(initial_state, stream_mode="updates"):
        for name, payload in update.items():
            if name.startswith("__"):
                continue
            node_order.append(name)
            all_messages.extend(payload.get("messages", []))
            if name == "verify":
                last_verify = payload
                verify_updates.append(payload)
    return node_order, last_verify, all_messages, verify_updates


@pytest.mark.asyncio
async def test_graph_verify_loop_success_terminates_at_format(monkeypatch):
    """路径 A：确认联网 → search_web 补上年份 → verify 完整性通过 → 终节点 format。

    回归 Critical #1：verify 最后一轮必须显式复位 _needs_regenerate=False，
    否则 LastValue 通道残留 True，route_verify 永远回 agent 直至 GraphRecursionError。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(
                    content="2024年营收3943亿"
                ),  # 首轮：仅覆盖 2024，缺 2023/2025
                AIMessage(
                    content="", tool_calls=[_search_web_call()]
                ),  # 次轮：收到指引后调 search_web
                AIMessage(
                    content="2023年营收3000亿，2024年营收3943亿，2025年营收4500亿"
                ),
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, _all_messages, _verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"  # 图正常终止于 format，未回 agent 死循环
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False  # 完整性通过后显式复位
        assert "2025" in last_verify["answer"]  # 答案已覆盖全部要求年份
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_graph_verify_loop_terminates_at_regen_fuse(monkeypatch):
    """路径 B：确认联网但 agent 反复带漏 search_web queries → 修订保险丝耗尽 → 标注直通。

    决策化语义：终止不再由 _agent_iterations 上限驱动，改由 _verify_regenerations
    保险丝（MAX_VERIFY_REGENERATIONS=2）兜底。queries 逐轮带漏缺失年份 → 决策分支
    每次仍判定需重生成，计数 0→1→2；第 3 次 verify 达上限标注直通复位，不空转。
    回归 Critical #1 终止路径（标注直通显式复位）与 Important #3（指引只注入一次）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(content="2024年营收3943亿"),  # 第 1 轮：缺 2023/2025
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 带漏：queries 不含缺失年份
                AIMessage(content="2024年营收3943亿"),  # 第 3 轮仍缺年份
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 再带漏
                AIMessage(content="2024年营收3943亿"),  # 第 5 轮：保险丝耗尽
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, all_messages, verify_updates = await _run_graph(
            graph, initial
        )

        assert (
            node_order[-1] == "format"
        )  # 保险丝标注直通后正常终止，无 GraphRecursionError
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False  # 标注直通显式复位
        assert "知识库与网络均未覆盖" in last_verify["answer"]  # 新标注文案
        assert "仅 [2024] 有数据" in last_verify["answer"]
        guided = [
            m
            for m in all_messages
            if isinstance(m, SystemMessage) and "用户已确认联网" in (m.content or "")
        ]
        assert len(guided) == 1  # #3：循环期间指引只注入一次，不重复堆积
        # 保险丝逐轮自增：第 1 次 verify 注入指引计数 1，第 2 次重申计数 2，第 3 次达上限直通
        assert [u.get("_verify_regenerations") for u in verify_updates] == [1, 2, None]
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_graph_verify_never_searched_injects_guidance(monkeypatch):
    """决策分支：agent 从未调 search_web → 注入联网指引（无"一次带全"提示）→ 重生成。"""
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(content="2024年营收3943亿"),  # 缺 2023/2025，尚未联网
                AIMessage(
                    content="2023年营收3000亿，2024年营收3943亿，2025年营收4500亿"
                ),  # 指引后直接补全答案
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, all_messages, verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False
        guided = [
            m
            for m in all_messages
            if isinstance(m, SystemMessage) and "用户已确认联网" in (m.content or "")
        ]
        assert len(guided) == 1  # 首轮注入完整联网指引
        assert "缺失年份 [2023, 2025]" in guided[0].content
        assert "一次带全" not in guided[0].content  # 未调过 search_web 不附带全提示
        # 首次 verify 注入指引并计数 +1（0→1）
        assert verify_updates[0]["_needs_regenerate"] is True
        assert verify_updates[0]["_verify_regenerations"] == 1
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_graph_verify_partial_queries_reinjects_with_hint(monkeypatch):
    """决策分支（canonical 序）：完整指引先行 → agent 带漏搜索 → verify 补发独立 hint。

    断言 hint 是真正送达 agent 的独立 SystemMessage（不在首条完整指引内）：指引注入后
    agent 调 search_web 只带 [2023]（漏 2025），verify 重申轮必须补发 hint（新信息：
    还缺哪些年 + 一次带全再查一次），不能再被 already_guided 静默吞掉。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(
                    content="2024年营收3943亿"
                ),  # 首答仅覆盖 2024 → verify 注完整指引
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2023年报"])]
                ),  # 收到指引后带漏搜索：只带 2023，漏 2025
                AIMessage(
                    content="2024年营收3943亿，2023年营收3000亿"
                ),  # 采纳 2023 但仍缺 2025 → verify 补发 hint
                AIMessage(
                    content="2023年营收3000亿，2024年营收3943亿，2025年营收4500亿"
                ),  # 收到 hint 后带全补答
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, all_messages, verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False
        assert "2023" in last_verify["answer"] and "2025" in last_verify["answer"]
        guided = [
            m
            for m in all_messages
            if isinstance(m, SystemMessage) and "用户已确认联网" in (m.content or "")
        ]
        assert len(guided) == 1  # 完整指引只在首轮注入一次
        assert "一次带全以下年份" not in guided[0].content  # hint 已移出指引正文
        hints = [
            m
            for m in all_messages
            if isinstance(m, SystemMessage) and "一次带全以下年份" in (m.content or "")
        ]
        assert len(hints) == 1  # hint 独立成条且至多发一次
        assert "缺失年份 [2025]" in hints[0].content  # hint 点名仍缺年份
        assert "再调用一次 search_web" in hints[0].content  # 指示再查一次
        assert "用户已确认联网" not in hints[0].content  # hint 不含完整指引标记
        # 首轮注完整指引计数 0→1；重申轮补发 hint 计数 1→2；末轮完整性通过不再计数
        assert verify_updates[0]["_needs_regenerate"] is True
        assert verify_updates[0]["_verify_regenerations"] == 1
        assert verify_updates[1]["_needs_regenerate"] is True
        assert verify_updates[1]["_verify_regenerations"] == 2
        assert verify_updates[1]["messages"][0] is hints[0]  # 重申轮实际送达 hint 消息
        assert [u.get("_verify_regenerations") for u in verify_updates] == [1, 2, None]
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_graph_verify_web_exhausted_annotates(monkeypatch):
    """决策分支：search_web 已带全缺失年份仍缺 → 网络已穷尽 → 标注直通不空转。

    关键：web-exhausted 决策先于保险丝触发（计数停在 1 不耗尽 2），修订循环被决策化提前终止。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(content="2024年营收3943亿"),  # 缺 2023/2025
                AIMessage(
                    content="",
                    tool_calls=[_search_web_call(["腾讯2023年报", "腾讯2025年报"])],
                ),  # 一次带全缺失年份调 search_web
                AIMessage(content="2024年营收3943亿，2025年4500亿"),  # 仍缺 2023
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, _all_messages, verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False  # 直通显式复位
        assert "知识库与网络均未覆盖" in last_verify["answer"]
        assert "[2023]" in last_verify["answer"]  # 仅标注仍缺的 2023
        assert "仅 [2024, 2025] 有数据" in last_verify["answer"]
        # 第 1 次 verify 注入指引计数 1；第 2 次带全仍缺 → web exhausted 直通，未耗保险丝
        assert [u.get("_verify_regenerations") for u in verify_updates] == [1, None]
    finally:
        current_request_ctx.reset(token)


def _make_search_web_spy():
    """构造带调用记录的 search_web 测试工具，返回 (tool, calls)。

    calls 按调用序记录每次执行的 queries（测试桩不发起真实网络请求）；
    供 V2 回归用例断言 regen 轮的 search_web 确实被执行（而非被 route_agent 吞掉）。
    """
    calls: list[list[str]] = []

    @tool("search_web")
    async def spy_search_web(queries: list[str], top_k: int = 5) -> str:
        """联网搜索（测试桩）：记录查询并返回含 2023/2025 年份数据的固定文本。"""
        calls.append(list(queries))
        return "2023年营收3000亿。2025年营收4500亿。"

    return spy_search_web, calls


@pytest.mark.asyncio
async def test_graph_verify_regen_round_uses_full_iteration_budget(monkeypatch):
    """回归 change 3.5①（V2 bug）：首轮耗尽主循环预算 → regen 轮 search_web 仍完整执行。

    首轮 agent 反复调 search_web（queries 带漏）直到第 5 次迭代被 route_agent 强制收尾，
    verify 注指引并 regen。regen 返回必须带 _agent_iterations=0 复位主循环预算：否则回
    agent 后迭代计数续在触顶值上，本轮的 search_web 工具调用会在 route_agent 被上限吞掉
    （工具不执行、答案空白），verify 会把空答案误判为"网络均未覆盖"标注直通。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        spy_search_web, spy_calls = _make_search_web_spy()
        # 每次迭代用独立 AIMessage 对象（复用同一对象会被 add_messages 去重吞掉）
        llm = SequenceChatModel(
            [
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 1：搜索但 queries 带漏
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 2
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 3
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 4
                AIMessage(
                    content="2024年营收3943亿"
                ),  # 迭代 5：达上限强制收尾，仍缺年份
                AIMessage(
                    content="",
                    tool_calls=[_search_web_call(["腾讯2023年报", "腾讯2025年报"])],
                ),  # regen 轮：一次带全缺失年份调 search_web（必须真正执行）
                AIMessage(
                    content="2023年营收3000亿，2024年营收3943亿，2025年营收4500亿"
                ),  # 带全后产出完整答案
            ]
        )
        graph = _build_test_graph(llm, tools=[spy_search_web])
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, _all_messages, _verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False
        # 无 Fix 2 时 regen 轮工具调用被吞：spy 只有首轮 4 次、答案带"均未覆盖"标注
        assert len(spy_calls) == 5  # 首轮 4 次 + regen 轮 1 次（完整执行）
        assert spy_calls[-1] == ["腾讯2023年报", "腾讯2025年报"]  # regen 轮带全缺失年份
        assert "网络均未覆盖" not in last_verify["answer"]  # 未被误判为网络穷尽
        assert "2023" in last_verify["answer"] and "2025" in last_verify["answer"]
    finally:
        current_request_ctx.reset(token)


def _make_quota_aware_search_web_spy():
    """构造带调用记录 + 真实配额门限语义的 search_web 测试工具，返回 (tool, calls)。

    calls 按执行序记录 queries；工具先按真实 search_web 的门限判断（ctx.web_count >=
    WEB_SEARCH_PER_TURN_LIMIT → 达限返回 WEB_SEARCH_LIMIT_TEXT 不执行，未达限 +1 后执行），
    供配额维度回归断言 regen 轮的 search_web 真正执行（首段耗尽不复位 → regen 轮被拦）。
    """
    calls: list[list[str]] = []

    @tool("search_web")
    async def spy_search_web(queries: list[str], top_k: int = 5) -> str:
        """联网搜索（测试桩）：模拟真实配额门限并记录执行，返回含 2023/2025 的固定文本。"""
        ctx = current_request_ctx.get()
        if ctx is not None:
            if ctx.web_count >= settings.WEB_SEARCH_PER_TURN_LIMIT:
                return SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT
            ctx.web_count += 1
        calls.append(list(queries))
        return "2023年营收3000亿。2025年营收4500亿。"

    return spy_search_web, calls


@pytest.mark.asyncio
async def test_graph_verify_regen_round_resets_web_quota(monkeypatch):
    """回归 review Finding（配额维度）：首段 search_web 配额耗尽 → regen 轮仍完整执行。

    ctx.web_count 是请求级累计计数：第 1 段 4 次 search_web 调用把配额耗尽（预置
    web_count=limit，首次调用即达限被拦）。若 verify regen 不复位配额，回 agent 后
    regen 轮的 search_web 同样达限返回 WEB_SEARCH_LIMIT_TEXT 不执行 → verify 误判
    "知识库与网络均未覆盖"。regen 决策必须带 ctx.web_count=0（与 _agent_iterations=0
    同为"每段 regen 轮全新主循环预算"设计）：断言 spy 恰好执行一次（仅 regen 轮）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify.ask_confirm._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    _ctx, token = _make_verify_ctx()
    try:
        _ctx.web_count = settings.WEB_SEARCH_PER_TURN_LIMIT  # 第 1 段配额已耗尽
        spy_search_web, spy_calls = _make_quota_aware_search_web_spy()
        # 每次迭代用独立 AIMessage 对象（复用同一对象会被 add_messages 去重吞掉）
        llm = SequenceChatModel(
            [
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 1：搜索但 queries 带漏（配额已耗尽 → 被拦不执行）
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 2
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 3
                AIMessage(
                    content="", tool_calls=[_search_web_call(["腾讯2024年报"])]
                ),  # 迭代 4
                AIMessage(
                    content="2024年营收3943亿"
                ),  # 迭代 5：达上限强制收尾，仍缺年份
                AIMessage(
                    content="",
                    tool_calls=[_search_web_call(["腾讯2023年报", "腾讯2025年报"])],
                ),  # regen 轮：配额已复位，带全缺失年份调 search_web（必须真正执行）
                AIMessage(
                    content="2023年营收3000亿，2024年营收3943亿，2025年营收4500亿"
                ),  # 带全后产出完整答案
            ]
        )
        graph = _build_test_graph(llm, tools=[spy_search_web])
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, _all_messages, _verify_updates = await _run_graph(
            graph, initial
        )

        assert node_order[-1] == "format"
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False
        # 无配额复位时 regen 轮工具达限被拦：spy 一次都不执行（calls 为空）
        assert len(spy_calls) == 1  # 仅 regen 轮真正执行（第 1 段 4 次全被配额拦下）
        assert spy_calls[0] == ["腾讯2023年报", "腾讯2025年报"]  # regen 轮带全缺失年份
        assert _ctx.web_count == 1  # 复位后 regen 轮消耗 1 次新额度
        assert "网络均未覆盖" not in last_verify["answer"]  # 未被误判为网络穷尽
        assert "2023" in last_verify["answer"] and "2025" in last_verify["answer"]
    finally:
        current_request_ctx.reset(token)


def test_build_graph_passes_delegate_task_to_rag_tools(monkeypatch):
    """build_graph 默认分支须把 delegate_task 透传给 make_rag_tools（接线守卫）。"""
    from unittest.mock import MagicMock, sentinel

    from src.agents.graph import workflow as wf

    captured = {}

    def fake_make_rag_tools(vector_store, bm25, reranker, prompt_manager, **kwargs):
        captured["delegate_task"] = kwargs.get("delegate_task")
        return []  # 空工具列表即可（本测试只验证透传，不跑图）

    monkeypatch.setattr(wf, "make_rag_tools", fake_make_rag_tools)
    wf.build_graph(
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        delegate_task=sentinel.delegate_tool,
    )
    assert captured["delegate_task"] is sentinel.delegate_tool
