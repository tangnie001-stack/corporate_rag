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
