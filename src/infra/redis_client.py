"""共享 Redis 客户端工厂 — 提供统一的 Redis 连接创建入口。

所有需要 Redis 连接的模块（middleware、ChatManager 等）都应从此模块获取客户端，
避免各自创建连接实例。
"""

import redis.asyncio as redis_async

from src.config import REDIS_URL


_client: redis_async.Redis | None = None


def get_redis_client() -> redis_async.Redis:
    """获取 Redis 异步客户端单例。

    首次调用时创建连接，后续复用同一实例。
    使用延迟初始化，避免导入阶段产生网络连接。

    Returns:
        redis.asyncio.Redis 客户端实例
    """
    global _client
    if _client is None:
        _client = redis_async.from_url(REDIS_URL, decode_responses=True)
    return _client
