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


def test_sse_citation_kind_web():
    """to_sse 应透传 kind=web 到 wire 格式。"""
    from src.utils.sse import SSECitationEvent, to_sse

    event = SSECitationEvent(source="https://a.com", page=0, snippet="x", kind="web")
    text = to_sse(event)
    assert '"kind": "web"' in text


def test_sse_citation_kind_default_kb():
    """sse_citation 未指定 kind 时默认序列化为 kb。"""
    from src.utils.sse import SSECitationEvent, to_sse

    event = SSECitationEvent(source="a.pdf", page=0, snippet="x")
    text = to_sse(event)
    assert '"kind": "kb"' in text


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
    """reasoning 增量事件序列化为标准 SSE 文本。"""
    text = to_sse(SSEReasoningDeltaEvent(reasoning_delta="思考片段"))
    assert text == 'event: reasoning\ndata: {"delta": "思考片段"}\n\n'


def test_from_payload_unknown_delegate_ok():
    from src.utils.sse import SSEDelegateEvent, from_payload

    ev = from_payload(
        "delegate",
        {"delegate_id": "d", "action": "delta", "kind": "thinking", "delta": "x"},
    )
    assert isinstance(ev, SSEDelegateEvent)
    assert ev.action == "delta"


# ==================== citation tier 字段（source-tier-labeling）====================


def test_citation_event_tier_in_payload():
    """tier 显式传入时进 payload。"""
    from src.utils.sse import SSECitationEvent

    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s", tier=2)
    assert ev.payload_for_buffer()["tier"] == 2


def test_citation_event_tier_default_none():
    """tier 默认 None（存量/未定档语义），payload 键值为 null。"""
    from src.utils.sse import SSECitationEvent

    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s")
    assert ev.payload_for_buffer()["tier"] is None


def test_citation_wire_json_contains_tier():
    """wire 契约：to_sse 输出（前端实际收到的 data: 行）必须含 tier 键。

    该测试守住「payload_for_buffer 有 tier 但 wire 序列化漏 tier」的断链——
    此类断链单测上半段全绿、线上徽标全无。
    """
    from src.utils.sse import SSECitationEvent

    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s", tier=1)
    wire = to_sse(ev)
    assert "event: citation" in wire
    assert '"tier": 1' in wire


def test_citation_wire_json_tier_none_serialized():
    """tier=None（存量语义）在 wire 中序列化为 null 键，前端据 null 降级。"""
    from src.utils.sse import SSECitationEvent

    ev = SSECitationEvent(source="a.pdf", page=1, snippet="s")
    assert '"tier": null' in to_sse(ev)
