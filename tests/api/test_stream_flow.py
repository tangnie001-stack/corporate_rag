"""/api/chat/stream 流程改造测试 — user 同步落库（保持 GET）。

验证：流式请求开始即同步写 user（MySQL），端点方法仍为 GET，
不破坏前端 EventSource 依赖；并发防护先查进程内注册表再取 Redis 锁。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from src.api.chat import chat_stream
from src.chat.streaming import streaming_manager


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


@pytest.mark.asyncio
async def test_chat_stream_conflict_returns_409(mock_app_service):
    """注册表已有活跃任务 → chat_stream 先查注册表返回 409（不依赖 Redis 锁）。

    在模块级 streaming_manager 单例上登记一个未完成的任务，端点必须
    在取 Redis 锁之前被 is_running 拦下。用唯一 session_id 并在
    teardown 注销/取消，避免污染其它测试。
    """
    session_id = "s-conflict-409"
    task = asyncio.create_task(asyncio.sleep(10))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        assert streaming_manager.is_running(session_id) is True
        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                request=Request({"type": "http", "method": "GET"}),
                session_id=session_id,
                kb_id="kb1",
                query="营收多少",
                deep_thinking=False,
                svc=mock_app_service,
            )
        assert exc_info.value.status_code == 409
    finally:
        streaming_manager.unregister(session_id)
        task.cancel()
