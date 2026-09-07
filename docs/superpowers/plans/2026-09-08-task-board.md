# task-board Implementation Plan（依赖 core 先行）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提供会话级任务注册表 + 主 agent 可用的 Task 工具集（create/get/list/update/output/stop）+ 前端只读"任务/进度"看板，让委派与后台执行进度对用户可见可跟踪，并与回答内"分析过程"折叠区互补。

**Architecture:** 进程内 `SessionTaskRegistry`（key 含 session_id，TTL 30min 惰性清理）挂在 chat 层；`delegate_task` fork 自动登记 `type=execution` 条目（`task_id=delegate_id`，终态按 `DelegateStopReason` 映射）；主 agent 经 Task 工具创建 `type=plan` 条目；注册表变更经 streaming buffer 推 SSE `task` 事件；前端头部 chip + 右侧只读面板渲染，刷新/切换会话经 `GET /api/sessions/tasks` 拉快照初始化。stage 更新粒度 coarse（仅 start/end/中断边界），实时活动摘要由前端从 core 的 delegate 增量事件派生。

**Tech Stack:** Python 3.11 / FastAPI / langchain tools / 原生 JS（无框架）。规则见 CLAUDE.md。

**Spec:** `docs/openspec/changes/task-board/`（proposal.md / design.md / specs/task-board/spec.md / tasks.md）
**前端设计稿:** `docs/design/pages/chat-delegate-progress-2026-09-07.md` §3（前端任务须读）
**依赖:** 必须先在 `delegate-hardening-observability`（core）之上应用——共享 `chat.html` / `sse.py`/`from_payload` / `api_contract.md` / `data-flow.md` 文件链顺序合并；core 的 delegate 事件已携带 delegate_id、中断 reason 统一取 `DelegateStopReason`。

## Global Constraints

- 注册表进程内 + TTL：key 含 session_id；同一会话新 POST 保留既有任务直至 TTL，仅清本轮事件缓冲（`streaming_manager.clear_buffer` 行为不变）。
- `type=plan`（主 agent Task 工具建）与 `type=execution`（delegate 自动登记）互不覆盖；execution 的 `task_id=delegate_id`（同一 id 贯通看板与过程区）。
- 前端**只读**：无任何写入口；Task 工具仅主 agent 工具面暴露，经 `ctx.session_id` 定位。
- 中断/终态 reason 只用 `DelegateStopReason` 词表（normal/idle/total/turn/failed/cancelled）；task 事件 `status` 为展示语义（done/failed/timeout/cancelled），`reason` 存枚举值。
- 事件链一次改完：`sse.py` 序列化/`from_payload`/`SSEEvent` 联合（未知类型 raise）+ `tests/utils/test_sse_roundtrip.py` CASES 同步。
- 质量门禁：`pytest tests/ -v`、`ruff check .`、`pyright src/`（不新增 error）；无 `print()`/TODO。
- 顺序：core → task-board。task-board 改动全部在 core 合入后的代码状态上进行。

---

### Task A: 会话级任务注册表模块 + 常量

**Files:**
- Create: `src/chat/task_registry.py`
- Modify: `src/config/const.py`（追加 TaskType/TaskStatus 常量类）
- Test: `tests/chat/test_task_registry.py`（新建）

**Interfaces:**
- Produces:
  - `const.TaskType`: `PLAN = "plan"` / `EXECUTION = "execution"`
  - `const.TaskStatus`: `PENDING = "pending"` / `RUNNING = "running"` / `DONE = "done"` / `FAILED = "failed"` / `TIMEOUT = "timeout"` / `CANCELLED = "cancelled"`
  - `TaskItem` dataclass：`task_id: str`、`title: str`、`type: str`、`status: str = "pending"`、`stage: str = ""`、`summary: str = ""`、`delegate_id: str = ""`、`dependencies: list[str]`、`reason: str = ""`、`created_at: float`、`updated_at: float`，及 `to_dict()`/`touch()`
  - `SessionTaskRegistry`（模块单例 `task_registry`）：`create_task(session_id, type, title, *, delegate_id="", stage="") -> TaskItem`、`get_task(session_id, task_id) -> TaskItem | None`、`update_task(session_id, task_id, **fields) -> TaskItem | None`、`list_session(session_id) -> list[dict]`、`list_tasks_for_delegate(session_id, delegate_id)`、`mark_terminal(session_id, delegate_id, status, reason) -> TaskItem | None`、`sweep_expired()`
  - 事件推送 helper `emit_task_event(session_id, action, task)`（`action ∈ {created, updated, terminal}`；terminal 仅终态）

- [ ] **Step 1: 写失败测试**

`tests/chat/test_task_registry.py`：

