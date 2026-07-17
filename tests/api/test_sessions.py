"""Sessions 端点测试 — list / messages / delete。"""

from unittest.mock import AsyncMock, MagicMock, patch

from tests.api.mock_data import make_session, make_message


@patch("src.api.sessions._get_service")
def test_list_sessions(mock_get_service, auth_client):
    """POST /api/sessions/list 返回会话列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[
        make_session("s1", "财报问答"),
        make_session("s2", "年报分析"),
    ])

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["title"] == "财报问答"


@patch("src.api.sessions._get_service")
def test_list_sessions_empty(mock_get_service, auth_client):
    """POST /api/sessions/list 无会话返回空列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_sessions = AsyncMock(return_value=[])

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    assert response.json()["data"] == []


@patch("src.api.sessions._get_service")
def test_session_messages(mock_get_service, auth_client):
    """POST /api/sessions/messages 返回消息列表。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_svc.db.get_messages = AsyncMock(return_value=[
        make_message("user", "2024年营收多少"),
        make_message("assistant", "2024年营收为100亿"),
    ])

    response = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"


@patch("src.api.sessions._get_service")
def test_session_messages_not_found(mock_get_service, auth_client):
    """POST /api/sessions/messages session 不存在返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.get_session_by_id = AsyncMock(return_value=None)

    response = auth_client.post("/api/sessions/messages", json={"session_id": "missing"})

    assert response.status_code == 404


@patch("src.api.sessions._get_service")
def test_delete_session(mock_get_service, auth_client):
    """POST /api/sessions/delete 删除成功。"""
    mock_svc = mock_get_service.return_value
    mock_svc.rag_chain.chat_manager.cleanup_session = MagicMock()
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=True)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["success"] is True


@patch("src.api.sessions._get_service")
def test_delete_session_not_found(mock_get_service, auth_client):
    """POST /api/sessions/delete session 不存在返回 404。"""
    mock_svc = mock_get_service.return_value
    mock_svc.db.delete_session_and_messages = AsyncMock(return_value=False)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "missing"})

    assert response.status_code == 404
