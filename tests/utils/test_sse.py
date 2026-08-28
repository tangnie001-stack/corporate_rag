"""Tests for SSE 事件格式化。"""

from src.services.agent_service import _convert_event
from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAskUserEvent,
    SSEReasoningDeltaEvent,
    to_sse,
)


def test_sse_citation_with_index():
    """sse_citation 应序列化 index 字段。"""
    from src.utils.sse import SSECitationEvent, to_sse

    event = SSECitationEvent(source="a.pdf", page=3, snippet="内容", index=2)
    text = to_sse(event)
    assert '"index": 2' in text
    assert '"source": "a.pdf"' in text


def test_ask_user_event_serializes():
    """to_sse 应序列化 ask_user 事件及 questions 字段。"""
    ev = SSEAskUserEvent(
        questions=[
            {
                "id": "q1",
                "question": "您想查询哪一年？",
                "options": ["2024年", "2023年"],
                "multi_select": False,
            }
        ]
    )
    text = to_sse(ev)
    assert '"type": "ask_user"' in text
    assert '"questions"' in text
    assert "您想查询哪一年？" in text


def test_abstention_event():
    """to_sse 应序列化 abstention 事件及转人工文案。"""
    ev = SSEAbstentionEvent()
    text = to_sse(ev)
    assert '"type": "abstention"' in text
    assert "转人工咨询" in text


def test_convert_ask_user_item():
    """_convert_event 将 ask_user item 转为 SSEAskUserEvent 列表。"""
    questions = [
        {
            "id": "q1",
            "question": "您想查询哪家公司？",
            "options": ["东软"],
            "multi_select": False,
        }
    ]
    result = _convert_event({"type": "ask_user", "questions": questions})
    assert isinstance(result[0], SSEAskUserEvent)
    assert result[0].questions == questions
    assert result[0].type == "ask_user"


def test_sse_reasoning_delta_event():
    text = to_sse(SSEReasoningDeltaEvent(reasoning_delta="思考片段"))
    assert text == 'event: reasoning\ndata: {"delta": "思考片段"}\n\n'