```python
"""测试会话级任务注册表 — 生命周期 / TTL / 跨轮保留 / plan-execution 隔离 / 事件。"""

import time
from unittest.mock import patch

import pytest

from src.chat.task_registry import SessionTaskRegistry, TaskItem
from src.config.const import TaskStatus, TaskType


@pytest.fixture
def reg():
    # on_change=None：注册表方法不写真实 streaming_manager（避免跨文件缓冲污染）
    r = SessionTaskRegistry(on_change=None)
    r._tasks.clear()  # 干净实例（模块单例在测试间保留会串，用构造隔离）
    return r


def test_create_and_get_plan_task(reg):
    t = reg.create_task("s1", TaskType.PLAN, title="查营收")
    assert t.task_id and t.type == "plan" and t.status == "pending"
    got = reg.get_task("s1", t.task_id)
    assert got is not None and got.title == "查营收"
    # 跨会话隔离
    assert reg.get_task("s2", t.task_id) is None


def test_update_task_fields(reg):
    t = reg.create_task("s1", TaskType.PLAN, title="a")
    upd = reg.update_task("s1", t.task_id, status=TaskStatus.DONE, summary="完成")
    assert upd is not None and upd.status == "done" and upd.summary == "完成"
    assert reg.update_task("s1", "missing", status="done") is None


def test_update_to_terminal_emits_terminal_action():
    """update_task 进入终态（cancelled/done）发 action=terminal，非终态发 updated。"""
    emitted = []
    r = SessionTaskRegistry(on_change=lambda sid, action, item: emitted.append((sid, action)))
    t = r.create_task("s1", TaskType.PLAN, title="a")          # created
    r.update_task("s1", t.task_id, status=TaskStatus.RUNNING)   # updated
    assert emitted[-1] == ("s1", "updated")
    r.update_task("s1", t.task_id, status=TaskStatus.CANCELLED)  # 终态 → terminal
    assert emitted[-1] == ("s1", "terminal")


def test_execution_terminal_mapping(reg):
    t = reg.create_task("s1", TaskType.EXECUTION, title="委派 x", delegate_id="d1", status=TaskStatus.RUNNING)
    done = reg.mark_terminal("s1", "d1", TaskStatus.DONE, reason="")
    assert done is not None and done.status == "done"
    t2 = reg.create_task("s1", TaskType.EXECUTION, title="委派 y", delegate_id="d2", status=TaskStatus.RUNNING)
    timed = reg.mark_terminal("s1", "d2", TaskStatus.TIMEOUT, reason="idle")
    assert timed is not None and timed.status == "timeout" and timed.reason == "idle"


def test_plan_and_execution_do_not_override(reg):
    plan = reg.create_task("s1", TaskType.PLAN, title="p")
    exe = reg.create_task("s1", TaskType.EXECUTION, title="e", delegate_id="d1")
    # 两个条目并存；plan 不以 delegate 查、execution 独立
    assert len(reg.list_session("s1")) == 2
    assert reg.list_tasks_for_delegate("s1", "d1")[0].task_id == exe.task_id
    # 更新 plan 不影响 execution
    assert plan.task_id != exe.task_id


def test_ttl_sweep_expired_only(reg):
    t_old = reg.create_task("s1", TaskType.PLAN, title="old")
    t_old.updated_at = time.time() - 3600  # 超 TTL
    t_new = reg.create_task("s1", TaskType.PLAN, title="new")
    reg.sweep_expired()
    assert reg.get_task("s1", t_old.task_id) is None
    assert reg.get_task("s1", t_new.task_id) is not None


def test_new_post_keeps_existing_tasks(reg):
    """同一会话跨轮保留（注册表独立于事件缓冲，clear_buffer 不清表）。"""
    t = reg.create_task("s1", TaskType.PLAN, title="keep")
    assert reg.get_task("s1", t.task_id) is not None  # 语义由 streaming.clear_buffer 不触表保证
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/chat/test_task_registry.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: const 常量类**

`src/config/const.py` 追加（delegate 区之后）：

```python
class TaskType:
    """任务看板条目类型（task-board change）。

    分工：plan = 主 agent 经 Task 工具创建的计划/跟踪项；
    execution = delegate fork 自动登记的执行追踪（task_id=delegate_id）。
    """

    PLAN: str = "plan"  # 主 agent 计划项
    EXECUTION: str = "execution"  # delegate 执行追踪


class TaskStatus:
    """任务条目的展示状态（前端胶囊/徽标依据；中断原因另存 reason）。

    终态映射（execution）：reason=normal → done；failed → failed；
    idle/total/turn → timeout（reason 保留具体枚举值）；cancelled → cancelled。
    """

    PENDING: str = "pending"  # 待处理（plan 建项默认）
    RUNNING: str = "running"  # 进行中（execution 运行中 / plan 手动置）
    DONE: str = "done"  # 完成
    FAILED: str = "failed"  # 失败
    TIMEOUT: str = "timeout"  # 超时中断（idle/total/turn）
    CANCELLED: str = "cancelled"  # 取消


# execution 自动登记标题模板（task-board）：{skill} 为命中 skill 名；
# 放 const 集中管理（CLAUDE.md 硬编码集中规则），delegate_task 登记用
DELEGATE_TASK_TITLE_TMPL = "{skill} · 领域专家分析"
```

- [ ] **Step 4: 实现 `task_registry.py`**

```python
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

    task_id: str
    title: str
    type: str
    status: str = TaskStatus.PENDING
    stage: str = ""
    summary: str = ""
    delegate_id: str = ""
    dependencies: list[str] = field(default_factory=list)
    reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

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

    def list_tasks_for_delegate(self, session_id: str, delegate_id: str) -> list[TaskItem]:
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
            alive = {k: v for k, v in bucket.items() if now - v.updated_at <= self.ttl_seconds}
            if alive:
                if len(alive) != len(bucket):
                    self._tasks[session_id] = alive
            else:
                expired_sessions.append(session_id)
        for sid in expired_sessions:
            self._tasks.pop(sid, None)


# 模块级共享注册表（与 streaming_manager 同生命周期假设；on_change 默认 emit_task_event）
task_registry = SessionTaskRegistry()
```

`TaskItem.touch()` 方法：在 `to_dict()` 之后追加（可并入 mark_terminal/update 的 setattr 逻辑；建议 TaskItem 提供 `touch()` 更新时间戳）：

```python
    def touch(self) -> None:
        """刷新 updated_at（更新/终态时调用）。"""
        self.updated_at = time.time()
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/chat/test_task_registry.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/chat/task_registry.py src/config/const.py tests/chat/test_task_registry.py
git commit -m "feat(tasks): add session task registry with TTL and SSE emit"
```

---

### Task B: SSE task 事件（sse.py 链式 + roundtrip）

**Files:**
- Modify: `src/utils/sse.py`（`SSETaskEvent` + 序列化 + `from_payload` + `SSEEvent` 联合）
- Test: `tests/utils/test_sse_roundtrip.py`

**Interfaces:**
- Produces: `SSETaskEvent`（type="task"）：`action: str`（created|updated|terminal）、`task: dict`、`seq`

- [ ] **Step 1: 写失败测试**

`tests/utils/test_sse_roundtrip.py` CASES 追加（import 加 `SSETaskEvent`）：

```python
    SSETaskEvent(
        action="terminal",
        task={
            "task_id": "d1",
            "title": "finance-analyst",
            "type": "execution",
            "status": "timeout",
            "stage": "",
            "summary": "",
            "delegate_id": "d1",
            "dependencies": [],
            "reason": "idle",
            "created_at": 1.0,
            "updated_at": 2.0,
        },
    ),
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_sse_roundtrip.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

`src/utils/sse.py`（`SSEDelegateEvent` 后）新增：

