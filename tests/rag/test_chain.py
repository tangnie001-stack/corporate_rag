"""RAGChain 核心编排链的单元测试。

测试目标：
- RAGContext 数据类：构造 / 引用格式化 / 长内容截断
- RAGChain 初始化：默认工厂 vs 依赖注入

注意：所有外部依赖（LLM / Embeddings / Reranker / VectorStore / MySQL / ChatManager）
均通过 unittest.mock 进行 mock，无需真实 API Key。
"""

from unittest.mock import MagicMock, patch


from src.rag.chain import RAGChain, RAGContext


# ==================== RAGContext 数据类测试 ====================
class TestRAGContext:
    """测试 RAGContext 数据类的字段、引用格式化和默认值。"""

    def test_create_context(self):
        """基本构造：所有字段正确存储。"""
        ctx = RAGContext(
            content="test content",
            source="年报2023.pdf",
            page=5,
            doc_id="doc123",
            chunk_id="doc123:0",
            score=0.95,
        )
        assert ctx.content == "test content"
        assert ctx.source == "年报2023.pdf"
        assert ctx.page == 5

    def test_to_citation(self):
        """引用格式化：必须包含文件名和页码。"""
        ctx = RAGContext(
            content="贵州茅台2024年营收1,741亿元",
            source="年报2024.pdf",
            page=3,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.9,
        )
        citation = ctx.to_citation()
        assert "年报2024.pdf" in citation  # 包含来源文件名
        assert "第3页" in citation  # 包含页码引用

    def test_to_citation_truncates_long_content(self):
        """长内容截断：引用文本不应超过 300 字符。"""
        long_content = "A" * 500
        ctx = RAGContext(
            content=long_content,
            source="test.pdf",
            page=1,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.5,
        )
        citation = ctx.to_citation()
        # 内容截断到 200 字符 + 前缀信息，总长 < 300
        assert len(citation) < 300

    def test_default_score(self):
        """默认分数：未传入 score 时应为 0.0。"""
        ctx = RAGContext(
            content="test",
            source="test.pdf",
            page=1,
            doc_id="doc1",
            chunk_id="doc1:0",
        )
        assert ctx.score == 0.0


# ==================== RAGChain 初始化测试 ====================
class TestRAGChainInit:
    """测试 RAGChain 的初始化与依赖注入。"""

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_init_defaults(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """默认初始化：调用工厂函数创建所有依赖。"""
        chain = RAGChain()
        assert chain.llm is not None
        assert chain.embeddings is not None
        assert chain.reranker is not None
        assert chain.vector_store is not None
        assert chain.db is not None
        assert chain.chat_manager is not None

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_init_custom_deps(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """依赖注入：传入自定义依赖时不调用工厂函数。"""
        vs = MagicMock()
        db = MagicMock()
        cm = MagicMock()

        chain = RAGChain(
            vector_store=vs,
            mysql_db=db,
            chat_manager=cm,
        )
        # 验证依赖注入生效
        assert chain.vector_store is vs
        assert chain.db is db
        assert chain.chat_manager is cm
        # 工厂函数不应被调用（因为依赖已注入）
        mock_get_emb.assert_not_called()
        mock_get_llm.assert_not_called()
        mock_get_rerank.assert_not_called()


