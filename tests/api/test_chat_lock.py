"""per-session 并发锁（_acquire_session_lock / _release_session_lock）单元测试。

直接 mock redis（AsyncMock），不发真实网络。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.chat import _acquire_session_lock, _release_session_lock
from src.config.const import SESSION_LOCK_TTL


@pytest.mark.asyncio
async def test_acquire_success():
    """SETNX 返回 True → 获取成功。"""
    redis = AsyncMock()
    redis.set.return_value = True

    acquired = await _acquire_session_lock(redis, "s1")

    assert acquired is True
    redis.set.assert_awaited_once_with(
        "chat_lock:s1", "1", nx=True, ex=SESSION_LOCK_TTL
    )


@pytest.mark.asyncio
async def test_acquire_conflict():
    """SETNX 返回 False（已有锁）→ 获取失败（端点层应返回 409）。"""
    redis = AsyncMock()
    redis.set.return_value = False

    acquired = await _acquire_session_lock(redis, "s1")

    assert acquired is False


@pytest.mark.asyncio
async def test_release():
    """释放锁 → 删除对应 Redis key。"""
    redis = AsyncMock()

    await _release_session_lock(redis, "s1")

    redis.delete.assert_awaited_once_with("chat_lock:s1")


def test_stream_conflict_returns_409():
    """锁冲突（SETNX 返回 False）→ /chat/stream 端点返回 409。"""
    from src.api.dependencies import get_app_service
    from src.main import app

    mock_svc = MagicMock()
    redis = AsyncMock()
    redis.set.return_value = False
    mock_svc.chat_manager._redis = redis
    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        response = TestClient(app).get(
            "/api/chat/stream?session_id=s1&kb_id=kb-1&query=hi"
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.pop(get_app_service, None)
