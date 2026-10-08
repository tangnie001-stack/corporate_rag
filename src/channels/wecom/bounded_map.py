"""进程内有界 TTL 映射：TTL + 容量双淘汰（design D10/D14）。

两处共用：msgid 去重（`mark_if_new`）与澄清"会话 → 触发者"登记（`put`/`get`）。
裸 dict 会随消息量/会话数无界增长，故所有累积结构都必须走本类。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class BoundedTtlMap:
    """按写入时间做 TTL 淘汰、超容量时淘汰最旧条目的有界映射。"""

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            capacity: 容量上限（超出淘汰最旧）
            ttl_seconds: 条目存活秒数（自写入时刻起算，命中不刷新）
            monotonic: 单调时钟（测试注入以稳定断言过期）
        """
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def mark_if_new(self, key: str, value: str) -> bool:
        """记录并判断是否首次见到该 key。

        Args:
            key: 判重键（如 msgid）
            value: 随条目保存的值（判重场景可传空串）

        Returns:
            True 表示此前未见（已记录）；False 表示窗口内已见过
        """
        now = self._monotonic()
        self._evict_expired(now)
        if key in self._items:
            return False
        self._write(key, value, now)
        return True

    def put(self, key: str, value: str) -> None:
        """写入或覆盖一个条目。

        Args:
            key: 键
            value: 值
        """
        now = self._monotonic()
        self._evict_expired(now)
        self._write(key, value, now)

    def get(self, key: str) -> str | None:
        """读取条目；不存在或已过期返回 None。

        Args:
            key: 键

        Returns:
            值或 None
        """
        now = self._monotonic()
        self._evict_expired(now)
        item = self._items.get(key)
        if item is None:
            return None
        return item[1]

    def __len__(self) -> int:
        """当前记录条数（不触发过期清理；过期项在下一次读写时被淘汰）。"""
        return len(self._items)

    def _write(self, key: str, value: str, now: float) -> None:
        """写入并对容量做淘汰。"""
        self._items[key] = (now, value)
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def _evict_expired(self, now: float) -> None:
        """从最旧端淘汰已过期条目。"""
        while self._items:
            oldest_key = next(iter(self._items))
            written_at = self._items[oldest_key][0]
            if now - written_at <= self._ttl_seconds:
                return
            self._items.popitem(last=False)
