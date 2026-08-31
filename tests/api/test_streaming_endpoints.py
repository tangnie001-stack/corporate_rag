"""流式 SSE 恢复端点测试 — GET /api/sessions/events（resume）。

验证：刷新页面后通过 after_seq 回放缓冲中未消费事件（token / done），
并沿用其它 session 端点的所有权检查（404）。
缓冲直接操作模块级 streaming_manager 单例，用唯一 session_id 并在
finally 清理，避免污染其它测试。
"""

from unittest.mock import AsyncMock

from src.chat.streaming import streaming_manager
from tests.api.mock_data import make_message, make_session


def test_resume_endpoint_replays(auth_client, mock_app_service):
    """GET /api/sessions/events 回放缓冲 seq>after_seq 的事件（token + done）。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    streaming_manager.clear_buffer("s1")
    streaming_manager.add_event("s1", "token", {"token": "a"})
    streaming_manager.add_event("s1", "done", {"trace_id": "t"})
    try:
        resp = auth_client.get(
            "/api/sessions/events", params={"session_id": "s1", "after_seq": 0}
        )
        assert resp.status_code == 200
        body = resp.text
        assert "token" in body and "done" in body
    finally:
        streaming_manager.clear_buffer("s1")


def test_resume_endpoint_session_not_found(auth_client, mock_app_service):
    """GET /api/sessions/events session 不存在或无权访问返回 404。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=None)

    resp = auth_client.get(
        "/api/sessions/events", params={"session_id": "missing", "after_seq": 0}
    )

    assert resp.status_code == 404


def test_task_status_three_states(auth_client, mock_app_service):
    """GET /api/sessions/task-status 三态：generating / completed / idle。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_app_service.get_messages = AsyncMock(return_value=[make_message("user", "q")])
    streaming_manager.clear_buffer("s1")
    try:
        # generating：缓冲存在且无终态
        streaming_manager.add_event("s1", "token", {"token": "a"})
        r1 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
        assert r1.json()["data"]["status"] == "generating"
        assert r1.json()["data"]["buffer_seq"] == 1

        # completed：缓冲有终态
        streaming_manager.add_event("s1", "done", {"trace_id": "t"})
        r2 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
        assert r2.json()["data"]["status"] == "completed"

        # idle：无缓冲且 MySQL 无 assistant 消息
        streaming_manager.clear_buffer("s1")
        r3 = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
        assert r3.json()["data"]["status"] == "idle"
    finally:
        streaming_manager.clear_buffer("s1")


def test_task_status_completed_via_assistant(auth_client, mock_app_service):
    """无缓冲但 MySQL 存在 assistant 消息 → completed。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_app_service.get_messages = AsyncMock(
        return_value=[make_message("assistant", "a")]
    )
    streaming_manager.clear_buffer("s1")
    try:
        resp = auth_client.get("/api/sessions/task-status", params={"session_id": "s1"})
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "completed"
    finally:
        streaming_manager.clear_buffer("s1")


def test_task_status_session_not_found(auth_client, mock_app_service):
    """GET /api/sessions/task-status session 不存在或无权访问返回 404。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=None)

    resp = auth_client.get(
        "/api/sessions/task-status", params={"session_id": "missing"}
    )

    assert resp.status_code == 404