```python
@dataclass
class SSETaskEvent:
    """任务/进度看板变更事件（task-board change）。

    action=created：条目创建；updated：非终态字段更新；
    terminal：条目进入终态（done/failed/timeout/cancelled）。
    task 为任务快照 dict（TaskItem.to_dict()，含 type/delegate_id/status/stage/reason）。
    """

    action: str  # created | updated | terminal
    task: dict  # TaskItem.to_dict() 快照
    type: str = "task"  # SSE 事件名（event: task）
    seq: int | None = field(
        default=None, compare=False, repr=False
    )  # SSE 帧序列号（消费者注入；None 不序列化，不参与相等比较）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {"action": self.action, "task": self.task}
```

`SSEEvent` 联合追加 `| SSETaskEvent`。`to_sse` 加 case（委托 `sse_task`）：

```python
def sse_task(event: SSETaskEvent) -> str:
    """构建 task 事件（任务/进度看板）。"""
    data: dict = {"action": event.action, "task": event.task}
    if event.seq is not None:
        data["seq"] = event.seq
    return f"event: task\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

```python
        case SSETaskEvent(action=action, task=task, seq=seq):
            return sse_task(SSETaskEvent(action=action, task=task, seq=seq))
```

`from_payload` 末尾 `raise` 前追加：

```python
    if etype == "task":
        return SSETaskEvent(action=payload["action"], task=payload["task"])
```

- [ ] **Step 4: 运行测试 + 提交**

Run: `pytest tests/utils/test_sse_roundtrip.py tests/utils/test_sse.py -v`
Expected: PASS。

```bash
git add src/utils/sse.py tests/utils/test_sse_roundtrip.py
git commit -m "feat(sse): add task event type for task board"
```

---

### Task C: delegate fork 自动登记 execution + 终态

> 本 Task 在 core 已重写的 `delegate_task.py`（fork 分支含 delegate_id/start/end）之上追加登记。

**Files:**
- Modify: `src/agents/skills/delegate_task.py`（fork 分支登记 execution、结束 mark_terminal）
- Test: `tests/agents/skills/test_delegate_task.py`

**Interfaces:**
- Consumes: `task_registry`（`src/chat/task_registry.py`）、`const.TaskType/TaskStatus`、`DelegateStopReason`
- Produces: delegate fork 启动 `create_task(type=execution, status=running, stage="正在分析…")`；结束 `mark_terminal`（status 按 reason 映射）

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_delegate_task.py` 追加（用 Task A/既有 fake helper）：测试文件需要 helper `_record`/`_FakeRegistry`（文件已有）、`RequestContext`/`current_request_ctx`（文件已有）；`_fake_sub_agent`/`_event` 与 `AIMessageChunk`/`AIMessage` 自 core 改造后的 `tests/agents/skills/test_skill_executor.py` 复制（core plan Task E Step 1 定义的 helper）。

```python
@pytest.mark.asyncio
async def test_fork_auto_registers_execution_and_terminal():
    """fork 自动登记 execution（task_id=delegate_id）；正常结束置 done。"""
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.task_registry import SessionTaskRegistry
    from src.config.const import TaskStatus, TaskType

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    with patch.object(dt_mod, "task_registry", reg):
        rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
        tool = make_delegate_task(_FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock()))
        fake_sub = _fake_sub_agent(
            _event("on_chat_model_start"),
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
            _event("on_chat_model_end", output=AIMessage(content="分析")),
        )
        ctx = RequestContext(session_id="s1")
        token = current_request_ctx.set(ctx)
        try:
            with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
                await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
        finally:
            current_request_ctx.reset(token)

    running_items = [it for it in reg.list_session("s1") if it.status == TaskStatus.RUNNING]
    assert running_items == []  # 已终态
    done = reg.list_session("s1")[0]
    assert done.type == TaskType.EXECUTION
    assert done.delegate_id and done.task_id == done.delegate_id
    assert done.status == TaskStatus.DONE and done.reason == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_delegate_task.py::test_fork_auto_registers_execution_and_terminal -v`
Expected: FAIL（无登记逻辑）。

- [ ] **Step 3: 实现登记与终态**

`src/agents/skills/delegate_task.py` import **仅新增 task-board 专属项**（`DelegateStopReason`/`Event`/`core_logging` 已在 core Task G 引入，勿重复添加）：

```python
from src.chat.task_registry import task_registry
from src.config.const import (
    DELEGATE_TASK_TITLE_TMPL,
    TaskStatus,
    TaskType,
)
```

fork 分支（core 重构后的 `delegate_id = uuid.uuid4().hex[:8]` 之后，start 事件前）追加登记：

```python
        # 任务看板自动登记（task-board）：execution 条目 task_id=delegate_id，
        # stage 仅 coarse 边界更新（此处 start、finally 终态）
        task_registry.create_task(
            ctx.session_id,
            TaskType.EXECUTION,
            title=DELEGATE_TASK_TITLE_TMPL.format(skill=record.name),
            delegate_id=delegate_id,
            status=TaskStatus.RUNNING,
            stage="正在分析…",
        )
```

`finally` 块内（delegate end 事件之后、`ctx.delegate_id = ""` 复位之前）追加终态：

```python
            # execution 终态映射（展示状态与 DelegateStopReason 词表）
            if ok:
                terminal_status = TaskStatus.DONE
            elif reason is DelegateStopReason.CANCELLED:
                terminal_status = TaskStatus.CANCELLED
            elif reason is DelegateStopReason.FAILED:
                terminal_status = TaskStatus.FAILED
            else:  # idle / total / turn
                terminal_status = TaskStatus.TIMEOUT
            task_registry.mark_terminal(
                ctx.session_id,
                delegate_id,
                terminal_status,
                reason="" if ok else reason.value,
            )
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/agents/skills/test_delegate_task.py tests/chat/test_task_registry.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/agents/skills/delegate_task.py tests/agents/skills/test_delegate_task.py
git commit -m "feat(delegate): auto-register execution tasks and terminal on end"
```

---

### Task D: Task 工具集（仅主 agent）

**Files:**
- Create: `src/agents/tools/task_tools.py`
- Modify: `src/agents/graph/workflow.py`（`build_graph` rag_tools 列表追加 task tools）
- Test: `tests/agents/tools/test_task_tools.py`（新建）、`tests/agents/tools/test_rag_tools.py`（确认既有集合断言不受影响）

