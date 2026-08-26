"""_dual_stream 双路合并与 _convert_event 事件转换单元测试。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessageChunk

from src.agents.graph.state import LangGraphEvent, LangGraphKey, LangGraphNode
from src.infra.llm.request_context import current_request_ctx
from src.services.agent_service import AgentService, _convert_event, _dual_stream
from src.utils.sse import (
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSETokenEvent,
)


def _make_token_item(content: str) -> dict:
    """构造 agent 节点 on_chat_model_stream 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
        LangGraphKey.NAME: "ChatOpenAI",  # 事件 name 是模型类名，不是节点名
        "metadata": {"langgraph_node": "agent"},
        LangGraphKey.DATA: {LangGraphKey.CHUNK: AIMessageChunk(content=content)},
    }


def _make_format_end_item(citations: list[dict]) -> dict:
    """构造 format 节点 on_chain_end 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
        LangGraphKey.NAME: LangGraphNode.Format.NAME,
        LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"citations": citations}},
    }


class TestDualStream:
    """_dual_stream 双路合并与哨兵收尾测试。"""

    @pytest.mark.asyncio
    async def test_dual_stream_sentinel_error(self):
        """事件源先产出事件再抛异常 → 正常事件产出后收到 error 事件并结束。"""

        async def fake_events():
            yield _make_token_item("你好")
            raise ValueError("boom")

        events = []
        async for e in _dual_stream(fake_events(), asyncio.Queue(), asyncio.Event()):
            events.append(e)

        assert events[0] == SSETokenEvent("你好")
        errors = [e for e in events if isinstance(e, SSEErrorEvent)]
        assert len(errors) == 1
        assert "boom" in errors[0].error

    @pytest.mark.asyncio
    async def test_dual_stream_cancel_propagates(self):
        """Task B 被 aclose 取消 → finally 置位 abort 并取消事件源（Task A）。"""
        cancelled = asyncio.Event()
        entered_sleep = asyncio.Event()

        async def fake_events():
            yield _make_token_item("你好")
            entered_sleep.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        abort_signal = asyncio.Event()
        gen = _dual_stream(fake_events(), asyncio.Queue(), abort_signal)
        it = gen.__aiter__()
        first = await it.__anext__()
        assert first == SSETokenEvent("你好")
        # 等待 Task A 进入阻塞 sleep 后再取消 Task B，确保取消落在事件源上
        await asyncio.wait_for(entered_sleep.wait(), timeout=2)
        await gen.aclose()
        assert cancelled.is_set()
        assert abort_signal.is_set()

    @pytest.mark.asyncio
    async def test_dual_stream_normal_end(self):
        """事件源正常结束 → 全部可转换事件产出后结束，忽略项不产出。"""
        citations = [
            {
                "index": 1,
                "source": "财报.pdf",
                "page": 5,
                "snippet": "营收100亿",
                "score": 0.95,
            },
            {
                "index": 2,
                "source": "财报.pdf",
                "page": 6,
                "snippet": "净利50亿",
                "score": 0.9,
            },
        ]

        async def fake_events():
            yield _make_token_item("你好")
            yield _make_format_end_item(citations)
            yield {  # 忽略事件（on_chain_start）不应产出任何 SSE 事件
                LangGraphKey.EVENT: LangGraphEvent.CHAIN_START,
                LangGraphKey.NAME: "agent",
                LangGraphKey.DATA: {},
            }

        events = []
        async for e in _dual_stream(fake_events(), asyncio.Queue(), asyncio.Event()):
            events.append(e)

        assert events == [
            SSETokenEvent("你好"),
            SSECitationEvent(
                source="财报.pdf",
                page=5,
                snippet="营收100亿",
                score=0.95,
                index=1,
            ),
            SSECitationEvent(
                source="财报.pdf",
                page=6,
                snippet="净利50亿",
                score=0.9,
                index=2,
            ),
        ]


class TestConvertEvent:
    """_convert_event 转换规则测试。"""

    def test_convert_chat_model_stream_agent_token(self):
        """agent 节点 on_chat_model_stream → SSETokenEvent。"""
        result = _convert_event(_make_token_item("你好"))
        assert result == [SSETokenEvent("你好")]

    def test_convert_chat_model_stream_non_agent_ignored(self):
        """非 agent 节点（generate）的流式 token 不产出。"""
        item = {
            LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
            LangGraphKey.NAME: "ChatOpenAI",
            "metadata": {"langgraph_node": "generate"},
            LangGraphKey.DATA: {LangGraphKey.CHUNK: AIMessageChunk(content="忽略")},
        }
        assert _convert_event(item) == []

    def test_convert_chat_model_stream_empty_content_ignored(self):
        """agent 节点但 chunk 内容为空的流式事件不产出。"""
        item = {
            LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
            LangGraphKey.NAME: "ChatOpenAI",
            "metadata": {"langgraph_node": "agent"},
            LangGraphKey.DATA: {LangGraphKey.CHUNK: AIMessageChunk(content="")},
        }
        assert _convert_event(item) == []

    def test_convert_format_end_produces_citations(self):
        """format on_chain_end → 逐个 SSECitationEvent。"""
        citations = [
            {
                "index": 1,
                "source": "财报.pdf",
                "page": 5,
                "snippet": "营收100亿",
                "score": 0.95,
            },
            {
                "index": 2,
                "source": "年报.pdf",
                "page": 8,
                "snippet": "净利50亿",
                "score": 0.88,
            },
        ]
        result = _convert_event(_make_format_end_item(citations))
        assert result == [
            SSECitationEvent(
                source="财报.pdf",
                page=5,
                snippet="营收100亿",
                score=0.95,
                index=1,
            ),
            SSECitationEvent(
                source="年报.pdf",
                page=8,
                snippet="净利50亿",
                score=0.88,
                index=2,
            ),
        ]

    def test_convert_format_end_empty_citations(self):
        """format 明确无引用（空 citations）→ 不产出。"""
        assert _convert_event(_make_format_end_item([])) == []

    def test_convert_ask_user_item(self):
        """ask_user 工具经 clarify_channel 推送的 item → SSEAskUserEvent 问题卡片。"""
        item = {
            "type": "ask_user",
            "questions": [
                {
                    "id": "q1",
                    "question": "您想查询哪家公司？",
                    "dimension": "company",
                }
            ],
        }
        result = _convert_event(item)
        assert result == [SSEAskUserEvent(questions=item["questions"])]

    def test_convert_unknown_item(self):
        """无法识别的 item → 空列表（不产出）。"""
        assert _convert_event({"foo": "bar"}) == []


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


class TestStreamChatWrapper:
    """stream_chat 外层生命周期测试（断连清理 / assistant Redis 写入）。"""

    @pytest.mark.asyncio
    async def test_stream_chat_aclose_clean(self):
        """客户端断连：aclose 不抛 RuntimeError，ctx 已 reset，abort_signal 已置位。"""
        service, _ = _make_service()

        entered_block = asyncio.Event()  # 事件源已进入阻塞的标志

        async def fake_astream(*args, **kwargs):
            yield _make_token_item("你好")
            entered_block.set()
            await asyncio.sleep(60)  # 阻塞直至被 Task A 取消

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        agen = service.stream_chat("kb1", "session1", "营收多少")
        it = agen.__aiter__()
        first = await it.__anext__()
        assert first == SSETokenEvent("你好")
        ctx = current_request_ctx.get()
        assert ctx is not None
        # 等待事件源进入阻塞后再断连，确保取消落在挂起的 Task A 上
        await asyncio.wait_for(entered_block.wait(), timeout=2)
        await agen.aclose()
        assert current_request_ctx.get() is None
        assert ctx.abort_signal.is_set()

    @pytest.mark.asyncio
    async def test_stream_chat_persists_assistant_to_redis(self):
        """正常结束后累积的 assistant 文本写入 chat_manager（Redis 历史）。"""
        service, chat_manager = _make_service()

        async def fake_astream(*args, **kwargs):
            yield _make_token_item("你好")
            yield _make_token_item("，世界")

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session1", "营收多少"):
            events.append(event)

        tokens = [e for e in events if isinstance(e, SSETokenEvent)]
        assert [t.token for t in tokens] == ["你好", "，世界"]
        done_events = [e for e in events if isinstance(e, SSEDoneEvent)]
        assert len(done_events) == 1
        # user 消息先写，assistant 消息后写（含全部累积 token）
        chat_manager.add_message_async.assert_any_call(
            "session1", "assistant", "你好，世界"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_no_assistant_write_when_empty(self):
        """无任何 token 产出时（full_answer 为空）不写 assistant 到 chat_manager。"""
        service, chat_manager = _make_service()

        async def fake_astream(*args, **kwargs):
            yield _make_format_end_item([])

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session1", "营收多少"):
            events.append(event)

        done_events = [e for e in events if isinstance(e, SSEDoneEvent)]
        assert len(done_events) == 1
        chat_manager.add_message_async.assert_any_call("session1", "user", "营收多少")
        for call in chat_manager.add_message_async.await_args_list:
            assert call.args[1] != "assistant"
