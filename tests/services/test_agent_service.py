"""AgentService 单元测试（agent 循环链路）。

覆盖：状态事件按事件类型接线、abstention 判定、model_used 捕获。
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from src.agents.graph.state import (
    AgentState,
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
)
from src.config import TOP_K_RERANK
from src.config.const import SSEInteractionTexts
from src.rag.context import RAGContext
from src.services.agent_service import (
    AgentService,
    _convert_event,
    _is_abstention,
    _record_event,
    _StreamCapture,
)
from src.utils.sse import (
    SSEAbstentionEvent,
    SSECitationEvent,
    SSEDoneEvent,
    SSEModelInfoEvent,
    SSEStatusEvent,
    SSETokenEvent,
)


def _chat_model_start_item() -> dict:
    """构造 agent 节点 on_chat_model_start 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_START,
        LangGraphKey.NAME: "ChatOpenAI",
        "metadata": {"langgraph_node": "agent"},
        LangGraphKey.DATA: {},
    }


def _chat_model_stream_item(content: str) -> dict:
    """构造 agent 节点 on_chat_model_stream 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
        LangGraphKey.NAME: "ChatOpenAI",
        "metadata": {"langgraph_node": "agent"},
        LangGraphKey.DATA: {LangGraphKey.CHUNK: AIMessageChunk(content=content)},
    }


def _chat_model_end_item(model: str) -> dict:
    """构造 agent 节点 on_chat_model_end 事件（output 携带 response_metadata.model_name）。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_END,
        LangGraphKey.NAME: "ChatOpenAI",
        "metadata": {"langgraph_node": "agent"},
        LangGraphKey.DATA: {
            LangGraphKey.OUTPUT: AIMessage(
                content="", response_metadata={"model_name": model}
            )
        },
    }


def _tool_start_item(name: str) -> dict:
    """构造工具 on_tool_start 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: name,
        LangGraphKey.DATA: {},
    }


def _tool_end_item(name: str) -> dict:
    """构造工具 on_tool_end 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_END,
        LangGraphKey.NAME: name,
        LangGraphKey.DATA: {},
    }


def _make_context() -> RAGContext:
    """构造最小 RAGContext 实例（模拟 retrieve_kb 产出的检索上下文）。"""
    return RAGContext(content="x", source="s", page=1, doc_id="d", chunk_id="c")


def _finalize_end_item(answer: str, has_contexts: bool) -> dict:
    """构造 agent_finalize 节点 on_chain_end 事件（产出最终 answer 与 tool_contexts）。"""
    contexts = [_make_context()] if has_contexts else []
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
        LangGraphKey.NAME: "agent_finalize",
        LangGraphKey.DATA: {
            LangGraphKey.OUTPUT: {"answer": answer, "tool_contexts": contexts}
        },
    }


