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


# ==================== chat_with_citations 全流程测试 ====================
class TestRAGChainChat:
    """测试 RAGChain.chat_with_citations 的各种场景。"""

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_kb_not_found(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """kb_id="" 时调用 similarity_search_all 且无结果时返回"未找到"提示。"""
        chain = RAGChain()
        chain.vector_store.similarity_search_all = MagicMock(return_value=[])

        gen, citations = chain.chat_with_citations(
            kb_id="",
            session_id="sess1",
            query="test query",
        )
        result = "".join(gen)
        assert "未找到" in result or "相关" in result
        assert len(citations) == 0

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_chat_search_all(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """kb_id="" 时调用 similarity_search_all 而非返回不存在。"""
        mock_rerank = MagicMock()
        mock_rerank.rerank.return_value = [{"index": 0, "relevance_score": 0.9}]
        mock_get_rerank.return_value = mock_rerank
        # mock similarity_search_all 返回匹配结果
        chain = RAGChain()
        chain.vector_store.similarity_search_all = MagicMock(
            return_value=[
                {
                    "id": "a:0",
                    "content": "苹果2024年营收为3910亿美元。",
                    "metadata": {"source": "a.txt", "page": 1, "doc_id": "doc1"},
                    "distance": 0.1,
                }
            ]
        )
        # mock LLM 流式输出
        mock_llm = MagicMock()
        mock_llm.stream.return_value = iter(["这是", "一个", "回答"])
        mock_get_llm.return_value = mock_llm

        gen, citations = chain.chat_with_citations(
            kb_id="",
            session_id="sess_all",
            query="苹果营收",
        )
        result = "".join(gen)
        assert "回答" in result
        assert len(citations) > 0
        chain.vector_store.similarity_search_all.assert_called_once()

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_chat_search_all_no_kbs(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """kb_id="" 且无任何 KB 时返回"未找到"而非报错。"""
        chain = RAGChain()
        chain.vector_store.similarity_search_all = MagicMock(return_value=[])
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        gen, citations = chain.chat_with_citations(
            kb_id="",
            session_id="sess_all_empty",
            query="test",
        )
        result = "".join(gen)
        assert "未找到" in result or "相关" in result
        assert len(citations) == 0

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_vector_search_empty(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """场景 2：向量搜索无结果时返回"未找到"提示。"""
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"
        vs = MagicMock()
        vs.similarity_search.return_value = []  # 模拟空搜索结果

        chain = RAGChain(mysql_db=db, vector_store=vs)
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="test query",
        )
        result = "".join(gen)
        assert "未在文档中找到相关数据" in result
        assert len(citations) == 0

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_vector_search_exception(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """场景 3：向量搜索异常时返回"检索失败"提示。"""
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"
        vs = MagicMock()
        vs.similarity_search.side_effect = Exception("ChromaDB error")

        chain = RAGChain(mysql_db=db, vector_store=vs)
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="test query",
        )
        result = "".join(gen)
        assert "检索失败" in result
        assert len(citations) == 0

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_full_pipeline(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """场景 4：完整流水线 — 检索 + 重排序 + LLM 流式生成 + 引用。"""
        # ---- Mock MySQL：知识库存在 ----
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"

        # ---- Mock VectorStore：返回 2 个检索结果 ----
        vs = MagicMock()
        vs.similarity_search.return_value = [
            {
                "id": "doc1:0",
                "content": "贵州茅台2024年营收1,741亿元",
                "metadata": {"source": "年报2024.pdf", "page": 3, "doc_id": "doc1"},
                "distance": 0.1,  # 距离越小越相关
            },
            {
                "id": "doc1:1",
                "content": "贵州茅台2024年净利润862亿元",
                "metadata": {"source": "年报2024.pdf", "page": 4, "doc_id": "doc1"},
                "distance": 0.2,
            },
        ]

        # ---- Mock ChatManager：空历史 ----
        cm = MagicMock()
        cm.get_window.return_value = []

        # ---- Mock Reranker：按相关性排序 ----
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.95},  # 第 1 个结果更相关
            {"index": 1, "relevance_score": 0.85},
        ]

        # ---- Mock LLM：流式返回 3 个 token ----
        llm = MagicMock()
        llm.stream.return_value = [
            MagicMock(content="贵州"),
            MagicMock(content="茅台"),
            MagicMock(content="2024年营收1,741亿元。"),
        ]

        chain = RAGChain(
            mysql_db=db,
            vector_store=vs,
            chat_manager=cm,
            reranker=reranker,
            llm=llm,
        )
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="茅台营收增长原因",
        )
        result = "".join(gen)
        # 验证流式输出拼接正确
        assert "贵州" in result
        assert "1,741亿元" in result
        # 验证引用：2 个检索结果 → 2 个引用
        assert len(citations) == 2
        assert citations[0].source == "年报2024.pdf"
        assert citations[1].source == "年报2024.pdf"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_rerank_fallback(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """场景 5：重排序失败时降级到原始检索顺序。"""
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"

        vs = MagicMock()
        vs.similarity_search.return_value = [
            {
                "id": "doc1:0",
                "content": "content 1",
                "metadata": {"source": "a.pdf", "page": 1, "doc_id": "doc1"},
                "distance": 0.1,
            },
        ]

        cm = MagicMock()
        cm.get_window.return_value = []

        # 模拟 Reranker API 失败
        reranker = MagicMock()
        reranker.rerank.side_effect = Exception("Rerank API error")

        llm = MagicMock()
        llm.stream.return_value = [MagicMock(content="answer")]

        chain = RAGChain(
            mysql_db=db,
            vector_store=vs,
            chat_manager=cm,
            reranker=reranker,
            llm=llm,
        )
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="test query",
        )
        result = "".join(gen)
        assert "answer" in result
        # 降级后仍返回引用（按原始顺序）
        assert len(citations) == 1
        assert citations[0].source == "a.pdf"

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_llm_stream_failure(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """场景 6：LLM 流式生成失败时返回错误提示。"""
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"

        vs = MagicMock()
        vs.similarity_search.return_value = [
            {
                "id": "doc1:0",
                "content": "content",
                "metadata": {"source": "a.pdf", "page": 1, "doc_id": "doc1"},
                "distance": 0.1,
            },
        ]

        cm = MagicMock()
        cm.get_window.return_value = []

        reranker = MagicMock()
        reranker.rerank.return_value = [{"index": 0, "relevance_score": 0.9}]

        # 模拟 LLM 超时
        llm = MagicMock()
        llm.stream.side_effect = Exception("LLM timeout")

        chain = RAGChain(
            mysql_db=db,
            vector_store=vs,
            chat_manager=cm,
            reranker=reranker,
            llm=llm,
        )
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="test query",
        )
        result = "".join(gen)
        assert "生成回答失败" in result  # 提示 LLM 生成失败
        assert len(citations) == 1  # 引用仍然返回

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_short_query_returns_friendly_message(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """场景 7：查询过短时直接返回友好提示，不走检索。"""
        chain = RAGChain()
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb", session_id="test_session", query="你好"
        )
        response = "".join(gen)
        assert "查询内容过短" in response
        assert len(citations) == 0

    # ==================== RAGChain 拆分方法测试 ====================

    @pytest.mark.asyncio
    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_chat_with_citations_delegates_to_split_methods(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """chat_with_citations() 应委托给 search/rerank/stream_answer。"""
        chain = RAGChain()

        async def mock_search(query, kb_id):
            return [
                {
                    "id": "1",
                    "content": "test",
                    "metadata": {"source": "a.pdf", "page": 1, "doc_id": "d1"},
                }
            ]

        chain.search = mock_search
        chain.rerank = MagicMock(return_value=[])
        chain._stream_answer = MagicMock(return_value=iter(["answer"]))
        chain.chat_manager = MagicMock()
        chain.chat_manager.get_window.return_value = []
        chain._build_prompt = MagicMock(return_value=[])

        gen, citations = chain.chat_with_citations("kb_123", "session_1", "query")
        result = "".join(gen)
        assert "answer" in result


# ==================== 内部辅助方法测试 ====================
class TestRAGChainHelpers:
    """测试 RAGChain 的内部辅助方法。"""

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_saves_user_message_to_history(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """用户消息保存：chat_with_citations 应将用户查询写入历史。"""
        db = MagicMock()
        db.get_kb_by_name.return_value = "kb_123"

        vs = MagicMock()
        vs.similarity_search.return_value = [
            {
                "id": "doc1:0",
                "content": "content",
                "metadata": {"source": "a.pdf", "page": 1, "doc_id": "doc1"},
                "distance": 0.1,
            },
        ]

        cm = MagicMock()
        cm.get_window.return_value = []

        reranker = MagicMock()
        reranker.rerank.return_value = [{"index": 0, "relevance_score": 0.9}]

        llm = MagicMock()
        llm.stream.return_value = [MagicMock(content="answer")]

        chain = RAGChain(
            mysql_db=db,
            vector_store=vs,
            chat_manager=cm,
            reranker=reranker,
            llm=llm,
        )
        gen, citations = chain.chat_with_citations(
            kb_id="test_kb",
            session_id="sess1",
            query="贵州茅台2024年营收多少",
        )
        _ = "".join(gen)  # 消费生成器，触发保存逻辑
        # 验证用户查询被写入 ChatManager
        cm.add_message.assert_called_once_with(
            "sess1", "user", "贵州茅台2024年营收多少"
        )


# ==================== 查询改写测试 ====================


