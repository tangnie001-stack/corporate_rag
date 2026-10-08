"""WeComPresenter 核心投影单测：骨架/快照累积/节流/上限/来源段/终态与兜底。"""

import pytest

from src.channels.wecom.presenter import WeComPresenter, is_blank
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAgentUsedEvent,
    SSECitationEvent,
    SSEDelegateEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETaskEvent,
    SSETokenEvent,
)
from tests.channels.wecom_presenter_fakes import (
    _frozen_clock,
    _RecordingSink,
    _stream,
)


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
