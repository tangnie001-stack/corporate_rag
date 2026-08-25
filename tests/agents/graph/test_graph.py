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
        contexts=[
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


def test_format_node_ignores_invalid_index():
    """超出范围的引用编号应被忽略。"""
    state = AgentState(
        answer="内容[9]",  # 只有 1 个 context，编号 9 非法
        contexts=[
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


def test_format_node_empty_when_abstention():
    """回答含拒答语时 citations 应为空。"""
    state = AgentState(
        answer="未在文档中找到相关数据。",
        contexts=[
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
