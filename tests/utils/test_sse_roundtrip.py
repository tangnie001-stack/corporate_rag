"""SSE 事件序列化契约 round-trip 测试。

验证：缓冲 payload == 实时 data: 内容 ——
`to_sse(from_payload(e.type, e.payload_for_buffer())) == to_sse(e)`，
是 resume 回放与实时渲染同路径的基础。
"""

import pytest

from src.utils.sse import (
    SSEAbstentionEvent,
    SSEAskUserEvent,
    SSECitationEvent,
    SSEDoneEvent,
    SSEErrorEvent,
    SSEModelInfoEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETokenEvent,
    from_payload,
    to_sse,
)

CASES = [
    SSETokenEvent(token="你好"),
    SSEStatusEvent(stage="retrieving", message="检索中"),
    SSEStatusEvent(stage="retrieving", message="检索中", detail="详情"),
    SSECitationEvent(
        source="财报.pdf",
        page=3,
        snippet="摘要",
        score=0.9,
        highlighted_snippet="<mark>摘要</mark>",
        index=1,
        kind="kb",
    ),
    SSEDoneEvent(trace_id="trace_x"),
    SSEErrorEvent(error="boom"),
    # 注意：type 须等于事件名 "ask_user"（from_payload 以事件名路由），
    # 不能用 data 内的子类型（如 "clarify"）——brief 原值在此处需修正。
    SSEAskUserEvent(
        type="ask_user",
        questions=[{"id": "q1", "question": "哪个公司？", "options": ["A", "B"]}],
    ),
    SSEAbstentionEvent(type="abstention", message="未在文档中找到相关数据"),
    SSEReasoningDeltaEvent(reasoning_delta="思考中..."),
    SSEModelInfoEvent(model="qwen-max", is_fallback=False),
]


@pytest.mark.parametrize("ev", CASES, ids=lambda e: type(e).__name__)
def test_sse_roundtrip(ev):
    rebuilt = from_payload(ev.type, ev.payload_for_buffer())
    assert to_sse(rebuilt) == to_sse(ev)
