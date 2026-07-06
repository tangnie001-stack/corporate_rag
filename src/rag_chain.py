"""RAG 问答链模块 — 编排"检索 → 重排序 → 生成回答"的完整 RAG 流水线。

本模块是 RAG 系统的核心编排层，负责：
  1. 在 ChromaDB 中进行语义检索（按 kb_id 指定知识库，或空 kb_id 搜索全部）
  2. 调用 Reranker 对候选结果精排（保留 top-N）
  3. 构建 prompt（系统指令 + 对话历史 + 文档上下文 + 用户问题）
  4. 流式生成回答（减少用户感知延迟）
  5. 返回引用来源（citation），支持回答溯源

所有模型（LLM、Embedding、Reranker）采用延迟初始化（lazy init），
只在首次实际使用时才创建实例，避免模块导入阶段产生网络请求。

依赖关系：
  VectorStore（向量检索）→ RAGChain._rerank_results（精排）→ RAGChain._stream_answer（生成）
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Generator, Optional

from loguru import logger
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from src.config import (
    TOP_K_RETRIEVAL,
    TOP_K_RERANK,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_INTERVAL,
    RETRY_BACKOFF_FACTOR,
    HYBRID_SEARCH_ENABLED,
    BM25_INDEX_DIR,
)
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.infra.search.query_router import QueryRouter
from src.models import get_embeddings, get_llm, get_rerank
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.infra.db.vector_store import VectorStore
from src.infra.db.mysql_db import MySQLDB
from src.chat_manager import ChatManager


def _estimate_usage(messages: list, output: str) -> dict:
    """粗略估算 token 用量用于 Langfuse 展示。

    不对消息做完整 tokenize（性能考虑），按中文字符数除以 2 估算。
    Langfuse 只需近似值而非精确统计。

    Args:
        messages: LangChain 消息列表
        output: LLM 生成的完整回答文本

    Returns:
        包含 input/output token 估算值和单位的字典
    """
    input_text = " ".join(
        getattr(m, "content", "") for m in messages if hasattr(m, "content")
    )
    input_tokens = max(1, len(input_text) // 2)
    output_tokens = max(1, len(output) // 2)
    return {
        "input": input_tokens,
        "output": output_tokens,
        "unit": "TOKENS",
    }


@dataclass
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。

    在 RAG 流水线中，从检索到精排后保留的每个分块都会包装为 RAGContext，
    最终用于构建 prompt 和生成引用（citation）。

    Attributes:
        content: 分块的原文内容（优先使用父级上下文）
        source: 原始文件名（如 "2024年年报.pdf"）
        page: 所在页码
        doc_id: 所属文档的 UUID
        chunk_id: 分���在 ChromaDB 中的 ID
        parent_content: 父级块完整内容（分块策略输出），用于替换 content 提供更完整上下文
        score: 重排序相关性分数（越高越相关）
    """

    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0

    def to_citation(self) -> str:
        """将上下文格式化为 Markdown 引用块，用于在回答末尾展示来源。

        Returns:
            Markdown 格式的引用字符串，包含来源文件名、页码和内容摘要（前 200 字）
        """
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"


