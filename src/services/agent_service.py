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
    SSEEvent,
    SSEStatusEvent,
    SSETokenEvent,
    SSECitationEvent,
    SSEErrorEvent,
    SSEDoneEvent,
    SSEModelInfoEvent,
    SSEClarificationEvent,
)
from src.agents.graph.workflow import build_graph
from src.agents.graph.state import AgentState
from src.rag.context import RAGContext
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.infra.llm.langfuse_tracing import LangfuseTracer, traced
from src.infra.llm.prompt_manager import PromptManager
from src.chat.manager import ChatManager
from src.config.const import (
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
    LangGraph,
    SSE_STATUS,
    ABSTENTION_STATUS_MSG,
)
from src.config.prompts import ABSTENTION_TEXT


class AgentService:
    """图生命周期管理服务。"""

    def __init__(
        self,
        vector_store: VectorStore,
        bm25: BM25Index | None,
        chat_manager: ChatManager,
        llm=None,
        classify_llm=None,
        reranker=None,
        prompt_manager: PromptManager | None = None,
    ):
        from src.models import get_llm, get_classify_llm, get_rerank, get_embeddings

        self._vector_store = vector_store
        self._bm25 = bm25
        self._llm = llm or get_llm()
        self._classify_llm = classify_llm or get_classify_llm()
        self._reranker = reranker or get_rerank()
        self._chat_manager = chat_manager
        self._prompt_manager = prompt_manager or PromptManager()
        self._tracer = LangfuseTracer()

        self._graph: CompiledStateGraph = build_graph(
            vector_store,
            bm25,
            self._llm,
            self._classify_llm,
            self._reranker,
            get_embeddings(),
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
        answer = ""
        formatted_citations = None  # None=Format 节点未捕获（兜底用）；[]=明确无引用
        model_used = ""
        is_fallback = False

        try:
            t0 = time.perf_counter()
            contexts: list[RAGContext] = []
            downgraded = False
            downgrade_reason = ""
            _clarification_pending = None

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
                        metadata = event.get("metadata", {}) or {}
                        node_name = metadata.get("langgraph_node", "")
                        chunk = event.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
                        content = chunk.content if chunk is not None else ""
                        if LangGraphNode.Generate.NAME not in node_name:
                            if content:
                                logger.info(
                                    "CHAT_MODEL_STREAM filtered: node={} content_prefix={!r:.50}",
                                    node_name,
                                    content,
                                )
                            continue
                        if content:
                            full_answer += content
                            yield SSETokenEvent(content)

                    case LangGraphEvent.CHAIN_END:
                        output = event.get(LangGraphKey.DATA, {}).get(
                            LangGraphKey.OUTPUT
                        )
                        if isinstance(output, dict):
                            if LangGraphNode.Classify.NAME in name:
                                missing = output.get("missing_entities", [])
                                if missing:
                                    logger.info(
                                        "classify detected missing_entities={}", missing
                                    )
                                    _clarification_pending = {
                                        "type": "entity_completion",
                                        "missing_entities": missing,
                                    }
                            elif LangGraphNode.Rerank.NAME in name:
                                contexts = output.get(
                                    LangGraphNode.Rerank.CONTEXTS, contexts
                                )
                            elif LangGraphNode.Grader.NAME in name:
                                if output.get(LangGraphNode.Grader.DOWNGRADED):
                                    downgraded = True
                                    downgrade_reason = output.get(
                                        LangGraphNode.Grader.DOWNGRADE_REASON, ""
                                    )
                            elif LangGraphNode.Generate.NAME in name:
                                answer = output.get("answer", "") or answer
                                model_used = output.get("model_used", model_used)
                                is_fallback = output.get("is_fallback", is_fallback)
                            elif LangGraphNode.Format.NAME in name:
                                formatted_citations = (
                                    output.get(LangGraphNode.Format.CITATIONS, []) or []
                                )

            # 追问处理：缺少实体时返回澄清事件
            if _clarification_pending:
                cp = _clarification_pending
                first = cp["missing_entities"][0]
                entity_type = first.get("type", "default")
                from src.infra.search.query_router import SUGGESTIONS_MAP

                suggestions = SUGGESTIONS_MAP.get(
                    entity_type, SUGGESTIONS_MAP["default"]
                )
                yield SSEClarificationEvent(
                    type=cp["type"],
                    question=first.get("question", "请补充相关信息"),
                    missing_entities=cp["missing_entities"],
                    suggestions=suggestions,
                )
                yield SSEDoneEvent()
                return

            # abstention 兜底：generate 返回静态 answer 且无流式 token 时，作为 token 送达
            if not full_answer and answer:
                full_answer = answer
                if answer == ABSTENTION_TEXT:
                    yield SSEStatusEvent("generate", ABSTENTION_STATUS_MSG)
                yield SSETokenEvent(answer)

            # 引用事件：优先用 format_node 过滤后的 citations
            # formatted_citations 为 None 表示 Format 节点未捕获（异常路径），兜底遍历 contexts
            # formatted_citations 为 [] 表示明确无引用（拒答/未引用任何文档），直接不发
            if formatted_citations is None:
                seen = set()
                citations_to_send = []
                for ctx in contexts:
                    key = (ctx.source, ctx.page or 0)
                    if key in seen:
                        continue
                    seen.add(key)
                    citations_to_send.append(
                        {
                            "source": ctx.source or "",
                            "page": ctx.page or 0,
                            "snippet": (ctx.content or "")[:200],
                            "score": ctx.score or 0,
                            "index": 0,
                        }
                    )
            else:
                citations_to_send = formatted_citations
            for c in citations_to_send:
                yield SSECitationEvent(
                    source=c.get("source", ""),
                    page=c.get("page", 0),
                    snippet=c.get("snippet", ""),
                    score=c.get("score", 0.0),
                    index=c.get("index", 0),
                )

            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )

            # 模型信息（含 fallback 状态）
            yield SSEModelInfoEvent(
                model=model_used or "",
                is_fallback=is_fallback,
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
