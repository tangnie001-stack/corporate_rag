"""流式生成运行管理 — 单 worker 进程内任务注册表与 abort 信号映射。

后台生成任务由本管理器持有强引用，防止被 GC；同时维护
session_id → abort_signal 映射，供 POST /api/sessions/cancel 触达任务。
"""

import asyncio

from loguru import logger


class StreamingRunManager:
    """进程内任务注册表 + abort 信号映射（单 worker 部署假设）。"""

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._abort_signals: dict[str, asyncio.Event] = {}

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
