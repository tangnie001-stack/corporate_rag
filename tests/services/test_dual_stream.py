"""_dual_stream 双路合并与 _convert_event 事件转换单元测试。"""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessageChunk

from src.agents.graph.state import LangGraphEvent, LangGraphKey, LangGraphNode
from src.chat.streaming import _subscribe_buffer, streaming_manager
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
    async def test_disconnect_does_not_set_abort(self):
        """断连（aclose）不置位 abort_signal —— 仅 cancel 端点经 manager 置位。"""
        abort_signal = asyncio.Event()

        async def fake_events():
            yield _make_token_item("a")
            await asyncio.sleep(5)

        gen = _dual_stream(fake_events(), asyncio.Queue(), abort_signal)
        ait = gen.__aiter__()
        first = await ait.__anext__()
        assert first == SSETokenEvent("a")
        await gen.aclose()  # 模拟客户端断开
        assert abort_signal.is_set() is False  # 断连不得置位 abort

    @pytest.mark.asyncio
    async def test_dual_stream_cancel_propagates(self):
        """Task B 被 aclose 取消 → 取消事件源（Task A），但不置位 abort。"""
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
        assert abort_signal.is_set() is False  # 断连不置位 abort（仅 cancel 置位）

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


class TestSubscribeBuffer:
    """_subscribe_buffer 缓冲回放消费者测试。"""

    @pytest.mark.asyncio
    async def test_subscribe_buffer_replays_then_tails(self):
        """缓冲中已有事件全部回放为 SSE 帧，遇 done 终态自然结束（不产生超时 error）。"""
        from src.chat.streaming import StreamingRunManager

        mgr = StreamingRunManager()
        mgr.clear_buffer("s1")
        mgr.add_event("s1", "token", {"token": "a"})
        mgr.add_event("s1", "token", {"token": "b"})
        mgr.add_event("s1", "done", {"trace_id": ""})

        collected = []
        async for event in _subscribe_buffer("s1", mgr, after_seq=0, max_idle=0.2):
            collected.append(event)

        # 回放 token a/b（done 帧一并回放），由 has_terminal 结束而非空闲超时
        assert len(collected) == 3
        assert "event: token" in collected[0]
        assert '"token": "a"' in collected[0]
        assert "event: token" in collected[1]
        assert '"token": "b"' in collected[1]
        assert "event: done" in collected[2]
        assert not any("event: error" in e for e in collected)


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
    """stream_chat 外层生命周期测试（后台任务启动 / 订阅消费 / 断连不中止生成）。"""

    @pytest.mark.asyncio
    async def test_stream_chat_history_excludes_current_query(self):
        """stream_chat 固定顺序：先取历史（不含当前 query）再写 user 消息再启动任务。

        P3 顺序约束：若先写 user 再取历史，当前 query 会作为历史进入下一轮
        prompt 上下文，造成 prompt 上下文污染。
        """
        from src.services.agent_service import AgentService

        svc = AgentService.__new__(AgentService)
        svc._chat_manager = Mock()
        calls = []

        async def fake_get_history(session_id):
            calls.append(("history", session_id))
            return []

        async def fake_add(session_id, role, content, **kwargs):
            calls.append(("add", role, content))

        svc._chat_manager.get_history_async = fake_get_history
        svc._chat_manager.add_message_async = fake_add
        svc._tracer = Mock()  # @traced 装饰器读取 self._tracer

        # 后台任务所需最小图：零事件，避免任务异常噪音
        async def empty_astream(*args, **kwargs):
            return
            yield  # pragma: no cover

        svc._graph = Mock()
        svc._graph.astream_events = empty_astream

        agen = svc.stream_chat("kb1", "session-order", "营收多少", False)
        it = agen.__aiter__()
        await it.__anext__()  # 启动生成器至首个 yield 点：此时 history/add 已按序执行
        # 先 history 后 add（否则当前 query 会作为历史进 prompt）
        assert [c[0] for c in calls] == ["history", "add"]
        await agen.aclose()

    @pytest.mark.asyncio
    async def test_stream_chat_aclose_does_not_abort_generation(self):
        """客户端断连：aclose 不置位 abort，后台任务继续生成并写缓冲。"""
        service, _ = _make_service()
        resume = asyncio.Event()

        async def fake_astream(*args, **kwargs):
            yield _make_token_item("你好")
            await resume.wait()
            yield _make_token_item("，世界")

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        agen = service.stream_chat("kb1", "session-aclose", "营收多少")
        it = agen.__aiter__()
        first = await it.__anext__()
        assert first == SSETokenEvent("你好")
        abort_signal = streaming_manager.get_abort_signal("session-aclose")
        assert abort_signal is not None
        await agen.aclose()  # 模拟客户端断开
        assert abort_signal.is_set() is False  # 断连不得置位 abort
        # 后台任务继续运行：解除阻塞后仍产出事件到缓冲（生成未被中断）
        resume.set()
        await asyncio.sleep(0.3)
        events = streaming_manager.get_events_since("session-aclose", 0)
        token_texts = [payload["token"] for _, et, payload in events if et == "token"]
        assert token_texts == ["你好", "，世界"]
        assert streaming_manager.has_terminal("session-aclose") is True

    @pytest.mark.asyncio
    async def test_stream_chat_subscribes_tokens_and_done(self):
        """正常结束后订阅收到全部 token 与 done 终态（assistant 写入由 Task 2.8 负责）。"""
        service, chat_manager = _make_service()

        async def fake_astream(*args, **kwargs):
            yield _make_token_item("你好")
            yield _make_token_item("，世界")

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session-persist", "营收多少"):
            events.append(event)

        tokens = [e for e in events if isinstance(e, SSETokenEvent)]
        assert [t.token for t in tokens] == ["你好", "，世界"]
        done_events = [e for e in events if isinstance(e, SSEDoneEvent)]
        assert len(done_events) == 1
        # user 消息仍同步写入（assistant 收尾延迟到 Task 2.8）
        chat_manager.add_message_async.assert_any_call(
            "session-persist", "user", "营收多少"
        )

    @pytest.mark.asyncio
    async def test_stream_chat_empty_output_still_terminates(self):
        """无 token 产出时订阅仍以 done 终态结束（不悬挂，且不写 assistant）。"""
        service, chat_manager = _make_service()

        async def fake_astream(*args, **kwargs):
            yield _make_format_end_item([])

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session-empty", "营收多少"):
            events.append(event)

        done_events = [e for e in events if isinstance(e, SSEDoneEvent)]
        assert len(done_events) == 1
        chat_manager.add_message_async.assert_any_call(
            "session-empty", "user", "营收多少"
        )
        # assistant 写入不在本任务范围（Task 2.8 收尾），防止回归旧行为
        for call in chat_manager.add_message_async.await_args_list:
            assert call.args[1] != "assistant"