class RAGChain:
    """RAG 问答链 — 编排检索、重排序、prompt 构建和流式生成的完整流水线。

    流水线步骤：
      kb_id → 向量检索（单 KB 或全部）→ Rerank 精排 → 构建 prompt → LLM 流式生成 → 引用

    LLM、Embedding、Reranker 均通过 @property 延迟初始化，
    工厂函数只在实际需要时才被调用。
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        mysql_db: Optional[MySQLDB] = None,
        chat_manager: Optional[ChatManager] = None,
        llm=None,
        embeddings=None,
        reranker=None,
    ) -> None:
        """初始化 RAGChain，所有依赖均可选注入（方便测试时 mock）。

        Args:
            vector_store: 向量存储实例，默认创建新的 VectorStore
            mysql_db: MySQL 数据库实例，默认创建新的 MySQLDB
            chat_manager: 对话管理实例，默认创建新的 ChatManager
            llm: LLM 实例（可选，延迟初始化）
            embeddings: Embedding 实例（可选，延迟初始化）
            reranker: Reranker 实例（可选，延迟初始化）
        """
        self.vector_store = vector_store or VectorStore()
        self.db = mysql_db or MySQLDB()
        self.chat_manager = chat_manager or ChatManager()
        # 以下三个模型实例在首次访问时才初始化（见 @property）
        self._llm = llm
        self._embeddings = embeddings
        self._reranker = reranker

        # Langfuse tracer / Prompt manager（内部读取配置，调用方零配置）
        self._tracer = LangfuseTracer()
        self._prompt_manager = PromptManager()

        # 查询意图路由器
        self.router = QueryRouter()

        # 混合检索：BM25 词法索引（由开关控制，可降级为纯 Dense 检索）
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )

    async def search(self, query: str, kb_id: str) -> list[dict]:
        """在 ChromaDB 中进行语义检索（单 KB 或全部），
        当混合检索开启时并行执行 Dense + BM25 并通过 RRF 融合。

        Args:
            query: 用户查询文本
            kb_id: 知识库 UUID（空字符串表示搜索所有知识库）

        Returns:
            检索结果列表（混合模式下为 RRF 融合后的结果）
        """
        # 混合检索：Dense + BM25 并行执行后 RRF 融合
        if HYBRID_SEARCH_ENABLED and self.bm25 and kb_id:
            dense_t = asyncio.to_thread(
                self.vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
            )
            bm25_t = asyncio.to_thread(self.bm25.search, kb_id, query, TOP_K_RETRIEVAL)
            d, b = await asyncio.gather(dense_t, bm25_t)
            return rrf_fusion(d, b)

        if not kb_id:
            return await asyncio.to_thread(
                self.vector_store.similarity_search_all, query, k=TOP_K_RETRIEVAL
            )
        return await asyncio.to_thread(
            self.vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
        )

    def rerank(self, query: str, results: list[dict]) -> list[RAGContext]:
        """对检索结果进行 Reranker 精排，返回 RAGContext 列表。

        Args:
            query: 用户查询文本
            results: 向量检索返回的原始结果列表

        Returns:
            精排后的 RAGContext 列表
        """
        return self._rerank_results(query, results)

    def stream_answer(
        self,
        query: str,
        contexts: list[RAGContext],
        history: list,
        trace_id: Optional[str] = None,
    ) -> Generator[str, None, None]:
        """构建 prompt 并流式生成回答。

        Args:
            query: 用户查询文本
            contexts: 精排后的检索上下文列表
            history: 对话历史列表
            trace_id: Langfuse trace ID（可选，由调用方传入）

        Yields:
            每次 yield 一小段文本（token）
        """
        context_str = self._format_context(contexts)
        prompt = self._build_prompt(query, context_str, history)
        return self._stream_answer(prompt, trace_id=trace_id)

    @property
    def llm(self):
        """延迟初始化 LLM — 首次调用 get_llm() 创建实例并缓存。"""
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    @property
    def embeddings(self):
        """延迟初始化 Embedding 模型 — 首次调用时创建并缓存。"""
        if self._embeddings is None:
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def reranker(self):
        """延迟初始化 Reranker 模型 — 首次调用时创建并缓存。"""
        if self._reranker is None:
            self._reranker = get_rerank()
        return self._reranker

    def _classify_query(self, query: str) -> str:
        """根据规则对用户查询进行分类。

        分类类型：
          - clear: 清晰直接的金融查询
          - fuzzy_short: 模糊短查询（<10 字符）
          - colloquial: 口语化/分析性查询
          - compound: 对比/比较类查询

        Args:
            query: 用户查询文本

        Returns:
            分类标签字符串
        """
        cleaned = query.strip()
        if not cleaned:
            return "clear"
        if any(w in cleaned for w in ["对比", "比较", "差异", "versus", "vs"]):
            return "compound"
        if any(w in cleaned for w in ["分析", "解释", "说明", "为什么"]):
            return "colloquial"
        if len(cleaned) < 10:
            return "fuzzy_short"
        return "clear"

    def _expand_query(self, query: str, history: list[dict]) -> str:
        """对模糊短查询进行扩展，利用历史对话补充上下文。

        Args:
            query: 用户短查询
            history: 对话历史列表

        Returns:
            扩展后的查询文本
        """
        if not history:
            return query
        for msg in reversed(history):
            if msg.get("role") == "user" and msg["content"] != query:
                context = msg["content"]
                return f"{context} {query}"
        return query

    def _condense_query(self, query: str) -> str:
        """将口语化查询转化为简洁的金融检索查询。

        Args:
            query: 用户口语化查询

        Returns:
            精简后的查询文本
        """
        condense_patterns = ["分析", "解释", "说明", "为什么"]
        cleaned = query
        for pat in condense_patterns:
            cleaned = cleaned.replace(pat, "").strip()
        return cleaned if cleaned else query

    def _decompose_query(self, query: str) -> list[str]:
        """将对比类查询分解为多个子查询。

        Args:
            query: 用户对比查询

        Returns:
            子查询列表
        """
        separators = ["对比", "比较", "差异", "versus", "vs", "和", "与"]
        parts = [query]
        for sep in separators:
            new_parts = []
            for p in parts:
                new_parts.extend(p.split(sep))
            parts = [p.strip() for p in new_parts if p.strip()]
        return [p for p in parts if p]

    def _rewrite_query(self, query: str, history: list[dict]) -> str | list[str]:
        """根据查询分类结果执行相应的改写策略。

        改写策略：
          - clear: 直接返回原查询（passthrough）
          - fuzzy_short: 调用 _expand_query 利用历史扩展
          - colloquial: 调用 _condense_query 精简表达
          - compound: 调用 _decompose_query 拆分子查询

        Args:
            query: 用户查询文本
            history: 对话历史列表

        Returns:
            改写后的查询（字符串或字符串列表）
        """
        t = self._classify_query(query)
        if t == "clear":
            return query
        if t == "fuzzy_short":
            return self._expand_query(query, history)
        if t == "colloquial":
            return self._condense_query(query)
        if t == "compound":
            return self._decompose_query(query)
        return query

    def chat_with_citations(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> tuple[Generator[str, None, None], list[RAGContext]]:
        """生成带引用来源的流式回答 — RAG 流水线的主入口。

        完整流程：
          1. 在 ChromaDB 中进行语义检索（top-K 候选）
          2. 调用 Reranker 精排（保留 top-N）
          3. 构建 prompt 并流式生成回答
          4. 将用户问题写入对话历史（Redis）

        Args:
            kb_id: 知识库 UUID
            session_id: 会话 ID（用于对话历史管理）
            query: 用户输入的查询文本

        Returns:
            (token_generator, citations_list) 元组：
            - token_generator: 流式输出的 token 生成器（每次 yield 一小段文本）
            - citations_list: RAGContext 列表，表示回答引用的来源文档分块
        """
        # Langfuse trace 记录
        trace_id = None
        trace_id = self._tracer.start_trace(
            "chat_with_citations",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )

        # ====== 意图路由：根据查询类型分流 ======
        route = self.router.route(query)
        history = self.chat_manager.get_window(session_id)

        if route == "simple":
            logger.info("Route: simple — direct LLM answer (no RAG)")
            prompt = self._build_simple_prompt(query, history)
            token_gen = self._stream_answer(prompt)
            self.chat_manager.add_message(session_id, "user", query)
            return token_gen, []

        if route in ("vague", "complex"):
            logger.info("Route: {} — rewriting query", route)
            query = self._rewrite_query(query, history)
            if isinstance(query, list):
                query = " ".join(query)

        # ====== Short query guard ======
        SHORT_QUERY_THRESHOLD = 5
        cleaned = query.strip()
        if len(cleaned) < SHORT_QUERY_THRESHOLD:
            logger.info(
                "Query too short: '{}' (< {} chars)", cleaned, SHORT_QUERY_THRESHOLD
            )
            citations: list[RAGContext] = []

            def _short_query_gen() -> Generator[str, None, None]:
                yield '查询内容过短，请输入更具体的财务问题（如"2024年营业收入是多少？"）'

            self._tracer.end_trace(trace_id, output="查询内容过短")
            return _short_query_gen(), citations

        if not kb_id:
            logger.info("kb_id 为空，搜索所有知识库")

        # ====== Step 1-2: 向量检索（通过 async search）======
        try:
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(self.search(query, kb_id))
            loop.close()
        except Exception as e:
            error_msg = str(e)
            logger.error("Vector search failed for kb_id={}: {}", kb_id, error_msg)
            citations = []

            def _search_err_gen() -> Generator[str, None, None]:
                yield f"检索失败: {error_msg}"

            return _search_err_gen(), citations

        # 检索结果为空时直接返回友好提示
        if not results:
            logger.info("No results found for query from kb_id='{}'", kb_id)
            citations = []

            def _no_result_gen() -> Generator[str, None, None]:
                yield "未在文档中找到相关数据。"

            return _no_result_gen(), citations

        # ====== Step 3: Reranker 精排 ======
        rag_contexts = self.rerank(query, results)

        # ====== Step 4-5: 构建 prompt + 流式生成 ======
        history = self.chat_manager.get_window(session_id)
        token_generator = self.stream_answer(query, rag_contexts, history)

        # 将用户本轮问题追加到对话历史，供下一轮上下文使用
        self.chat_manager.add_message(session_id, "user", query)

        return token_generator, rag_contexts

    def _rerank_results(self, query: str, results: list[dict]) -> list[RAGContext]:
        """对检索结果进行 Reranker 精排，返回 top-N 的 RAGContext 列表。

        Reranker 是外部 API 调用，可能因网络波动失败，因此内置指数退避重试。
        如果所有重试都失败，则降级使用原始检索顺序（不中断流程）。

        Args:
            query: 用户查询文本
            results: ChromaDB 返回的原始检索结果列表

        Returns:
            精排后的 RAGContext 列表（最多 TOP_K_RERANK 条）
        """
        if not results:
            return []

        docs = [r["content"] for r in results]
        last_error: Optional[Exception] = None

        # 指数退避重试 Reranker API 调用
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                reranked = self.reranker.rerank(query, docs)
                break
            except Exception as e:
                last_error = e
                if attempt < RETRY_MAX_ATTEMPTS:
                    wait = RETRY_INITIAL_INTERVAL * (
                        RETRY_BACKOFF_FACTOR ** (attempt - 1)
                    )
                    logger.warning(
                        "Rerank failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                        attempt,
                        RETRY_MAX_ATTEMPTS,
                        e,
                        wait,
                    )
                    time.sleep(wait)
        else:
            # 所有重试失败：降级使用原始检索顺序（distance 分数作为 score）
            logger.warning(
                "Rerank failed after {} attempts (using raw order): {}",
                RETRY_MAX_ATTEMPTS,
                last_error,
            )
            reranked = [
                {"index": i, "relevance_score": r.get("distance", 0)}
                for i, r in enumerate(results)
            ]

        # 将精排结果转为 RAGContext 列表（只取 top-N）
        contexts = []
        for item in reranked[:TOP_K_RERANK]:
            idx = item["index"]
            r = results[idx]
            metadata = r.get("metadata", {})
            pc = metadata.get("parent_content")
            score = item.get("relevance_score", 0)
            contexts.append(
                RAGContext(
                    content=pc if pc else r["content"],
                    source=metadata.get("source", ""),
                    page=metadata.get("page", 0),
                    doc_id=metadata.get("doc_id", ""),
                    chunk_id=r["id"],
                    parent_content=pc,
                    score=score,
                )
            )
        return contexts

    @staticmethod
    def _format_context(contexts: list[RAGContext]) -> str:
        """将检索上下文格式化为 prompt 中的参考文档块。

        每个上下文按编号排列，包含来源文件名、页码和原文内容，
        供 LLM 在生成回答时引用。

        Args:
            contexts: RAGContext 列表

        Returns:
            拼接好的参考文档字符串
        """
        blocks = []
        for i, ctx in enumerate(contexts):
            blocks.append(
                f"[{i + 1}] 来源: {ctx.source} (第{ctx.page}页)\n内容: {ctx.content}"
            )
        return "\n\n".join(blocks)

    def _build_prompt(
        self,
        query: str,
        context: str,
        history: list[dict],
    ) -> list:
        """构建完整的 LLM 消息列表（System + History + User）。

        消息结构：
          1. SystemMessage: 金融问答系统指令（约束回答行为）
          2. 对话历史: 按角色交替的 HumanMessage / AIMessage
          3. HumanMessage: 包含参考文档 + 用户问题的完整输入

        Args:
            query: 用户查询文本
            context: 已格式化的参考文档字符串
            history: 对话历史列表（从 ChatManager 获取）

        Returns:
            LangChain 消息列表，可直接传给 llm.stream()
        """
        messages = [SystemMessage(content=self._prompt_manager.get_system_prompt())]

        # 将历史消息转为 LangChain 消息格式
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

        # 构建最终用户消息：参考文档 + 问题
        user_content = self._prompt_manager.get_user_template(
            context=context, query=query
        )
        messages.append(HumanMessage(content=user_content))
        return messages

    def _build_simple_prompt(
        self,
        query: str,
        history: list[dict],
    ) -> list:
        """构建无检索上下文的简洁 prompt（用于 simple 路由的直接回答）。

        Args:
            query: 用户查询文本
            history: 对话历史列表

        Returns:
            LangChain 消息列表，不含参考文档上下文
        """
        messages = [SystemMessage(content=self._prompt_manager.get_system_prompt())]

        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))

        messages.append(HumanMessage(content=query))
        return messages

    def _stream_answer(
        self, messages: list, trace_id: Optional[str] = None
    ) -> Generator[str, None, None]:
        """流式生成 LLM 回答，支持指数退避重试。

        使用 stream() 而非 invoke()，逐 token 输出回答内容，
        减少用户感知延迟（前端可以边生成边显示）。

        如果 LLM API 调用失败，会按指数退避重试；
        所有重试失败后 yield 错误提示信息。

        Args:
            messages: 完整的 LangChain 消息列表

        Yields:
            每次 yield 一小段文本（token），前端拼接后显示
        """
        # Langfuse generation 记录
        gen_id = None
        tracer = self._tracer
        messages_snapshot = [
            {"role": getattr(m, "type", "unknown"), "content": m.content}
            for m in messages
            if hasattr(m, "type") or hasattr(m, "content")
        ]
        gen_id = tracer.start_generation(
            trace_id,
            "llm_stream",
            input_data=messages_snapshot,
            model=getattr(self.llm, "model", None),
        )

        last_error: Optional[Exception] = None
        full_output = ""
        self.last_token_usage = {}
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                stream = self.llm.stream(messages)
                for chunk in stream:
                    content = chunk.content if hasattr(chunk, "content") else str(chunk)
                    if content:
                        full_output += content
                        yield content
                    # 捕获 DashScope 返回的精确 token 用量
                    if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                        u = chunk.usage_metadata
                        self.last_token_usage = {
                            "prompt_tokens": u.get("input_tokens", 0),
                            "completion_tokens": u.get("output_tokens", 0),
                            "total_tokens": u.get("total_tokens", 0),
                        }
                # 流式输出正常完成
                if not self.last_token_usage:
                    # Fallback：未捕获到精确用量时用估算值
                    usage = _estimate_usage(messages, full_output)
                    self.last_token_usage = {
                        "prompt_tokens": usage.get("input", 0),
                        "completion_tokens": usage.get("output", 0),
                        "total_tokens": usage.get("input", 0) + usage.get("output", 0),
                    }
                tracer.end_generation(
                    gen_id,
                    trace_id,
                    output=full_output,
                    usage=self.last_token_usage,
                )
                return
            except Exception as e:
                last_error = e
                if attempt < RETRY_MAX_ATTEMPTS:
                    wait = RETRY_INITIAL_INTERVAL * (
                        RETRY_BACKOFF_FACTOR ** (attempt - 1)
                    )
                    logger.warning(
                        "LLM stream failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                        attempt,
                        RETRY_MAX_ATTEMPTS,
                        e,
                        wait,
                    )
                    time.sleep(wait)

        # 所有重试均失败
        logger.error("LLM stream failed after {} attempts", RETRY_MAX_ATTEMPTS)
        error_msg = f"生成回答失败: {last_error}"
        full_output = error_msg
        tracer.end_generation(gen_id, trace_id, output=error_msg)
        yield error_msg
