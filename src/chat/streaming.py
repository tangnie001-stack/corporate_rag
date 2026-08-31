"""流式生成运行管理 — 单 worker 进程内任务注册表与 abort 信号映射。

后台生成任务由本管理器持有强引用，防止被 GC；同时维护
session_id → abort_signal 映射，供 POST /api/sessions/cancel 触达任务。
"""

import asyncio
from typing import Any

from loguru import logger


class StreamingRunManager:
    """进程内任务注册表 + abort 信号映射 + 事件缓冲（单 worker 部署假设）。"""

    MAX_ITEMS = 2000
    TTL_SECONDS = 300
    _TERMINAL_TYPES = ("done", "error")

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._abort_signals: dict[str, asyncio.Event] = {}
        self._stream_buffers: dict[str, list[tuple[int, str, Any]]] = {}
        self._buffer_done_at: dict[str, float] = {}
        self._seq_counters: dict[str, int] = {}

    def register(
        self, session_id: str, task: asyncio.Task, abort_signal: asyncio.Event
    ) -> None:
        """登记任务与对应 abort 信号；任务完成时由调用方 unregister。"""
        self._session_tasks[session_id] = task
        self._abort_signals[session_id] = abort_signal
        logger.info("streaming task registered: session_id={}", session_id)

    def unregister(self, session_id: str) -> None:
        """注销任务与 abort 信号（任务 done_callback 调用）。"""
        self._session_tasks.pop(session_id, None)
        self._abort_signals.pop(session_id, None)

    def is_running(self, session_id: str) -> bool:
        """该 session 是否已有活跃生成任务。"""
        task = self._session_tasks.get(session_id)
        return task is not None and not task.done()

    def get_abort_signal(self, session_id: str) -> asyncio.Event | None:
        """取该 session 的 abort 信号；无活跃任务返回 None。"""
        return self._abort_signals.get(session_id)

    def set_abort(self, session_id: str) -> None:
        """置位该 session 的 abort 信号（cancel 接口调用）。"""
        signal = self._abort_signals.get(session_id)
        if signal is not None:
            signal.set()

    def get_active_session_ids(self) -> list[str]:
        """返回所有活跃任务的 session_id（测试/清理用）。"""
        return [sid for sid, t in self._session_tasks.items() if not t.done()]

    def clear_buffer(self, session_id: str) -> None:
        """清空该 session 缓冲并重置 seq（新一轮 POST 时调用）。"""
        self._stream_buffers.pop(session_id, None)
        self._buffer_done_at.pop(session_id, None)
        self._seq_counters.pop(session_id, None)

    def add_event(self, session_id: str, etype: str, payload: Any) -> int:
        """追加一条事件，返回其 seq。终态事件登记完成时间供 TTL 清理。"""
        self.sweep_expired()
        seq = self._seq_counters.get(session_id, 0) + 1
        self._seq_counters[session_id] = seq
        buf = self._stream_buffers.setdefault(session_id, [])
        buf.append((seq, etype, payload))
        if len(buf) > self.MAX_ITEMS:
            del buf[:50]
        if etype in self._TERMINAL_TYPES:
            import time as _t

            self._buffer_done_at[session_id] = _t.time()
        return seq

    def get_events_since(
        self, session_id: str, after_seq: int
    ) -> list[tuple[int, str, Any]]:
        """返回缓冲中 seq 大于 after_seq 的事件（回放用）。"""
        return [
            it for it in self._stream_buffers.get(session_id, []) if it[0] > after_seq
        ]

    def buffer_exists(self, session_id: str) -> bool:
        """该 session 是否有缓冲。"""
        return session_id in self._stream_buffers

    def has_terminal(self, session_id: str) -> bool:
        """缓冲是否已含终态事件（done/error）。"""
        return any(
            et in self._TERMINAL_TYPES
            for _, et, _ in self._stream_buffers.get(session_id, [])
        )

    def get_buffer_max_seq(self, session_id: str) -> int:
        """当前缓冲最大 seq（status 接口返回）。"""
        buf = self._stream_buffers.get(session_id, [])
        return buf[-1][0] if buf else 0

    def sweep_expired(self) -> None:
        """惰性清理：已完成且超过 TTL 的会话缓冲。"""
        if not self._buffer_done_at:
            return
        import time as _t

        now = _t.time()
        expired = [
            sid
            for sid, ts in self._buffer_done_at.items()
            if now - ts > self.TTL_SECONDS
        ]
        for sid in expired:
            self.clear_buffer(sid)