def _format_end_item(citations: list[dict]) -> dict:
    """构造 format 节点 on_chain_end 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
        LangGraphKey.NAME: LangGraphNode.Format.NAME,
        LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"citations": citations}},
    }


def _make_service() -> tuple[AgentService, AsyncMock]:
    """构造最小可用的 AgentService（跳过 __init__，仅 mock 外部依赖）。

    Returns:
        (service, chat_manager)：chat_manager 单独返回以便直接断言 mock 调用
    """
    service = AgentService.__new__(AgentService)
    service._llm = Mock()
    chat_manager = AsyncMock()
    chat_manager.get_history_async.return_value = []
    chat_manager.get_session_agent_async.return_value = ""
    chat_manager.add_message_async = AsyncMock()
    service._chat_manager = chat_manager
    service._prompt_manager = Mock()
    service._tracer = Mock()
    return service, chat_manager


async def _collect_events(
    service: AgentService,
    kb_id: str,
    session_id: str,
    query: str,
    deep_thinking: bool = False,
) -> tuple[list, Mock]:
    """模拟 API 层接线：启动 _run_with_finalize 后台任务并收集订阅到的全部 SSE 事件。

    stream_chat 只返回 (订阅生成器, 启动上下文)，生成任务由 API 层启动；
    本 helper 复刻 _stream_rag_response 的启动逻辑，供服务层测试直接验证
    完整的事件流与收尾落库。

    Returns:
        (events, fake_svc)：events 为订阅到的 SSE 事件列表；
        fake_svc 的 save_assistant_async 供断言收尾落库
    """
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import streaming_manager
    from src.services.agent_service import _run_generation

    agen, launch_ctx = await service.stream_chat(
        kb_id, session_id, query, deep_thinking
    )
    partial_holder = {"text": ""}
    abort_signal = asyncio.Event()
    ctx = launch_ctx["ctx"]
    fake_svc = Mock()
    fake_svc.save_assistant_async = AsyncMock()
    fake_svc.chat_manager = Mock()
    fake_svc.chat_manager.add_message_async = AsyncMock()

    async def answer_builder() -> str:
        return await _run_generation(
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            launch_ctx["query"],
            launch_ctx["history"],
            launch_ctx["deep_thinking"],
            ctx,
            streaming_manager,
            graph=launch_ctx["graph"],
            partial_holder=partial_holder,
        )

    task = asyncio.create_task(
        _run_with_finalize(
            fake_svc,
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            partial_holder,
            answer_builder,
            streaming_manager,
            abort_signal,
            lambda: None,
            ctx,
        )
    )
    streaming_manager.register(launch_ctx["session_id"], task, abort_signal)
    task.add_done_callback(
        lambda _t: streaming_manager.unregister_if_current(
            launch_ctx["session_id"], task
        )
    )

    events = []
    async for event in agen:
        events.append(event)
    return events, fake_svc


class TestIsAbstention:
    """_is_abstention 判定逻辑。"""

    def test_answer_matches_marker_is_abstention_without_contexts(self):
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.answer = "未在文档中找到相关数据"
        assert _is_abstention(state)

    def test_answer_matches_marker_is_abstention(self):
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.tool_contexts.append(_make_context())
        state.answer = "抱歉，未在文档中找到相关数据"
        assert _is_abstention(state)

    def test_normal_answer_is_not_abstention(self):
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.tool_contexts.append(_make_context())
        state.answer = "2024年营收为100亿 [1]"
        assert not _is_abstention(state)

    def test_empty_contexts_normal_answer_not_abstention(self):
        """无检索上下文 + 正常回答 → 不判定 abstention（收窄判定，闲聊不误报）。"""
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.answer = "你好！这是概念解释……"
        assert not _is_abstention(state)

    def test_marker_only_is_abstention(self):
        """仅拒答标记（无 [n] 引用）→ 判定 abstention（纯拒答）。"""
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.answer = "未在文档中找到相关信息"
        assert _is_abstention(state)

    def test_marker_with_citation_reference_is_not_abstention(self):
        """拒答标记 + [1] 引用 → 不判定 abstention（web 兜底回答保留引用）。

        与 format_node 的防御式规则对齐：web 兜底回答即使混入拒答措辞，
        只要带引用标记就保留引用，不发 SSEAbstentionEvent。
        """
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.answer = "未在文档中找到该信息，网络搜索结果显示[1]..."
        assert not _is_abstention(state)


class TestConvertEventStatus:
    """_convert_event 状态事件接线测试。"""

    def test_chat_model_start_agent_produces_thinking_status(self):
        assert _convert_event(_chat_model_start_item()) == [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_AGENT,
                SSEInteractionTexts.AGENT_STATUS_THINKING,
            )
        ]

    def test_chat_model_start_non_agent_ignored(self):
        item = _chat_model_start_item()
        item["metadata"] = {"langgraph_node": "other"}
        assert _convert_event(item) == []

    def test_tool_start_retrieve_produces_retrieving_status(self):
        assert _convert_event(_tool_start_item("retrieve_kb")) == [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_RETRIEVE,
                SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
            )
        ]

    def test_tool_start_ask_user_ignored(self):
        assert _convert_event(_tool_start_item("ask_user")) == []

    def test_tool_end_retrieve_produces_retrieved_status(self):
        assert _convert_event(_tool_end_item("retrieve_kb")) == [
            SSEStatusEvent(
                SSEInteractionTexts.STAGE_RETRIEVE,
                SSEInteractionTexts.AGENT_STATUS_RETRIEVED,
            )
        ]

    def test_chat_model_end_captures_model_used(self):
        capture = _StreamCapture()
        assert _convert_event(_chat_model_end_item("qwen-max"), capture) == []
        assert capture.model_used == "qwen-max"

    def test_agent_finalize_captures_final_state(self):
        capture = _StreamCapture()
        assert _convert_event(_finalize_end_item("答案", True), capture) == []
        assert capture.final_answer == "答案"
        assert len(capture.final_contexts) == 1


def test_convert_event_extracts_reasoning():
    """on_chat_model_stream 且 chunk 带 reasoning_content 时产出 reasoning 事件。"""
    from langchain_core.messages import AIMessageChunk

    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEReasoningDeltaEvent

    chunk = AIMessageChunk(
        content="",
        additional_kwargs={"reasoning_content": "思考增量"},
    )
    item = {
        "event": "on_chat_model_stream",
        "name": "ChatModel",
        "metadata": {"langgraph_node": "agent"},
        "data": {"chunk": chunk},
    }
    events = _convert_event(item)
    assert any(
        isinstance(e, SSEReasoningDeltaEvent) and e.reasoning_delta == "思考增量"
        for e in events
    )


def test_convert_event_content_and_reasoning_both():
    """chunk 同时有 content 和 reasoning 时，产出 token + reasoning 两个事件。"""
    from langchain_core.messages import AIMessageChunk

    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEReasoningDeltaEvent, SSETokenEvent

    chunk = AIMessageChunk(
        content="正文",
        additional_kwargs={"reasoning_content": "思考"},
    )
    item = {
        "event": "on_chat_model_stream",
        "name": "ChatModel",
        "metadata": {"langgraph_node": "agent"},
        "data": {"chunk": chunk},
    }
    events = _convert_event(item)
    kinds = {type(e) for e in events}
    assert SSETokenEvent in kinds and SSEReasoningDeltaEvent in kinds


@pytest.mark.asyncio
async def test_stream_chat_emits_full_event_sequence():
    """受控事件流 → 状态/token/citation/done 完整产出。"""
    service, _ = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_end_item("gpt-4o")
        yield _tool_start_item("retrieve_kb")
        yield _tool_end_item("retrieve_kb")
        yield _chat_model_start_item()
        yield _chat_model_stream_item("这是回答")
        yield _chat_model_end_item("gpt-4o")
        yield _finalize_end_item("这是回答 [1]", has_contexts=True)
        yield _format_end_item(
            [
                {
                    "index": 1,
                    "source": "财报.pdf",
                    "page": 5,
                    "snippet": "营收100亿",
                    "score": 0.95,
                }
            ]
        )

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events, _ = await _collect_events(service, "kb1", "session-seq", "营收多少")

    statuses = [e for e in events if isinstance(e, SSEStatusEvent)]
    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    dones = [e for e in events if isinstance(e, SSEDoneEvent)]

    # 状态事件按事件类型接线：两次 agent 思考 + 检索开始/完成
    assert [s.stage for s in statuses] == [
        SSEInteractionTexts.STAGE_AGENT,
        SSEInteractionTexts.STAGE_RETRIEVE,
        SSEInteractionTexts.STAGE_RETRIEVE,
        SSEInteractionTexts.STAGE_AGENT,
    ]
    assert [s.message for s in statuses] == [
        SSEInteractionTexts.AGENT_STATUS_THINKING,
        SSEInteractionTexts.AGENT_STATUS_RETRIEVING,
        SSEInteractionTexts.AGENT_STATUS_RETRIEVED,
        SSEInteractionTexts.AGENT_STATUS_THINKING,
    ]
    assert tokens == [SSETokenEvent("这是回答")]
    assert [c.source for c in citations] == ["财报.pdf"]
    assert [c.kind for c in citations] == [
        "kb"
    ]  # KB 检索引用默认 kind=kb（web 兜底为 web）
    assert len(dones) == 1
    # done 恒在末尾（生产者写终态事件入缓冲）
    assert isinstance(events[-1], SSEDoneEvent)


@pytest.mark.asyncio
async def test_stream_chat_passes_deep_thinking():
    """deep_thinking 参数应透传至初始 state（最终控制 agent LLM enable_thinking）。"""
    service, _ = _make_service()
    seen = {}

    async def fake_astream(initial_state, version):
        seen["deep_thinking"] = initial_state.deep_thinking
        yield _chat_model_end_item("gpt-4o")
        yield _finalize_end_item("答案", has_contexts=True)

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    await _collect_events(service, "kb1", "session-deep", "q", deep_thinking=True)
    assert seen["deep_thinking"] is True


@pytest.mark.asyncio
async def test_stream_chat_full_path_emits_model_info_without_abstention():
    """正常作答链路：捕获到 model_used 则补发 model_info，不产出 abstention 事件。"""
    service, _ = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("这是回答")
        yield _chat_model_end_item("qwen-max")
        yield _finalize_end_item("这是回答", has_contexts=True)
        yield _format_end_item([])

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events, _ = await _collect_events(service, "kb1", "session-abst", "营收多少")

    # 正常回答不触发 abstention；model_info 由捕获的 model_used 补发
    abstentions = [e for e in events if isinstance(e, SSEAbstentionEvent)]
    model_infos = [e for e in events if isinstance(e, SSEModelInfoEvent)]
    assert abstentions == []
    assert len(model_infos) == 1
    assert model_infos[0].model == "qwen-max"
    assert model_infos[0].is_fallback is False
    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    assert [t.token for t in tokens] == ["这是回答"]
    assert isinstance(events[-1], SSEDoneEvent)


@pytest.mark.asyncio
async def test_stream_chat_persists_assistant_via_background_task():
    """正常结束订阅收到全部 token 与 done；assistant 由后台任务落库为 complete。"""
    service, chat_manager = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("你好")
        yield _chat_model_stream_item("，世界")
        yield _chat_model_end_item("gpt-4o")
        yield _finalize_end_item("你好，世界", has_contexts=True)
        yield _format_end_item([])

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events, fake_svc = await _collect_events(
        service, "kb1", "session-assist", "营收多少"
    )

    # user 消息仍同步写入（assistant 收尾由后台任务完成）
    chat_manager.add_message_async.assert_any_call("session-assist", "user", "营收多少")
    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    assert [t.token for t in tokens] == ["你好", "，世界"]
    assert isinstance(events[-1], SSEDoneEvent)
    # 后台任务将完整回答落库为 complete
    fake_svc.save_assistant_async.assert_awaited_once()
    args = fake_svc.save_assistant_async.await_args.args
    assert args[2] == "你好，世界"
    assert args[4] == "complete"
    # 完整回答同时写 Redis 对话历史（跨 turn 上下文）
    fake_svc.chat_manager.add_message_async.assert_any_call(
        "session-assist", "assistant", "你好，世界"
    )


@pytest.mark.asyncio
async def test_stream_chat_no_abstention_without_final_state():
    """未捕获到 agent_finalize 产物时不判定 abstention（无最终 state 可判）。"""
    service, _ = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("你好")
        yield _chat_model_end_item("gpt-4o")
        yield _format_end_item([])

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events, _ = await _collect_events(service, "kb1", "session-no-final", "营收多少")

    assert [e for e in events if isinstance(e, SSEAbstentionEvent)] == []
    assert isinstance(events[-1], SSEDoneEvent)


def test_search_web_tool_status_events():
    """search_web 工具 start/end 映射为 STAGE_WEB_SEARCH 状态事件。"""
    assert _convert_event(_tool_start_item("search_web")) == [
        SSEStatusEvent(
            SSEInteractionTexts.STAGE_WEB_SEARCH,
            SSEInteractionTexts.WEB_SEARCH_STATUS_START,
        )
    ]
    assert _convert_event(_tool_end_item("search_web")) == [
        SSEStatusEvent(
            SSEInteractionTexts.STAGE_WEB_SEARCH,
            SSEInteractionTexts.WEB_SEARCH_STATUS_END,
        )
    ]


def test_convert_tool_start_retrieve_kb_carries_query_and_non_default_top_k():
    """retrieve_kb on_tool_start → SSEStatusEvent.detail 含 query 与非默认 top_k。"""
    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "retrieve_kb",
        LangGraphKey.DATA: {"input": {"query": "腾讯2024年报 业绩", "top_k": 3}},
    }
    events = _convert_event(item)
    assert len(events) == 1
    status = events[0]
    assert isinstance(status, SSEStatusEvent)
    assert status.detail == "query=腾讯2024年报 业绩 top_k=3"


def test_convert_tool_start_retrieve_kb_omits_default_top_k():
    """top_k 等于默认（TOP_K_RERANK）时 detail 不附加 top_k（展示冗余省略）。"""
    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "retrieve_kb",
        LangGraphKey.DATA: {
            "input": {"query": "腾讯2024年报 业绩", "top_k": TOP_K_RERANK}
        },
    }
    events = _convert_event(item)
    assert len(events) == 1
    status = events[0]
    assert isinstance(status, SSEStatusEvent)
    assert status.detail == "query=腾讯2024年报 业绩"
    assert "top_k=" not in status.detail


def test_convert_tool_start_search_web_carries_queries_detail():
    """search_web on_tool_start → SSEStatusEvent.detail 含 queries。"""
    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "search_web",
        LangGraphKey.DATA: {"input": {"queries": ["腾讯 2023 年报", "腾讯 2025 年报"]}},
    }
    events = _convert_event(item)
    assert len(events) == 1
    status = events[0]
    assert isinstance(status, SSEStatusEvent)
    assert status.detail is not None
    assert "腾讯 2023 年报" in status.detail
    assert "queries=" in status.detail


def test_convert_tool_start_ask_user_no_detail():
    """ask_user 等不展示的工具不填 detail（保持原行为）。"""
    item = {
        LangGraphKey.EVENT: LangGraphEvent.TOOL_START,
        LangGraphKey.NAME: "ask_user",
    }
    assert _convert_event(item) == []


def test_citation_event_passes_kind():
    """format 输出 citations 的 kind 透传到 SSECitationEvent。"""
    events = _convert_event(
        _format_end_item(
            [
                {
                    "index": 1,
                    "source": "https://a.com",
                    "page": 0,
                    "snippet": "网页",
                    "score": 0.9,
                    "kind": "web",
                }
            ]
        )
    )
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    assert citations[0].kind == "web"


def test_citation_event_passes_tier():
    """format 输出 citations 的 tier 透传到 SSECitationEvent；缺省为 None。"""
    events = _convert_event(
        _format_end_item(
            [
                {
                    "index": 1,
                    "source": "https://www.caixin.com/a",
                    "page": 0,
                    "snippet": "财经",
                    "score": 0.9,
                    "kind": "web",
                    "tier": 2,
                }
            ]
        )
    )
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    assert citations[0].tier == 2


@pytest.mark.asyncio
async def test_run_generation_writes_events_to_buffer(monkeypatch):
    """生产者 coroutine：图事件→带 seq 缓冲，token 累积为完整回答。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_stream_item("你")
        yield _chat_model_stream_item("好")

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    answer = await _run_generation(
        "s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph
    )
    assert answer == "你好"
    events = mgr.get_events_since("s1", 0)
    assert any(et == "token" for _, et, _ in events)
    # 未捕获 model_used / final_answer → 不补发 abstention / model_info
    assert not any(et in ("abstention", "model_info") for _, et, _ in events)