**Interfaces:**
- Consumes: `current_request_ctx`、`task_registry`、`const.TaskType/TaskStatus`、`DelegateStopReason`
- Produces: `make_task_tools() -> list[BaseTool]`，含 `task_create / task_get / task_list / task_update / task_output / task_stop`
- 语义：create → type=plan 条目并返回 task_id；update → status/stage/summary/dependencies；stop → 置 cancelled + 运行中 delegate 经 `ctx.abort_signal` 尽力取消；output → 返回摘要/阶段
- `build_graph`：tools 缺省分支生成 `make_rag_tools(...) + make_task_tools()`（task tools 恒注册，独立于 skill 存在性）

- [ ] **Step 1: 写失败测试**

`tests/agents/tools/test_task_tools.py`：

```python
"""测试 Task 工具集 — 会话级读写 / plan 条目 / stop 语义。"""

import pytest
from unittest.mock import patch

from src.agents.tools.task_tools import make_task_tools
from src.config.const import TaskStatus, TaskType
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _tools():
    return {t.name: t for t in make_task_tools()}


@pytest.mark.asyncio
async def test_task_create_and_list_roundtrip():
    """create → 返回 task_id；list 可读（ctx.session_id 定位）。"""
    from src.chat.task_registry import SessionTaskRegistry
    import src.agents.tools.task_tools as tt

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch.object(tt, "task_registry", reg):
            tools = _tools()
            out = await tools["task_create"].ainvoke({"title": "梳理报告口径"})
            assert "task_id" in out or "任务已创建" in out
            listing = await tools["task_list"].ainvoke({})
            assert "梳理报告口径" in listing
            items = reg.list_session("s1")
            assert items and items[0].type == TaskType.PLAN
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_task_stop_cancels_without_aborting_request():
    """stop 语义（已收敛，openspec design D3/tasks 2.1 同步标注）：
    置 cancelled 且不 set abort_signal——fork 阻塞式执行内主 agent 无法并行调 task_stop，
    跨请求 delegate 属旧请求 ContextVar 触达不到，运行中 delegate 的真实取消走 cancel 端点
    （core：fork 响应同一 ctx.abort_signal 收敛为 reason=cancelled）。
    """
    import src.agents.tools.task_tools as tt
    from src.chat.task_registry import SessionTaskRegistry
    from src.config.const import TaskStatus, TaskType

    reg = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch.object(tt, "task_registry", reg):
            reg.create_task("s1", TaskType.EXECUTION, title="e", delegate_id="d1", status=TaskStatus.RUNNING)
            tools = _tools()
            out = await tools["task_stop"].ainvoke({"task_id": "d1"})
            item = reg.get_task("s1", "d1")
            assert item is not None and item.status == TaskStatus.CANCELLED
            assert "取消" in out
            assert ctx.abort_signal.is_set() is False  # 不误杀主请求
    finally:
        current_request_ctx.reset(token)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/tools/test_task_tools.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 `task_tools.py`**

```python
"""Task 工具集 — 会话级任务注册表的读写（仅主 agent 工具面，前端只读）。

工具：create/get/list/update/output/stop 最小集。经 ctx.session_id 定位会话；
无请求上下文（ctx None）时返回错误文案给 LLM。

**stop 语义（已收敛，见 openspec design D3/tasks 2.1 标注）**：fork 在 ToolNode 内
阻塞式串行执行——主 agent 无法在 delegate 运行期间并行调 task_stop；跨请求 delegate
属旧请求 ContextVar，本请求 abort 触达不到；同请求竞态 set abort 又等于取消整个主请求
（非仅该 delegate）。因此 task_stop **不**置位 ctx.abort_signal，仅对非终态条目
（含残留 running/pending）置 cancelled；运行中 delegate 的真实取消收敛到 cancel 端点
（core：fork 响应同一 ctx.abort_signal，中断原因=cancelled，delegate end/注册表终态一致）。
"""

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.chat.task_registry import task_registry
from src.config.const import TaskStatus, TaskType
from src.infra.llm.request_context import current_request_ctx


class TaskCreateArgs(BaseModel):
    """task_create 入参（LLM 可见）。"""

    title: str = Field(description="任务标题（要跟踪的事项）")
    status: str = Field(
        default=TaskStatus.PENDING, description="初始状态（pending/running/done）"
    )
    stage: str = Field(default="", description="当前阶段（可选）")


class TaskUpdateArgs(BaseModel):
    """task_update 入参。"""

    task_id: str = Field(description="要更新的任务 id（task_create/task_list 获得）")
    status: str | None = Field(default=None, description="新状态（pending/running/done/failed）")
    stage: str | None = Field(default=None, description="新阶段")
    summary: str | None = Field(default=None, description="进展摘要")
    dependencies: list[str] | None = Field(
        default=None, description="依赖任务 id 列表（可选，覆盖声明）"
    )


class TaskStopArgs(BaseModel):
    """task_stop 入参。"""

    task_id: str = Field(description="要停止的任务 id")


def _ctx_session_id() -> str:
    """取当前请求会话 id；无上下文返回空串（工具将报错）。"""
    ctx = current_request_ctx.get()
    if ctx is None:
        return ""
    return ctx.session_id


