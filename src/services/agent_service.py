"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件（双路合并：graph 事件 + ask_user 澄清）
4. 异常边界处理和 abort 信号联动
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TypeAlias

from langchain_core.runnables.schema import StreamEvent
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.agents.graph.state import (
    AgentState,
    LangGraph,
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
)
from src.agents.graph.workflow import build_graph
from src.chat.manager import ChatManager
from src.config.const import ASK_USER_STATUS_MSG
from src.config.prompts import SSE_ERROR_PREFIX
from src.infra.db.vector_store import VectorStore
from src.infra.llm.langfuse_tracing import LangfuseTracer, traced
from src.infra.llm.prompt_manager import PromptManager
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.search.bm25_index import BM25Index
from src.utils.sse import (
    SSECitationEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSEEvent,
    SSEStatusEvent,
    SSETokenEvent,
)


class _EndMarker:
    """事件源正常结束哨兵，标记 queue 中不再有新事件。"""


class _ErrorMarker:
    """事件源异常哨兵，携带原始异常。"""

    def __init__(self, error: Exception) -> None:
        self.error = error


# 合并队列元素类型：LangGraph 事件（StreamEvent）或 ask_user 事件 dict，或哨兵
_QueueItem: TypeAlias = StreamEvent | dict | _EndMarker | _ErrorMarker


def _convert_event(item: _QueueItem) -> list[SSEEvent]:
    """把 queue 中的 item 转成 SSE 事件列表（空列表 = 无需产出）。

    queue 中混有两类 item：
    - ask_user 工具经 clarify_channel 推送的 {"type": "ask_user", "questions": [...]}
      → 过渡期产出 SSEStatusEvent(stage="ask_user")（Task 9 换 SSEAskUserEvent）
    - LangGraph astream_events 事件 dict：
      on_chat_model_stream（metadata.langgraph_node == "agent" 且 chunk 内容非空）
      → SSETokenEvent（agent 节点对 LLM 的流式 token）
      on_chain_end（name == "format"）→ output.citations 逐个转 SSECitationEvent
    其余事件（on_chain_start / tools 等）忽略；状态事件由 Task 12 以事件类型重建。

    Args:
        item: queue 中取出的原始 item（clarify_channel 推送 dict 或 LangGraph 事件 dict）

    Returns:
        list[SSEEvent]: 转换后的 SSE 事件列表；无法转换/无需产出的 item 返回空列表
    """
    if isinstance(item, dict) and item.get("type") == "ask_user":
        return [SSEStatusEvent(stage="ask_user", message=ASK_USER_STATUS_MSG)]

    # 哨兵类已被 _dual_stream 提前消费，此处防御性排除以收窄类型
    if not isinstance(item, dict):
        return []

    kind = item.get(LangGraphKey.EVENT, "")
    name = item.get(LangGraphKey.NAME, "")

    if kind == LangGraphEvent.CHAT_MODEL_STREAM:
        metadata = item.get("metadata", {}) or {}
        if metadata.get("langgraph_node") == "agent":
            chunk = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
            if chunk is not None:
                content = chunk.content
            else:
                content = ""
            if content:
                return [SSETokenEvent(content)]
        return []

    if kind == LangGraphEvent.CHAIN_END and name == LangGraphNode.Format.NAME:
        output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
        citations = output.get(LangGraphNode.Format.CITATIONS, []) or []
        return [
            SSECitationEvent(
                source=c.get("source", ""),
                page=c.get("page", 0),
                snippet=c.get("snippet", ""),
                score=c.get("score", 0.0),
                index=c.get("index", 0),
            )
            for c in citations
        ]

    return []


async def _dual_stream(
    event_source: AsyncIterator[StreamEvent | dict],
    queue: asyncio.Queue[_QueueItem],
    abort_signal: asyncio.Event,
) -> AsyncGenerator[SSEEvent, None]:
    """双路合并：Task A 迭代事件源推 queue，Task B（本生成器）消费并转换产出 SSE 事件。

    queue 同时承载 graph.astream_events 事件与 ask_user 工具经 clarify_channel
    推送的澄清 item（应为无界 queue，避免哨兵 put 在取消路径阻塞）。事件源
    异常/正常收尾统一用哨兵表达：异常 → _ErrorMarker 产出 SSEErrorEvent 后
    break；正常结束 → _EndMarker 后 break。

    本生成器（Task B）无论正常结束还是被取消（客户端断连触发 aclose），
    finally 都会置位 abort_signal、取消 Task A 并 gather 等待其退出，
    保证事件源不再滞留。

    Args:
        event_source: 事件源异步迭代器（graph.astream_events 返回值）
        queue: 双路事件合并队列（graph 事件 + clarify_channel 澄清 item）
        abort_signal: 请求级中止信号（置位后 ask_user 等待被唤醒并中断）

    Yields:
        SSEEvent: 事件源事件转换后的 SSE 事件（token / citation / status / error）
    """

    async def run_source():
        """Task A：迭代事件源逐个推入 queue，异常/正常收尾统一放哨兵。"""
        try:
            async for ev in event_source:
                await queue.put(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await queue.put(_ErrorMarker(e))
        finally:
            await queue.put(_EndMarker())

    task_a = asyncio.create_task(run_source())
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _EndMarker):
                break
            if isinstance(item, _ErrorMarker):
                yield SSEErrorEvent(f"{SSE_ERROR_PREFIX}{item.error}")
                break
            for event in _convert_event(item):
                yield event
    finally:
        abort_signal.set()
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)


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
        from src.models import get_classify_llm, get_embeddings, get_llm, get_rerank

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
        """执行图并流式返回 SSE 事件。

        graph 事件与 ask_user 澄清经 _dual_stream 双路合并产出；本方法创建
        RequestContext 并 set 到 current_request_ctx（工具/节点经 contextvar
        读取 queue/abort/tool_contexts），finally 中 reset。循环正常结束后
        将累积的 assistant 文本写入 chat_manager（Redis 历史，供多轮对话
        构建 prompt）；MySQL 持久化由 api 层（_stream_rag_response 流结束后
        create_task）负责。

        Args:
            kb_id: 知识库 ID（空字符串表示跨库搜索）
            session_id: 会话 ID
            query: 用户查询文本

        Yields:
            SSEEvent: 转换后的 SSE 事件（token / citation / status / error / done）
        """
        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        initial_state = AgentState.make_initial_state(session_id, kb_id, query, history)

        ctx = RequestContext(session_id=session_id)
        ctx_token = current_request_ctx.set(ctx)
        full_answer = ""
        stream = _dual_stream(
            self._graph.astream_events(initial_state, version=LangGraph.VERSION),
            ctx.clarify_channel,
            ctx.abort_signal,
        )
        try:
            async for event in stream:
                if isinstance(event, SSETokenEvent):
                    full_answer += event.token
                yield event
        except Exception as e:  # noqa: BLE001
            logger.exception("AgentService stream_chat failed: {}", e)
            yield SSEErrorEvent(f"{SSE_ERROR_PREFIX}{str(e)[:100]}")
            yield SSEDoneEvent()
        else:
            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )
            yield SSEDoneEvent()
        finally:
            current_request_ctx.reset(ctx_token)
            # 显式关闭事件源：本生成器被 aclose 时 async for 不传播关闭，
            # 需手动触发 _dual_stream finally（abort 置位 + 取消 Task A）
            await stream.aclose()
