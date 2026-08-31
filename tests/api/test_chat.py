"""Tests for SSE streaming chat endpoint."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_app_service
from src.main import app

client = TestClient(app)


def test_chat_stream_returns_sse():
    """POST /api/chat/stream returns SSE event stream."""
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    async def _sub():
        yield SSETokenEvent("净利润")
        yield SSEDoneEvent(trace_id="")

    async def fake_stream_chat(kb_id, session_id, query, deep_thinking=False):
        return (
            _sub(),
            {
                "history": [],
                "ctx": None,
                "graph": None,
                "session_id": session_id,
                "kb_id": kb_id,
                "query": query,
                "deep_thinking": deep_thinking,
            },
        )

    mock_svc = AsyncMock()
    mock_svc.agent_service.stream_chat = fake_stream_chat
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        with patch("src.api.chat._run_with_finalize", new=AsyncMock()):
            response = client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "净利润多少"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "净利润" in response.text
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_user_id():
    """chat_stream 应从请求上下文提取 user_id 传给流生成器。

    回归场景：会话持久化时未传 user_id，导致会话列表按用户过滤后为空。
    """
    mock_svc = AsyncMock()
    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        with patch("src.api.chat._stream_rag_response") as mock_gen:

            async def _ag():
                yield b""

            mock_gen.return_value = _ag()
            client.post(
                "/api/chat/stream",
                json={"session_id": "s1", "kb_id": "kb-1", "query": "hi"},
                cookies={"user_id": "user-123"},
            )
            # 第 5 个位置参数应为 user_id（auth middleware 从 user_id cookie 提取）
            args = mock_gen.call_args.args
            assert args[4] == "user-123"
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_deep_thinking():
    """deep_thinking 请求体字段应透传至 agent_service.stream_chat。

    回归场景：前端「深度思考」开关打开时，请求应携带 deep_thinking=true，
    最终传递给 agent LLM 的 enable_thinking 参数；若断链则开关无效。
    """
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    captured = {}

    async def _sub():
        yield SSETokenEvent("ok")
        yield SSEDoneEvent(trace_id="")

    async def fake_stream_chat(kb_id, session_id, query, deep_thinking=False):
        captured["deep_thinking"] = deep_thinking
        return (
            _sub(),
            {
                "history": [],
                "ctx": None,
                "graph": None,
                "session_id": session_id,
                "kb_id": kb_id,
                "query": query,
                "deep_thinking": deep_thinking,
            },
        )

    mock_svc = AsyncMock()
    mock_svc.agent_service.stream_chat = fake_stream_chat
    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        # 后台任务（_run_with_finalize）mock 掉，本用例只验证 deep_thinking 透传
        with patch("src.api.chat._run_with_finalize", new=AsyncMock()):
            response = client.post(
                "/api/chat/stream",
                json={
                    "session_id": "s1",
                    "kb_id": "kb-1",
                    "query": "hi",
                    "deep_thinking": True,
                },
            )
        assert response.status_code == 200
        assert captured["deep_thinking"] is True
    finally:
        app.dependency_overrides.pop(get_app_service, None)


@pytest.mark.asyncio
async def test_normal_answer_persisted():
    """正常回答（有 token 无澄清）时后台任务将完整回答落库为 complete。"""
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext

    statuses = []
    svc = MagicMock()
    svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: statuses.append(a[4])
    )
    partial_holder = {"text": ""}

    async def answer_builder():
        partial_holder["text"] = "净利润100亿"
        return "净利润100亿"

    await _run_with_finalize(
        svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        StreamingRunManager(),
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )
    assert statuses == ["complete"]
    svc.save_assistant_async.assert_awaited_once()
