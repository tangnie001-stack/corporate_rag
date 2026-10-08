"""ChatManager 摘要读写的单测（内存降级路径，不连真实 Redis）。"""

import pytest

from src.chat.manager import ChatManager


@pytest.fixture
def manager(monkeypatch):
    """构造一个强制走内存降级路径的 ChatManager。"""
    mgr = ChatManager.__new__(ChatManager)
    mgr.ttl = 60
    mgr._redis_url = "redis://localhost:6379/0"
    mgr._redis = None
    mgr._in_memory = True
    mgr._memory_store = {}
    mgr._memory_summaries = {}
    mgr._persistence = None
    return mgr


@pytest.mark.asyncio
async def test_summary_roundtrip_in_memory(manager):
    """无 Redis 时摘要仍可读写（内存降级分支）。"""
    await manager.save_summary_async("s1", "摘要正文", 6)
    assert await manager.get_summary_async("s1") == ("摘要正文", 6)


@pytest.mark.asyncio
async def test_get_summary_missing_returns_empty(manager):
    """不存在时返回空串与 0。"""
    assert await manager.get_summary_async("nope") == ("", 0)


@pytest.mark.asyncio
async def test_clear_history_also_clears_summary(manager):
    """清空历史时摘要一并清除（同一会话数据同生命周期）。"""
    await manager.save_summary_async("s1", "摘要正文", 6)
    await manager.clear_history_async("s1")
    assert await manager.get_summary_async("s1") == ("", 0)


def test_redis_property_exposes_client(manager):
    """公开的 redis 访问口：内存降级时为 None（供摘要锁判定是否可用）。"""
    assert manager.redis is None
