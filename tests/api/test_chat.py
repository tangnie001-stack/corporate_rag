"""Tests for SSE streaming chat endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from tests.api.mock_data import make_chunk

from src.main import app

client = TestClient(app)


@patch("src.api.chat._get_service")
def test_chat_stream_returns_sse(mock_get_service):
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = mock_get_service.return_value
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

    response = client.get(
        "/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
