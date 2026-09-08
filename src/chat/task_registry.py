"""会话级任务注册表 — 进程内、key 含 session_id、TTL 惰性清理。

来源：task-board change（在 delegate-hardening-observability core 之上）。
用途：委派与后台执行的可视化状态源；主 agent Task 工具与前端只读看板共用。
约束：单 worker 进程内状态（与 streaming_manager 同假设，见
docs/agents/defensive-patterns.md「流式生成状态必须留在进程内」）；不持久化 DB。

变更推送：create/update/mark_terminal 后经 emit_task_event 写 streaming buffer
（action=created|updated|terminal），由 SSE task 事件推前端；刷新/切会话由
GET /api/sessions/tasks 拉快照补齐（D2b）。
"""

import time
from dataclasses import dataclass, field

from src.config.const import TaskStatus, TaskType

TASK_TTL_SECONDS = 1800  # 条目 TTL（默认 30min），惰性清理（仿事件缓冲 sweep）


@dataclass
class TaskItem:
    """单个任务条目。

    Attributes:
        task_id: 任务 id（plan=工具生成短 uuid；execution=delegate_id）
        title: 标题（LLM 提供或 skill 名）
        type: 条目类型（TaskType.plan|execution）
        status: 展示状态（TaskStatus 值；pending/running/done/failed/timeout/cancelled）
        stage: coarse 阶段（仅 delegate start/end/中断边界更新，不做逐 delta 写入）
        summary: 摘要文本（plan 进展 / execution 活动说明）
        delegate_id: execution 专属，= task_id（关联"分析过程"折叠区）
        dependencies: plan 依赖任务 id 列表
        reason: 中断原因（DelegateStopReason 值；normal 为空串）
        created_at / updated_at: unix 时间戳（updated_at 供 TTL 清理排序）
    """

    task_id: str  # 任务 id（plan=工具生成短 uuid；execution=delegate_id）
    title: str  # 标题（LLM 提供或 skill 名）
    type: str  # 条目类型（TaskType.plan|execution）
    status: str = TaskStatus.PENDING  # 展示状态（done/failed/timeout/cancelled 为终态）
    stage: str = ""  # coarse 阶段（delegate start/end/中断边界更新）
    summary: str = ""  # 摘要文本（plan 进展 / execution 活动说明）
    delegate_id: str = ""  # execution 专属 id（= task_id，关联"分析过程"折叠区）
    dependencies: list[str] = field(default_factory=list)  # plan 依赖任务 id 列表
    reason: str = ""  # 中断原因（DelegateStopReason 值；normal 为空串）
    created_at: float = field(default_factory=time.time)  # 创建时间（unix 时间戳）
    updated_at: float = field(default_factory=time.time)  # unix 时间戳（TTL 清理排序）

    def to_dict(self) -> dict:
        """返回 JSON 可序列化快照（SSE task 事件 payload 的 task 字段）。"""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "stage": self.stage,
            "summary": self.summary,
            "delegate_id": self.delegate_id,
            "dependencies": list(self.dependencies),
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def touch(self) -> None:
        """刷新 updated_at（更新/终态时调用）。"""
        self.updated_at = time.time()


_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.TIMEOUT,
        TaskStatus.CANCELLED,
    }
)


def emit_task_event(session_id: str, action: str, item: TaskItem) -> None:
    """注册表变更 → SSE task 事件（写入该 session 事件缓冲）。

    Args:
        session_id: 会话 id
        action: created | updated | terminal（terminal 仅终态变更）
        item: 变更后的任务条目
    """
    from src.chat.streaming import streaming_manager

    streaming_manager.add_event(
        session_id,
        "task",
        {"action": action, "task": item.to_dict()},
    )


