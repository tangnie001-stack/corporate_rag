"""Tests for LangGraph node functions."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agents.graph.nodes import make_classify_node
from src.agents.graph.state import AgentState, LangGraphNode, RAGQueryIntent


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


def test_route_by_intent_skips_clarify_when_skip_clarify():
    """skip_clarify=True 时即使有 missing_entities 也走正常路由（评估模式跳过追问）。"""
    from src.agents.graph.workflow import route_by_intent

    state = AgentState(
        query="联营公司非经常性损益影响金额",
        intent=RAGQueryIntent(route="medium"),
        missing_entities=[{"type": "year", "question": "哪一年？"}],
        skip_clarify=True,
    )
    assert route_by_intent(state) == "medium"


@pytest.mark.asyncio
async def test_make_classify_node_returns_expected_keys():
    """make_classify_node 返回的节点应包含所有预期 key。"""
    mock_llm = Mock()
    mock_route_result = {
        LangGraphNode.Classify.INTENT: RAGQueryIntent(route="simple"),
        LangGraphNode.Classify.EXTRACTED_ENTITIES: [],
        LangGraphNode.Classify.MISSING_ENTITIES: [],
        LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: 1.0,
    }
    with patch("src.agents.graph.nodes.QueryRouter") as mock_router_cls:
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.route.return_value = mock_route_result
        node = make_classify_node(llm=mock_llm)
        state = AgentState(query="2024年营收多少")
        result = await node(state)
        assert result[LangGraphNode.Classify.INTENT].route == "simple"
        assert result[LangGraphNode.Classify.EXTRACTED_ENTITIES] == []
        assert result[LangGraphNode.Classify.MISSING_ENTITIES] == []
        assert result[LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE] == 1.0
        # 无 _resolved_kb_ids 时聚合为空，但 KB 透传字段仍需存在
        assert result[LangGraphNode.Classify.KB_ENTITIES] == "无"
        assert result[LangGraphNode.Classify.KB_SUGGESTIONS] == {}


@pytest.mark.asyncio
async def test_make_classify_node_passes_kb_aggregate_to_state():
    """classify_node 应把聚合的 KB 候选透传进返回 dict（agent_service 从 CHAIN_END 读）。"""
    from src.infra.search.query_router import KbEntityAggregate

    mock_llm = Mock()
    mock_aggregate = KbEntityAggregate(
        text="公司: 腾讯", companies=["腾讯"], periods=[], codes=[]
    )
    mock_route_result = {
        LangGraphNode.Classify.INTENT: RAGQueryIntent(route="medium"),
        LangGraphNode.Classify.EXTRACTED_ENTITIES: [],
        LangGraphNode.Classify.MISSING_ENTITIES: [
            {"type": "company", "question": "您想查询哪家公司？"}
        ],
        LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: 0.8,
    }
    with (
        patch("src.agents.graph.nodes.QueryRouter") as mock_router_cls,
        patch(
            "src.agents.graph.nodes.aggregate_kb_entities",
            new=AsyncMock(return_value=mock_aggregate),
        ),
    ):
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.route.return_value = mock_route_result
        node = make_classify_node(llm=mock_llm)
        state = AgentState(query="营收多少", _resolved_kb_ids=["kb-1"])
        result = await node(state)

        mock_router.route.assert_called_once_with(
            "营收多少", state._history, kb_entities="公司: 腾讯"
        )
        # 返回 dict 含 KB 透传字段（agent_service 从 classify CHAIN_END output 读取）
        assert result[LangGraphNode.Classify.KB_ENTITIES] == "公司: 腾讯"
        assert result[LangGraphNode.Classify.KB_SUGGESTIONS] == {
            "company": ["腾讯", "其他"]
        }


@pytest.mark.asyncio
async def test_make_classify_node_skips_aggregate_for_greeting():
    """问候/短查询应跳过 KB 聚合（route 短路，无需候选），仍透传空聚合。"""
    mock_llm = Mock()
    mock_route_result = {
        LangGraphNode.Classify.INTENT: RAGQueryIntent(route="simple"),
        LangGraphNode.Classify.EXTRACTED_ENTITIES: [],
        LangGraphNode.Classify.MISSING_ENTITIES: [],
        LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: 1.0,
    }
    with (
        patch("src.agents.graph.nodes.QueryRouter") as mock_router_cls,
        patch(
            "src.agents.graph.nodes.aggregate_kb_entities", new=AsyncMock()
        ) as mock_agg,
    ):
        mock_router = Mock()
        mock_router_cls.return_value = mock_router
        mock_router.route.return_value = mock_route_result
        node = make_classify_node(llm=mock_llm)
        state = AgentState(query="你好", _resolved_kb_ids=["kb-1"])
        result = await node(state)

        mock_agg.assert_not_called()
        assert result[LangGraphNode.Classify.KB_ENTITIES] == "无"
        assert result[LangGraphNode.Classify.KB_SUGGESTIONS] == {}


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
    from unittest.mock import Mock

    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.config.prompts import ABSTENTION_TEXT

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="阿里巴巴",
        contexts=[],
        skip_retrieval=False,
    )
    result = node(state)
    assert result["answer"] == ABSTENTION_TEXT
    # Mock 无 model 属性时 getattr 返回 Mock 对象，被 or "" 归一为空串
    assert result["model_used"] == ""
    assert result["is_fallback"] is False


def test_generate_node_abstention_uses_llm_model_attr():
    """llm 实例带 model 属性时，abstention 分支应展示该模型名。"""
    from unittest.mock import Mock

    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.config.prompts import ABSTENTION_TEXT

    llm = Mock()
    llm.model = "qwen-custom-model"
    node = make_generate_node(llm=llm, prompt_manager=Mock())
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="阿里巴巴",
        contexts=[],
        skip_retrieval=False,
    )
    result = node(state)
    assert result["answer"] == ABSTENTION_TEXT
    assert result["model_used"] == "qwen-custom-model"


def test_rerank_node_medium_uses_original_query():
    """medium 路由下 rerank 打分应使用原始 query，而非 rewritten_queries。"""
    from src.agents.graph.nodes import make_rerank_node
    from src.agents.graph.state import AgentState, RAGQueryIntent
    from src.infra.db.vector_store.types import ChunkResult

    def _cr(content, cid) -> ChunkResult:
        return ChunkResult(content=content, id=cid, distance=0.3)

    calls = []

    class FakeReranker:
        def rerank(self, docs, query):
            calls.append(query)
            return [
                {"index": i, "relevance_score": 0.5 - i * 0.1} for i in range(len(docs))
            ]

    state = AgentState(
        query="毛利率呢",
        intent=RAGQueryIntent(route="medium"),
        rewritten_queries=["腾讯2024年毛利率是多少", "毛利率呢"],
        retrieval_results=[_cr("毛利率数据", "c1"), _cr("营收数据", "c2")],
    )
    node = make_rerank_node(FakeReranker())
    out = node(state)
    assert calls[0] == "毛利率呢"  # medium 用原 query 打分
    assert "contexts" in out


def test_rerank_node_complex_scores_each_sub_query_and_dedups():
    """complex 路由下应逐子查询打分，并按 chunk_id 去重合并。"""
    from src.agents.graph.nodes import make_rerank_node
    from src.agents.graph.state import AgentState, RAGQueryIntent
    from src.infra.db.vector_store.types import ChunkResult

    def _cr(content, cid) -> ChunkResult:
        return ChunkResult(content=content, id=cid, distance=0.3)

    calls = []

    class FakeReranker:
        def rerank(self, docs, query):
            calls.append(query)
            return [
                {"index": i, "relevance_score": 0.5 - i * 0.1} for i in range(len(docs))
            ]

    state = AgentState(
        query="腾讯2024年营收和毛利率",
        intent=RAGQueryIntent(route="complex"),
        rewritten_queries=["腾讯2024年营收", "腾讯2024年毛利率"],
        retrieval_results=[_cr("营收数据", "c1"), _cr("毛利率数据", "c2")],
    )
    node = make_rerank_node(FakeReranker())
    out = node(state)
    assert calls == ["腾讯2024年营收", "腾讯2024年毛利率"]  # complex 逐子查询打分
    assert len(out["contexts"]) == 2  # 合并去重，c1/c2 各出现一次
    assert {c.chunk_id for c in out["contexts"]} == {"c1", "c2"}


def test_graph_no_grader_node():
    """图结构断言：grader 节点已删除，retrieve 直连 rerank。"""
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
    assert "grader" not in node_names
    assert node_names == {
        "kb_router",
        "classify",
        "rewrite",
        "retrieve",
        "rerank",
        "generate",
        "format",
    }


def test_generate_node_skip_retrieval_uses_simple_prompt():
    """skip_retrieval=True 时即使有 contexts 也走 build_simple_prompt。"""
    from unittest.mock import Mock, patch

    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="你好",
        rewritten_query="你好",
        contexts=[
            RAGContext(
                content="随机内容",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="c1",
                score=0.8,
            ),
        ],
        skip_retrieval=True,
    )
    with (
        patch("src.agents.graph.nodes.build_simple_prompt", return_value=[]) as m,
        patch("src.agents.graph.nodes.stream_answer", return_value=iter(["你好！"])),
    ):
        result = node(state)
    m.assert_called_once()
    assert "你好" in result["answer"]
