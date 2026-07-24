"""RAG 问答链 — 编排检索→精排→Prompt构建→流式生成的完整流水线。"""

from typing import Optional



from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.infra.search.bm25_index import BM25Index
from src.infra.db.vector_store import VectorStore
from src.infra.db.mysql_db import MySQLDB
from src.chat.manager import ChatManager
from src.models import get_embeddings, get_llm, get_rerank
from src.rag.context import RAGContext
from src.rag.prompt import build_prompt
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

    # 已删除 — 生产问答路径已迁移至 AgentService.stream_chat()。
    # CLI eval 请直接使用 graph.ainvoke()。

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
