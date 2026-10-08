"""投影层对澄清事件的呈现（阶段 4b 取代阶段 3 的"不产帧"契约）。"""

import pytest

from src.channels.wecom.presenter import WeComPresenter
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import SSEAskUserEvent, SSEDoneEvent, SSETokenEvent
from tests.channels.wecom_presenter_fakes import _RecordingSink, _stream


@pytest.mark.asyncio
async def test_ask_user_event_is_rendered_into_content():
    """澄清事件渲染进累积内容且**不 finalize**（回合未结束，终态帧只能发一次）。"""
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEAskUserEvent(
                    questions=[
                        {
                            "id": "q1",
                            "question": "选哪个口径？",
                            "options": ["营收", "毛利"],
                            "multi_select": False,
                        }
                    ]
                ),
            ]
        )
    )

    content = sink.contents[-1]
    assert "选哪个口径？" in content
    assert "营收" in content
    assert sink.calls[-1][1] is False  # 仍是非终态帧：等待用户作答


@pytest.mark.asyncio
async def test_clarify_question_and_answer_share_one_bubble():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEAskUserEvent(
                    questions=[
                        {
                            "id": "q1",
                            "question": "选哪个口径？",
                            "options": ["营收"],
                            "multi_select": False,
                        }
                    ]
                ),
                SSETokenEvent(token="按营收口径："),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    final = sink.contents[-1]
    assert "选哪个口径？" in final  # 问题仍在（快照累积）
    assert "按营收口径：" in final  # 后续答案接在同一气泡
    assert sum(1 for _content, finish, _fb in sink.calls if finish) == 1  # 终态帧只一次


@pytest.mark.asyncio
async def test_ask_user_event_without_questions_renders_nothing():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSEAskUserEvent(questions=[]), SSEDoneEvent()]))

    assert WeComPresenterTexts.FALLBACK_TEXT in sink.contents[-1]  # 走既有兜底


def test_render_questions_pure_function():
    from src.channels.wecom.clarify_render import render_questions

    assert render_questions([]) == ""
    assert render_questions([{"question": "  "}]) == ""
    rendered = render_questions([{"question": "选哪个？", "options": ["甲", "乙"]}])
    assert "1. 选哪个？" in rendered
    assert "甲、乙" in rendered


@pytest.mark.asyncio
async def test_clarify_then_upstream_exception_still_finalizes_desensitized():
    """已渲染澄清问题后上游异常：仍须 finalize 并发出脱敏错误文案，不得悬挂流。"""

    async def _boom():
        yield SSEAskUserEvent(
            questions=[{"id": "q1", "question": "选哪个口径？", "options": ["营收"]}]
        )
        raise RuntimeError("psycopg: connection refused to 10.0.0.5")

    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, footer_enabled=False
    )

    await presenter.run(_boom())

    final = sink.contents[-1]
    assert sink.calls[-1][1] is True  # 发了终态帧
    assert WeComPresenterTexts.ERROR_TEXT in final  # 脱敏错误文案
    assert "psycopg" not in final  # 不含原始异常串
    assert "10.0.0.5" not in final
