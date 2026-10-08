"""WeComPresenter 投影层单测：全部用假 sink，不发真实网络。"""

from collections.abc import AsyncIterator

import pytest

from src.channels.wecom.presenter import WeComPresenter, is_blank
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import (
    SSEAgentUsedEvent,
    SSEDelegateEvent,
    SSEDoneEvent,
    SSEEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETaskEvent,
    SSETokenEvent,
)


class _RecordingSink:
    """记录每次 reply_stream 调用的假 sink。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, dict | None]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.calls.append((content, finish, feedback))

    @property
    def contents(self) -> list[str]:
        return [content for content, _finish, _fb in self.calls]


def _frozen_clock(value: float = 1000.0):
    """返回恒定时钟（配合非零节流间隔，可稳定断言"被跳过"）。"""

    def _now() -> float:
        return value

    return _now


async def _stream(events: list[SSEEvent]) -> AsyncIterator[SSEEvent]:
    for event in events:
        yield event


def test_is_blank_covers_whitespace_and_zero_width():
    assert is_blank("") is True
    assert is_blank("   ") is True
    assert is_blank("\u200b\u200d\ufeff") is True
    assert is_blank("甲") is False


@pytest.mark.asyncio
async def test_frames_are_cumulative_snapshots():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSETokenEvent(token="丙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[:3] == ["甲", "甲乙", "甲乙丙"]
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_throttle_merges_mid_stream_frames():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0.1, monotonic=_frozen_clock()
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSETokenEvent(token="丙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    mid_stream = [content for content, finish, _fb in sink.calls if not finish]
    assert mid_stream == ["甲"]
    assert sink.contents[-1].startswith("甲乙丙")


@pytest.mark.asyncio
async def test_unchanged_content_is_skipped():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEReasoningDeltaEvent(reasoning_delta="想…"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 未变内容不产生第二个中间帧；总帧数 = 1 中间帧 + 1 终态帧
    assert len(sink.calls) == 2
    assert sink.contents[0] == "甲"


@pytest.mark.asyncio
async def test_blank_and_zero_width_never_sent_as_answer():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="\u200b"),
                SSETokenEvent(token="\u200b\u200b"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == WeComPresenterTexts.PLACEHOLDER_TEXT
    assert all("\u200b" not in content for content in sink.contents)


@pytest.mark.asyncio
async def test_status_event_becomes_placeholder_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEStatusEvent(stage="retrieve", message="正在检索相关文档..."),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == "正在检索相关文档..."


@pytest.mark.asyncio
async def test_frame_cap_stops_answer_frames_but_finalizes():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0, max_frames=2)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="a"),
                SSETokenEvent(token="b"),
                SSETokenEvent(token="c"),
                SSETokenEvent(token="d"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 中间帧：前两帧（承载正文）后触顶；终态帧仍发全量
    assert len(sink.calls) == 3
    assert sink.contents[:2] == ["a", "ab"]
    assert sink.contents[-1].startswith("abcd")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_long_content_keeps_tail():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0, max_chars=3)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="abcdef"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == "def"


@pytest.mark.asyncio
async def test_unrendered_events_are_dropped_without_interrupt():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEReasoningDeltaEvent(reasoning_delta="思考"),
                SSEDelegateEvent(delegate_id="d1", action="start", skill="s"),
                SSETaskEvent(action="created", task={"type": "skill"}),
                SSEModelInfoEvent(model="m1", is_fallback=False),
                SSEAgentUsedEvent(agent="finance"),
                SSETokenEvent(token="甲"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert len(sink.calls) == 2
    assert sink.contents[0] == "甲"
