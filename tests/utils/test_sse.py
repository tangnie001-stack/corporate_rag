"""Tests for SSE 事件格式化。"""

from src.utils.sse import (
    SSEClarificationEvent,
    sse_clarification,
    to_sse,
)


def test_sse_clarification_format():
    """验证 sse_clarification() 输出格式。"""
    event = SSEClarificationEvent(
        type="entity_completion",
        question="请问您想查询哪一年的数据？",
        missing_entities=[{"type": "year"}],
        suggestions=["2023年", "2024年", "其他"],
    )
    output = sse_clarification(event)
    assert output.startswith("event: clarification")
    assert "entity_completion" in output
    assert "2023年" in output
    assert output.endswith("\n\n")


def test_to_sse_handles_clarification():
    """验证 to_sse() 能正确调度 clarification 事件。"""
    event = SSEClarificationEvent(
        type="entity_completion",
        question="test",
        missing_entities=[{"type": "year"}],
        suggestions=["a", "b"],
    )
    output = to_sse(event)
    assert output.startswith("event: clarification")


def test_sse_citation_with_index():
    """sse_citation 应序列化 index 字段。"""
    from src.utils.sse import SSECitationEvent, to_sse

    event = SSECitationEvent(source="a.pdf", page=3, snippet="内容", index=2)
    text = to_sse(event)
    assert '"index": 2' in text
    assert '"source": "a.pdf"' in text
