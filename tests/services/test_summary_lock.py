"""摘要锁的单测（不连真实 Redis，用假客户端）。"""

import pytest

from src.services.summary_lock import (
    SUMMARY_LOCK_PREFIX,
    acquire_summary_lock,
    release_summary_lock,
)


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_acquire_uses_independent_key_with_ttl():
    """键独立于 chat_lock，且 SETNX 必须带 TTL。"""
    redis = _FakeRedis()
    assert await acquire_summary_lock(redis, "s1") is True
    key, _, nx, ex = redis.set_calls[0]
    assert key == f"{SUMMARY_LOCK_PREFIX}s1"
    assert key != "chat_lock:s1"
    assert nx is True
    assert ex is not None and ex > 0


@pytest.mark.asyncio
async def test_acquire_fails_when_held():
    """已持锁时再次获取失败（best-effort 跳过的前提）。"""
    redis = _FakeRedis()
    await acquire_summary_lock(redis, "s1")
    assert await acquire_summary_lock(redis, "s1") is False


@pytest.mark.asyncio
async def test_release_deletes_key():
    """释放后键消失。"""
    redis = _FakeRedis()
    await acquire_summary_lock(redis, "s1")
    await release_summary_lock(redis, "s1")
    assert redis.store == {}