def make_task_tools() -> list:
    """构建 Task 工具列表（create/get/list/update/output/stop）。"""

    @tool("task_create", args_schema=TaskCreateArgs)
    async def task_create(title: str, status: str = TaskStatus.PENDING, stage: str = "") -> str:
        """创建一条计划任务用于跟踪进展，返回 task_id。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.create_task(
            session_id, TaskType.PLAN, title=title, status=status, stage=stage
        )
        return f"任务已创建，task_id={item.task_id}"

    @tool("task_get", args_schema=TaskStopArgs)
    async def task_get(task_id: str) -> str:
        """按 task_id 查任务详情。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        return json.dumps(item.to_dict(), ensure_ascii=False)

    @tool("task_list")
    async def task_list() -> str:
        """列出当前会话全部任务（含 id/标题/状态），用于跟踪或引用 task_id。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        items = task_registry.list_session(session_id)
        if not items:
            return "当前会话暂无任务"
        lines = [f"- {it.task_id}: {it.title} [{it.status}]" for it in items]
        return "\n".join(lines)

    @tool("task_update", args_schema=TaskUpdateArgs)
    async def task_update(
        task_id: str,
        status: str | None = None,
        stage: str | None = None,
        summary: str | None = None,
        dependencies: list[str] | None = None,
    ) -> str:
        """更新任务状态/阶段/摘要/依赖。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.update_task(
            session_id,
            task_id,
            status=status,
            stage=stage,
            summary=summary,
            dependencies=dependencies,
        )
        if item is None:
            return f"任务不存在: {task_id}"
        return f"任务已更新: {task_id} [{item.status}]"

    @tool("task_output", args_schema=TaskStopArgs)
    async def task_output(task_id: str) -> str:
        """返回任务当前输出（阶段与摘要），供继续作答参考。"""
        session_id = _ctx_session_id()
        if not session_id:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        return json.dumps(
            {"task_id": item.task_id, "status": item.status, "stage": item.stage, "summary": item.summary},
            ensure_ascii=False,
        )

    @tool("task_stop", args_schema=TaskStopArgs)
    async def task_stop(task_id: str) -> str:
        """停止指定任务：置为 cancelled。

        收敛语义：不置位 ctx.abort_signal（阻塞式 fork 下跨请求 delegate 触达不到，
        同请求竞态会误杀整个主请求）；运行中 delegate 的真实取消由用户走 cancel 端点
        （core：fork 响应 ctx.abort_signal → reason=cancelled → 注册表终态）。
        本工具用于主 agent 主动清理/收尾非终态条目（含残留 running/pending）。
        """
        ctx = current_request_ctx.get()
        if ctx is None:
            return "Error: 请求上下文不可用"
        item = task_registry.get_task(ctx.session_id, task_id)
        if item is None:
            return f"任务不存在: {task_id}"
        if item.status in (TaskStatus.RUNNING, TaskStatus.PENDING):
            task_registry.update_task(ctx.session_id, task_id, status=TaskStatus.CANCELLED)
            return f"任务已取消: {task_id}"
        return f"任务已是终态（{item.status}），无需停止"

    return [task_create, task_get, task_list, task_update, task_output, task_stop]
```

- [ ] **Step 4: build_graph 合并 task tools**

`src/agents/graph/workflow.py` `build_graph` 缺省分支（现 65-71 行）改为：

```python
    else:
        base_tools = make_rag_tools(
            vector_store,
            bm25,
            reranker,
            prompt_manager,
            delegate_task=delegate_task,
        )
        from src.agents.tools.task_tools import make_task_tools

        # base_tools 经 list 归一化：make_rag_tools 生产必返回 list，但测试会
        # monkeypatch 成空 list（test_graph.py:726 fake_make_rag_tools return []），
        # 防御性 list() 防止 None 解包 TypeError
        rag_tools = [*(base_tools or []), *make_task_tools()]
```

（Task 工具恒注册；`enabled_tools()` 过滤只作用于 make_rag_tools 内部产物，task tools 追加在其后。）

- [ ] **Step 5: 运行测试**

Run: `pytest tests/agents/tools/ tests/agents/graph/test_graph.py -v`
Expected: PASS（确认既有 `test_make_rag_tools_without_delegate_keeps_fixed_set` 不受影响——它断言 make_rag_tools 自身输出，task tools 在 workflow 层追加）。

- [ ] **Step 6: 提交**

```bash
git add src/agents/tools/task_tools.py src/agents/graph/workflow.py tests/agents/tools/test_task_tools.py
git commit -m "feat(agents): add task tools (create/get/list/update/output/stop)"
```

---

### Task E: 任务快照接口 GET /api/sessions/tasks

**Files:**
- Modify: `src/api/sessions.py`（新增路由）
- Modify: `src/api/model/request.py`（如需要不新增——用 Query 参数即可，无需 body）
- Test: `tests/api/test_sessions.py`

**Interfaces:**
- Produces: `GET /api/sessions/tasks?session_id=`（权限同 sessions/events；会话不存在/无权 → 404 BusinessError；正常返回 `ResponseModel(data=任务快照列表)`，无任务 → data=[]）

- [ ] **Step 1: 写失败测试**

`tests/api/test_sessions.py` 追加（沿用该文件既有 `auth_client` + `mock_app_service` fixture；快照接口读注册表单例，测试用 `patch` 注入 on_change=None 的临时实例防缓冲污染）：

```python
from unittest.mock import patch

from src.chat.task_registry import SessionTaskRegistry
from src.config.const import TaskStatus, TaskType


def test_get_session_tasks_empty(auth_client, mock_app_service):
    """GET /api/sessions/tasks 无任务返回空列表。"""
    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    with patch("src.api.sessions.task_registry", SessionTaskRegistry(on_change=None)):
        resp = auth_client.get("/api/sessions/tasks?session_id=s1")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


def test_get_session_tasks_snapshot(auth_client, mock_app_service):
    """GET /api/sessions/tasks 返回该会话任务快照。"""
    mock_app_service.get_session_by_id = AsyncMock(
        return_value=make_session("s1", user_id="test-user-id")
    )
    fake_reg = SessionTaskRegistry(on_change=None)
    fake_reg.create_task("s1", TaskType.EXECUTION, title="e", delegate_id="d1", status=TaskStatus.RUNNING)
    with patch("src.api.sessions.task_registry", fake_reg):
        resp = auth_client.get("/api/sessions/tasks?session_id=s1")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1 and data[0]["delegate_id"] == "d1"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/api/test_sessions.py -v`
Expected: FAIL（404 路由不存在）。

- [ ] **Step 3: 实现路由**

`src/api/sessions.py` 增加 import 与路由（放在 `resume_session_events` 后）：

```python
from src.chat.task_registry import task_registry
```

```python
@router.get("/sessions/tasks", response_model=ResponseModel)
async def get_session_tasks(
    request: Request,
    session_id: str = Query(...),
    svc: AppService = Depends(get_app_service),
) -> ResponseModel:
    """任务快照读取：返回该会话注册表任务列表（页面刷新/切换会话后初始化看板）。

    权限与 sessions/events 一致（会话存在且属于当前用户）。仅读运行期
    进程内注册表，不落库；无任务返回空列表。

    Args:
        request: FastAPI 请求（从中提取 user_id）
        session_id: 会话 ID
        svc: 应用服务实例（由 FastAPI 注入）

    Returns:
        ResponseModel: data 为任务快照 dict 列表（TaskItem.to_dict()）

    Raises:
        BusinessError: 会话不存在或无权访问时返回 404
    """
    user_id = getattr(request.state, "user_id", "")
    session = await svc.get_session_by_id(session_id)
    if not session or (session.get("user_id") and session["user_id"] != user_id):
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)
    items = task_registry.list_session(session_id)
    return ResponseModel(data=[it.to_dict() for it in items])