class SessionTaskRegistry:
    """会话级任务注册表（session_id → {task_id: TaskItem}）。

    变更推送通过可注入的 on_change 回调（默认 emit_task_event 写 SSE buffer）；
    测试可传 on_change=None 隔离真实 streaming_manager，避免跨文件缓冲污染。
    """

    def __init__(
        self,
        ttl_seconds: float = TASK_TTL_SECONDS,
        on_change=emit_task_event,
    ) -> None:
        self._tasks: dict[str, dict[str, TaskItem]] = {}
        self.ttl_seconds = ttl_seconds
        self._on_change = on_change

    def _notify(self, session_id: str, action: str, item: TaskItem) -> None:
        """若注入过 on_change 则回调（None = 静默，供测试/离线场景）。"""
        if self._on_change is not None:
            self._on_change(session_id, action, item)

    def _bucket(self, session_id: str) -> dict[str, TaskItem]:
        return self._tasks.setdefault(session_id, {})

    def create_task(
        self,
        session_id: str,
        type_: str,
        title: str,
        *,
        delegate_id: str = "",
        status: str = TaskStatus.PENDING,
        stage: str = "",
        summary: str = "",
    ) -> TaskItem:
        """创建任务并推送 SSE task created 事件。

        Args:
            session_id: 会话 id（条目的命名空间 key）
            type_: TaskType.plan|execution
            title: 标题
            delegate_id: execution 专属任务 id（= delegate_id）
            status: 初始展示状态
            stage: coarse 阶段
            summary: 摘要文本

        Returns:
            新条目（task_id 由内部生成：execution 用 delegate_id，plan 用短 uuid）
        """
        self.sweep_expired()
        if type_ == TaskType.EXECUTION:
            task_id = delegate_id
        else:
            import uuid

            task_id = uuid.uuid4().hex[:8]
        item = TaskItem(
            task_id=task_id,
            title=title,
            type=type_,
            status=status,
            stage=stage,
            summary=summary,
            delegate_id=delegate_id,
        )
        self._bucket(session_id)[task_id] = item
        self._notify(session_id, "created", item)
        return item

    def get_task(self, session_id: str, task_id: str) -> TaskItem | None:
        """按 (session, task_id) 取条目。"""
        return self._bucket(session_id).get(task_id)

    def update_task(self, session_id: str, task_id: str, **fields) -> TaskItem | None:
        """更新条目字段并推送 SSE task 事件；缺失条目返回 None。

        事件 action 按新状态判定：进入终态集合（done/failed/timeout/cancelled）
        发 terminal，否则发 updated——保证"cancelled 也是终态"（D4 语义）。
        """
        item = self.get_task(session_id, task_id)
        if item is None:
            return None
        was_terminal = item.status in _TERMINAL_STATUSES
        for key, value in fields.items():
            if key in ("task_id", "created_at"):
                continue
            if value is not None:
                setattr(item, key, value)
        item.touch()
        is_terminal = item.status in _TERMINAL_STATUSES
        if is_terminal and not was_terminal:
            self._notify(session_id, "terminal", item)
        else:
            self._notify(session_id, "updated", item)
        return item

    def list_session(self, session_id: str) -> list[TaskItem]:
        """返回该会话全部条目（按 updated_at 降序，快照/看板渲染用）。"""
        items = list(self._bucket(session_id).values())
        items.sort(key=lambda it: it.updated_at, reverse=True)
        return items

    def list_tasks_for_delegate(
        self, session_id: str, delegate_id: str
    ) -> list[TaskItem]:
        """按 delegate_id 查 execution 条目（过程区关联 / 终态定位）。"""
        return [
            it
            for it in self._bucket(session_id).values()
            if it.delegate_id == delegate_id
        ]

    def mark_terminal(
        self, session_id: str, delegate_id: str, status: str, reason: str = ""
    ) -> TaskItem | None:
        """把指定 delegate 的 execution 条目置为终态并推送 task terminal 事件。

        仅当条目仍在 running/pending 时更新（stop 已置 cancelled 则跳过覆盖）。

        Args:
            session_id: 会话 id
            delegate_id: delegate 唯一 id（= execution task_id）
            status: 终态展示状态（TaskStatus.DONE/FAILED/TIMEOUT/CANCELLED）
            reason: DelegateStopReason 值（normal 时传 ""）

        Returns:
            更新的条目；无匹配或已终态返回 None
        """
        for item in self.list_tasks_for_delegate(session_id, delegate_id):
            if item.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
                item.status = status
                item.reason = reason
                item.touch()
                self._notify(session_id, "terminal", item)
                return item
        return None

    def sweep_expired(self) -> None:
        """惰性清理：updated_at 超 TTL 的条目所属会话（会话内无活跃条目则整桶删除）。"""
        now = time.time()
        expired_sessions = []
        for session_id, bucket in self._tasks.items():
            alive = {
                k: v
                for k, v in bucket.items()
                if now - v.updated_at <= self.ttl_seconds
            }
            if alive:
                if len(alive) != len(bucket):
                    self._tasks[session_id] = alive
            else:
                expired_sessions.append(session_id)
        for sid in expired_sessions:
            self._tasks.pop(sid, None)


# 模块级共享注册表（与 streaming_manager 同生命周期假设；on_change 默认 emit_task_event）
task_registry = SessionTaskRegistry()