@pytest.mark.asyncio
async def test_run_generation_emits_agent_used_event():
    """进入图事件循环前发一次 agent_used（含会话绑定值），且早于首个 graph 事件。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_stream_item("你好")
        yield _chat_model_end_item("qwen-max")

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.agent = "finance-expert"
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    events = mgr.get_events_since("s1", 0)
    agent_payloads = [payload for _, et, payload in events if et == "agent_used"]
    assert agent_payloads == [{"type": "agent_used", "agent": "finance-expert"}]
    # agent_used 必须早于首个 graph 事件（token）
    assert events[0][1] == "agent_used"


@pytest.mark.asyncio
async def test_run_generation_buffers_abstention_event(monkeypatch):
    """生产者将 _convert_event 产出的 abstention 事件写入缓冲。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services import agent_service
    from src.services.agent_service import _run_generation
    from src.utils.sse import SSEAbstentionEvent

    mgr = StreamingRunManager()
    seen = []

    def fake_convert(item, capture):
        seen.append(item)
        return [SSEAbstentionEvent()]

    monkeypatch.setattr(agent_service, "_convert_event", fake_convert)

    async def fake_astream(*args, **kwargs):
        yield {"event": "on_chain_end", "data": {}}

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert any(et == "abstention" for _, et, _ in mgr.get_events_since("s1", 0))


