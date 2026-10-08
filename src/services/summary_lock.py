"""跨轮历史摘要的并发守卫（Redis SETNX，独立于会话轮次锁）。

**不复用** `chat_lock:{sid}`：轮次锁承载"同一会话同时只跑一轮"的语义、
且在回合收尾的 finally 里才释放；摘要生成发生在收尾之后，两者语义与
生命周期都不同，复用会互相阻塞。

best-effort：拿不到即跳过本次摘要（不等待、不排队）。锁带 TTL，
并且前缀纳入启动期残留锁清理——否则进程被杀会留下永久锁，使该会话
此后永远无法再生成摘要。
"""

from src.config.const import SUMMARY_LOCK_TTL

SUMMARY_LOCK_PREFIX = "chat_summary_lock:"


def _lock_key(session_id: str) -> str:
    """构造摘要锁 key。"""
    return f"{SUMMARY_LOCK_PREFIX}{session_id}"


async def acquire_summary_lock(redis, session_id: str) -> bool:
    """SETNX 获取摘要锁（带 TTL），返回是否获取成功。"""
    return bool(
        await redis.set(_lock_key(session_id), "1", nx=True, ex=SUMMARY_LOCK_TTL)
    )


async def release_summary_lock(redis, session_id: str) -> None:
    """释放摘要锁（删除对应 Redis key）。"""
    await redis.delete(_lock_key(session_id))
