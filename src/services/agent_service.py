"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件（双路合并：graph 事件 + ask_user 澄清）
4. 异常边界处理和 abort 信号联动
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import TypeAlias

from langchain_core.messages import BaseMessage
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
from src.config.const import SSEInteractionTexts
from src.infra.db.vector_store import VectorStore
from src.infra.llm.langfuse_tracing import LangfuseTracer, traced
from src.infra.llm.prompt_manager import PromptManager
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.trace_context import current_trace_id
from src.infra.search.bm25_index import BM25Index
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSEEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETokenEvent,
)


class _EndMarker:
    """事件源正常结束哨兵，标记 queue 中不再有新事件。"""


class _ErrorMarker:
    """事件源异常哨兵，携带原始异常。"""

    def __init__(self, error: Exception) -> None:
        self.error = error


@dataclass
class _StreamCapture:
    """单次流式执行的捕获结果（事件转换时收集，供 stream_chat 收尾使用）。

    捕获来源：
    - model_used：agent 节点 on_chat_model_end 的 response_metadata.model_name
    - final_answer / final_contexts：agent_finalize 节点 on_chain_end 的产物
    """

    model_used: str = ""  # agent 节点 LLM 实际使用的模型名（空串 = 未捕获）
    final_answer: str | None = (
        None  # agent_finalize 产物中的最终回答（None = 未走到收尾节点）
    )
    final_contexts: list = field(
        default_factory=list
    )  # agent_finalize 产物中的检索上下文列表


# 合并队列元素类型：LangGraph 事件（StreamEvent）或 ask_user 事件 dict，或哨兵
_QueueItem: TypeAlias = StreamEvent | dict | _EndMarker | _ErrorMarker


def _extract_model_name(output) -> str:
    """从 on_chat_model_end 的 output 提取模型名。

    Args:
        output: LLM 调用输出（AIMessage 或兼容对象）

    Returns:
        模型名；output 非 BaseMessage 或 response_metadata 无 model_name/model 时返回空字符串
    """
    if not isinstance(output, BaseMessage):
        return ""
    response_metadata = output.response_metadata
    if not isinstance(response_metadata, dict):
        return ""
    model_name = response_metadata.get("model_name")
    if isinstance(model_name, str):
        return model_name
    model = response_metadata.get("model")
    if isinstance(model, str):
        return model
    return ""


def _is_abstention(state: AgentState) -> bool:
    """abstention 判定：仅凭模型输出是否命中拒答标记。

    Args:
        state: agent 循环结束后的最终状态

    Returns:
        True 表示应提示转人工（answer 包含 SSEInteractionTexts.ABSTENTION_MARKERS 任一标记）；
        闲聊/概念问答等未触发检索但正常作答的场景不再误判
    """
    return any(
        marker in state.answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS
    )


def _convert_event(
    item: _QueueItem, capture: _StreamCapture | None = None
) -> list[SSEEvent]:
    """把 queue 中的 item 转成 SSE 事件列表（空列表 = 无需产出）。

    queue 中混有两类 item：
    - ask_user 工具经 clarify_channel 推送的 {"type": "ask_user", "questions": [...]}
      → SSEAskUserEvent（问题卡片）
    - LangGraph astream_events 事件 dict（按事件类型接线，不依赖节点名映射）：
      on_chat_model_start（metadata.langgraph_node == "agent"）→ SSEStatusEvent 思考中
      on_chat_model_stream（metadata.langgraph_node == "agent" 且 chunk 内容非空）
      → SSETokenEvent（agent 节点对 LLM 的流式 token）；chunk 带
      additional_kwargs.reasoning_content 时 → SSEReasoningDeltaEvent（思考增量）
      on_chat_model_end（metadata.langgraph_node == "agent"）→ 捕获 model_used 到 capture
      on_tool_start name == "retrieve_kb" → SSEStatusEvent 检索中；name == "ask_user" → 不发
      on_tool_end name == "retrieve_kb" → SSEStatusEvent 检索完成
      on_chain_end name == "format" → output.citations 逐个转 SSECitationEvent
      on_chain_end name == "agent_finalize" → 捕获最终 answer/tool_contexts 到 capture
    其余事件忽略。

    Args:
        item: queue 中取出的原始 item（clarify_channel 推送 dict 或 LangGraph 事件 dict）
        capture: 可选的流捕获容器（model_used / 最终 state），不传则跳过捕获

    Returns:
        list[SSEEvent]: 转换后的 SSE 事件列表；无法转换/无需产出的 item 返回空列表
    """
    if isinstance(item, dict) and item.get("type") == "ask_user":
        return [SSEAskUserEvent(questions=item.get("questions", []))]

    # 哨兵类已被 _dual_stream 提前消费，此处防御性排除以收窄类型
    if not isinstance(item, dict):
        return []

    kind = item.get(LangGraphKey.EVENT, "")
    name = item.get(LangGraphKey.NAME, "")
    metadata = item.get("metadata", {}) or {}

    if kind == LangGraphEvent.CHAT_MODEL_STREAM:
        if metadata.get("langgraph_node") == "agent":
            chunk = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
            if chunk is not None:
                content = chunk.content
                reasoning = (chunk.additional_kwargs or {}).get("reasoning_content", "")
            else:
                content = ""
                reasoning = ""
            events = []
            if content:
                events.append(SSETokenEvent(content))
            if reasoning:
                events.append(SSEReasoningDeltaEvent(reasoning))
            return events
        return []

    if kind == LangGraphEvent.CHAT_MODEL_START:
        if metadata.get("langgraph_node") == "agent":
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_AGENT,
                    SSEInteractionTexts.AGENT_STATUS_THINKING,
                )
            ]
        return []

    if kind == LangGraphEvent.CHAT_MODEL_END:
        if metadata.get("langgraph_node") == "agent":
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT)
            model = _extract_model_name(output)
            if model and capture is not None:
                capture.model_used = model
        return []

    if kind == LangGraphEvent.TOOL_START:
        if name == "search_web":
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_WEB_SEARCH,
                    SSEInteractionTexts.WEB_SEARCH_STATUS_START,
                )
            ]
        if name == "retrieve_kb":
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_RETRIEVE,
                    SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
                )
            ]
        # ask_user 等其他工具不发状态（composer 接管输入区）
        return []

    if kind == LangGraphEvent.TOOL_END and name == "search_web":
        return [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_WEB_SEARCH,
                SSEInteractionTexts.WEB_SEARCH_STATUS_END,
            )
        ]

    if kind == LangGraphEvent.TOOL_END and name == "retrieve_kb":
        return [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_RETRIEVE,
                SSEInteractionTexts.AGENT_STATUS_RETRIEVED,
            )
        ]

    if kind == LangGraphEvent.CHAIN_END:
        if name == LangGraphNode.Format.NAME:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            citations = output.get(LangGraphNode.Format.CITATIONS, []) or []
            return [
                SSECitationEvent(
                    source=c.get("source", ""),
                    page=c.get("page", 0),
                    snippet=c.get("snippet", ""),
                    score=c.get("score", 0.0),
                    index=c.get("index", 0),
                    kind=c.get("kind", SSEInteractionTexts.CITATION_KIND_KB),
                )
                for c in citations
            ]
        if name == "agent_finalize" and capture is not None:
            output = item.get(LangGraphKey.DATA, {}).get(LangGraphKey.OUTPUT) or {}
            capture.final_answer = output.get("answer", "")
            capture.final_contexts = output.get("tool_contexts", [])
        return []

    return []