@pytest.mark.asyncio
async def test_run_generation_buffers_reasoning_event(monkeypatch):
    """生产者将 _convert_event 产出的 reasoning（SSEReasoningDeltaEvent）事件写入缓冲。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services import agent_service
    from src.services.agent_service import _run_generation
    from src.utils.sse import SSEReasoningDeltaEvent

    mgr = StreamingRunManager()

    def fake_convert(item, capture):
        return [SSEReasoningDeltaEvent("思考增量")]

    monkeypatch.setattr(agent_service, "_convert_event", fake_convert)

    async def fake_astream(*args, **kwargs):
        yield {"event": "on_chain_end", "data": {}}

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    events = mgr.get_events_since("s1", 0)
    assert any(et == "reasoning" for _, et, _ in events)
    payload = next(payload for _, et, payload in events if et == "reasoning")
    assert payload == {"delta": "思考增量"}


@pytest.mark.asyncio
async def test_run_generation_buffers_ask_user_from_clarify_channel():
    """ask_user 经 clarify_channel 投递的问题 → 缓冲出现 ask_user 事件。

    回归防线：澄清通道须与图事件循环并行消费（_drain_clarify_channel），
    否则问题 payload 滞留队列、前端收不到 event: ask_user 澄清卡。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        # 模拟 ask_user 工具在生成中途投递问题（真实场景中工具随即 await 用户答案）
        await ctx.clarify_channel.put(
            {
                "type": "ask_user",
                "questions": [
                    {"id": "q1", "question": "请问查询哪家公司？", "options": []}
                ],
            }
        )
        await asyncio.sleep(0.05)  # 让并行消费任务有时间完成处理
        yield {"event": "on_chain_end", "data": {}}

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    events = mgr.get_events_since("s1", 0)
    ask_payloads = [payload for _, et, payload in events if et == "ask_user"]
    assert ask_payloads == [
        {
            "type": "ask_user",
            "questions": [
                {"id": "q1", "question": "请问查询哪家公司？", "options": []}
            ],
        }
    ]


