"""Tests for SSE streaming chat endpoint."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from tests.api.mock_data import make_chunk

from src.api.dependencies import get_app_service
from src.main import app

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
