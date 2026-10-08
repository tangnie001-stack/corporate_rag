"""跨轮历史摘要的调度 —— 触发判据、best-effort 抢锁、后台生成。

生成只在**回合正常完成后**发起（调用方负责选对分支）；本模块自身不等待，
失败一律降级为"不采用摘要"，绝不影响用户可见流程。
"""

import asyncio
import logging

from src.agents.graph.history_window import split_history_window
from src.chat.history_summary import summarize_history
from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_SUMMARY_TRIGGER_TOKENS,
    HISTORY_TOKEN_BUDGET,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.services import summary_lock

logger = logging.getLogger(__name__)

# 后台摘要任务的强引用容器：长 LLM 调用期间防被 GC。
_RUNNING: set[asyncio.Task] = set()


class _LockAdapter:
    """把 `summary_lock` 的模块级函数适配为可注入的锁对象（acquire/release）。

    生产默认走本适配器；测试注入同名方法的替身（`_FakeLock`）以替换实现。
    """

    async def acquire(self, redis, session_id: str) -> bool:
        """委托 `summary_lock.acquire_summary_lock`（SETNX + TTL）。"""
        return await summary_lock.acquire_summary_lock(redis, session_id)

    async def release(self, redis, session_id: str) -> None:
        """委托 `summary_lock.release_summary_lock`（删除 key）。"""
        await summary_lock.release_summary_lock(redis, session_id)


class SummaryScheduler:
    """一次摘要生成所需的最小依赖（便于测试替换）。"""

    def __init__(self, manager, redis, lock=None) -> None:
        """初始化。

        Args:
            manager: ChatManager（提供摘要读写）
            redis: redis.asyncio 客户端；None 时跳过（无 Redis 环境不生成摘要）
            lock: 锁替身；默认用适配 `src.services.summary_lock` 的 `_LockAdapter`
        """
        self._manager = manager
        self._redis = redis
        self._lock = lock if lock is not None else _LockAdapter()

    def should_schedule(self, history: list[ChatMessage]) -> bool:
        """判断是否应生成摘要：**将被丢弃段**非空且超触阈值。

        判据落在被丢弃段而非全量历史——否则"保留尾部覆盖全量"会被误判为触阈。
        """
        _, discarded = split_history_window(
            history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
        )
        if not discarded:
            return False
        used = 0
        for message in discarded:
            used += count_tokens(message.content)
        return used > HISTORY_SUMMARY_TRIGGER_TOKENS

    async def generate(self, session_id: str, history: list[ChatMessage]) -> None:
        """抢锁 → 生成 → 写回 → 释放锁；任何失败都降级，不外抛。"""
        try:
            acquired = await self._lock.acquire(self._redis, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] summary lock acquire failed err=%s", exc)
            return
        if not acquired:
            return
        try:
            _, discarded = split_history_window(
                history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
            )
            if not discarded:
                return
            previous_text, _ = await self._manager.get_summary_async(session_id)
            result = await summarize_history(previous_text, discarded)
            if result.degraded:
                # 核心模块只返回原因、不记日志；由本层统一落 [session] 降级事件。
                core_logging.log_event(
                    Event.SUMMARY_FALLBACK, reason=result.reason, err=""
                )
                return
            saved = await self._manager.save_summary_async(
                session_id, result.text, result.covered
            )
            if not saved:
                # 写回失败：store 层已记 SUMMARY_FALLBACK(store_failed)，
                # 本层不重复记 fallback、也不记「成功落库」的 summary done。
                return
            core_logging.log_event(
                Event.SUMMARY_DONE,
                covered=result.covered,
                tokens=count_tokens(result.text),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] summary generate failed err=%s", exc)
        finally:
            try:
                await self._lock.release(self._redis, session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[session] summary lock release failed err=%s", exc)


def maybe_schedule_summary(
    manager, redis, session_id: str, history: list[ChatMessage]
) -> None:
    """判定触阈后 spawn 后台摘要任务（**不等待**）。

    调用方 SHALL 只在回合**正常完成**且 assistant 已写入历史之后调用。
    history 为发起时冻结的快照，后台任务内不再读取会话历史。

    Args:
        manager: ChatManager
        redis: redis.asyncio 客户端；None 时不生成
        session_id: 会话 ID
        history: 冻结的历史快照
    """
    if redis is None:
        return
    scheduler = SummaryScheduler(manager, redis)
    if not scheduler.should_schedule(history):
        return
    frozen = list(history)
    task = asyncio.create_task(scheduler.generate(session_id, frozen))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
