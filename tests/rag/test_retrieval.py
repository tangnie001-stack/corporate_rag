"""检索模块（retrieval）单元测试。

测试目标：
- search()：向量检索功能
- rerank_results()：重排序功能
- expand_query() / condense_query() / decompose_query()：查询改写
- rewrite_query()：集成改写入口

注意：外部依赖通过 unittest.mock 进行 mock。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.rag import retrieval
from src.infra.db.entities.search import ChunkResult
from src.infra.llm.chat_message import ChatMessage


# ==================== 检索测试 ====================
class TestSearch:
    """测试 search() 检索功能。"""

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """search() 应调用 vector_store.similarity_search 并返回结果。"""
        vs = MagicMock()
        vs.similarity_search = MagicMock(return_value=[{"id": "1", "content": "test"}])
        with patch("src.rag.retrieval.HYBRID_SEARCH_ENABLED", False):
            results = await retrieval.search("query", "kb_123", vector_store=vs)
        assert len(results) == 1
        vs.similarity_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_all_when_no_kb(self):
        """无 kb_id 时应调用 similarity_search_all。"""
        vs = MagicMock()
        vs.similarity_search_all = MagicMock(
            return_value=[{"id": "1", "content": "test"}]
        )
        with patch("src.rag.retrieval.HYBRID_SEARCH_ENABLED", False):
            results = await retrieval.search("query", "", vector_store=vs)
        assert len(results) == 1
        vs.similarity_search_all.assert_called_once()


# ==================== 重排序测试 ====================
class TestRerank:
    """测试 rerank_results() 重排序功能。"""

    def test_rerank_returns_contexts(self):
        """rerank_results() 应返回 RAGContext 列表。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
        ]
        results = [
            ChunkResult(
                id="d1:0",
                content="test content",
                metadata={"source": "a.pdf", "page": 1, "doc_id": "d1"},
            )
        ]
        contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].source == "a.pdf"
        assert contexts[0].doc_id == "d1"

    def test_rerank_empty_results(self):
        """空结果应返回空列表。"""
        contexts = retrieval.rerank_results("query", [], MagicMock())
        assert contexts == []



# ==================== 查询改写测试 ====================
class TestQueryRewrite:
    """测试 rewrite_query() 及辅助改写函数。"""

    def test_simple_passthrough(self):
        """简单查询应直接原样返回（passthrough）。"""
        result = retrieval.rewrite_query("2024年营业收入是多少？", [])
        assert result == "2024年营业收入是多少？"

    def test_short_query_expands(self):
        """模糊短查询应触发 expand_query。"""
        history = [ChatMessage(role="user", content="茅台2024年营收情况")]
        result = retrieval.rewrite_query("净利润呢", history)
        assert "茅台" in result
        assert "净利润" in result

    def test_medium_condenses(self):
        """分析类查询应触发 condense_query 去除口语引导词。"""
        result = retrieval.rewrite_query("分析一下茅台2024年的营收", [])
        assert "分析一下" not in result

    def test_complex_decomposes(self):
        """对比查询应触发 decompose_query 返回子查询列表。"""
        result = retrieval.rewrite_query(
            "对比茅台和五粮液营收", [], intent_route="complex"
        )
        assert isinstance(result, list)
        assert len(result) > 1

    def test_expand_with_history(self):
        """expand_query 应使用最近用户消息扩展短查询。"""
        history = [
            ChatMessage(role="user", content="茅台2024年营收情况"),
            ChatMessage(role="assistant", content="营收1741亿元"),
            ChatMessage(role="user", content="净利润呢"),
        ]
        result = retrieval.expand_query("净利润呢", history)
        assert "净利润呢" in result
        assert "茅台" in result

    def test_expand_no_history(self):
        """无历史时 expand_query 应返回原查询。"""
        result = retrieval.expand_query("营收", [])
        assert result == "营收"

    def test_condense_removes_patterns(self):
        """condense_query 应移除口语化引导词。"""
        result = retrieval.condense_query("分析一下茅台2024年营收")
        assert "分析一下" not in result
        assert "茅台2024年营收" in result

    def test_decompose_splits_comparison(self):
        """decompose_query 应将对比查询拆分为子查询列表。"""
        result = retrieval.decompose_query("对比茅台和五粮液营收")
        assert isinstance(result, list)
        assert len(result) >= 2
