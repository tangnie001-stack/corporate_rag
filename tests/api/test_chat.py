"""Tests for SSE streaming chat endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


@patch("src.api.routes.chat._get_service")
def test_chat_stream_returns_sse(mock_get_service):
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = mock_get_service.return_value
    mock_chain = mock_svc.rag_chain

    # Create a generator that yields tokens
    def token_gen():
        yield "净利润"
        yield "为"
        yield "100亿"
        yield "元"

    mock_chain.chat_with_citations.return_value = (token_gen(), [])

    response = client.get("/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