```

- [ ] **Step 4: 运行测试 + 提交**

Run: `pytest tests/api/test_sessions.py tests/api/test_streaming_endpoints.py -v`
Expected: PASS。

```bash
git add src/api/sessions.py tests/api/test_sessions.py
git commit -m "feat(api): add session task snapshot endpoint"
```

---

### Task F: 前端任务/进度看板（chip + 右侧只读面板 + task handler + 快照初始化）

> 前置：读 `docs/design/pages/chat-delegate-progress-2026-09-07.md` §3；视觉规格以设计稿与 MASTER 为准，用 `/frontend-design` 校准；在 core 已改的 `chat.html` 之上合并。改动须回写设计文档（防腐）。与引用抽屉互斥单开。

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS：chip/面板；DOM：chip 与面板插槽；JS：task handler、面板渲染、快照加载）

**Interfaces:**
- Consumes: `buildStreamHandlers`、`switchSession(sessionId)`（2061）、`boot()`/`loadSessions`、既有 `.cite-drawer`/`drawerBackdrop` 抽屉模式、`fetch`/`ResponseModel` 信封（`body.code === 'SUCCESS'`）
- Produces:
  - `buildStreamHandlers()` 增加 `task` handler（action=created|updated|terminal → upsert/remove 看板条目）
  - 看板状态 `taskBoard = { items: Map<task_id, task>, open: false }`；`openTaskBoard()/closeTaskBoard()/toggleTaskBoard()`；`upsertTask(data)`/`renderTaskBoard()`/`loadTaskSnapshot(sessionId)`
  - 初始化调用：`switchSession` 与 `boot` 恢复后调 `loadTaskSnapshot(state.sessionId)`

- [ ] **Step 1: CSS + DOM 插槽（头部 chip + 右侧面板）**

CSS（`.cite-drawer`/`.drawer-backdrop` 样式组之后）追加（与设计稿 §3 对齐，数值可微调）：

```css
  /* 任务/进度看板（task-board change；与引用抽屉互斥单开） */
  .task-chip {
    display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px;
    background: transparent; border: 1px solid var(--border); border-radius: 20px;
    cursor: pointer; font-size: 12.5px; color: var(--text-secondary); transition: all 150ms;
  }
  .task-chip:hover { border-color: var(--primary); color: var(--primary); }
  .task-chip .task-count { display: none; }
  .task-chip.has-running .task-count {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 16px; height: 16px; padding: 0 4px; border-radius: 8px;
    background: var(--primary); color: #fff; font-size: 10.5px;
  }
  .task-panel { ... } /* 复用 .cite-drawer 布局：fixed right 360px；仅头部标题换"任务/进度" */
```

DOM 插槽（chip 双实例 + 面板单实例）：

```html
<!-- chip 实例①：历史对话页主区顶栏右侧（.main-header .header-right 内、preview-tag 前） -->
<button type="button" class="task-chip" onclick="toggleTaskBoard()" aria-expanded="false" title="任务/进度">
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
  <span>任务</span>
  <span class="task-count">0</span>
</button>

<!-- chip 实例②：新对话页 composer-bottom 内、thinking-chip 之前（page-new 提问常在欢迎页发起，
     委派/任务计数须两页均可见，故双 chip 共享同一面板与 state）。
     插入位置：page-new 的 <div class="composer-bottom"> 内，紧挨 thinking-chip 左侧（chat.html:674 区域） -->
<!-- 复制同一份按钮 HTML 到该位置，排布细节以设计稿 §3 + /frontend-design 微调 -->

<!-- 右侧任务面板（复用 .cite-drawer 布局；与引用抽屉互斥单开，共用 drawerBackdrop） -->
<aside class="cite-drawer task-panel" id="taskPanel" aria-label="任务/进度" role="dialog" aria-hidden="true">
  <div class="drawer-head">
    <span class="head-icon"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/></svg></span>
    <h3>任务/进度</h3>
    <button class="drawer-close" type="button" onclick="closeTaskBoard()" title="关闭"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  </div>
  <div class="drawer-list" id="taskBoardList"></div>
</aside>
```

> chip 与 panel 视觉规格见设计稿 §3（chip 计数胶囊：running/pending 数>0 时显示）。`drawerBackdrop` 的 `onclick` 需从 `closeCiteDrawer()` 改为 `closeAllDrawers()`（Step 3 统一定义）。

- [ ] **Step 2: JS 状态与渲染函数**

`chat.html` 中新增（建议放在引用抽屉函数之后）：

```js
// ── 任务/进度看板（task-board change；只读，数据源 = SSE task 事件 + 快照接口）──
const taskBoard = { items: new Map(), open: false };

// delegate_id → 由 delegate 增量事件（core）实时派生的活动摘要文本（只读展示层，
// 不写回后端；design D5：注册表 stage 粒度 coarse，实时摘要由前端从 delegate 事件派生）
const delegateActivity = new Map();

function appendDelegateActivity(delegateId, text) {
  if (!delegateId || !text) return;
  const prev = delegateActivity.get(delegateId) || '';
  const next = (prev + text).slice(-800);  // 单条活动摘要上限，防无限累积
  delegateActivity.set(delegateId, next);
  if (taskBoard.open) renderTaskBoard();
}

function upsertTask(data) {
  const t = (data && data.task) || null;
  if (!t || !t.task_id) return;
  taskBoard.items.set(t.task_id, t);
  updateTaskCount();
  if (taskBoard.open) renderTaskBoard();
}

function removeTask(taskId) {
  taskBoard.items.delete(taskId);
  updateTaskCount();
  if (taskBoard.open) renderTaskBoard();
}

function taskChipEls() {
  return Array.from(document.querySelectorAll('.task-chip'));
}

function updateTaskCount() {
  const running = Array.from(taskBoard.items.values()).filter(t => t.status === 'running' || t.status === 'pending');
  taskChipEls().forEach(chip => chip.classList.toggle('has-running', running.length > 0));
  taskChipEls().forEach(chip => {
    const count = chip.querySelector('.task-count');
    if (count) count.textContent = String(running.length);
  });
}

