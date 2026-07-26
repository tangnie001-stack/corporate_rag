"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件
4. 异常边界处理和三降级策略
"""

import time
from typing import AsyncGenerator

from loguru import logger

from src.utils.sse import sse_status, sse_token, sse_citation, sse_done, sse_error
from src.agents.graph.workflow import build_graph
from src.agents.graph.state import make_initial_state, AgentState
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.chat.manager import ChatManager
from src.infra.llm.trace_context import current_trace_id


class AgentService:
    """图生命周期管理服务。"""

    def __init__(
        self,
        vector_store: VectorStore,
        bm25: BM25Index | None,
        chat_manager: ChatManager,
        llm=None,
        reranker=None,
        prompt_manager: PromptManager | None = None,
    ):
        from src.models import get_llm, get_rerank

        self._vector_store = vector_store
        self._bm25 = bm25
        self._llm = llm or get_llm()
        self._reranker = reranker or get_rerank()
        self._chat_manager = chat_manager
        self._prompt_manager = prompt_manager or PromptManager()
        self._tracer = LangfuseTracer()

        self._graph = build_graph(
            vector_store,
            bm25,
            self._llm,
            self._reranker,
            self._prompt_manager,
            self._tracer,
        )
        logger.info("AgentService initialized with compiled graph")

    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> AsyncGenerator[str, None]:
        """执行图并流式返回 SSE 事件。"""
        trace_id = current_trace_id.get()
        tracer_trace_id = self._tracer.start_trace(
            "chat_stream_agent",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )

        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        initial_state: AgentState = make_initial_state(session_id, kb_id, query, trace_id, history)

        full_answer = ""

        try:
            t0 = time.perf_counter()
            contexts: list[dict] = []
            downgraded = False
            downgrade_reason = ""

            async for event in self._graph.astream_events(
                initial_state,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")

                if kind == "on_chain_start":
                    if "classify" in name:
                        yield sse_status("classifying", "正在分析查询类型...")
                    elif "rewrite" in name:
                        yield sse_status("rewriting", "正在优化查询...")
                    elif "retrieve" in name or "retrieval" in name:
                        yield sse_status("retrieving", "正在检索相关文档...")
                    elif "rerank" in name:
                        yield sse_status("reranking", "正在精排结果...")
                    elif "generate" in name:
                        yield sse_status("generating", "正在生成回答...")

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk", {})
                    content = getattr(chunk, "content", "") or ""
                    if content:
                        full_answer += content
                        yield sse_token(content)

                elif kind == "on_chain_end":
                    output = event.get("data", {}).get("output", {})
                    if "rerank" in name and isinstance(output, dict):
                        contexts = output.get("contexts", contexts)
                    elif "grader" in name and isinstance(output, dict):
                        if output.get("downgraded"):
                            downgraded = True
                            downgrade_reason = output.get("downgrade_reason", "")

            # 从流式事件中已收集了 contexts / downgraded，不再需要 ainvoke
            seen = set()
            for ctx in contexts:
                key = (ctx.source, ctx.page or 0)
                if key in seen:
                    continue
                seen.add(key)
                yield sse_citation(
                    ctx.source or "",
                    ctx.page or 0,
                    (ctx.content or "")[:200],
                    ctx.score or 0,
                )

            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )

            t1 = time.perf_counter()
            logger.info(
                "[{}] AgentService stream_chat completed: total={:.1f}s "
                "downgraded={} reason={} contexts={}",
                trace_id,
                t1 - t0,
                downgraded,
                downgrade_reason,
                len(contexts),
            )

        except Exception as e:
            logger.exception("[{}] AgentService stream_chat failed: {}", trace_id, e)
            yield sse_error(f"暂时无法回答：{str(e)[:100]}")
        finally:
            self._tracer.end_trace(tracer_trace_id)
            yield sse_done()
