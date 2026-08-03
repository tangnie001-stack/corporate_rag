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


def test_generate_node_abstention_when_no_contexts():
    """无 contexts 且非 skip_retrieval 时，generate_node 应返回 abstention 静态文案。"""
    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.config.prompts import ABSTENTION_TEXT
    from unittest.mock import Mock

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="阿里巴巴",
        contexts=[],
        skip_retrieval=False,
    )
    result = node(state)
    assert result["answer"] == ABSTENTION_TEXT
    assert result["model_used"] == ""
    assert result["is_fallback"] is False


def test_grader_node_short_circuit_on_first_fail():
    """首轮 score<0.5 且无上一轮改写记录时，应记录 _prev_rewritten_query 并走重试。"""
    from src.agents.graph.nodes import grader_node
    from src.agents.graph.state import AgentState
    from src.infra.db.vector_store.types import ChunkResult

    results = [ChunkResult(id="c1", content="无关内容", metadata={})]
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="本季度营收情况如何？ 阿里巴巴",
        retrieval_results=results,
        retrieval_retries=0,
    )
    with patch(
        "src.agents.graph.nodes.RetrievalGrader.grade", return_value=0.0
    ):
        result = grader_node(state)
    assert result["retrieval_retries"] == 1
    assert result["_prev_rewritten_query"] == "本季度营收情况如何？ 阿里巴巴"
    assert result.get("downgraded") is not True


def test_grader_node_short_circuit_when_rewrite_unchanged():
    """本轮改写查询与上一轮相同（rewrite 无信息增量）时直接降级，不再重试。"""
    from src.agents.graph.nodes import grader_node
    from src.agents.graph.state import AgentState
    from src.infra.db.vector_store.types import ChunkResult
    from src.config.const import DOWNGRADE_REASON_REWRITE_NO_INCREMENT

    results = [ChunkResult(id="c1", content="无关内容", metadata={})]
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="本季度营收情况如何？ 阿里巴巴",
        retrieval_results=results,
        retrieval_retries=1,
        _prev_rewritten_query="本季度营收情况如何？ 阿里巴巴",
    )
    with patch(
        "src.agents.graph.nodes.RetrievalGrader.grade", return_value=0.0
    ):
        result = grader_node(state)
    assert result["downgraded"] is True
    assert result["downgrade_reason"] == DOWNGRADE_REASON_REWRITE_NO_INCREMENT


def test_grader_node_still_retries_when_rewrite_changed():
    """本轮改写查询与上一轮不同时，应继续重试（最多到 retries<2）。"""
    from src.agents.graph.nodes import grader_node
    from src.agents.graph.state import AgentState
    from src.infra.db.vector_store.types import ChunkResult

    results = [ChunkResult(id="c1", content="无关内容", metadata={})]
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="腾讯2024年营收",
        retrieval_results=results,
        retrieval_retries=1,
        _prev_rewritten_query="本季度营收情况如何？ 阿里巴巴",
    )
    with patch(
        "src.agents.graph.nodes.RetrievalGrader.grade", return_value=0.0
    ):
        result = grader_node(state)
    assert result["retrieval_retries"] == 2
    assert result["_prev_rewritten_query"] == "腾讯2024年营收"
    assert result.get("downgraded") is not True


def test_generate_node_skip_retrieval_uses_simple_prompt():
    """skip_retrieval=True 时即使有 contexts 也走 build_simple_prompt。"""
    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext
    from unittest.mock import Mock, patch

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="你好",
        rewritten_query="你好",
        contexts=[
            RAGContext(content="随机内容", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="c1", score=0.8),
        ],
        skip_retrieval=True,
    )
    with patch("src.agents.graph.nodes.build_simple_prompt", return_value=[]) as m:
        with patch("src.agents.graph.nodes.stream_answer", return_value=iter(["你好！"])):
            result = node(state)
    m.assert_called_once()
    assert "你好" in result["answer"]
