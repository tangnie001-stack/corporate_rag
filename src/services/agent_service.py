"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件
4. 异常边界处理和三降级策略
"""

import time
import uuid
from typing import AsyncGenerator

from loguru import logger

from src.utils.sse import sse_status, sse_token, sse_citation, sse_done, sse_error
from src.agents.graph.workflow import build_graph
from src.agents.graph.state import AgentState
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.chat.manager import ChatManager


class AgentService:
    """图生命周期管理服务。"""

    def __init__(
        self,
        vector_store: VectorStore,
        bm25: BM25Index | None,
        llm,
        reranker,
        chat_manager: ChatManager,
        prompt_manager: PromptManager | None = None,
    ):
        self._vector_store = vector_store
        self._bm25 = bm25
        self._llm = llm
        self._reranker = reranker
        self._chat_manager = chat_manager
        self._prompt_manager = prompt_manager or PromptManager()
        self._tracer = LangfuseTracer()

        self._graph = build_graph(
            vector_store, bm25, llm, reranker, self._prompt_manager, self._tracer
        )
        logger.info("AgentService initialized with compiled graph")

    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> AsyncGenerator[str, None]:
        """执行图并流式返回 SSE 事件。"""
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        tracer_trace_id = self._tracer.start_trace(
            "chat_stream_agent",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )

        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        initial_state: AgentState = {
            "session_id": session_id,
            "kb_id": kb_id,
            "query": query,
            "trace_id": trace_id,
            "retrieval_retries": 0,
            "downgraded": False,
            "downgrade_reason": "",
            "_history": history,
        }

        full_answer = ""

        try:
            t0 = time.perf_counter()
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

            final_state = await self._graph.ainvoke(initial_state)
            contexts = final_state.get("contexts", [])
            seen = set()
            for ctx in contexts:
                key = (ctx.get("source", ""), ctx.get("page", 0))
                if key in seen:
                    continue
                seen.add(key)
                yield sse_citation(
                    ctx.get("source", ""),
                    ctx.get("page", 0),
                    (ctx.get("content", "")[:200]),
                    ctx.get("score", 0),
                )

            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )

            t1 = time.perf_counter()
            logger.info(
                "[{}] AgentService stream_chat completed: total={:.1f}s "
                "downgraded={} reason={} contexts={}",
                trace_id, t1 - t0,
                final_state.get("downgraded"),
                final_state.get("downgrade_reason"),
                len(contexts),
            )

        except Exception as e:
            logger.exception("[{}] AgentService stream_chat failed: {}", trace_id, e)
            yield sse_error(f"暂时无法回答：{str(e)[:100]}")
        finally:
            self._tracer.end_trace(tracer_trace_id)
            yield sse_done()
