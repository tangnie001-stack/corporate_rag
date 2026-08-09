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

from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.chat_message import ChatMessage
from src.rag import retrieval


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

    def test_rerank_passthrough_entities(self):
        """rerank 后 chunk metadata 中的实体键应透传到 RAGContext.entities。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
        ]
        results = [
            ChunkResult(
                id="d1:0",
                content="营收增长",
                metadata={
                    "source": "neusoft_2025_q1.pdf",
                    "page": 3,
                    "doc_id": "d1",
                    "company": "东软集团",
                    "report_period": "2025年第一季度",
                    "sec_code": "600718",
                },
            )
        ]
        contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].entities == {
            "company": "东软集团",
            "report_period": "2025年第一季度",
            "sec_code": "600718",
        }
        # to_prompt_text 渲染实体锚点
        text = contexts[0].to_prompt_text()
        assert "公司: 东软集团" in text
        assert "期间: 2025年第一季度" in text
        assert "代码: 600718" in text

    def test_rerank_empty_results(self):
        """空结果应返回空列表。"""
        contexts = retrieval.rerank_results("query", [], MagicMock())
        assert contexts == []

    def test_rerank_filters_below_threshold(self):
        """低于 RERANK_MIN_SCORE 的 context 应被过滤。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.1},
        ]
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(2)
        ]
        with patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].source == "a0.pdf"

    def test_rerank_all_below_threshold_returns_empty(self):
        """全部低于阈值应返回空列表。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.1},
            {"index": 1, "relevance_score": 0.05},
        ]
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(2)
        ]
        with patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert contexts == []

    def test_rerank_fallback_skips_threshold(self):
        """rerank 失败 fallback（1-distance 分数）不应用阈值。"""
        reranker = MagicMock()
        reranker.rerank.side_effect = RuntimeError("rerank down")
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                distance=0.5,  # 1-0.5=0.5，若应用阈值 0.3 会保留；用 0.9 距离=0.1 分验证不过滤
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(1)
        ]
        # 距离 0.9 → fallback 分数 0.1 < 0.3，若应用阈值会被过滤；不应被过滤
        results[0].distance = 0.9
        with (
            patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3),
            patch("src.rag.retrieval.with_retry", side_effect=lambda f, **kw: f),
        ):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1


# ==================== to_prompt_text 实体渲染测试 ====================
def test_to_prompt_text_with_entities():
    """RAGContext 带实体时，to_prompt_text 应按 ENTITY_RENDER_ORDER 渲染存在的实体。"""
    from src.rag.context import RAGContext

    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
        entities={"company": "东软集团", "report_period": "2025年第一季度"},
    )
    text = ctx.to_prompt_text()
    assert "东软集团" in text
    assert "2025年第一季度" in text


def test_to_prompt_text_without_entities():
    """无实体时，to_prompt_text 应保持原格式（来源/页码/内容）。"""
    from src.rag.context import RAGContext

    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
    )
    text = ctx.to_prompt_text()
    assert text == "来源: neusoft_2025_q1.pdf (第3页)\n内容: 营收增长"


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
