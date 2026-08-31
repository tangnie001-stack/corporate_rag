"""/api/chat/stream 流程改造测试 — user 同步落库（保持 GET）。

验证：流式请求开始即同步写 user（MySQL），端点方法仍为 GET，
不破坏前端 EventSource 依赖。
"""

from unittest.mock import AsyncMock

import pytest


def test_chat_stream_persists_user_before_stream(auth_client, mock_app_service):
    """请求开始即写 user（MySQL 同步），再进入流式生成。"""
    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.agent_service.stream_chat = AsyncMock()
    mock_app_service.agent_service.stream_chat.return_value = iter(())
    mock_app_service.save_user_async = AsyncMock()
    mock_app_service.save_session_async = AsyncMock()

    resp = auth_client.get(
        "/api/chat/stream",
        params={"session_id": "s1", "kb_id": "kb1", "query": "营收多少"},
    )
    assert resp.status_code == 200
    mock_app_service.save_session_async.assert_called_once()
    mock_app_service.save_user_async.assert_called_once_with("s1", "kb1", "营收多少")


@pytest.mark.asyncio
async def test_persist_conversation_writes_only_assistant(
    auth_client, mock_app_service
):
    """流结束后 _persist_conversation 仅写 assistant，不再写 user/session。"""
    from src.api.chat import _persist_conversation

    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.save_assistant_async = AsyncMock()
    mock_app_service.save_user_async = AsyncMock()

    await _persist_conversation(mock_app_service, "s1", "kb1", "完整回答", [], "u1")
    mock_app_service.save_assistant_async.assert_awaited_once()
    mock_app_service.save_user_async.assert_not_called()
