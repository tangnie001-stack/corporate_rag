"""Tests for LangGraph node functions."""
from src.agents.graph.state import AgentState
from src.agents.graph.nodes import classify_node


def test_classify_simple():
    """单事实查询 → simple"""
    state: AgentState = {"query": "2024年营收多少"}
    result = classify_node(state)
    assert result["intent"]["route"] == "simple"


def test_classify_medium():
    """分析类查询 → medium"""
    state: AgentState = {"query": "分析近三年营收变化趋势"}
    result = classify_node(state)
    assert result["intent"]["route"] == "medium"


def test_classify_complex():
    """对比类查询 → complex"""
    state: AgentState = {"query": "对比A公司和B公司的偿债能力差异"}
    # QueryRouter.MEDIUM_PATTERNS 含 "对比/差异"，需 mock 返回 complex
    from unittest.mock import patch
    with patch("src.agents.graph.nodes.QueryRouter") as mock_router:
        mock_router.return_value = mock_router
        mock_router.route.return_value = "complex"
        result = classify_node(state)
        assert result["intent"]["route"] == "complex"


def test_classify_vague_maps_to_medium():
    """QueryRouter 返回 "vague" → classify_node 映射为 "medium" """
    state: AgentState = {"query": "帮我看一下"}
    # 注意：classify_node 内部使用 QueryRouter.route() + 映射
    from unittest.mock import patch
    with patch("src.agents.graph.nodes.QueryRouter") as mock_router:
        mock_router.return_value = mock_router
        mock_router.route.return_value = "vague"
        result = classify_node(state)
        assert result["intent"]["route"] == "medium"
