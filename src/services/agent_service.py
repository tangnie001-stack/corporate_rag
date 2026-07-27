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
from langgraph.graph.state import CompiledStateGraph

from src.utils.sse import (
    SSEEvent, SSEStatusEvent, SSETokenEvent, SSECitationEvent, SSEErrorEvent, SSEDoneEvent,
)
from src.agents.graph.workflow import build_graph
from src.agents.graph.state import AgentState
from src.rag.context import RAGContext
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.infra.llm.langfuse_tracing import LangfuseTracer, traced
from src.infra.llm.prompt_manager import PromptManager
from src.chat.manager import ChatManager
from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode, LangGraph, SSE_STATUS


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

        self._graph: CompiledStateGraph = build_graph(
            vector_store,
            bm25,
            self._llm,
            self._reranker,
            self._prompt_manager,
        )
        logger.info("AgentService initialized with compiled graph")

    @traced("chat_stream_agent")
    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> AsyncGenerator[SSEEvent, None]:
        """执行图并流式返回 SSE 事件。"""

        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        initial_state = AgentState.make_initial_state(session_id, kb_id, query, history)

        full_answer = ""

        try:
            t0 = time.perf_counter()
            contexts: list[RAGContext] = []
            downgraded = False
            downgrade_reason = ""

            async for event in self._graph.astream_events(
                initial_state,
                version=LangGraph.VERSION,
            ):
                kind = event.get(LangGraphKey.EVENT, "")
                name = event.get(LangGraphKey.NAME, "")

                match kind:
                    case LangGraphEvent.CHAIN_START:
                        for node, message in SSE_STATUS.items():
                            if node in name:
                                yield SSEStatusEvent(node, message)
                                break

                    case LangGraphEvent.CHAT_MODEL_STREAM:
                        chunk = event.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
                        content = chunk.content if chunk is not None else ""
                        if content:
                            full_answer += content
                            yield SSETokenEvent(content)

                    case LangGraphEvent.CHAIN_END:
                        output = event.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT)
                        if LangGraphNode.Rerank.NAME in name and isinstance(output, dict):
                            contexts = output.get(LangGraphNode.Rerank.CONTEXTS, contexts)
                        elif LangGraphNode.Grader.NAME in name and isinstance(output, dict):
                            if output.get(LangGraphNode.Grader.DOWNGRADED):
                                downgraded = True
                                downgrade_reason = output.get(LangGraphNode.Grader.DOWNGRADE_REASON, "")

            # 从流式事件中已收集了 contexts / downgraded，不再需要 ainvoke
            seen = set()
            for ctx in contexts:
                key = (ctx.source, ctx.page or 0)
                if key in seen:
                    continue
                seen.add(key)
                yield SSECitationEvent(
                    source=ctx.source or "",
                    page=ctx.page or 0,
                    snippet=(ctx.content or "")[:200],
                    score=ctx.score or 0,
                )

            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )

            t1 = time.perf_counter()
            logger.info(
                "AgentService stream_chat completed: total={:.1f}s "
                "downgraded={} reason={} contexts={}",
                t1 - t0,
                downgraded,
                downgrade_reason,
                len(contexts),
            )

        except Exception as e:
            logger.exception("AgentService stream_chat failed: {}", e)
            yield SSEErrorEvent(f"暂时无法回答：{str(e)[:100]}")
        finally:
            yield SSEDoneEvent()
