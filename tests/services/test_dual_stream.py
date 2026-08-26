"""_dual_stream 双路合并与 _convert_event 事件转换单元测试。"""

import asyncio

import pytest
from langchain_core.messages import AIMessageChunk

from src.agents.graph.state import LangGraphEvent, LangGraphKey, LangGraphNode
from src.config.const import ASK_USER_STATUS_MSG
from src.services.agent_service import _convert_event, _dual_stream
from src.utils.sse import (
    SSECitationEvent,
    SSEErrorEvent,
    SSEStatusEvent,
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
        """ask_user 工具经 clarify_channel 推送的 item → 过渡状态事件。"""
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
        assert result == [SSEStatusEvent(stage="ask_user", message=ASK_USER_STATUS_MSG)]

    def test_convert_unknown_item(self):
        """无法识别的 item → 空列表（不产出）。"""
        assert _convert_event({"foo": "bar"}) == []
