"""文本答案解析：单问/多问、选项匹配、multi_select、无效判定，及 handler 级澄清注入。"""

import json

import pytest

from src.channels.wecom.clarify_parse import parse_answers
from src.channels.wecom.session import derive_session_id
from src.config.wecom_channel import WeComChannelTexts
from tests.channels.test_wecom_handler import _handler, _msg, _Sink

_ONE = [
    {
        "id": "q1",
        "question": "选哪个口径？",
        "options": ["营收", "毛利"],
        "multi_select": False,
    }
]
_TWO = [
    {
        "id": "q1",
        "question": "哪个指标？",
        "options": ["营收", "毛利"],
        "multi_select": False,
    },
    {"id": "q2", "question": "哪一期？", "options": None, "multi_select": False},
]


def test_single_question_option_hit_by_exact_text():
    assert parse_answers("毛利", _ONE) == [
        {"id": "q1", "selected": ["毛利"], "custom": ""}
    ]


def test_single_question_option_hit_by_index():
    assert parse_answers("2", _ONE) == [
        {"id": "q1", "selected": ["毛利"], "custom": ""}
    ]


def test_single_question_falls_back_to_custom():
    assert parse_answers("看三年平均", _ONE) == [
        {"id": "q1", "selected": [], "custom": "看三年平均"}
    ]


def test_multi_select_splits_by_separators():
    q = [
        {
            "id": "q1",
            "question": "选指标",
            "options": ["营收", "毛利", "ROE"],
            "multi_select": True,
        }
    ]
    assert parse_answers("营收、ROE", q) == [
        {"id": "q1", "selected": ["营收", "ROE"], "custom": ""}
    ]


def test_two_questions_require_numbered_reply():
    assert parse_answers("1) 营收\n2) 2024 年", _TWO) == [
        {"id": "q1", "selected": ["营收"], "custom": ""},
        {"id": "q2", "selected": [], "custom": "2024 年"},
    ]


def test_two_questions_without_numbering_is_invalid():
    assert parse_answers("营收和 2024 年", _TWO) is None


def test_blank_text_is_invalid():
    assert parse_answers("   ", _ONE) is None


def test_no_questions_is_invalid():
    assert parse_answers("随便", []) is None


@pytest.mark.asyncio
async def test_trigger_answer_is_injected_not_new_turn():
    """触发者的答复走注入：调用 resolve_answer 且**不**开新回合。"""
    resolves: list[tuple[str, list]] = []

    async def _resolve(svc, *, session_id, answers):
        resolves.append((session_id, answers))
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(
        session_id,
        json.dumps(
            [
                {
                    "id": "q1",
                    "question": "选哪个？",
                    "options": ["甲", "乙"],
                    "multi_select": False,
                }
            ]
        ),
    )
    sink = _Sink()

    await handler(_msg(text="乙"), sink)

    assert resolves == [(session_id, [{"id": "q1", "selected": ["乙"], "custom": ""}])]
    assert recorder["start_turn_calls"] == []
    assert sink.calls == []


@pytest.mark.asyncio
async def test_non_trigger_message_is_not_treated_as_answer():
    """非触发者的消息**不**注入（落原路径）。"""
    resolves: list[list] = []

    async def _resolve(svc, *, session_id, answers):
        resolves.append(answers)
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT9", from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(
        session_id,
        json.dumps(
            [
                {
                    "id": "q1",
                    "question": "选哪个？",
                    "options": ["甲"],
                    "multi_select": False,
                }
            ]
        ),
    )
    sink = _Sink()

    await handler(
        _msg(chattype="group", chatid="CHAT9", from_userid="U2", text="我也说一句"),
        sink,
    )

    assert resolves == []
    assert len(recorder["start_turn_calls"]) == 1


@pytest.mark.asyncio
async def test_invalid_answer_prompts_and_keeps_pending():
    """无法解析的答复：给提示、**不**调 resolve（挂起保留，用户可重答）。"""
    resolves: list[list] = []

    async def _resolve(svc, *, session_id, answers):
        resolves.append(answers)
        return True

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(
        session_id,
        json.dumps(
            [
                {
                    "id": "q1",
                    "question": "哪个指标？",
                    "options": ["营收"],
                    "multi_select": False,
                },
                {
                    "id": "q2",
                    "question": "哪一期？",
                    "options": None,
                    "multi_select": False,
                },
            ]
        ),
    )
    sink = _Sink()

    await handler(_msg(text="营收和 2024 年"), sink)

    assert resolves == []
    assert sink.calls == [(WeComChannelTexts.CLARIFY_INVALID_TEXT, True)]
    assert recorder["start_turn_calls"] == []


@pytest.mark.asyncio
async def test_resolve_miss_falls_back_to_new_turn():
    """挂起已超时/已消费（resolve 返回 False）：回落为新回合。"""

    async def _resolve(svc, *, session_id, answers):
        return False

    handler, recorder = _handler(resolve_answer=_resolve)
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    handler._triggers.put(session_id, "U1")
    handler._questions.put(
        session_id,
        json.dumps(
            [
                {
                    "id": "q1",
                    "question": "选哪个？",
                    "options": ["甲"],
                    "multi_select": False,
                }
            ]
        ),
    )
    sink = _Sink()

    await handler(_msg(text="甲"), sink)

    assert len(recorder["start_turn_calls"]) == 1