function taskStatusMeta(t) {
  // status → 圆点 class / 胶囊文案 / 中断原因（双通道，非仅颜色）
  if (t.status === 'running' || t.status === 'pending') return { dot: 'running', label: '进行中' };
  if (t.status === 'done') return { dot: 'done', label: '完成' };
  if (t.status === 'cancelled') return { dot: 'error', label: '已取消' };
  if (t.status === 'timeout') {
    const reasonText = { idle: '空闲超时', total: '超时', turn: '轮次上限' }[t.reason] || t.reason || '超时';
    return { dot: 'error', label: '超时', note: reasonText };
  }
  if (t.status === 'failed') return { dot: 'error', label: '失败', note: t.reason || '' };
  return { dot: 'done', label: t.status };
}

function renderTaskBoard() {
  const list = $('taskBoardList');
  if (!list) return;
  const items = Array.from(taskBoard.items.values()).sort((a, b) => b.updated_at - a.updated_at);
  if (!items.length) {
    list.innerHTML = '<div class="drawer-empty">暂无进行中的任务</div>';
    return;
  }
  list.innerHTML = items.map(t => {
    const meta = taskStatusMeta(t);
    const typeBadge = t.type === 'execution'
      ? '<span class="task-badge exec">执行</span>'
      : '<span class="task-badge plan">计划</span>';
    const note = meta.note ? `<div class="task-note">${escapeHtml(meta.note)}</div>` : '';
    // execution 实时活动摘要：前端从 delegate 增量事件派生（delegateActivity），不依赖后端 stage
    const activity = (t.type === 'execution' && delegateActivity.get(t.task_id))
      ? `<div class="task-activity">${escapeHtml(delegateActivity.get(t.task_id))}</div>` : '';
    const summary = t.summary ? `<div class="task-summary">${escapeHtml(t.summary)}</div>` : '';
    const stage = t.stage && t.status !== 'done' ? `<div class="task-stage">${escapeHtml(t.stage)}</div>` : '';
    return `
      <div class="task-item" data-task-id="${escapeHtml(t.task_id)}">
        <div class="task-headline">
          <span class="delegate-dot ${meta.dot}"></span>
          <span class="task-title">${escapeHtml(t.title)}</span>
          ${typeBadge}
          <span class="task-status">${meta.label}</span>
        </div>
        ${stage}${activity}${summary}${note}
        <div class="task-time">${escapeHtml(new Date(t.updated_at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))}</div>
      </div>`;
  }).join('');
}

function openTaskBoard() {
  taskBoard.open = true;
  closeCiteDrawer();            // 与引用抽屉互斥单开
  const p = $('taskPanel');
  if (p) { p.classList.add('open'); p.setAttribute('aria-hidden', 'false'); }
  taskChipEls().forEach(chip => chip.setAttribute('aria-expanded', 'true'));
  const backdrop = $('drawerBackdrop');
  if (backdrop) backdrop.classList.add('show');  // 与引用抽屉共用同一 backdrop
  renderTaskBoard();
}

function closeTaskBoard() {
  taskBoard.open = false;
  const p = $('taskPanel');
  if (p) { p.classList.remove('open'); p.setAttribute('aria-hidden', 'true'); }
  taskChipEls().forEach(chip => chip.setAttribute('aria-expanded', 'false'));
  const backdrop = $('drawerBackdrop');
  if (backdrop && !$('citeDrawer').classList.contains('open')) {
    backdrop.classList.remove('show');
  }
}

// 统一关闭任务看板与引用抽屉（drawerBackdrop onclick 用；Esc 也走这里）
function closeAllDrawers() {
  closeTaskBoard();
  closeCiteDrawer();
}

function toggleTaskBoard() {
  if (taskBoard.open) closeTaskBoard();
  else openTaskBoard();
}

async function loadTaskSnapshot(sessionId) {
  try {
    const res = await fetch('/api/sessions/tasks?session_id=' + encodeURIComponent(sessionId), {
      method: 'GET',
      headers: { 'X-Trace-ID': generateTraceId() },
    });
    const body = await res.json().catch(() => null);
    if (body && body.code === 'SUCCESS' && Array.isArray(body.data)) {
      taskBoard.items.clear();
      body.data.forEach(t => taskBoard.items.set(t.task_id, t));
      updateTaskCount();
      if (taskBoard.open) renderTaskBoard();
    }
  } catch (e) { /* 快照拉取失败静默：运行期靠 task 事件增量 */ }
}

// Esc 关闭：不在此新加全局 keydown（与既有 chat.html:2156 引用抽屉的 keydown 重复），
// 在既有 keydown handler 内补 task 面板分支（见 Step 3），或调用 closeAllDrawers()
```

- [ ] **Step 3: task handler + 快照初始化接线**

`buildStreamHandlers()` 增加（与 `delegate` handler 同级）：

```js
    task: (data) => {
      try {
        state.lastSeq = (data.seq !== undefined) ? data.seq : state.lastSeq;
        if (data.action === 'terminal' && data.task) {
          upsertTask(data);   // 终态保留在面板（状态胶囊置终态），不删条目
        } else if (data.action === 'created' || data.action === 'updated') {
          upsertTask(data);
        }
      } catch (err) { /* ignore */ }
    },
```

初始化接线与联动：
- `switchSession`（2061）末尾（`loadSessionMessages` 后）加 `loadTaskSnapshot(sessionId);`
- `newSession`（2074）末尾加 `taskBoard.items.clear(); delegateActivity.clear(); updateTaskCount();`
- `boot()` 的 `resumeIfGenerating` 完成后（或 `loadSessions` 成功初始 render 处）加一次 `loadTaskSnapshot(state.sessionId)`；若刷新处于 generating，resume 流中 task 事件增量会继续 upsert。空会话分支（无历史 → `showNewPage`）也补一次 `loadTaskSnapshot(state.sessionId)`（registry 可能残留异常条目，快照加载可无害覆盖）。
- `loadTaskSnapshot` 成功后同步清一次 `delegateActivity`（仅当会话切换）：把 `delegateActivity.clear()` 放在 `taskBoard.items.clear()` 后（断线续接时 activity 会由 delegate 增量重建，不做持久化承诺）。

**delegate 事件派生实时摘要（design D5 落地）**：core 的 `delegate` handler（action=delta）在本 change 之上追加看板联动——找到 `buildStreamHandlers` 里 `delegate` handler 的 `appendDelegateDelta(data)` 调用之后加：

```js
        // 看板实时活动摘要：execution 条目的 task_id=delegate_id，delta 派生到 activity
        if (data.action === 'delta') {
          appendDelegateActivity(data.delegate_id, data.delta || '');
        }
