"""trace 根：装饰契约与 trace 级字段（D2 / D3）。不发网络（测试期 tracing 已全局关停）。"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest

from src.chat.streaming import StreamingRunManager
from src.infra.llm.request_context import RequestContext
from src.services import agent_service
from src.services.agent_service import _run_generation


async def _fake_astream(*args, **kwargs):
    """最小图事件源：一个 token + 一次 model end。"""
    from tests.services.test_agent_service import (
        _chat_model_end_item,
        _chat_model_stream_item,
    )

    yield _chat_model_stream_item("你好")
    yield _chat_model_end_item("qwen-max")


@pytest.mark.asyncio
async def test_run_generation_records_trace_fields(monkeypatch):
    """根内写入 trace 级 input / session_id，且 input 只含标量。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            captured.setdefault("_obs", []).append(kwargs)

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        # 该 kwarg 由 @observe 包装器在调用前取走，静态签名看不到
        langfuse_observation_id="trace_unit_root",  # type: ignore[reportCallIssue]
    )

    assert captured["session_id"] == "s1"
    assert captured["input"]["query"] == "q"
    assert captured["input"]["kb_id"] == "kb1"
    assert all(
        isinstance(v, (str, bool, int, float, type(None)))
        for v in captured["input"].values()
    )


def test_run_generation_is_observed_without_input_capture():
    """根必须以 capture_input=False 装饰 —— 否则内部对象会被序列化进 trace。"""
    assert '@observe(name="chat_turn", capture_input=False)' in inspect.getsource(
        agent_service
    )


def test_answer_builder_passes_observation_id():
    """调用方必须把当前 trace_id 作为根 observation id 传入。"""
    from src.api import chat

    assert "langfuse_observation_id=" in inspect.getsource(chat._stream_rag_response)
