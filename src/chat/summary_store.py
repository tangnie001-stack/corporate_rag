"""跨轮历史摘要的存储层（mixin）。

以 mixin 形式为宿主类提供摘要的读写与清理，Redis 优先、内存降级。
与对话历史存储分离，使宿主文件不因摘要逻辑越过 400 行硬红线。

**宿主契约**：宿主类必须提供
  - `_ensure_redis_async() -> None`：异步校验/恢复 Redis 连接（内存期间可回切）
  - `_redis`：底层异步 Redis 客户端，内存降级时为 None
  - `_in_memory`：是否处于内存降级模式
  - `ttl`：摘要键过期时间（秒）
并在自身 `__init__` 中调用 `_init_summary_store()` 初始化内存摘要字典。
"""

import json
from typing import TYPE_CHECKING

from src.core import logging as core_logging
from src.core.log_events import Event

if TYPE_CHECKING:
    import redis.asyncio as redis_async


class SummaryStoreMixin:
    """跨轮历史摘要存储（Redis 优先，内存降级）。

    Redis 数据结构：
      Key:  "chat_summary:{session_id}"
      Type: String（JSON：{"text": 摘要正文, "covered": 已覆盖条数}）
      TTL:  与对话历史一致（宿主的 ttl）

    内存模式下摘要仅在当前进程存活，重启后丢失，适合本地开发调试。
    """

    # ── 宿主契约：由宿主类提供，此处仅作类型声明与文档说明 ──
    ttl: int
    _redis: "redis_async.Redis | None"
    _in_memory: bool
    _memory_summaries: dict[str, tuple[str, int]]

    async def _ensure_redis_async(self) -> None:
        """由宿主类实现：异步校验/恢复 Redis 连接。"""
        raise NotImplementedError

    def _init_summary_store(self) -> None:
        """初始化内存降级时的摘要存储（宿主 `__init__` 中调用）。"""
        # session_id -> (摘要正文, 覆盖条数)
        self._memory_summaries = {}

    @property
    def redis(self):
        """返回底层 Redis 客户端（内存降级时为 None）。

        供摘要锁等外部守卫取用；避免其它层直接摸 `_redis` 私有属性。
        """
        return self._redis

    def _summary_key(self, session_id: str) -> str:
        """生成摘要 Redis key，格式为 "chat_summary:{session_id}"。"""
        return f"chat_summary:{session_id}"

    async def save_summary_async(
        self, session_id: str, text: str, covered: int
    ) -> bool:
        """保存跨轮历史摘要（正文 + 已覆盖的消息条数）。

        写入成功时续期 TTL，使摘要与对话历史同生命周期。

        Args:
            session_id: 会话 ID
            text: 摘要正文
            covered: 已覆盖到的消息条数

        Returns:
            是否成功落盘；失败时已记 `SUMMARY_FALLBACK(reason="store_failed")` 并
            返回 False，调用方据此避免误记表示"成功落库"的 `summary done`。
        """
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_summaries[session_id] = (text, covered)
            return True
        assert self._redis is not None
        try:
            key = self._summary_key(session_id)
            payload = json.dumps({"text": text, "covered": covered}, ensure_ascii=False)
            await self._redis.set(key, payload)
            await self._redis.expire(key, self.ttl)
            return True
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(
                Event.SUMMARY_FALLBACK, reason="store_failed", err=str(e)
            )
            return False

    async def get_summary_async(self, session_id: str) -> tuple[str, int]:
        """读取跨轮历史摘要。

        Returns:
            (摘要正文, 覆盖条数)；不存在或读取/解析失败时返回 ("", 0)
        """
        await self._ensure_redis_async()
        if self._in_memory:
            return self._memory_summaries.get(session_id, ("", 0))
        assert self._redis is not None
        try:
            raw = await self._redis.get(self._summary_key(session_id))
            if not raw:
                return "", 0
            data = json.loads(raw)
            return str(data.get("text", "")), int(data.get("covered", 0))
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(
                Event.SUMMARY_FALLBACK, reason="read_failed", err=str(e)
            )
            return "", 0

    async def clear_summary_async(self, session_id: str) -> None:
        """清除跨轮历史摘要（会话删除/清空时调用）。"""
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_summaries.pop(session_id, None)
            return
        assert self._redis is not None
        try:
            await self._redis.delete(self._summary_key(session_id))
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(
                Event.SUMMARY_FALLBACK, reason="clear_failed", err=str(e)
            )
