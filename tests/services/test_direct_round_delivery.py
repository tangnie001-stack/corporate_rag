"""直出轮回答交付与落库（Plan 2 遗留 C1）。

/xxx 命令行直出轮（skill_direct 节点）产出的 answer 必须经生产交付链
（_convert_event → manager.add_event → capture → serialize_process）进入
SSE token 流并被落库。回归防线：直出轮 answer 若只写在 state 而未经
_convert_event 产出 SSETokenEvent，则订阅流无 token、full_answer 为空、
purified_answer 为空串，落库丢答案。
"""

from typing import Any
from unittest.mock import Mock

import pytest

from src.agents.graph.state import LangGraphEvent, LangGraphKey, LangGraphNode
from src.chat.process_log import serialize_process
from src.chat.streaming import StreamingRunManager
from src.infra.llm.request_context import RequestContext
from src.services.agent_service import (
    _convert_event,
    _record_event,
    _run_generation,
    _StreamCapture,
)
from src.utils.sse import SSETokenEvent


def _skill_direct_start_item() -> dict:
    """构造 skill_direct 节点 on_chain_start 事件。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_START,
        LangGraphKey.NAME: LangGraphNode.SkillDirect.NAME,
        LangGraphKey.DATA: {},
    }


def _skill_direct_end_item() -> dict:
    """构造 skill_direct 节点 on_chain_end 事件（产出直出 answer 与 tool_contexts）。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
        LangGraphKey.NAME: LangGraphNode.SkillDirect.NAME,
        LangGraphKey.DATA: {
            LangGraphKey.OUTPUT: {
                "answer": "直出结论[1]",
                "tool_contexts": [
                    {"content": "2024 年营收 1000 亿", "source": "annual.pdf"}
                ],
            }
        },
    }


def _format_end_item(citations: list[dict]) -> dict:
    """构造 format 节点 on_chain_end 事件（产出引用列表）。"""
    return {
        LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
        LangGraphKey.NAME: LangGraphNode.Format.NAME,
        LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"citations": citations}},
    }


def _fake_graph():
    """构造 astream_events 依序产出直出轮事件的 fake graph。"""

    async def fake_astream(*args, **kwargs):
        yield _skill_direct_start_item()
        yield _skill_direct_end_item()
        yield _format_end_item(
            [
                {
                    "index": 1,
                    "source": "annual.pdf",
                    "page": 5,
                    "snippet": "营收1000亿",
                    "score": 0.95,
                }
            ]
        )

    graph = Mock()
    graph.astream_events = fake_astream
    return graph


@pytest.mark.asyncio
async def test_direct_round_delivers_token_through_run_generation():
    """直出轮经 _run_generation 生产链：token 入缓冲、full_answer 累积、净化正文非空。"""
    mgr = StreamingRunManager()
    partial_holder: dict[str, Any] = {"text": ""}

    answer = await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        RequestContext(session_id="s1"),
        mgr,
        graph=_fake_graph(),
        partial_holder=partial_holder,
    )

    # 核心判别点：订阅流（manager 缓冲）出现 token 事件且文本为直出正文
    events = mgr.get_events_since("s1", 0)
    token_payloads = [payload for _, etype, payload in events if etype == "token"]
    assert token_payloads == [{"token": "直出结论[1]"}]
    # full_answer 与 partial_holder["text"] 一致（取消/出错回读路径同样可见）
    assert answer == "直出结论[1]"
    assert partial_holder["text"] == answer
    # 落库净化正文非空：events_log 共享引用，serialize_process 提取末次待定区
    _, purified = serialize_process(partial_holder["events_log"])
    assert purified == "直出结论[1]"


@pytest.mark.asyncio
async def test_direct_round_fills_capture_and_purified_answer():
    """直出轮经等价生产链（_convert_event + manager.add_event + capture + serialize）落库。"""
    capture = _StreamCapture()
    full_answer = ""
    mgr = StreamingRunManager()

    async for item in _fake_graph().astream_events():
        for event in _convert_event(item, capture):
            _record_event(capture, event)
            mgr.add_event("s1", event.type, event.payload_for_buffer())
            if isinstance(event, SSETokenEvent):
                full_answer += event.token

    # capture 被填充供落库
    assert capture.final_answer == "直出结论[1]"
    assert capture.final_contexts == [
        {"content": "2024 年营收 1000 亿", "source": "annual.pdf"}
    ]
    # full_answer 累积自 SSETokenEvent
    assert full_answer == "直出结论[1]"
    # serialize_process 的 purified_answer 非空（末次待定区 = 直出正文）
    _, purified = serialize_process(capture.events_log)
    assert purified == "直出结论[1]"
