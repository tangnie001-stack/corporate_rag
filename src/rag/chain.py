"""RAG 问答链 — 编排检索→精排→Prompt构建→流式生成的完整流水线。"""

from typing import Generator, Optional

from loguru import logger

from src.config import (
    HYBRID_SEARCH_ENABLED, BM25_INDEX_DIR,
)
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.infra.search.query_router import QueryRouter
from src.infra.search.bm25_index import BM25Index
from src.infra.db.vector_store import VectorStore
from src.infra.db.mysql_db import MySQLDB
from src.chat import ChatManager
from src.models import get_embeddings, get_llm, get_rerank
from src.rag.context import RAGContext
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.prompt import build_prompt, build_simple_prompt
from src.rag.stream import stream_answer


class RAGChain:
    """RAG 问答链 — 编排检索、重排序、prompt 构建和流式生成的完整流水线。"""

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        mysql_db: Optional[MySQLDB] = None,
        chat_manager: Optional[ChatManager] = None,
        llm=None,
        embeddings=None,
        reranker=None,
    ) -> None:
        self.vector_store = vector_store or VectorStore()
        self.db = mysql_db or MySQLDB()
        self.chat_manager = chat_manager or ChatManager()
        self._llm = llm
        self._embeddings = embeddings
        self._reranker = reranker
        self._tracer = LangfuseTracer()
        self._prompt_manager = PromptManager()
        self.router = QueryRouter()
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    @property
    def embeddings(self):
        if self._embeddings is None:
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = get_rerank()
        return self._reranker

    @property
    def prompt_manager(self):
        return self._prompt_manager

    # ═══════════ API 端调用方法 — SSR 流式端点专用 ═══════════

    async def search(self, query: str, kb_id: str) -> list[dict]:
        """执行语义检索，委托给 retrieval.search。

        Args:
            query: 用户查询文本
            kb_id: 知识库 UUID（空字符串表示跨库搜索）

        Returns:
            检索结果列表
        """
        import src.rag.retrieval as _retrieval
        return await _retrieval.search(query, kb_id, self.vector_store, self.bm25)

    def rerank(self, query: str, results: list[dict]) -> list[RAGContext]:
        """Reranker 精排，委托给 retrieval.rerank_results。

        Args:
            query: 用户查询文本
            results: 检索结果列表

        Returns:
            精排后的 RAGContext 列表
        """
        import src.rag.retrieval as _retrieval
        return _retrieval.rerank_results(query, results, self.reranker)

    # ═══════════ chat_with_citations — 主入口 ═══════════

    def chat_with_citations(
        self, kb_id: str, session_id: str, query: str,
    ) -> tuple[Generator[str, None, None], list[RAGContext]]:
        """生成带引用来源的流式回答 — RAG 流水线主入口。"""
        trace_id = self._tracer.start_trace(
            "chat_with_citations",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )
        route = self.router.route(query)
        history = self.chat_manager.get_window(session_id)

        # Simple route
        if route == "simple":
            return self._handle_simple_route(query, history, trace_id)

        # Vague / Complex route — 改写查询
        if route in ("vague", "complex"):
            query = self._rewrite_if_needed(query, history)

        # Short query guard
        SHORT_QUERY_THRESHOLD = 5
        if len(query.strip()) < SHORT_QUERY_THRESHOLD:
            return self._handle_short_query(trace_id)

        # 检索
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(
                search(query, kb_id, self.vector_store, self.bm25)
            )
            loop.close()
        except Exception as e:
            return self._handle_search_error(e, trace_id)

        if not results:
            return self._handle_no_results(trace_id)

        # Rerank → Prompt → Stream
        rag_contexts = rerank_results(query, results, self.reranker)
        history = self.chat_manager.get_window(session_id)
        token_generator = self.stream_answer(query, rag_contexts, history, trace_id)
        self.chat_manager.add_message(session_id, "user", query)
        return token_generator, rag_contexts

    # ═══════════ 子方法 ═══════════

    def _handle_simple_route(self, query, history, trace_id):
        """处理 simple 路由：无检索，直接 LLM 回答。"""
        logger.info("Route: simple — direct LLM answer (no RAG)")
        prompt = build_simple_prompt(query, history, self.prompt_manager)
        token_gen = stream_answer(prompt, self.llm, self._tracer, trace_id)
        self.chat_manager.add_message("", "user", query)
        return token_gen, []

    def _handle_short_query(self, trace_id):
        """处理过短查询。"""
        logger.info("Query too short (< {} chars)", 5)
        citations: list[RAGContext] = []

        def _gen():
            yield '查询内容过短，请输入更具体的财务问题（如"2024年营业收入是多少？"）'

        self._tracer.end_trace(trace_id, output="查询内容过短")
        return _gen(), citations

    def _handle_search_error(self, error: Exception, trace_id):
        """处理检索失败。"""
        error_msg = str(error)
        logger.exception("Vector search failed: {}", error_msg)
        citations: list[RAGContext] = []

        def _gen():
            yield f"检索失败: {error_msg}"

        return _gen(), citations

    def _handle_no_results(self, trace_id):
        """处理检索结果为空。"""
        logger.info("No results found")
        citations: list[RAGContext] = []

        def _gen():
            yield "未在文档中找到相关数据。"

        return _gen(), citations

    def _rewrite_if_needed(self, query: str, history: list) -> str:
        """根据需要执行查询改写。"""
        rewritten = rewrite_query(query, history)
        if isinstance(rewritten, list):
            rewritten = " ".join(rewritten)
        return rewritten

    # ═══════════ 公共方法 ═══════════

    def stream_answer(self, query, contexts, history, trace_id=None):
        """构建 prompt 并流式生成回答，完成后记录 token 用量。"""
        from src.rag.prompt import format_context
        from src.rag.stream import estimate_usage
        context_str = format_context(contexts)
        prompt = build_prompt(query, context_str, history, self.prompt_manager)
        internal_gen = stream_answer(prompt, self.llm, self._tracer, trace_id)
        full_text = ""
        for token in internal_gen:
            full_text += token
            yield token
        # 生成完成后估算 token 用量，供 chat.py 读取
        usage = estimate_usage(prompt, full_text)
        self._last_token_usage = {
            "prompt_tokens": usage.get("input", 0),
            "completion_tokens": usage.get("output", 0),
            "total_tokens": usage.get("input", 0) + usage.get("output", 0),
        }
