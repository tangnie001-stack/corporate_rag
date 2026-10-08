"""会话锁助手：key 格式与 SETNX 语义不得改变（线上锁兼容）。"""

import pytest

from src.services.chat_lock import acquire_session_lock, release_session_lock


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None)


@pytest.mark.asyncio
async def test_acquire_conflict_release_cycle():
    redis = _FakeRedis()
    assert await acquire_session_lock(redis, "s1") is True
    assert await acquire_session_lock(redis, "s1") is False
    # key 格式必须与既有线上锁一致
    assert "chat_lock:s1" in redis.store
    await release_session_lock(redis, "s1")
    assert await acquire_session_lock(redis, "s1") is True
