"""WeComPresenter 投影层单测：全部用假 sink，不发真实网络。"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from src.channels.wecom.presenter import WeComPresenter, is_blank
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAgentUsedEvent,
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDelegateEvent,
    SSEDoneEvent,
    SSEErrorEvent,
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


async def _slow_stream(
    events: list[SSEEvent], delays: list[float]
) -> AsyncIterator[SSEEvent]:
    """按给定延迟逐个产出事件（用于保活与首帧超时用例）。"""
    for event, delay in zip(events, delays):
        await asyncio.sleep(delay)
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


@pytest.mark.asyncio
async def test_sources_only_in_final_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSECitationEvent(source="财报.pdf", page=3, snippet="…"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert "参考来源" not in sink.contents[0]
    assert "财报.pdf" not in sink.contents[0]
    assert "参考来源" in sink.contents[-1]
    assert "财报.pdf" in sink.contents[-1]
    assert "第 3 页" in sink.contents[-1]


@pytest.mark.asyncio
async def test_no_sources_no_sources_block():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))

    assert "参考来源" not in sink.contents[-1]


@pytest.mark.asyncio
async def test_trace_footer_in_final_frame_and_switchable():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_abc", min_interval_seconds=0, footer_enabled=True
    )
    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))
    assert "trace_abc" in sink.contents[-1]

    sink2 = _RecordingSink()
    presenter2 = WeComPresenter(
        sink2,
        "trace_abc",
        min_interval_seconds=0,
        footer_enabled=False,
        feedback_enabled=True,
    )
    await presenter2.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))
    assert "trace_abc" not in sink2.contents[-1]


@pytest.mark.asyncio
async def test_feedback_id_only_on_first_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, feedback_enabled=True
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.calls[0][2] == {"id": "trace_1"}
    assert sink.calls[1][2] is None


@pytest.mark.asyncio
async def test_error_is_desensitized():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEErrorEvent(error="psycopg: connection refused to 10.0.0.5"),
            ]
        )
    )

    final = sink.contents[-1]
    assert WeComPresenterTexts.ERROR_TEXT in final
    assert "psycopg" not in final
    assert "10.0.0.5" not in final
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_abstention_appends_transfer_hint():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="未在文档中找到"),
                SSEAbstentionEvent(),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert WeComPresenterTexts.ABSTENTION_TEXT in sink.contents[-1]


@pytest.mark.asyncio
async def test_empty_answer_falls_back_and_never_hangs():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSEDoneEvent(trace_id="trace_1")]))

    assert sink.calls, "终态帧必发，不得悬挂"
    assert WeComPresenterTexts.FALLBACK_TEXT in sink.contents[-1]
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_stream_failure_degrades_to_single_final_send():
    class _FailingSink(_RecordingSink):
        async def reply_stream(
            self, content: str, finish: bool, feedback: dict | None = None
        ) -> None:
            if not finish:
                raise RuntimeError("reply ack timeout")
            await super().reply_stream(content, finish, feedback)

    sink = _FailingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        footer_enabled=False,
        feedback_enabled=True,
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 降级路径下中间帧发送失败，终态帧成为首个成功帧，故携带反馈标识
    assert sink.calls == [("甲乙", True, {"id": "trace_1"})]


@pytest.mark.asyncio
async def test_keepalive_fires_during_silence():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, keepalive_seconds=0.05
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 静默期内至少重发一次当前快照，且内容与上一帧相同
    assert sink.contents.count("甲") >= 2


@pytest.mark.asyncio
async def test_keepalive_does_not_terminate_upstream():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, keepalive_seconds=0.05
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 保活发生过之后，上游后续事件仍被消费到终态
    assert sink.contents[-1].startswith("甲乙")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_placeholder_frame_when_first_frame_is_late():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        first_frame_timeout=0.05,
        keepalive_seconds=1.0,
    )

    await presenter.run(
        _slow_stream([SSETokenEvent(token="甲"), SSEDoneEvent()], [0.2, 0.0])
    )

    assert sink.contents[0] == WeComPresenterTexts.PLACEHOLDER_TEXT


@pytest.mark.asyncio
async def test_run_ends_when_stream_ends_without_terminal_event():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSETokenEvent(token="甲")]))

    assert sink.contents[-1].startswith("甲")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_keepalive_frames_not_counted_toward_cap():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        max_frames=1,
        keepalive_seconds=0.05,
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 正文帧已触顶（"甲乙" 不再作为中间帧发出），但保活帧仍照发（豁免上限）
    mid_stream = [content for content, finish, _fb in sink.calls if not finish]
    assert "甲乙" not in mid_stream
    assert mid_stream.count("甲") >= 2
    assert sink.contents[-1].startswith("甲乙")


@pytest.mark.asyncio
async def test_run_returns_even_if_upstream_never_ends():
    async def _endless() -> AsyncIterator[SSEEvent]:
        yield SSETokenEvent(token="甲")
        yield SSEDoneEvent(trace_id="trace_1")
        await asyncio.sleep(30)
        yield SSETokenEvent(token="不该到达")

    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    async with asyncio.timeout(2):
        await presenter.run(_endless())

    assert sink.calls[-1][1] is True
    assert all("不该到达" not in content for content, _f, _fb in sink.calls)


@pytest.mark.asyncio
async def test_run_finalizes_when_consume_loop_raises(monkeypatch):
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    async def _boom(event: SSEEvent) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(presenter, "update", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await presenter.run(_stream([SSETokenEvent(token="甲")]))

    # 投影 spec 禁止悬挂未结束的流：异常路径也必须发出终态帧
    assert sink.calls
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_placeholder_frame_when_first_events_are_unrenderable():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        first_frame_timeout=0.05,
        keepalive_seconds=5.0,
    )

    await presenter.run(
        _slow_stream(
            [
                SSEReasoningDeltaEvent(reasoning_delta="先想一想"),
                SSETokenEvent(token="甲"),
                SSEDoneEvent(),
            ],
            [0.0, 0.3, 0.0],
        )
    )

    # 首事件不可渲染（reasoning 被丢弃）时，占位帧仍须在 first_frame_timeout 内发出；
    # keepalive_seconds 故意设 5s —— 若按"收到过事件"切换超时，这里就不会有占位帧
    assert sink.contents[0] == WeComPresenterTexts.PLACEHOLDER_TEXT
    assert sink.contents[-1].startswith("甲")


@pytest.mark.asyncio
async def test_upstream_exception_yields_desensitized_terminal():
    async def _boom():
        yield SSETokenEvent(token="甲")
        raise RuntimeError("psycopg: connection refused to 10.0.0.5")

    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, footer_enabled=False
    )

    await presenter.run(_boom())

    final = sink.contents[-1]
    assert WeComPresenterTexts.ERROR_TEXT in final
    assert "psycopg" not in final
    assert "10.0.0.5" not in final
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_ask_user_event_produces_no_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEAskUserEvent(questions=[{"id": "q1", "question": "?"}]),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 契约：澄清事件不产帧（二期由组 6 处理）；帧数 = 1 中间帧 + 1 终态帧
    assert len(sink.calls) == 2
    assert sink.contents[-1].startswith("甲")


@pytest.mark.asyncio
async def test_footer_forced_on_when_feedback_unavailable_at_instance_level():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_abc",
        min_interval_seconds=0,
        footer_enabled=False,
        feedback_enabled=False,
    )

    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))

    assert "trace_abc" in sink.contents[-1]


@pytest.mark.asyncio
async def test_cancelled_done_event_still_finalizes():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEDoneEvent(trace_id="trace_1", cancelled=True),
            ]
        )
    )

    assert sink.contents[-1].startswith("甲")
    assert sink.calls[-1][1] is True