```

**Esc 与 backdrop（与引用抽屉一致交互）**：
- 既有全局 keydown（chat.html:2156）在 Escape 分支里追加：`else if (taskBoard.open) { closeTaskBoard(); }`（不再新加独立 keydown 监听）。
- `drawerBackdrop` 元素（chat.html:727）`onclick` 从 `closeCiteDrawer()` 改为 `closeAllDrawers()`（共用 backdrop，任一抽屉打开即 .show）。

CSS 补充徽标类：

```css
  .task-badge { font-size: 10px; padding: 1px 6px; border-radius: 4px; }
  .task-badge.exec { background: var(--primary-light); color: var(--primary); }
  .task-badge.plan { border: 1px solid var(--border); color: var(--text-secondary); }
  .task-item { padding: 12px 18px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 4px; }
  .task-item:last-child { border-bottom: none; }
  .task-headline { display: flex; align-items: center; gap: 8px; }
  .task-title { flex: 1; font-weight: 500; font-size: 13px; min-width: 0; }
  .task-status { font-size: 11px; color: var(--text-secondary); }
  .task-note, .task-stage, .task-summary { font-size: 12px; color: var(--text-secondary); }
  .task-activity {
    font-size: 12px; color: var(--text-secondary); line-height: 1.5;
    max-height: 44px; overflow: hidden; white-space: pre-wrap; word-break: break-word;
  }
  .task-time { font-size: 11px; color: var(--text-muted); }
```

- [ ] **Step 4: 静态自检 + 冒烟准备**

Run: 人工核对 `handlers.task` ↔ `event: task`、chip/panel id 引用一致；与引用抽屉 `openCiteDrawer` 互斥（`openCiteDrawer` 内加 `closeTaskBoard()` 一行，见 Step 5）。

- [ ] **Step 5: 与引用抽屉互斥**

`openCiteDrawer`（现 1136-1160）开头加 `closeTaskBoard();`。

- [ ] **Step 6: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): add read-only task board with chip, panel, and snapshot load"
```

---

### Task G: playwright 冒烟 + 文档 + 收尾

**Files:**
- Modify: `docs/openspec/changes/task-board/`（validate）
- Modify: `docs/agents/api_contract.md`（task 契约增量）、`docs/agents/data-flow.md`、`docs/agents/logging-rules.md`（如需）、`docs/agents/glossary.md`（确认已登记 task type/DelegateStopReason，缺则补）

- [ ] **Step 1: 冒烟**

- 委托一次 + 用 Task 工具建项（人工或 agent 对话触发）：前端 chip 出现、两类条目（plan outline 徽标 / execution primary 徽标）可见、execution 实时活动摘要由 delegate 增量派生（不止静态 stage）、终态/超时原因正确、前端无写入口
- 刷新/切换会话：看板经快照接口恢复（刷新时 running 场景先 resume 再由快照+事件校正）
- delegate 中断：看板终态带 reason（idle/total/turn）；**停止按钮（cancel 端点）** → cancelled；主 agent 调 task_stop 仅置 cancelled 不动主请求（收敛语义）
- 引用抽屉与任务面板互斥单开；Esc/backdrop 关闭一致；面板条目 >8 内部滚动
- playwright 冒烟沿用 core Task M 方式（委托一次即可覆盖看板）

- [ ] **Step 2: api_contract / 文档同步**

- api_contract.md：SSE 事件表（2.3.1）新增 `task` 事件行（`action=created|updated|terminal`、payload task：type=plan|execution、delegate_id、status、stage/summary、reason）；会话管理小节新增 `GET /api/sessions/tasks`（任务快照；权限同 sessions/events；data=任务列表；无任务为空列表）
- data-flow.md 补看板数据流（registry → SSE task 事件 / 快照接口；delegate 事件派生活动摘要）
- logging-rules.md：如 task 事件需登记则登记（task 事件为 SSE 非日志事件，通常无需登记；仅在需要 [task] 前缀日志时登记）
- glossary：核对 task type=plan|execution、DelegateStopReason 已登记（core 已登记则引用即可）
- **TTL 口径注明**（api_contract/data-flow）：注册表 TTL 30min > 事件缓冲 TTL 5min（streaming buffer 300s）——SSE `task` 事件只保证 5min 内可达，更长窗口内权威数据源是 `GET /api/sessions/tasks` 快照接口；文档标注"事件=增量提示、快照=权威"，避免 resume 因缓冲被清而误判任务消失

- [ ] **Step 3: 质量门禁 + validate**

Run:
```bash
pytest tests/ -v
ruff check .
pyright src/
npx opsx validate task-board   # 如实际命令不同以仓库为准
```
Expected: 全绿 + valid。

- [ ] **Step 4: 提交**

```bash
git add docs/agents/api_contract.md docs/agents/data-flow.md docs/agents/glossary.md docs/agents/logging-rules.md docs/openspec/changes/task-board/
git commit -m "docs(task-board): register task SSE contract and snapshot endpoint"
```

---

## Self-Review 记录

- **Spec 覆盖**：Task 工具集 → D；委派自动登记 → C；快照读取 → E；注册表生命周期 → A；task 事件与看板 UI → B + F；无任务空返回 → E 测试；delegate 事件派生实时活动摘要（design D5）→ F。
- **Placeholder 扫描**：CSS 数值引用设计稿 token（视觉微调属设计稿范围）；前端测试依赖既有 playwright 模式属验收步骤。
- **Type/名一致**：`TaskType.PLAN/EXECUTION`、`TaskStatus` 值、`SSETaskEvent(action/task)`、delegate dict 事件字段（core）与 `mark_terminal(session_id, delegate_id, status, reason)` 在 A/B/C/D/E/F 各任务一致；`task_registry` 模块单例名在 C/D/E 一致。
- **评审修正记录**：①stop 语义收敛（不 set abort_signal，运行中 delegate 取消走 cancel 端点）——同步 openspec design D3/tasks 2.1；②Task F 落地前端 delegate→活动摘要派生；③注册表支持 `on_change=None` 注入防测试缓冲污染；④终态统一发 `action=terminal`（update_task 判定）；⑤Task E 测试按 `auth_client`/`mock_app_service` fixture 重写；⑥workflow 工具合并用 `list(base_tools or [])` 防御；⑦标题文案进 const 模板 `DELEGATE_TASK_TITLE_TMPL`；⑧Esc/backdrop 与引用抽屉统一（`closeAllDrawers`）。
