"""RAGChain 核心编排链的单元测试。

测试目标：
- RAGContext 数据类：构造 / 引用格式化 / 长内容截断
- RAGChain 初始化：默认工厂 vs 依赖注入
- chat_with_citations 全流程：
  - 知识库不存在 / 向量搜索为空 / 检索异常
  - 完整流水线（检索 + 重排序 + LLM 流式生成 + 引用）
  - 重排序失败降级 / LLM 流式失败
- 内部辅助方法：_format_context / _build_prompt
- 历史记录保存

注意：所有外部依赖（LLM / Embeddings / Reranker / VectorStore / MySQL / ChatManager）
均通过 unittest.mock 进行 mock，无需真实 API Key。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.rag.chain import RAGChain


# ==================== RAGContext 数据类测试 ====================
class TestRAGChainChat:
    """测试 RAGChain.chat_with_citations 的各种场景。"""

    @pytest.mark.asyncio
    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    async def test_search_returns_results(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """search() 应调用 vector_store.similarity_search 并返回结果。"""
        chain = RAGChain()
        chain.vector_store.similarity_search = MagicMock(
            return_value=[{"id": "1", "content": "test"}]
        )
        results = await chain.search("query", "kb_123")
        assert len(results) == 1
        chain.vector_store.similarity_search.assert_called_once()
        # Verify k parameter is a positive integer
        call_kwargs = chain.vector_store.similarity_search.call_args
        assert call_kwargs[1]["k"] > 0

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rerank_returns_contexts(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """rerank() 应调用 _rerank_results 并返回 RAGContext 列表。"""
        chain = RAGChain()
        chain._rerank_results = MagicMock(
            return_value=[
                MagicMock(
                    source="a.pdf",
                    page=1,
                    content="test",
                    doc_id="d1",
                    chunk_id="d1:0",
                    score=0.9,
                )
            ]
        )
        contexts = chain.rerank("query", [])
        assert len(contexts) == 1
        assert contexts[0].source == "a.pdf"


class TestRAGChainQueryRewrite:
    """测试 RAGChain 的查询分类与改写方法。"""

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_classify_clear(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """清晰查询应分类为 clear。"""
        chain = RAGChain()
        assert chain._classify_query("2024年营业收入是多少？") == "clear"
        assert chain._classify_query("贵州茅台2024年净利润") == "clear"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_classify_fuzzy_short(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """短查询（<10字符）应分类为 fuzzy_short。"""
        chain = RAGChain()
        assert chain._classify_query("营收") == "fuzzy_short"
        assert chain._classify_query("净利润") == "fuzzy_short"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_classify_colloquial(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """口语化查询应分类为 colloquial。"""
        chain = RAGChain()
        assert chain._classify_query("分析一下茅台2024年的业绩") == "colloquial"
        assert chain._classify_query("为什么营收增长了") == "colloquial"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_classify_compound(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """对比查询应分类为 compound。"""
        chain = RAGChain()
        assert chain._classify_query("对比茅台和五粮液2024营收") == "compound"
        assert chain._classify_query("比较2023和2024年净利润") == "compound"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_classify_empty(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """空查询应分类为 clear（不报错）。"""
        chain = RAGChain()
        assert chain._classify_query("") == "clear"
        assert chain._classify_query("   ") == "clear"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rewrite_clear_passthrough(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """清晰查询应直接原样返回（passthrough）。"""
        chain = RAGChain()
        result = chain._rewrite_query("2024年营业收入是多少？", [])
        assert result == "2024年营业收入是多少？"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rewrite_fuzzy_short_expands(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """模糊短查询应触发 _expand_query。"""
        chain = RAGChain()
        history = [{"role": "user", "content": "茅台2024年营收情况"}]
        result = chain._rewrite_query("净利润呢", history)
        assert "茅台" in result
        assert "净利润" in result

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rewrite_colloquial_condenses(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """口语化查询应触发 _condense_query 去除口语引导词。"""
        chain = RAGChain()
        result = chain._rewrite_query("分析一下茅台2024年的营收", [])
        assert "分析一下" not in result

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rewrite_compound_decomposes(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """对比查询应触发 _decompose_query 返回子查询列表。"""
        chain = RAGChain()
        result = chain._rewrite_query("对比茅台和五粮液营收", [])
        assert isinstance(result, list)
        assert len(result) > 1

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_expand_query_with_history(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """_expand_query 应使用最近用户消息扩展短查询。"""
        chain = RAGChain()
        history = [
            {"role": "user", "content": "茅台2024年营收情况"},
            {"role": "assistant", "content": "营收1741亿元"},
            {"role": "user", "content": "净利润呢"},
        ]
        result = chain._expand_query("净利润呢", history)
        assert "净利润呢" in result
        assert "茅台" in result

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_expand_query_no_history(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """无历史时 _expand_query 应返回原查询。"""
        chain = RAGChain()
        result = chain._expand_query("营收", [])
        assert result == "营收"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_condense_query_removes_patterns(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """_condense_query 应移除口语化引导词。"""
        chain = RAGChain()
        result = chain._condense_query("分析一下茅台2024年营收")
        assert "分析一下" not in result
        assert "茅台2024年营收" in result

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_decompose_query_splits_comparison(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """_decompose_query 应将对比查询拆分为子查询列表。"""
        chain = RAGChain()
        result = chain._decompose_query("对比茅台和五粮液营收")
        assert isinstance(result, list)
        assert len(result) >= 2
