"""Tests for KB CRUD endpoints."""

from unittest.mock import AsyncMock

from tests.api.mock_data import make_kb


def test_list_kbs(mock_app_service, auth_client):
    """POST /api/kbs/list 返回知识库列表。"""
    mock_svc = mock_app_service
    mock_svc.list_knowledge_bases = AsyncMock(
        return_value=[
            make_kb("kb-1", "年报知识库", doc_count=5),
            make_kb("kb-2", "财报知识库", doc_count=3),
        ]
    )

    response = auth_client.post("/api/kbs/list", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["name"] == "年报知识库"


def test_create_kb(mock_app_service, auth_client):
    """POST /api/kbs 创建新知识库。"""
    mock_svc = mock_app_service
    mock_svc.create_knowledge_base = AsyncMock(return_value=("new-kb-uuid", True))

    response = auth_client.post(
        "/api/kbs", json={"name": "测试库", "description": "测试"}
    )

    assert response.status_code == 201
    assert response.json()["data"] == {"id": "new-kb-uuid", "created": True}


def test_create_kb_missing_name(auth_client):
    """POST /api/kbs 缺 name 字段应返回 422。"""
    response = auth_client.post("/api/kbs", json={"description": "缺名称"})
    assert response.status_code == 422


def test_delete_kb_exists(mock_app_service, auth_client):
    """POST /api/kbs/delete 删除已存在的知识库。"""
    mock_svc = mock_app_service
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(True, "知识库已删除"))

    response = auth_client.post("/api/kbs/delete", json={"kb_id": "kb-1"})

    assert response.status_code == 200


def test_delete_kb_not_found(mock_app_service, auth_client):
    """POST /api/kbs/delete 不存在的知识库返回 404。"""
    mock_svc = mock_app_service
    mock_svc.delete_knowledge_base = AsyncMock(return_value=(False, "知识库不存在"))

    response = auth_client.post("/api/kbs/delete", json={"kb_id": "kb-missing"})

    assert response.status_code == 404
