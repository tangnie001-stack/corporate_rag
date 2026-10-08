"""WeComPresenter 流式与降级单测：保活/首帧超时/上游异常/取消/发送失败降级。"""

import asyncio
from collections.abc import AsyncIterator

import pytest

from src.channels.wecom.presenter import WeComPresenter
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import (
    SSEDoneEvent,
    SSEEvent,
    SSEReasoningDeltaEvent,
    SSETokenEvent,
)
from tests.channels.wecom_presenter_fakes import (
    _RecordingSink,
    _slow_stream,
    _stream,
)


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
