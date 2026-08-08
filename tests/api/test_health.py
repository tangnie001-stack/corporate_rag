"""Tests for health check and config endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health_returns_200():
    """GET /api/health returns 200 with status ok."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


def test_app_config_returns_max_size(mock_app_service):
    """POST /api/config 返回上传大小限制。"""
    mock_app_service.settings.MAX_FILE_SIZE = 10485760

    response = client.post("/api/config")

    assert response.status_code == 200
    assert response.json()["data"]["max_upload_size"] == 10485760


@pytest.mark.asyncio
async def test_lifespan_warms_up_chromadb():
    """lifespan 启动时应调用 _warmup_chromadb。"""
    from src.main import lifespan

    with patch("src.main._warmup_chromadb") as mock_warmup:
        async with lifespan(app):
            mock_warmup.assert_called_once()


def test_warmup_chromadb_survives_failure():
    """_warmup_chromadb 内部异常应被捕获，不向外传播。"""
    from src.main import _warmup_chromadb

    with patch(
        "src.infra.db.vector_store.VectorStore",
        side_effect=RuntimeError("chroma down"),
    ):
        _warmup_chromadb()  # 不抛异常即通过