@pytest.mark.asyncio
async def test_run_generation_buffers_abstention_from_captured_final_answer():
    """agent_finalize 产物命中拒答标记 → 循环结束后 abstention 事件入缓冲（位于 model_info 之前）。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("未在文档中找到相关数据")
        yield _chat_model_end_item("qwen-max")
        yield _finalize_end_item("未在文档中找到相关数据", has_contexts=False)

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    answer = await _run_generation(
        "s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph
    )
    assert answer == "未在文档中找到相关数据"
    events = mgr.get_events_since("s1", 0)
    abstention_payloads = [payload for _, et, payload in events if et == "abstention"]
    assert abstention_payloads == [SSEAbstentionEvent().payload_for_buffer()]
    # 收尾顺序复刻旧语义：abstention 在 model_info 之前
    tail_kinds = [et for _, et, _ in events if et in ("abstention", "model_info")]
    assert tail_kinds == ["abstention", "model_info"]


@pytest.mark.asyncio
async def test_run_generation_emits_abstain_after_retrieve_signal(monkeypatch):
    """绑 KB 检索过却拒答 → 产 abstain_after_retrieve 行为信号（检索质量缺陷）。

    signal 数据源取 _run_generation 参数 kb_id/query（final_state 临时构造不含）；
    capture 未存迭代数，iteration 约定传 0。
    """
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal
        captured["query"] = query
        captured["iteration"] = iteration
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("未在文档中找到相关数据")
        yield _chat_model_end_item("qwen-max")
        yield _finalize_end_item("未在文档中找到相关数据", has_contexts=True)

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1", "kb1", "腾讯2024营收", [], False, ctx, mgr, graph=fake_graph
    )

    # 检索过且绑 KB → 信号携带 kb_id 与上下文数；abstention 事件照常入缓冲
    assert captured["signal"] == "abstain_after_retrieve"
    assert captured["query"] == "腾讯2024营收"
    assert captured["iteration"] == 0
    assert captured["fields"]["kb_id"] == "kb1"
    assert captured["fields"]["tool_context_count"] == 1
    events = mgr.get_events_since("s1", 0)
    assert any(et == "abstention" for _, et, _ in events)


@pytest.mark.asyncio
async def test_run_generation_no_abstain_signal_when_not_retrieved(monkeypatch):
    """拒答但未检索到上下文（final_contexts 空）→ 不发 abstain_after_retrieve 信号。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        """mock retrieval_signal：捕获调用参数。"""
        captured["signal"] = signal

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _finalize_end_item("未在文档中找到相关数据", has_contexts=False)

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    assert "signal" not in captured  # 无检索行为 → 非检索质量缺陷，不发信号
    assert any(et == "abstention" for _, et, _ in mgr.get_events_since("s1", 0))


