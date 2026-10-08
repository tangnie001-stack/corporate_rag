"""ChatManager 摘要读写的单测（内存降级 + Redis 分支，均不连真实 Redis）。"""

import json

import pytest

from src.chat.manager import ChatManager
from src.core.log_events import Event


async def _noop_ensure_redis() -> None:
    """占位实现：让实例的连接模式稳定不变，绝不触发真实 Redis 连接。

    `_ensure_redis_async` 在内存模式下会尝试重连并回切 Redis；
    测试需要确定性，故把它整段替换为空操作。
    """
    return


class _FakeRedis:
    """只实现摘要相关命令的假 Redis 客户端，记录 expire 调用。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def set(self, key: str, value: str) -> None:
        """写入键值（模拟无附加参数的基础 SET）。"""
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        """读取键值，缺失时返回 None（与真实客户端一致）。"""
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        """删除键。"""
        self.store.pop(key, None)

    async def expire(self, key: str, ttl: int) -> None:
        """记录 TTL 续期调用。"""
        self.expire_calls.append((key, ttl))


def _new_manager(monkeypatch, in_memory: bool) -> ChatManager:
    """构造一个连接模式已定型的 ChatManager（不触发真实 Redis）。"""
    mgr = ChatManager.__new__(ChatManager)
    mgr.ttl = 60
    mgr._redis_url = "redis://localhost:6379/0"
    mgr._in_memory = in_memory
    if in_memory:
        mgr._redis = None
    else:
        # 测试用假客户端，接口与 redis.asyncio.Redis 的摘要相关命令对齐即可
        mgr._redis = _FakeRedis()  # pyright: ignore[reportAttributeAccessIssue]
    mgr._memory_store = {}
    mgr._init_summary_store()
    mgr._persistence = None
    monkeypatch.setattr(mgr, "_ensure_redis_async", _noop_ensure_redis)
    return mgr


@pytest.fixture
def manager(monkeypatch) -> ChatManager:
    """构造一个强制走内存降级路径的 ChatManager。"""
    return _new_manager(monkeypatch, in_memory=True)


@pytest.fixture
def redis_manager(monkeypatch) -> ChatManager:
    """构造一个走 Redis 分支（假客户端）的 ChatManager。"""
    return _new_manager(monkeypatch, in_memory=False)


# ── 内存降级分支 ──


@pytest.mark.asyncio
async def test_summary_roundtrip_in_memory(manager):
    """无 Redis 时摘要仍可读写（内存降级分支），写回返回 True。"""
    saved = await manager.save_summary_async("s1", "摘要正文", 6)
    assert saved is True
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


# ── Redis 分支（假客户端） ──


@pytest.mark.asyncio
async def test_save_summary_writes_payload_and_ttl(redis_manager):
    """Redis 分支：写入 payload 为 {text, covered}、续期 TTL，并返回 True。"""
    saved = await redis_manager.save_summary_async("s1", "摘要正文", 6)
    assert saved is True
    key = "chat_summary:s1"
    raw = await redis_manager._redis.get(key)
    assert json.loads(raw) == {"text": "摘要正文", "covered": 6}
    assert redis_manager._redis.expire_calls == [(key, 60)]


@pytest.mark.asyncio
async def test_save_summary_returns_false_when_redis_write_fails(
    redis_manager, monkeypatch
):
    """Redis 写异常：不外抛、返回 False，并记 store_failed 降级事件。"""
    events: list[dict] = []

    def fake_log_event(event, **fields):
        events.append({"event": event, **fields})

    monkeypatch.setattr("src.chat.summary_store.core_logging.log_event", fake_log_event)

    async def _boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_manager._redis, "set", _boom)
    saved = await redis_manager.save_summary_async("s1", "摘要正文", 6)
    assert saved is False
    fallbacks = [e for e in events if e["event"] is Event.SUMMARY_FALLBACK]
    assert fallbacks and fallbacks[0]["reason"] == "store_failed"


@pytest.mark.asyncio
async def test_get_summary_reads_back_from_redis(redis_manager):
    """Redis 分支：写入后能按契约读回。"""
    await redis_manager.save_summary_async("s1", "摘要正文", 6)
    assert await redis_manager.get_summary_async("s1") == ("摘要正文", 6)


@pytest.mark.asyncio
async def test_get_summary_missing_returns_empty_in_redis(redis_manager):
    """Redis 分支：键缺失时返回 ("", 0)。"""
    assert await redis_manager.get_summary_async("absent") == ("", 0)


@pytest.mark.asyncio
async def test_clear_summary_deletes_redis_key(redis_manager):
    """Redis 分支：清理后摘要键消失、读回为空。"""
    await redis_manager.save_summary_async("s1", "摘要正文", 6)
    await redis_manager.clear_summary_async("s1")
    assert await redis_manager._redis.get("chat_summary:s1") is None
    assert await redis_manager.get_summary_async("s1") == ("", 0)


@pytest.mark.asyncio
async def test_clear_history_redis_also_clears_summary(redis_manager):
    """Redis 分支：清空历史复用摘要清理逻辑，摘要键一并删除。"""
    await redis_manager.save_summary_async("s1", "摘要正文", 6)
    await redis_manager.clear_history_async("s1")
    assert await redis_manager.get_summary_async("s1") == ("", 0)
    assert redis_manager._redis.store == {}
