"""桥接 handler 单测：标识推导、去重、事件分流、TurnBusy 翻译、投影接线。"""

from typing import Any, cast

import pytest

from src.channels.base import InboundMessage
from src.channels.wecom.bounded_map import BoundedTtlMap
from src.channels.wecom.handler import RagChannelHandler, extract_feedback_id
from src.channels.wecom.session import derive_session_id
from src.config.const import WECOM_EVENT_FEEDBACK
from src.config.wecom_channel import WeComChannelTexts
from src.services import turn_runner
from src.services.app_service import AppService
from src.utils.sse import SSEAskUserEvent, SSEDoneEvent, SSETokenEvent


class _Sink:
    """记录回复的假 sink。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.calls.append((content, finish))


class _FakeTurnHandle:
    def __init__(self, session_id: str, events: list[Any]) -> None:
        self.session_id = session_id
        self.events = _aiter(events)


async def _aiter(events: list[Any]):
    for event in events:
        yield event


def _msg(**overrides: Any) -> InboundMessage:
    base = {
        "msgid": "M1",
        "aibotid": "AIB1",
        "chatid": None,
        "chattype": "single",
        "from_userid": "U1",
        "msgtype": "text",
        "text": "你好",
        "event_type": None,
        "raw": {},
    }
    base.update(overrides)
    return InboundMessage(**base)


def _handler(*, start_turn=None, events=None):
    """构造被测 handler，返回 (handler, 记录用的容器)。"""
    recorded: dict[str, Any] = {"start_turn_calls": []}
    if events is None:
        events = [SSETokenEvent(token="甲"), SSEDoneEvent()]

    async def _default_start_turn(svc, **kwargs):
        recorded["start_turn_calls"].append(kwargs)
        return cast(
            turn_runner.TurnHandle,
            _FakeTurnHandle(kwargs["session_id"], events),
        )

    async def _get_service():
        return cast(AppService, object())

    def _resolve_bot_key(aibotid: str) -> str | None:
        if aibotid == "AIB1":
            return "dev"
        if aibotid == "AIB2":
            return "support"
        return None

    handler = RagChannelHandler(
        start_turn=start_turn or _default_start_turn,
        get_service=_get_service,
        resolve_bot_key=_resolve_bot_key,
        dedup=BoundedTtlMap(capacity=10, ttl_seconds=600.0),
        triggers=BoundedTtlMap(capacity=10, ttl_seconds=300.0),
        kb_id="",
    )
    return handler, recorded


@pytest.mark.asyncio
async def test_text_message_runs_turn_and_replies():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(), sink)

    assert len(recorded["start_turn_calls"]) == 1
    call = recorded["start_turn_calls"][0]
    assert call["kb_id"] == ""
    assert call["query"] == "你好"
    assert call["title"].startswith("[企微·dev]")
    assert len(call["session_id"]) == 36
    assert len(call["user_id"]) == 36
    assert sink.calls[-1][1] is True
    assert sink.calls[-1][0].startswith("甲")


@pytest.mark.asyncio
async def test_unknown_bot_is_ignored():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(aibotid="UNKNOWN"), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


@pytest.mark.asyncio
async def test_duplicate_msgid_is_dropped():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(), sink)
    first_call_count = len(sink.calls)
    await handler(_msg(), sink)

    assert len(recorded["start_turn_calls"]) == 1
    assert len(sink.calls) == first_call_count  # 重推不产生任何新帧


@pytest.mark.asyncio
async def test_same_msgid_from_two_bots_both_run():
    """去重键含 bot_key：同一 msgid 来自两台不同机器人不算重推。"""
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(aibotid="AIB1", msgid="M-SHARED"), sink)
    await handler(_msg(aibotid="AIB2", msgid="M-SHARED"), sink)

    assert len(recorded["start_turn_calls"]) == 2


@pytest.mark.asyncio
async def test_turn_busy_replies_busy_text():
    async def _busy(svc, **kwargs):
        raise turn_runner.TurnBusy()

    handler, _recorded = _handler(start_turn=_busy)
    sink = _Sink()

    await handler(_msg(), sink)

    assert sink.calls == [(WeComChannelTexts.BUSY_TEXT, True)]


@pytest.mark.asyncio
async def test_non_text_message_gets_unsupported_hint():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(msgtype="image", text=None), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == [(WeComChannelTexts.UNSUPPORTED_TEXT, True)]


@pytest.mark.asyncio
async def test_event_frame_does_not_start_turn():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(msgtype="event", text=None, event_type="enter_chat"), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


@pytest.mark.asyncio
async def test_feedback_event_does_not_start_turn():
    handler, recorded = _handler()
    sink = _Sink()
    raw = {
        "event": {
            "eventtype": WECOM_EVENT_FEEDBACK,
            "feedback_event": {"id": "trace_abc", "type": 1},
        }
    }

    await handler(
        _msg(msgtype="event", text=None, event_type=WECOM_EVENT_FEEDBACK, raw=raw),
        sink,
    )

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


def test_extract_feedback_id_from_real_frame():
    """字段路径以 Spike E10 实测帧为准：body.event.feedback_event.id。"""
    raw = {
        "msgid": "76a963c5",
        "msgtype": "event",
        "event": {
            "eventtype": "feedback_event",
            "feedback_event": {"id": "trace_spike_e10", "type": 1},
        },
    }
    assert extract_feedback_id(raw) == "trace_spike_e10"


def test_extract_feedback_id_tolerates_malformed_frames():
    assert extract_feedback_id({}) is None
    assert extract_feedback_id({"event": None}) is None
    assert extract_feedback_id({"event": {"feedback_event": {}}}) is None
    assert extract_feedback_id({"event": {"feedback_event": {"type": 1}}}) is None


@pytest.mark.asyncio
async def test_ask_user_event_registers_trigger_and_does_not_reply():
    ask_user = SSEAskUserEvent(questions=[{"id": "q1", "question": "?"}])
    handler, _recorded = _handler(events=[ask_user, SSEDoneEvent()])
    sink = _Sink()

    await handler(_msg(chattype="group", chatid="CHAT9"), sink)

    session_id = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT9", from_userid="U1"
    )
    assert handler.registered_trigger(session_id) == "U1"
    # 澄清事件不产帧（二期由组 6 呈现问题）：帧 = 终态帧
    assert len(sink.calls) == 1
    assert sink.calls[-1][1] is True
