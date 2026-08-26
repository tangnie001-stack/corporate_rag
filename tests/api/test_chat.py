"""Tests for SSE streaming chat endpoint."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_app_service
from src.main import app
from tests.api.mock_data import make_chunk

client = TestClient(app)


def test_chat_stream_returns_sse():
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = MagicMock()
    mock_chain = mock_svc.rag_chain

    async def fake_search(query, kb_id):
        return [make_chunk("1", "test", page=1)]

    def fake_stream(query, contexts, history, trace_id=None):
        yield "净利润"
        yield "为"
        yield "100亿"
        yield "元"

    mock_chain.search = fake_search
    mock_chain.rerank = MagicMock(return_value=[])
    mock_chain.stream_answer = fake_stream

    app.dependency_overrides[get_app_service] = lambda: mock_svc

    try:
        response = client.get(
            "/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少"
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
    finally:
        app.dependency_overrides.pop(get_app_service, None)


def test_chat_stream_passes_user_id():
    """chat_stream 应从请求上下文提取 user_id 传给流生成器。

    回归场景：会话持久化时未传 user_id，导致会话列表按用户过滤后为空。
    """
    mock_svc = MagicMock()
    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        with patch("src.api.chat._stream_rag_response") as mock_gen:

            async def _ag():
                yield b""

            mock_gen.return_value = _ag()
            client.get(
                "/api/chat/stream?session_id=s1&kb_id=kb-1&query=hi",
                cookies={"user_id": "user-123"},
            )
            # 第 5 个位置参数应为 user_id（auth middleware 从 user_id cookie 提取）
            args = mock_gen.call_args.args
            assert args[4] == "user-123"
    finally:
        app.dependency_overrides.pop(get_app_service, None)


@pytest.mark.asyncio
async def test_normal_answer_persisted():
    """正常回答（有 token 无澄清）时应持久化对话。"""
    from src.api.chat import _stream_rag_response
    from src.utils.sse import SSEDoneEvent, SSETokenEvent

    svc = MagicMock()
    events = [
        SSETokenEvent("净利润100亿"),
        SSEDoneEvent(),
    ]

    async def _stream(kb_id, session_id, query):
        for e in events:
            yield e

    with patch("src.api.chat._persist_conversation") as mock_persist:
        svc.agent_service.stream_chat = _stream
        async for _ in _stream_rag_response(svc, "", "s1", "净利润多少"):
            pass
    mock_persist.assert_called_once()
