"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件（双路合并：graph 事件 + ask_user 澄清）
4. 异常边界处理和 abort 信号联动
"""

import asyncio
import re
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from langchain_core.messages import BaseMessage
from langchain_core.runnables.schema import StreamEvent
from langgraph.graph.state import CompiledStateGraph

from src.agents.graph.state import (
    AgentState,
    LangGraph,
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
)
from src.agents.graph.workflow import build_graph
from src.chat.manager import ChatManager
from src.chat.streaming import (
    StreamingRunManager,
    _subscribe_events,
    streaming_manager,
)
from src.config import TOP_K_RERANK, settings
from src.config.const import SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event, Signal
from src.infra.db.vector_store import VectorStore
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.infra.llm.request_context import RequestContext
from src.infra.search.bm25_index import BM25Index
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDelegateEvent,
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
    - events_log：两条转换路径（主循环 + clarify drain）经 _record_event 采集的
      全量 SSE 事件 [{"type", "payload"}]，供取消/收尾时落库（历史回放持久化源）
    """

    model_used: str = ""  # agent 节点 LLM 实际使用的模型名（空串 = 未捕获）
    final_answer: str | None = (
        None  # agent_finalize 产物中的最终回答（None = 未走到收尾节点）
    )
    final_contexts: list = field(
        default_factory=list
    )  # agent_finalize 产物中的检索上下文列表
    events_log: list[dict[str, Any]] = field(
        default_factory=list
    )  # 过程事件采集（历史回放持久化源，design D1）


# 合并队列元素类型：LangGraph 事件（StreamEvent）或工具经 ctx.clarify_channel
# 投递的事件 dict（ask_user/delegate），或哨兵
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
    """abstention 判定（防御式）：命中拒答标记 且 不含 [n] 引用标记 才视为纯拒答。

    与 format_node 的拒答检测保持一致：web 兜底回答即使混入"未在文档中找到"
    措辞，只要带了引用标记就保留引用，不触发 SSEAbstentionEvent（避免
    "既发引用又发转人工"的矛盾 UX）。

    Args:
        state: agent 循环结束后的最终状态

    Returns:
        True 表示应提示转人工（answer 包含 SSEInteractionTexts.ABSTENTION_MARKERS
        任一标记 且 不含 [n] 引用标记）；闲聊/概念问答等未触发检索但正常作答的
        场景不再误判
    """
    has_abstention_marker = any(
        marker in state.answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS
    )
    has_citation_marker = re.search(r"\[\d+\]", state.answer) is not None
    return has_abstention_marker and not has_citation_marker


def _tool_detail_from_input(item: StreamEvent | dict, key: str) -> str | None:
    """从 LangGraph on_tool_start 事件的 data.input 提取工具入参为可读 detail。

    Args:
        item: astream_events 事件 dict（ToolStart 事件，含 data.input 入参）
        key: 要展示的入参键（retrieve_kb→"query"；search_web→"queries"）

    Returns:
        detail 字符串（如 'query=腾讯2024年报' / 'queries=["腾讯 2023 年报", ...]'）；
        input 缺失或 key 不在 input 时返回 None
    """
    data = item.get(LangGraphKey.DATA) or {}
    tool_input = data.get("input") or {}
    if not isinstance(tool_input, dict):
        return None
    value = tool_input.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        shown = [str(v)[:40] for v in value]
        return f"{key}={shown!r}"
    text = str(value)[:40]
    return f"{key}={text}"


