"""per-session 并发锁（Redis SETNX）。

下沉自 src/api/chat.py，供站点与企微通道共用；锁 key 格式保持
`chat_lock:{session_id}` 不变（与线上既有锁兼容），TTL 兜底防流中断后锁不释放。
"""

from src.config.const import SESSION_LOCK_TTL

_LOCK_KEY_PREFIX = "chat_lock:"


def _lock_key(session_id: str) -> str:
    """构造会话锁 key。"""
    return f"{_LOCK_KEY_PREFIX}{session_id}"


async def acquire_session_lock(redis, session_id: str) -> bool:
    """SETNX 获取 per-session 并发锁，返回是否获取成功。

    Args:
        redis: redis.asyncio 客户端（调用方保证非 None）
        session_id: 会话 ID

    Returns:
        bool: 获取成功 True；已有锁（并发冲突）False
    """
    return bool(
        await redis.set(_lock_key(session_id), "1", nx=True, ex=SESSION_LOCK_TTL)
    )


async def release_session_lock(redis, session_id: str) -> None:
    """释放 per-session 并发锁（删除对应 Redis key）。"""
    await redis.delete(_lock_key(session_id))