@pytest.mark.asyncio
async def test_run_generation_buffers_model_info_when_model_captured():
    """agent 节点 on_chat_model_end 捕获 model_used → 循环结束后 model_info 事件入缓冲。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("你好")
        yield _chat_model_end_item("qwen-max")
        yield _finalize_end_item("你好", has_contexts=True)

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation("s1", "kb1", "q", [], False, ctx, mgr, graph=fake_graph)

    events = mgr.get_events_since("s1", 0)
    model_info_payloads = [payload for _, et, payload in events if et == "model_info"]
    assert model_info_payloads == [{"model": "qwen-max", "is_fallback": False}]


@pytest.mark.asyncio
async def test_run_generation_accumulates_citation_sources():
    """生产者流经 SSECitationEvent 时，partial_holder["sources"] 累积结构化 dict。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()
    partial_holder = {"text": ""}

    async def fake_astream(*args, **kwargs):
        yield _format_end_item(
            [
                {
                    "index": 1,
                    "source": "财报.pdf",
                    "page": 5,
                    "snippet": "营收100亿",
                    "score": 0.95,
                }
            ]
        )

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        partial_holder=partial_holder,
    )
    assert partial_holder["sources"] == [
        {
            "source": "财报.pdf",
            "page": 5,
            "snippet": "营收100亿",
            "kind": "kb",
            "index": 1,
            "tier": None,
        }
    ]


