"""Tests for LangGraph node functions and graph topology."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.tools import tool

from src.agents.graph.nodes import format_node
from src.agents.graph.state import AgentState
from src.agents.graph.workflow import build_graph
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


def test_graph_topology():
    """图结构断言：kb_router → agent 循环 → format，不含固定流水线节点。"""
    graph = build_graph(
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
    )
    nodes = graph.get_graph().nodes
    # LangGraph 内部哨兵节点 __start__/__end__ 不属于业务节点，断言前剔除
    node_names = set(nodes) - {"__start__", "__end__"}
    assert node_names == {
        "kb_router",
        "agent",
        "tools",
        "agent_finalize",
        "verify",
        "format",
    }
    # 固定流水线节点已删除
    for removed in ("classify", "rewrite", "retrieve", "rerank", "generate"):
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
async def fake_search_web(query: str, top_k: int = 5) -> str:
    """联网搜索（测试桩）：返回含 2023/2025 年份数据的固定文本，不发真实请求。"""
    return "2023年营收3000亿。2025年营收4500亿。"


def _search_web_call() -> dict:
    """构造 ToolNode 可执行的 search_web 工具调用 dict。"""
    return {
        "name": "search_web",
        "args": {"query": "这几年营收"},
        "id": "c1",
        "type": "tool_call",
    }


def _build_test_graph(llm) -> object:
    """编译测试图：注入 fake search_web 工具，其余依赖全 MagicMock。"""
    return build_graph(
        MagicMock(),  # vector_store
        None,  # bm25
        llm,  # agent LLM（fake，按序响应）
        MagicMock(),  # classify_llm
        MagicMock(),  # reranker
        MagicMock(),  # embed_fn
        StubPromptManager(),
        tools=[fake_search_web],
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
    """运行编译图（updates 模式），返回 (节点顺序, 最后一条 verify 更新, 累计 messages)。

    stream_mode="updates" 逐节点 yield {节点名: 节点返回 dict}；
    累计 messages 按追加顺序拼接（agent 首轮含初始 system/user，后续只含新增消息）。
    """
    node_order: list[str] = []
    last_verify: dict | None = None
    all_messages: list = []
    async for update in graph.astream(initial_state, stream_mode="updates"):
        for name, payload in update.items():
            if name.startswith("__"):
                continue
            node_order.append(name)
            all_messages.extend(payload.get("messages", []))
            if name == "verify":
                last_verify = payload
    return node_order, last_verify, all_messages


@pytest.mark.asyncio
async def test_graph_verify_loop_success_terminates_at_format(monkeypatch):
    """路径 A：确认联网 → search_web 补上年份 → verify 完整性通过 → 终节点 format。

    回归 Critical #1：verify 最后一轮必须显式复位 _needs_regenerate=False，
    否则 LastValue 通道残留 True，route_verify 永远回 agent 直至 GraphRecursionError。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.agents.graph.verify_node.faithfulness_check",
        AsyncMock(return_value=[]),
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
        node_order, last_verify, _all_messages = await _run_graph(graph, initial)

        assert node_order[-1] == "format"  # 图正常终止于 format，未回 agent 死循环
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False  # 完整性通过后显式复位
        assert "2025" in last_verify["answer"]  # 答案已覆盖全部要求年份
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_graph_verify_loop_terminates_at_iteration_limit(monkeypatch):
    """路径 B：确认联网但一直缺年份 → _agent_iterations 超限 → verify 标注直通 → format。

    回归 Critical #1 终止路径（超限标注直通显式复位）与 Important #3
    （循环期间联网指引只注入一次，不逐轮堆积重复 SystemMessage）。
    """
    monkeypatch.setattr("src.config.settings.VERIFY_ENABLED", True)
    monkeypatch.setattr(
        "src.agents.graph.verify_node._ask_web_confirm",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "src.agents.graph.verify_node.faithfulness_check",
        AsyncMock(return_value=[]),
    )
    _ctx, token = _make_verify_ctx()
    try:
        llm = SequenceChatModel(
            [
                AIMessage(content="2024年营收3943亿"),  # 第 1 轮
                AIMessage(content="", tool_calls=[_search_web_call()]),
                AIMessage(content="2024年营收3943亿"),  # 第 3 轮仍缺年份
                AIMessage(content="", tool_calls=[_search_web_call()]),
                AIMessage(content="2024年营收3943亿"),  # 第 5 轮达迭代上限
            ]
        )
        graph = _build_test_graph(llm)
        initial = AgentState.make_initial_state("s1", "kb1", "这几年营收怎么样", [])
        node_order, last_verify, all_messages = await _run_graph(graph, initial)

        assert (
            node_order[-1] == "format"
        )  # 超限标注直通后正常终止，无 GraphRecursionError
        assert last_verify is not None
        assert last_verify["_needs_regenerate"] is False  # 标注直通显式复位
        assert "未联网补充" in last_verify["answer"]  # 已标注缺失年份
        guided = [
            m
            for m in all_messages
            if isinstance(m, SystemMessage) and "用户已确认联网" in (m.content or "")
        ]
        assert len(guided) == 1  # #3：循环期间指引只注入一次，不重复堆积
    finally:
        current_request_ctx.reset(token)
