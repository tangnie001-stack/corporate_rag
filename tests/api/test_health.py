"""Tests for health check and config endpoints."""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health_returns_200():
    """GET /api/health returns 200 with status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_config_returns_max_size(mock_app_service):
    """POST /api/config 返回上传大小限制。"""
    mock_app_service.settings.MAX_FILE_SIZE = 10485760

    response = client.post("/api/config")

    assert response.status_code == 200
    assert response.json()["data"]["max_upload_size"] == 10485760