@pytest.mark.asyncio
async def test_run_generation_aborts_when_signal_set():
    """abort_signal 在循环中置位后，生产者处理完当前事件即抛 CancelledError 中断生成。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()
    abort_signal = asyncio.Event()
    consumed = []

    async def fake_astream(*args, **kwargs):
        consumed.append("a")
        yield _chat_model_stream_item("你好")
        abort_signal.set()
        consumed.append("b")
        yield _chat_model_stream_item("世界")
        consumed.append("c")
        yield _chat_model_stream_item("!")

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")

    with pytest.raises(asyncio.CancelledError):
        await _run_generation(
            "s1",
            "kb1",
            "q",
            [],
            False,
            ctx,
            mgr,
            graph=fake_graph,
            abort_signal=abort_signal,
        )
    # 置位后处理完当前事件即中断：第二个事件仍入缓冲，第三个事件未被消费
    assert consumed == ["a", "b"]
    events = mgr.get_events_since("s1", 0)
    # agent_used 在进入图循环前恒发一次，随后是两次 token
    assert [et for _, et, _ in events] == ["agent_used", "token", "token"]


@pytest.mark.asyncio
async def test_run_generation_aborts_before_loop_when_signal_pre_set():
    """abort_signal 在进入循环前已置位 → 直接抛 CancelledError，不消费事件源。"""
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.services.agent_service import _run_generation

    mgr = StreamingRunManager()
    abort_signal = asyncio.Event()
    abort_signal.set()
    consumed = []

    async def fake_astream(*args, **kwargs):
        consumed.append(1)
        yield _chat_model_stream_item("你好")

    fake_graph = Mock()
    fake_graph.astream_events = fake_astream
    ctx = RequestContext(session_id="s1")

    with pytest.raises(asyncio.CancelledError):
        await _run_generation(
            "s1",
            "kb1",
            "q",
            [],
            False,
            ctx,
            mgr,
            graph=fake_graph,
            abort_signal=abort_signal,
        )
    assert consumed == []
    assert mgr.get_events_since("s1", 0) == []


@pytest.mark.asyncio
async def test_stream_chat_persists_citation_sources_on_complete():
    """完整路径收尾时 save_assistant_async 携带生产者累积的引用来源。"""
    service, _ = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_stream_item("营收为100亿 [1]")
        yield _chat_model_end_item("gpt-4o")
        yield _finalize_end_item("营收为100亿 [1]", has_contexts=True)
        yield _format_end_item(
            [
                {
                    "index": 1,
                    "source": "财报.pdf",
                    "page": 5,
                    "snippet": "营收100亿",
                    "score": 0.95,
                }
            ]
        )

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    _, fake_svc = await _collect_events(service, "kb1", "session-src", "营收多少")

    fake_svc.save_assistant_async.assert_awaited_once()
    args = fake_svc.save_assistant_async.await_args.args
    assert args[4] == "complete"
    assert args[3] == [
        {
            "source": "财报.pdf",
            "page": 5,
            "snippet": "营收100亿",
            "kind": "kb",
            "index": 1,
            "tier": None,
        }
    ]


def test_convert_delegate_dict_to_sse_delegate_event():
    """_convert_event 把 delegate dict 转 SSEDelegateEvent（delta/end 语义）。"""
    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEDelegateEvent

    events = _convert_event(
        {
            "type": "delegate",
            "action": "delta",
            "delegate_id": "d1",
            "skill": "analyst",
            "kind": "thinking",
            "delta": "思考A",
            "ok": True,
            "reason": "",
        }
    )
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SSEDelegateEvent)
    assert ev.delegate_id == "d1" and ev.action == "delta"
    assert ev.kind == "thinking" and ev.delta == "思考A"

    end = _convert_event(
        {
            "type": "delegate",
            "action": "end",
            "delegate_id": "d1",
            "skill": "analyst",
            "kind": "",
            "delta": "",
            "ok": False,
            "reason": "idle",
        }
    )[0]
    assert isinstance(end, SSEDelegateEvent)
    assert end.ok is False and end.reason == "idle"


def test_convert_event_scope_not_main_ignores_graph_events():
    """scope != main 时 graph 事件不转换（显式 scope 隔离，防误归属）。"""
    from src.services.agent_service import _convert_event

    item = {
        "event": "on_chat_model_stream",
        "metadata": {"langgraph_node": "agent"},
        "data": {"chunk": type("C", (), {"content": "x", "additional_kwargs": {}})()},
    }
    assert _convert_event(item, scope="delegate") == []


@pytest.mark.asyncio
async def test_stream_chat_sets_ctx_deep_thinking():
    """deep_thinking 应写入 launch_ctx 的 RequestContext（fork thinking 跟随来源）。"""
    service, _ = _make_service()
    service._graph = Mock()
    _, launch_ctx = await service.stream_chat("", "s1", "q", deep_thinking=True)
    assert launch_ctx["ctx"].deep_thinking is True
    assert launch_ctx["deep_thinking"] is True
    # 默认 False 档
    _, launch_ctx2 = await service.stream_chat("", "s1", "q")
    assert launch_ctx2["ctx"].deep_thinking is False


class TestEventsLogCollection:
    def test_main_loop_events_collected(self):
        """主循环路径：转换出的 SSE 事件被采集进 events_log。"""
        capture = _StreamCapture()
        event = SSEStatusEvent(
            SSEInteractionTexts.STAGE_AGENT, SSEInteractionTexts.AGENT_STATUS_THINKING
        )
        _record_event(capture, event)
        assert capture.events_log == [
            {"type": "status", "payload": event.payload_for_buffer()}
        ]

    def test_drain_path_events_collected(self):
        """clarify drain 路径：delegate/ask_user 帧同样被采集（评审 F2）。"""
        capture = _StreamCapture()
        delegate_item = {
            "type": "delegate",
            "action": "start",
            "delegate_id": "d1",
            "skill": "finance-analyst",
        }
        for ev in _convert_event(delegate_item, capture):
            _record_event(capture, ev)
        assert capture.events_log[0]["type"] == "delegate"

    @pytest.mark.asyncio
    async def test_drain_clarify_channel_records_delegate_event(self):
        """集成：真正执行 _drain_clarify_channel，delegate item 采集进 events_log 并写缓冲。

        回归防线（评审 Important）：原 test_drain_path_events_collected 直接调
        _convert_event + _record_event，drain 循环内的 _record_event 行被误删时
        该测试仍通过。本测试走真实 drain 函数，删行即失败。

        退出方式说明：drain 函数是 while True 无哨兵消费循环，生产中由
        _run_generation 的 finally cancel 收敛（agent_service.py drain_task），
        因此测试同样用 cancel + gather(return_exceptions=True) 收尾——与生产
        语义一致且确定性强；asyncio.wait_for 超时方式依赖任意时限且会残留
        pending 任务，故不采用。消费确认用带时限的轮询（add_event 被调用即止）。
        """
        from src.infra.llm.request_context import RequestContext
        from src.services.agent_service import _drain_clarify_channel

        ctx = RequestContext(session_id="s1")
        ctx.clarify_channel = asyncio.Queue()
        await ctx.clarify_channel.put(
            {
                "type": "delegate",
                "action": "start",
                "delegate_id": "d1",
                "skill": "finance-analyst",
            }
        )
        manager = Mock()
        manager.add_event = Mock()
        capture = _StreamCapture()

        drain_task = asyncio.create_task(
            _drain_clarify_channel(ctx, manager, "s1", capture)
        )
        # 轮询等待队列条目被消费（2s 时限兜底，避免异常时无限挂起）
        async with asyncio.timeout(2):
            while not manager.add_event.called:
                await asyncio.sleep(0.01)
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)

        assert len(capture.events_log) == 1
        assert capture.events_log[0]["type"] == "delegate"
        manager.add_event.assert_called_once_with(
            "s1", "delegate", capture.events_log[0]["payload"]
        )

    def test_capture_none_noop(self):
        _record_event(None, SSEStatusEvent("s", "m"))  # 不抛异常即可
