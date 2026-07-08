"""Tests for KB CRUD endpoints."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def _setup_auth():
    """为请求设置认证 cookie，绕过中间件的 token 验证。"""
    client.cookies.set("token", "test-token")
    p = patch("src.middleware.auth.UserAuth.get_user_id_from_token_async",
              new_callable=AsyncMock, return_value="test-user-id")
    p.start()
    return p


@patch("src.api.routes.knowledge_base._get_service")
def test_list_kbs(mock_get_service):
    """POST /api/kbs/list 返回知识库列表。"""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        mock_svc.list_knowledge_bases = AsyncMock(return_value=[
            ("kb-1", "年报知识库"),
            ("kb-2", "财报知识库"),
        ])

        response = client.post("/api/kbs/list", json={})

        assert response.status_code == 200
    finally:
        auth_patcher.stop()
        client.cookies.clear()


@patch("src.api.routes.knowledge_base._get_service")
def test_create_kb(mock_get_service):
    """POST /api/kbs creates a new KB."""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        mock_svc.create_knowledge_base = AsyncMock(return_value=("new-kb-uuid", True))

        response = client.post("/api/kbs", json={"name": "测试库", "description": "测试"})

        assert response.status_code == 201
        assert response.json()["data"] == {"id": "new-kb-uuid", "created": True}
    finally:
        auth_patcher.stop()
        client.cookies.clear()


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_exists(mock_get_service):
    """POST /api/kbs/delete 删除已存在的知识库。"""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        mock_svc.delete_knowledge_base = AsyncMock(return_value=(True, "知识库已删除"))

        response = client.post("/api/kbs/delete", json={"kb_id": "kb-1"})

        assert response.status_code == 200
    finally:
        auth_patcher.stop()
        client.cookies.clear()


@patch("src.api.routes.knowledge_base._get_service")
def test_delete_kb_not_found(mock_get_service):
    """POST /api/kbs/delete 不存在的知识库返回 404。"""
    auth_patcher = _setup_auth()
    try:
        mock_svc = mock_get_service.return_value
        mock_svc.delete_knowledge_base = AsyncMock(return_value=(False, "知识库不存在"))

        response = client.post("/api/kbs/delete", json={"kb_id": "kb-missing"})

        assert response.status_code == 404
    finally:
        auth_patcher.stop()
        client.cookies.clear()