def _convert_event(
    item: _QueueItem,
    capture: _StreamCapture | None = None,
    scope: str = "main",
) -> list[SSEEvent]:
    """把 queue 中的 item 转成 SSE 事件列表（空列表 = 无需产出）。

    scope 标识事件归属：main = 主图事件（LangGraph astream_events / 澄清通道），
    delegate = fork 子代理事件（executor/delegate_task 经 clarify_channel 投递，
    自带 type=delegate 标记）。delegate 转换不依赖 metadata.langgraph_node=="agent"
    判定，杜绝子代理事件被误当主 token。scope 供后续调用方显式区分，当前实现
    下 graph 事件仅在 scope=="main" 时转换。

    queue 中混有三类 item：
    - ask_user 工具经 clarify_channel 推送的 {"type": "ask_user", "questions": [...]}
      → SSEAskUserEvent（问题卡片）
    - delegate_task fork 分支 / executor 经 clarify_channel 推送的
      {"type": "delegate", action: start|delta|end, delegate_id, skill, kind, delta,
      ok, reason} → SSEDelegateEvent（fork 过程增量，不进主 token 流/full_answer）
    - LangGraph astream_events 事件 dict（按事件类型接线，不依赖节点名映射）：
      on_chat_model_start（metadata.langgraph_node == "agent"）→ SSEStatusEvent 思考中
      on_chat_model_stream（metadata.langgraph_node == "agent" 且 chunk 内容非空）
      → SSETokenEvent（agent 节点对 LLM 的流式 token）；chunk 带
      additional_kwargs.reasoning_content 时 → SSEReasoningDeltaEvent（思考增量）
      on_chat_model_end（metadata.langgraph_node == "agent"）→ 捕获 model_used 到 capture
      on_tool_start name == "retrieve_kb" → SSEStatusEvent 检索中（detail 携带入参 query 及非默认 top_k）；
      name == "ask_user" → 不发
      on_tool_end name == "retrieve_kb" → SSEStatusEvent 检索完成
      on_chain_end name == "format" → output.citations 逐个转 SSECitationEvent
      on_chain_end name == "agent_finalize" → 捕获最终 answer/tool_contexts 到 capture
    其余事件忽略。

    Args:
        item: queue 中取出的原始 item（clarify_channel 推送 dict 或 LangGraph 事件 dict）
        capture: 可选的流捕获容器（model_used / 最终 state），不传则跳过捕获
        scope: 事件归属标识（main=主图；delegate=fork 子代理），graph 事件仅 main 转换

    Returns:
        list[SSEEvent]: 转换后的 SSE 事件列表；无法转换/无需产出的 item 返回空列表
    """
    if isinstance(item, dict) and item.get("type") == "ask_user":
        return [SSEAskUserEvent(questions=item.get("questions", []))]

    if isinstance(item, dict) and item.get("type") == "delegate":
        # delegate_task / executor 经 ctx.clarify_channel 投递的委派事件 dict →
        # SSEDelegateEvent（fork 过程增量 start/delta/end；不进主 token 流/full_answer，
        # 防污染不变量由"delegate 仅经本分支转 delegate 事件"保证）
        return [
            SSEDelegateEvent(
                delegate_id=item.get("delegate_id", ""),
                action=item.get("action", "delta"),
                skill=item.get("skill", ""),
                kind=item.get("kind", ""),
                delta=item.get("delta", ""),
                ok=bool(item.get("ok", True)),
                reason=item.get("reason", ""),
            )
        ]

    if scope != "main":
        return []

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
            detail = _tool_detail_from_input(item, "queries")
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_WEB_SEARCH,
                    SSEInteractionTexts.WEB_SEARCH_STATUS_START,
                    detail=detail,
                )
            ]
        if name == "retrieve_kb":
            detail = _tool_detail_from_input(item, "query")
            tool_input = (item.get(LangGraphKey.DATA) or {}).get("input")
            if isinstance(tool_input, dict):
                top_k = tool_input.get("top_k")
                if detail is not None and top_k is not None and top_k != TOP_K_RERANK:
                    detail = f"{detail} top_k={top_k}"
            return [
                SSEStatusEvent(
                    SSEInteractionTexts.STAGE_RETRIEVE,
                    SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
                    detail=detail,
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


def _record_event(capture: _StreamCapture | None, event: SSEEvent) -> None:
    """事件采集：追加进本次生成的私有事件日志（design D1，独立于 manager 缓冲）。

    Args:
        capture: 流捕获容器（None 时静默跳过——无消费者的采集无意义）
        event: 转换后的 SSE 事件
    """
    if capture is None:
        return
    capture.events_log.append(
        {"type": event.type, "payload": event.payload_for_buffer()}
    )


async def _dual_stream(
    event_source: AsyncIterator[StreamEvent | dict],
    queue: asyncio.Queue[_QueueItem],
    abort_signal: asyncio.Event,
    capture: _StreamCapture | None = None,
) -> AsyncGenerator[SSEEvent, None]:
    """双路合并：Task A 迭代事件源推 queue，Task B（本生成器）消费并转换产出 SSE 事件。

    queue 同时承载 graph.astream_events 事件与工具经 clarify_channel
    推送的事件 item（ask_user 澄清 / delegate 委派，应为无界 queue，避免哨兵
    put 在取消路径阻塞）。事件源异常/正常收尾统一用哨兵表达：异常 →
    _ErrorMarker 产出 SSEErrorEvent 后 break；正常结束 → _EndMarker 后 break。

    本生成器（Task B）无论正常结束还是被取消（客户端断连触发 aclose），
    finally 都会取消 Task A 并 gather 等待其退出，保证事件源不再滞留。
    abort_signal 不再由断连置位——仅 cancel 端点经 StreamingRunManager.set_abort
    置位，断连只停止消费（事件源由生产者任务自行管理）。

    Args:
        event_source: 事件源异步迭代器（graph.astream_events 返回值）
        queue: 双路事件合并队列（graph 事件 + clarify_channel 澄清 item）
        abort_signal: 请求级中止信号（由 cancel 端点置位，本函数不再写）
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
        # 断连只停止消费，不置位 abort（仅 cancel 经 manager 置位）
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)


async def _drain_clarify_channel(
    ctx: RequestContext,
    manager: StreamingRunManager,
    session_id: str,
    capture: _StreamCapture,
) -> None:
    """消费 clarify_channel：ask_user 澄清 / delegate 委派事件转 SSE 事件写入缓冲。

    ask_user 工具与 verify 节点的 web_confirm 投递 {"type": "ask_user", ...}；
    delegate_task fork 分支与 executor 投递 {"type": "delegate", ...}（start/delta/end）。
    两者都进 ctx.clarify_channel（无界队列）。本任务与 _run_generation 的
    graph 事件循环并行运行，取出后经 _convert_event 转 SSEAskUserEvent /
    SSEDelegateEvent 写入 manager 缓冲，由 SSE 消费者推给前端渲染澄清卡与
    委派过程区；若缺此消费方，payload 滞留队列，前端收不到对应事件（ask_user
    等满 ASK_USER_TIMEOUT 超时）。任务随生成循环结束/异常/取消被 _run_generation
    的 finally 取消。

    Args:
        ctx: 请求上下文（clarify_channel 的单一消费方）
        manager: StreamingRunManager（事件缓冲写入）
        session_id: 会话 ID
        capture: 流捕获容器（透传 _convert_event，ask_user 不捕获字段）
    """
    while True:
        item = await ctx.clarify_channel.get()
        try:
            for event in _convert_event(item, capture):
                _record_event(capture, event)
                manager.add_event(session_id, event.type, event.payload_for_buffer())
        except Exception as exc:  # noqa: BLE001  # 单条转换失败不终止消费
            core_logging.log_event(
                Event.EVENT_CONVERT_FAILED,
                item_type=type(item).__name__,
                err=str(exc),
            )


async def _run_generation(
    session_id: str,
    kb_id: str,
    query: str,
    history: list,
    deep_thinking: bool,
    ctx: RequestContext,
    manager: StreamingRunManager,
    graph: CompiledStateGraph | None = None,
    partial_holder: dict | None = None,
    abort_signal: asyncio.Event | None = None,
) -> str:
    """后台生成任务：迭代图事件转换为带 seq 事件写入缓冲，返回完整回答。

    graph.astream_events 产出的 LangGraph 事件经 _convert_event 转成 SSE
    事件，逐个以 (seq, event.type, event.payload_for_buffer()) 写入
    manager 的 per-session 缓冲（缓冲 payload 与 to_sse 的 data: 同构）；
    token 事件同步累积 full_answer，并可选写入 partial_holder 供取消/出错
    时回读部分回答。转换期间经 _StreamCapture 捕获 model_used（agent 节点
    on_chat_model_end）与 final_answer/tool_contexts（agent_finalize 节点
    on_chain_end）；循环结束后按捕获结果补发 abstention / model_info 事件
    到缓冲（复刻旧 stream_chat 语义，供前端拒答提示与模型名展示）。
    ask_user / delegate 通道由 _drain_clarify_channel 并行消费并写入同一缓冲，
    与图事件同路推送给前端。

    Args:
        session_id: 会话 ID
        kb_id: 知识库 ID
        query: 用户查询
        history: 对话历史（不含当前 query）
        deep_thinking: 深度思考开关
        ctx: 请求上下文（含 clarify_channel / abort_signal）
        manager: StreamingRunManager（事件缓冲写入）
        graph: 图实例（测试注入用）。模块级函数无法访问 AgentService 的
            self._graph，生产侧须由调用方显式传入，None 时抛 ValueError。
        partial_holder: 可选的 {"text": str, "sources": list[dict]} 共享 dict，
            随 token 产出更新 text，随 citation 事件累积 sources
            （[{source, page, snippet, kind, index}]，历史回放重建引用用），
            供取消/出错时写 interrupted 部分回答、收尾落库引用来源；
            capture 创建后挂 events_log（events_log 的同一列表引用，取消路径
            亦持续可见），finally 写 model_name（capture.model_used）
        abort_signal: 可选的请求级中止信号（cancel 端点置位）；置位后本任务
            在循环内尽快抛 CancelledError 中断生成，交由调用方收尾落库

    Returns:
        完整回答（全部 token 累积结果）

    Raises:
        ValueError: graph 未传入（默认图需调用方显式注入）
        asyncio.CancelledError: abort_signal 置位时抛出（中断生成）
    """
    if graph is None:
        raise ValueError("_run_generation 需显式传 graph（默认图由调用方注入）")
    initial_state = AgentState.make_initial_state(
        session_id, kb_id, query, history, deep_thinking
    )
    capture = _StreamCapture()
    full_answer = ""
    if partial_holder is not None:
        partial_holder["events_log"] = (
            capture.events_log
        )  # 共享列表引用，取消路径亦持续可见
    if abort_signal is not None and abort_signal.is_set():
        raise asyncio.CancelledError
    # 澄清通道与图事件循环并行：ask_user / web_confirm 经 clarify_channel
    # 投递的问题 payload 须转 SSE 写入缓冲，否则前端收不到澄清卡
    drain_task = asyncio.create_task(
        _drain_clarify_channel(ctx, manager, session_id, capture)
    )
    try:
        async for item in graph.astream_events(
            initial_state, version=LangGraph.VERSION
        ):
            for event in _convert_event(item, capture):
                _record_event(capture, event)
                manager.add_event(session_id, event.type, event.payload_for_buffer())
                if isinstance(event, SSETokenEvent):
                    full_answer += event.token
                    if partial_holder is not None:
                        partial_holder["text"] = full_answer
                elif isinstance(event, SSECitationEvent) and partial_holder is not None:
                    # 落库保留完整引用结构（source/page/snippet/kind/index），
                    # 供历史回放重建引用横条与抽屉；旧数据为 "url (第x页)" 扁平串由前端降级
                    partial_holder.setdefault("sources", []).append(
                        {
                            "source": event.source,
                            "page": event.page,
                            "snippet": event.snippet,
                            "kind": event.kind,
                            "index": event.index,
                        }
                    )
            if abort_signal is not None and abort_signal.is_set():
                raise asyncio.CancelledError
    finally:
        # 生成结束/异常/取消后停止澄清消费（工具等待期间图事件循环阻塞，
        # 澄清项已被并行任务即时消费，收尾时队列已空）
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        if partial_holder is not None:
            partial_holder["model_name"] = capture.model_used
    # 收尾：复刻旧 stream_chat 语义，按捕获结果补发 abstention / model_info
    # 事件（capture 在循环内经 _convert_event 填充 model_used / final_answer）
    if capture.final_answer is not None:
        final_state = AgentState(
            answer=capture.final_answer,
            tool_contexts=capture.final_contexts,
        )
        if _is_abstention(final_state):
            # abstain_after_retrieve 行为信号：绑 KB 且检索过却拒答 → 检索质量缺陷
            # （检索结果不足以支撑作答，供 P1 检索质量诊断）
            has_kb_retrieved = bool(capture.final_contexts) and bool(kb_id)
            if has_kb_retrieved:
                core_logging.retrieval_signal(
                    Signal.ABSTAIN_AFTER_RETRIEVE,
                    query,
                    0,  # iteration 非关键：capture 未存迭代数，传 0（YAGNI 不做 capture 改造）
                    kb_id=kb_id,
                    tool_context_count=len(capture.final_contexts or []),
                )
            abstention_event = SSEAbstentionEvent()
            manager.add_event(
                session_id,
                abstention_event.type,
                abstention_event.payload_for_buffer(),
            )
    if capture.model_used:
        model_info_event = SSEModelInfoEvent(
            model=capture.model_used, is_fallback=False
        )
        manager.add_event(
            session_id,
            model_info_event.type,
            model_info_event.payload_for_buffer(),
        )
    return full_answer


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
        from src.agents.skills import (
            SkillExecutor,
            SkillLoader,
            SkillRegistry,
            make_delegate_task,
        )
        from src.models import get_llm, get_rerank

        self._vector_store = vector_store
        self._bm25 = bm25
        self._llm = llm or get_llm()
        self._reranker = reranker or get_rerank()
        self._chat_manager = chat_manager
        self._prompt_manager = prompt_manager or PromptManager()
        self._tracer = LangfuseTracer()

        # skill 委派（agent-delegation-skills）：SKILLS_DIR 环境变量覆盖，缺省项目根 skills/
        skills_dir = settings.SKILLS_DIR
        if not skills_dir:
            skills_dir = str(Path(__file__).resolve().parents[2] / "skills")
        delegate_task_tool = None
        if Path(skills_dir).exists():
            skill_registry = SkillRegistry(SkillLoader(Path(skills_dir)))
            skill_registry.reload_if_changed()  # description 在 make_delegate_task 时按当前注册表生成
            if skill_registry.names():
                skill_executor = SkillExecutor(self._llm)
                delegate_task_tool = make_delegate_task(skill_registry, skill_executor)
            else:
                # skills 目录存在但无任何 skill：delegate_task 不注册（描述会列空列表，注册无意义）
                core_logging.log_event(Event.DELEGATE_SKIP, reason="registry_empty")
        else:
            # skills 目录缺失（volume 未挂载/路径错）→ delegate 静默不可用需可观测
            core_logging.log_event(
                Event.DELEGATE_SKIP, reason="skills_dir_missing", skills_dir=skills_dir
            )

        self._graph: CompiledStateGraph = build_graph(
            vector_store,
            bm25,
            self._llm,
            self._reranker,
            self._prompt_manager,
            delegate_task=delegate_task_tool,
        )
        core_logging.log_event(Event.SERVICE_READY)

    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
        deep_thinking: bool = False,
    ) -> tuple[AsyncGenerator[SSEEvent, None], dict]:
        """准备一轮生成的订阅生成器与启动上下文，不再启动后台任务。

        固定顺序（prompt 上下文正确性关键）：先取历史（不含当前 query）、
        再写 user 消息到 Redis，然后清空该 session 缓冲。生成任务的启动与
        assistant 收尾由 API 层负责（_run_with_finalize + _run_generation）：
        本方法只返回 (subscription_generator, launch_context)，让 API 层
        拿到启动上下文后再 create_task，避免任务生命周期与 SSE 消费耦合。

        Args:
            kb_id: 知识库 ID（空字符串表示不检索（未绑定 KB））
            session_id: 会话 ID
            query: 用户查询文本
            deep_thinking: 深度思考开关（默认 False）；为 True 时 agent LLM
                以思考模式调用（enable_thinking）

        Returns:
            (subscription_generator, launch_context)：
            - subscription_generator：订阅事件缓冲的 SSE 消费者生成器
              （status / token / citation / ask_user / error / done 事件）
            - launch_context：启动后台任务所需的上下文 dict，键包括
              history / ctx / graph / session_id / kb_id / query / deep_thinking
        """
        # 顺序约束（prompt 上下文正确性关键）：先取历史（不含当前 query），
        # 再写 Redis user
        history = await self._chat_manager.get_history_async(session_id) or []
        await self._chat_manager.add_message_async(session_id, "user", query)

        # 新一轮生成前清空该 session 缓冲，避免同一会话二次提问回放上一轮事件
        streaming_manager.clear_buffer(session_id)

        ctx = RequestContext(session_id=session_id)
        ctx.kb_id = kb_id
        ctx.kb_bound = bool(kb_id)
        ctx.deep_thinking = (
            deep_thinking  # fork thinking 跟随的请求级来源（executor 读取）
        )
        launch_context = {
            "history": history,
            "ctx": ctx,
            "graph": self._graph,
            "session_id": session_id,
            "kb_id": kb_id,
            "query": query,
            "deep_thinking": deep_thinking,
        }
        # 主 POST 订阅不按 180s 空闲收流（长静默由任务生命周期收口，含 ask_user
        # 等待、fork 长跑等合法静默）；resume 端点（sessions/events）保留空闲兜底
        return _subscribe_events(
            session_id, streaming_manager, max_idle=None
        ), launch_context
