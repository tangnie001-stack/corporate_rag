"""Sessions 端点测试 — list / messages / delete。"""

import asyncio
from unittest.mock import AsyncMock, patch

from src.chat.streaming import streaming_manager
from src.chat.task_registry import SessionTaskRegistry
from src.config.const import TaskStatus, TaskType
from tests.api.mock_data import make_message, make_session


def test_list_sessions(auth_client, mock_app_service):
    """POST /api/sessions/list 返回会话列表。"""
    mock_app_service.get_sessions = AsyncMock(
        return_value=[
            make_session("s1", "财报问答"),
            make_session("s2", "年报分析"),
        ]
    )

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["title"] == "财报问答"


def test_list_sessions_empty(auth_client, mock_app_service):
    """POST /api/sessions/list 无会话返回空列表。"""
    mock_app_service.get_sessions = AsyncMock(return_value=[])

    response = auth_client.post("/api/sessions/list", json={})

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_session_messages(auth_client, mock_app_service):
    """POST /api/sessions/messages 返回消息列表。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_app_service.get_messages = AsyncMock(
        return_value=[
            make_message("user", "2024年营收多少"),
            make_message("assistant", "2024年营收为100亿"),
        ]
    )

    response = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"


def test_session_messages_include_status(auth_client, mock_app_service):
    """POST /api/sessions/messages 返回消息 status 字段。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
    mock_app_service.get_messages = AsyncMock(
        return_value=[
            make_message("user", "q"),
            make_message("assistant", "a", status="interrupted"),
        ]
    )
    resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})
    data = resp.json()["data"]
    assert data[1]["status"] == "interrupted"


class TestMessagesProcessField:
    """messages 接口 process / model_name 字段返回。"""

    def test_process_deserialized_to_dict(self, auth_client, mock_app_service):
        """mock svc.get_messages 返回含 process JSON 串与 model_name 的行。"""
        mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
        mock_app_service.get_messages = AsyncMock(
            return_value=[
                make_message(
                    "assistant",
                    "2024年营收为100亿",
                    process='{"format_version": 1, "events": []}',
                    model_name="qwen3.7-flash",
                )
            ]
        )

        resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["process"] == {"format_version": 1, "events": []}
        assert item["model_name"] == "qwen3.7-flash"

    def test_legacy_null_process(self, auth_client, mock_app_service):
        """process/model_name 为 None 的存量行。"""
        mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
        mock_app_service.get_messages = AsyncMock(
            return_value=[
                make_message(
                    "assistant",
                    "存量回答",
                    process=None,
                    model_name=None,
                )
            ]
        )

        resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["process"] is None
        assert item["model_name"] is None

    def test_process_invalid_json_degrades(self, auth_client, mock_app_service):
        """process 列为非法 JSON 串时降级为 null，不阻断消息返回。"""
        mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
        mock_app_service.get_messages = AsyncMock(
            return_value=[
                make_message(
                    "assistant",
                    "脏数据回答",
                    process="not-json{",
                    model_name="qwen3.7-flash",
                )
            ]
        )

        resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["process"] is None
        assert item["model_name"] == "qwen3.7-flash"

    def test_process_non_dict_json_degrades(self, auth_client, mock_app_service):
        """process 列为合法 JSON 但非 dict（如 '123'）时同样降级为 null。"""
        mock_app_service.get_session_by_id = AsyncMock(return_value=make_session("s1"))
        mock_app_service.get_messages = AsyncMock(
            return_value=[
                make_message(
                    "assistant",
                    "非 dict 回答",
                    process="123",
                    model_name="qwen3.7-flash",
                )
            ]
        )

        resp = auth_client.post("/api/sessions/messages", json={"session_id": "s1"})

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["process"] is None
        assert item["model_name"] == "qwen3.7-flash"


def test_session_messages_not_found(auth_client, mock_app_service):
    """POST /api/sessions/messages session 不存在返回 404。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=None)

    response = auth_client.post(
        "/api/sessions/messages", json={"session_id": "missing"}
    )

    assert response.status_code == 404


def test_delete_session(auth_client, mock_app_service):
    """POST /api/sessions/delete 删除成功。"""
    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    mock_app_service.delete_session_and_messages = AsyncMock(return_value=True)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["success"] is True


def test_delete_session_not_found(auth_client, mock_app_service):
    """POST /api/sessions/delete session 不存在返回 404。"""
    mock_app_service.get_session_by_id = AsyncMock(return_value=None)
    mock_app_service.delete_session_and_messages = AsyncMock(return_value=False)

    response = auth_client.post("/api/sessions/delete", json={"session_id": "missing"})

    assert response.status_code == 404


def test_delete_session_cancels_running_task(auth_client, mock_app_service):
    """POST /api/sessions/delete 清理运行态：置位 abort 信号、清空缓冲。"""
    signal = asyncio.Event()
    streaming_manager._abort_signals["s1"] = signal
    streaming_manager.clear_buffer("s1")
    streaming_manager.add_event("s1", "token", {"token": "a"})

    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    mock_app_service.delete_session_and_messages = AsyncMock(return_value=True)

    try:
        resp = auth_client.post("/api/sessions/delete", json={"session_id": "s1"})
        assert resp.status_code == 200
        assert signal.is_set() is True  # 任务被取消
        assert streaming_manager.buffer_exists("s1") is False  # 缓冲被清
    finally:
        streaming_manager.unregister("s1")
        streaming_manager.clear_buffer("s1")


def test_get_session_tasks_empty(auth_client, mock_app_service):
    """GET /api/sessions/tasks 无任务返回空列表。"""
    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    with patch("src.api.sessions.task_registry", SessionTaskRegistry(on_change=None)):
        resp = auth_client.get("/api/sessions/tasks?session_id=s1")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


def test_get_session_tasks_snapshot(auth_client, mock_app_service):
    """GET /api/sessions/tasks 返回该会话任务快照。"""
    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    fake_reg = SessionTaskRegistry(on_change=None)
    fake_reg.create_task(
        "s1", TaskType.EXECUTION, title="e", delegate_id="d1", status=TaskStatus.RUNNING
    )
    with patch("src.api.sessions.task_registry", fake_reg):
        resp = auth_client.get("/api/sessions/tasks?session_id=s1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1 and data[0]["delegate_id"] == "d1"
