"""Tests for health check and config endpoints."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health_returns_200():
    """GET /api/health returns 200 with status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("src.api.health._get_service")
def test_app_config_returns_max_size(mock_get_service):
    """POST /api/config 返回上传大小限制。"""
    mock_svc = mock_get_service.return_value
    mock_svc.get_max_upload_size = AsyncMock(return_value=10485760)

    response = client.post("/api/config")

    assert response.status_code == 200
    assert response.json()["data"]["max_upload_size"] == 10485760
