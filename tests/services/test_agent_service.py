"""AgentService 单元测试（agent 循环链路）。

覆盖：状态事件按事件类型接线、abstention 判定、model_used 捕获。
"""

from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from src.agents.graph.state import (
    AgentState,
    LangGraphEvent,
    LangGraphKey,
    LangGraphNode,
)
from src.config.const import SSEInteractionTexts
from src.rag.context import RAGContext
from src.services.agent_service import (
    AgentService,
    _convert_event,
    _is_abstention,
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
    chat_manager.add_message_async = AsyncMock()
    service._chat_manager = chat_manager
    service._prompt_manager = Mock()
    service._tracer = Mock()
    return service, chat_manager


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


@pytest.mark.asyncio
async def test_stream_chat_emits_full_event_sequence():
    """受控事件流 → 状态/token/citation/model_info/done 完整产出。"""
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

    events = []
    async for event in service.stream_chat("kb1", "session1", "营收多少"):
        events.append(event)

    statuses = [e for e in events if isinstance(e, SSEStatusEvent)]
    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    model_infos = [e for e in events if isinstance(e, SSEModelInfoEvent)]
    abstentions = [e for e in events if isinstance(e, SSEAbstentionEvent)]
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
    assert len(model_infos) == 1
    assert model_infos[0].model == "gpt-4o"
    assert model_infos[0].is_fallback is False
    assert abstentions == []
    assert len(dones) == 1
    # 有检索上下文 + 正常回答 → 不 abstention；done 恒在末尾
    assert isinstance(events[-1], SSEDoneEvent)
    assert service._last_model_used == "gpt-4o"


@pytest.mark.asyncio
async def test_stream_chat_emits_abstention_when_no_context():
    """检索无上下文 → 循环结束发 SSEAbstentionEvent（位于 model_info / done 之前）。"""
    service, _ = _make_service()

    async def fake_astream(*args, **kwargs):
        yield _chat_model_start_item()
        yield _chat_model_stream_item("未在文档中找到相关数据")
        yield _chat_model_end_item("qwen-max")
        yield _finalize_end_item("未在文档中找到相关数据", has_contexts=False)
        yield _format_end_item([])

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events = []
    async for event in service.stream_chat("kb1", "session1", "营收多少"):
        events.append(event)

    abstentions = [e for e in events if isinstance(e, SSEAbstentionEvent)]
    dones = [e for e in events if isinstance(e, SSEDoneEvent)]
    model_infos = [e for e in events if isinstance(e, SSEModelInfoEvent)]
    assert len(abstentions) == 1
    assert len(dones) == 1
    assert events.index(abstentions[0]) < events.index(model_infos[0])
    assert events.index(abstentions[0]) < events.index(dones[0])
    assert model_infos[0].model == "qwen-max"


@pytest.mark.asyncio
async def test_stream_chat_persists_assistant_to_chat_manager():
    """正常结束累积 token 写入 chat_manager（assistant 消息）。"""
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

    events = []
    async for event in service.stream_chat("kb1", "session1", "营收多少"):
        events.append(event)

    chat_manager.add_message_async.assert_any_call(
        "session1", "assistant", "你好，世界"
    )
    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    assert [t.token for t in tokens] == ["你好", "，世界"]


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

    events = []
    async for event in service.stream_chat("kb1", "session1", "营收多少"):
        events.append(event)

    assert [e for e in events if isinstance(e, SSEAbstentionEvent)] == []
    assert isinstance(events[-1], SSEDoneEvent)