async def _dual_stream(
    event_source: AsyncIterator[StreamEvent | dict],
    queue: asyncio.Queue[_QueueItem],
    abort_signal: asyncio.Event,
    capture: _StreamCapture | None = None,
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
        capture: 可选的流捕获容器，透传给 _convert_event 收集 model_used / 最终 state

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
                yield SSEErrorEvent(
                    f"{SSEInteractionTexts.SSE_ERROR_PREFIX}{item.error}"
                )
                break
            for event in _convert_event(item, capture):
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
        self._last_model_used: str | None = (
            None  # 最近一次流式执行的模型名（on_chat_model_end 捕获）
        )

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
        deep_thinking: bool = False,
    ) -> AsyncGenerator[SSEEvent, None]:
        """执行图并流式返回 SSE 事件。

        graph 事件与 ask_user 澄清经 _dual_stream 双路合并产出；本方法创建
        RequestContext 并 set 到 current_request_ctx（工具/节点经 contextvar
        读取 queue/abort/tool_contexts），finally 中 reset。循环正常结束后
        将累积的 assistant 文本写入 chat_manager（Redis 历史，供多轮对话
        构建 prompt）；MySQL 持久化由 api 层（_stream_rag_response 流结束后
        create_task）负责。最终 state 取自 agent_finalize 节点 on_chain_end
        的产物（经 capture 收集），据此判定 abstention；model_used 取自
        on_chat_model_end 捕获值。

        Args:
            kb_id: 知识库 ID（空字符串表示跨库搜索）
            session_id: 会话 ID
            query: 用户查询文本
            deep_thinking: 深度思考开关（默认 False）；为 True 时 agent LLM
                以思考模式调用（enable_thinking）

        Yields:
            SSEEvent: 转换后的 SSE 事件（status / token / citation / ask_user /
                abstention / model_info / error / done）
        """
        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        initial_state = AgentState.make_initial_state(
            session_id, kb_id, query, history, deep_thinking
        )

        ctx = RequestContext(session_id=session_id)
        ctx_token = current_request_ctx.set(ctx)
        full_answer = ""
        self._last_model_used = None
        capture = _StreamCapture()
        stream = _dual_stream(
            self._graph.astream_events(initial_state, version=LangGraph.VERSION),
            ctx.clarify_channel,
            ctx.abort_signal,
            capture,
        )
        try:
            async for event in stream:
                if isinstance(event, SSETokenEvent):
                    full_answer += event.token
                yield event
        except Exception as e:  # noqa: BLE001
            logger.exception("AgentService stream_chat failed: {}", e)
            yield SSEErrorEvent(f"{SSEInteractionTexts.SSE_ERROR_PREFIX}{str(e)[:100]}")
            yield SSEDoneEvent(trace_id=current_trace_id.get() or "")
        else:
            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )
            if capture.final_answer is not None:
                final_state = AgentState(
                    answer=capture.final_answer,
                    tool_contexts=capture.final_contexts,
                )
                if _is_abstention(final_state):
                    yield SSEAbstentionEvent()
            model_used = capture.model_used
            self._last_model_used = model_used
            yield SSEModelInfoEvent(model=model_used, is_fallback=False)
            yield SSEDoneEvent(trace_id=current_trace_id.get() or "")
        finally:
            current_request_ctx.reset(ctx_token)
            # 显式关闭事件源：本生成器被 aclose 时 async for 不传播关闭，
            # 需手动触发 _dual_stream finally（abort 置位 + 取消 Task A）
            await stream.aclose()
