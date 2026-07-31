"""Tests for LangGraph node functions."""

from unittest.mock import Mock, patch
import pytest

from src.agents.graph.state import AgentState, RAGQueryIntent
from src.agents.graph.nodes import make_classify_node


def test_make_classify_node_returns_callable():
    """make_classify_node 应返回可调用对象。"""
    node = make_classify_node(llm=Mock())
    assert callable(node)


def test_route_by_intent_returns_clarify_when_missing_entities():
    """missing_entities 非空时返回 clarify。"""
    from src.agents.graph.workflow import route_by_intent

    state = AgentState(
        query="营收多少",
        intent=RAGQueryIntent(route="medium"),
        missing_entities=[{"type": "year", "question": "哪一年？"}],
    )
    assert route_by_intent(state) == "clarify"


def test_route_by_intent_returns_normal():
    """missing_entities 为空时返回正常路由。"""
    from src.agents.graph.workflow import route_by_intent

    state = AgentState(
        query="2024年营收多少",
        intent=RAGQueryIntent(route="medium"),
        missing_entities=[],
    )
    assert route_by_intent(state) == "medium"


@pytest.mark.asyncio
async def test_make_classify_node_returns_expected_keys():
    """make_classify_node 返回的节点应包含所有预期 key。"""
    mock_llm = Mock()
    mock_route_result = {
        "intent": RAGQueryIntent(route="simple"),
        "extracted_entities": [],
        "missing_entities": [],
        "classification_confidence": 1.0,
    }
    with patch("src.agents.graph.nodes.QueryRouter") as mock_router_cls:
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.route.return_value = mock_route_result
        node = make_classify_node(llm=mock_llm)
        state = AgentState(query="2024年营收多少")
        result = await node(state)
        assert result["intent"].route == "simple"
        assert result["extracted_entities"] == []
        assert result["missing_entities"] == []
        assert result["classification_confidence"] == 1.0


def test_format_node_only_keeps_cited_sources():
    """format_node 应只保留回答中实际引用的来源，并带原始编号。"""
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

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
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

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
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

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
