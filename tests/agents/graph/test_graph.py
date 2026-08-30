"""Tests for LangGraph node functions and graph topology."""

from src.agents.graph.nodes import format_node
from src.agents.graph.state import AgentState
from src.rag.context import RAGContext


def test_graph_topology():
    """图结构断言：kb_router → agent 循环 → format，不含固定流水线节点。"""
    from unittest.mock import MagicMock

    from src.agents.graph.workflow import build_graph

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
