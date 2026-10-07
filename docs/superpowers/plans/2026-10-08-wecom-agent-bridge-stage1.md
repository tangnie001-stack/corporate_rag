# 企微接站点同款 Agent —— 阶段 1：编排下沉 + 依赖注入 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把站点生成编排从 `src/api/chat.py` 下沉为 `services/` 的单一入口 `start_turn(...)`，并把 `AppService` 单例访问器下沉到 `services/`，使通道侧日后能复用同一入口——**站点外部行为不变**。

**Architecture:** 新增 `src/services/turn_runner.py`（`start_turn` → `TurnHandle.events`，内含落库前置、原子闸门、后台收尾任务）与 `src/services/chat_lock.py`（per-session 锁助手）；`src/api/chat.py` 退化为"取服务 → 转 SSE 帧 → 异常映射"；`src/api/sessions.py` 的锁 import 改向 services。**本阶段不碰通道**。

**Tech Stack:** Python 3.11+ / FastAPI / asyncio / Redis(asyncio) / pytest。

**Spec:** `docs/openspec/changes/wecom-agent-bridge/design.md`（**D12** 下沉入口、**D20** 依赖注入、D19 会话可区分）+ `specs/wecom-agent-bridge/spec.md`（「入站消息喂入站点同款 Agent 管线」的闸门/失败语义要求）。

## Global Constraints

- `channels` 不得 import `api`；`api/` 只做参数校验与路由转发（层间规则）。
- 单文件 400 行、单函数 80 行红线。
- **站点外部行为不变**：`POST /api/chat/stream` 的响应码（冲突 409）、SSE 事件名与序列、`/api/sessions/*` resume 行为均不得改变。
- 测试须带 `POSTGRES_HOST=localhost` 前缀（宿主侧）；容器内不加。
- 不用三元表达式；类型不确定处显式 `isinstance` / `is not None`。
- worktree 缺 gitignored 夹具 `data/test_docs/*` → 涉及解析器的用例会报 FileNotFoundError，属**环境性**、非本阶段回归。
- 提交遵守本仓 commit 形状（单命令 / 输出重定向到文件 / 后台跑 / 禁 `| tail`）。

---

## File Structure

| 文件 | 责任 |
|---|---|
| `src/services/app_service.py`（改） | 新增模块级 `get_app_service()` 单例访问器（原在 api 层） |
| `src/api/dependencies.py`（改） | 退化为转发薄壳（`Depends(get_app_service)` 用法不变） |
| `src/services/chat_lock.py`（新） | per-session 并发锁助手（`acquire_session_lock` / `release_session_lock`），从 `api/chat.py` 下沉 |
| `src/services/turn_runner.py`（新） | 单一编排入口：`TurnBusy` / `TurnHandle` / `start_turn(...)` + 后台收尾任务 |
| `src/api/chat.py`（改） | 退化为：取服务 → `start_turn` → `to_sse` 转帧；`TurnBusy` → 409 |
| `src/api/sessions.py`（改） | 锁释放函数 import 从 `api.chat` 改为 `services.chat_lock` |

---

### Task 1: 下沉 `AppService` 单例访问器（D20）

**Files:**
- Modify: `src/services/app_service.py`（文件末尾追加访问器）
- Modify: `src/api/dependencies.py`（改为转发）
- Test: `tests/services/test_app_service_accessor.py`

**Interfaces:**
- Produces: `src.services.app_service.get_app_service() -> AppService`（`async def`，保持原签名与语义）
- Produces: `src.api.dependencies.get_app_service`（**同一函数对象**的再导出）

- [ ] **Step 1: 写失败测试**

```python
# tests/services/test_app_service_accessor.py
"""AppService 单例访问器下沉到 services 后，api 层只做再导出。"""


def test_api_dependencies_reexports_services_accessor():
    from src.api import dependencies as dep_mod
    from src.services import app_service as svc_mod

    assert dep_mod.get_app_service is svc_mod.get_app_service


def test_accessor_is_a_coroutine_function():
    import inspect

    from src.services.app_service import get_app_service

    assert inspect.iscoroutinefunction(get_app_service)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_app_service_accessor.py -v`
Expected: FAIL —— `ImportError: cannot import name 'get_app_service' from 'src.services.app_service'`

- [ ] **Step 3: 实现**

在 `src/services/app_service.py` **文件末尾**追加（模块级单例，延迟初始化，与原名语义一致）：

```python
# ── 单例访问器 ────────────────────────────────────────────────
# 下沉自 api/dependencies.py：channels 不得 import api，故访问器须住在 services。
_service: AppService | None = None


async def get_app_service() -> AppService:
    """提供 AppService 单例。

    延迟初始化：首次调用时创建实例，后续复用——避免模块导入阶段产生
    网络或数据库连接。api 层与通道层共用本函数。
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service
```

把 `src/api/dependencies.py` **整体替换**为：

```python
"""FastAPI 依赖注入 — 转发到 services 层的共享依赖。"""

from src.services.app_service import AppService, get_app_service

__all__ = ["AppService", "get_app_service"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_app_service_accessor.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 全量确认无 import 断裂**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 与基线一致（worktree 可能因缺 `data/test_docs/*` 有环境性失败，逐条确认与本站点改动无关）

- [ ] **Step 6: 提交**

```bash
git add src/services/app_service.py src/api/dependencies.py tests/services/test_app_service_accessor.py
git commit -m "refactor(services): AppService 单例访问器下沉到 services（D20）"
```

---

### Task 2: 下沉会话锁助手（D12 前置）

**Files:**
- Create: `src/services/chat_lock.py`
- Modify: `src/api/chat.py`（删本地 `_acquire_session_lock` / `_release_session_lock`，改 import）
- Modify: `src/api/sessions.py:13`（import 改向）
- Test: `tests/services/test_chat_lock.py`

**Interfaces:**
- Produces: `src.services.chat_lock.acquire_session_lock(redis, session_id: str) -> bool`
- Produces: `src.services.chat_lock.release_session_lock(redis, session_id: str) -> None`
- 锁 key 格式保持 `chat_lock:{session_id}`（与线上既有锁兼容）

- [ ] **Step 1: 写失败测试**

```python
# tests/services/test_chat_lock.py
"""会话锁助手：key 格式与 SETNX 语义不得改变（线上锁兼容）。"""
import pytest

from src.services.chat_lock import acquire_session_lock, release_session_lock


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        return self.store.pop(key, None)


@pytest.mark.asyncio
async def test_acquire_conflict_release_cycle():
    redis = _FakeRedis()
    assert await acquire_session_lock(redis, "s1") is True
    assert await acquire_session_lock(redis, "s1") is False
    # key 格式必须与既有线上锁一致
    assert "chat_lock:s1" in redis.store
    await release_session_lock(redis, "s1")
    assert await acquire_session_lock(redis, "s1") is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_chat_lock.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.chat_lock'`

- [ ] **Step 3: 实现**

```python
# src/services/chat_lock.py
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
    return bool(await redis.set(_lock_key(session_id), "1", nx=True, ex=SESSION_LOCK_TTL))


async def release_session_lock(redis, session_id: str) -> None:
    """释放 per-session 并发锁（删除对应 Redis key）。"""
    await redis.delete(_lock_key(session_id))
```

在 `src/api/chat.py` 中：删除 `_acquire_session_lock` / `_release_session_lock` 两个本地函数，改为

```python
from src.services.chat_lock import acquire_session_lock, release_session_lock
```

并把文件内对 `_acquire_session_lock(` / `_release_session_lock(` 的调用点改名（无 `_` 前缀），行为逐字不变。

把 `src/api/sessions.py:13` 的

```python
from src.api.chat import _release_session_lock
```

改为

```python
from src.services.chat_lock import release_session_lock
```

并把该文件内的 `_release_session_lock(` 调用点改名。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_chat_lock.py -v`
Expected: PASS

- [ ] **Step 5: 确认 sessions 路由未断**

Run: `POSTGRES_HOST=localhost pytest tests/api/ -q`
Expected: 与基线一致

- [ ] **Step 6: 提交**

```bash
git add src/services/chat_lock.py src/api/chat.py src/api/sessions.py tests/services/test_chat_lock.py
git commit -m "refactor(services): 会话锁助手下沉到 services，sessions 路由改向 import"
```

---

### Task 3: 新建 `turn_runner` — 单一编排入口 `start_turn`

**Files:**
- Create: `src/services/turn_runner.py`
- Test: `tests/services/test_turn_runner.py`

**Interfaces:**
- Consumes: `src.services.chat_lock.acquire_session_lock` / `release_session_lock`（Task 2）；`src.services.app_service.AppService`（Task 1）
- Produces:
  - `src.services.turn_runner.TurnBusy`（Exception，冲突时抛）
  - `src.services.turn_runner.TurnHandle`（dataclass：`session_id: str`、`events: AsyncIterator[SSEEvent]`）
  - `src.services.turn_runner.start_turn(svc, *, session_id, kb_id, query, user_id="", deep_thinking=False, agent="", title=None, abort_signal=None) -> TurnHandle`

**行为契约（本任务的验收口径，来自 design D12）：**

1. 先 `await svc.set_chat_repo()`（漏则 `save_*` 静默跳过）。
2. **原子闸门**：`streaming_manager.is_running(session_id)` 为真 → 抛 `TurnBusy`；否则取 Redis 锁（Redis 不可用则跳过，不阻塞），未取到 → 抛 `TurnBusy`。
3. 落库前置：`save_session_async(session_id, title_or_query20, kb_id, user_id)` + `save_user_async(...)`。
4. `stream_chat(...)` → `(subscription, launch_ctx)`。
5. 构造 `abort_signal`（若调用方传入则用其），接到 `ctx.abort_signal`。
6. 起后台任务：跑 `_run_generation` + 收尾落库 + 写终态事件 + 释放锁 + 注销注册表。
7. 返回 `TurnHandle`；`events` 由 `_subscribe_events(session_id, streaming_manager, max_idle=None)` 产出。
8. **失败语义**：步骤 2/3 失败 → 抛（并释放已取的锁与注册）；步骤 4 失败（`stream_chat` 调用本身）→ **不抛**，改为在 `events` 上产 `error` + `done`。

- [ ] **Step 1: 写失败测试（冲突前置 + 失败语义，用假 svc）**

```python
# tests/services/test_turn_runner.py
"""start_turn 的闸门与失败语义（不触真实 DB/Redis）。"""
import asyncio

import pytest

from src.chat.streaming import streaming_manager
from src.services.turn_runner import TurnBusy, start_turn


class _FakeAgentService:
    def __init__(self, raise_on_stream_chat: bool = False) -> None:
        self.raise_on_stream_chat = raise_on_stream_chat
        self.calls = 0

    async def stream_chat(self, kb_id, session_id, query, deep_thinking, agent=""):
        self.calls += 1
        if self.raise_on_stream_chat:
            raise RuntimeError("boom")
        raise AssertionError("本用例不应走到订阅成功路径")


class _FakeChatManager:
    def __init__(self) -> None:
        self._redis = None  # 触发"Redis 不可用"分支：跳过锁，不阻塞
        self.saved: list[tuple] = []

    async def save_session_async(self, session_id, title, kb_id, user_id, agent=""):
        self.saved.append(("session", session_id, title, kb_id, user_id))

    async def save_user_async(self, session_id, kb_id, user_msg):
        self.saved.append(("user", session_id, kb_id, user_msg))


class _FakeSvc:
    def __init__(self, **kw) -> None:
        self.chat_manager = _FakeChatManager()
        self.agent_service = _FakeAgentService(**kw)
        self.chat_repo_set = 0

    async def set_chat_repo(self) -> None:
        self.chat_repo_set += 1


@pytest.mark.asyncio
async def test_raises_turn_busy_when_session_running():
    """注册表已有该会话任务 → TurnBusy（不双开）。"""
    session_id = "sess-busy"
    task = asyncio.create_task(asyncio.sleep(5))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        svc = _FakeSvc()
        with pytest.raises(TurnBusy):
            await start_turn(svc, session_id=session_id, kb_id="", query="hi")
    finally:
        task.cancel()
        streaming_manager.unregister_if_current(session_id, task)


@pytest.mark.asyncio
async def test_set_chat_repo_called_before_persist():
    """落库前置必须先注入 repo（否则 save_* 静默跳过）。"""
    svc = _FakeSvc(raise_on_stream_chat=True)
    handle = await start_turn(svc, session_id="sess-1", kb_id="", query="你好世界")
    assert svc.chat_repo_set == 1
    assert ("session", "sess-1", "你好世界", "", "") in svc.chat_manager.saved

    events = []
    async for ev in handle.events:
        events.append(type(ev).__name__)
    # stream_chat 失败 → 不抛，产 error + done 终止态
    assert "SSEErrorEvent" in events
    assert events[-1] == "SSEDoneEvent"
```

> 注：`流式` 的 `events` 断言依赖 `_subscribe_events` 的终止态语义；若 `SSEErrorEvent` 名不同，按 `src/utils/sse.py` 的实际类名改。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_turn_runner.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.turn_runner'`

- [ ] **Step 3: 实现**

创建 `src/services/turn_runner.py`。**主体从 `src/api/chat.py` 搬移**（`_run_with_finalize` 与 `_stream_rag_response` 的主体），按下面改名与签名调整；`_run_generation` 调用参数不变。

```python
# src/services/turn_runner.py
"""单轮生成编排入口——站点与企微通道共用（design D12）。

从 api/chat.py 的 _stream_rag_response + _run_with_finalize 下沉而来：
产出结构化 SSEEvent 流，站点把它转成 SSE 帧，通道交给投影层。
编排前置（set_chat_repo / 原子闸门 / 落库）与收尾（终态落库 / 释放 / 注销）
内聚在本入口，避免调用方各写一半。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from loguru import logger

from src.chat.process_log import serialize_process
from src.services.chat_lock import acquire_session_lock, release_session_lock
from src.chat.streaming import (
    StreamingRunManager,
    _subscribe_events,
    streaming_manager,
)
from src.config.const import SESSION_LOCK_TTL  # noqa: F401  # TTL 由 chat_lock 使用
from src.infra.llm.request_context import RequestContext
from src.infra.llm.trace_context import (
    current_request_ctx,
    current_session_id,
    current_trace_id,
)
from src.services.agent_service import _run_generation
from src.services.app_service import AppService
from src.utils.sse import SSEErrorEvent, SSEDoneEvent, SSEEvent, to_sse


class TurnBusy(Exception):
    """同一会话已有进行中的生成（调用方译为用户可见结果）。"""


@dataclass
class TurnHandle:
    """一轮生成句柄。

    Attributes:
        session_id: 会话 ID（站点与通道共用）
        events: 结构化事件流（终止态由任务生命周期提供）
    """

    session_id: str
    events: AsyncIterator[SSEEvent]


async def _run_with_finalize(
    svc: AppService,
    session_id: str,
    kb_id: str,
    partial_holder: dict,
    answer_builder: Callable,
    manager: StreamingRunManager,
    abort_signal: asyncio.Event,
    release_lock: Callable[[], None],
    ctx: RequestContext,
    trace_id: str = "",
) -> None:
    """后台任务主体：跑生成，完成后按结果收尾落库，finally 释放锁并注销。

    **从 src/api/chat.py 的 _run_with_finalize 整段移入，函数体逐字不改**
    （参数、三支收尾、contextvar set/reset 顺序全部保持）。移入后仅新增
    文件级 import；不要改动逻辑，否则站点行为会变。
    """
    raise NotImplementedError("把 api/chat.py 的 _run_with_finalize 函数体整段移入")


async def start_turn(
    svc: AppService,
    *,
    session_id: str,
    kb_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
    agent: str = "",
    title: str | None = None,
    abort_signal: asyncio.Event | None = None,
) -> TurnHandle:
    """启动一轮生成，返回事件流句柄（站点与通道共用）。

    Args:
        svc: AppService
        session_id: 会话 ID
        kb_id: 知识库 ID（空串表示不检索）
        query: 用户文本
        user_id: 用户 ID（企微侧为派生 UUID，站点为登录用户）
        deep_thinking: 深度思考开关
        agent: 智能体预设名
        title: 会话标题（None → query[:20]；仅首次落库生效）
        abort_signal: 取消信号（None 时内部新建）

    Returns:
        TurnHandle：其 events 为结构化 SSEEvent 流

    Raises:
        TurnBusy: 同一会话已有进行中的生成（闸门未获得）
        Exception: 落库前置失败等编排前置错误（与站点现值一致）
    """
    raise NotImplementedError("按 Task 3 Step 3 说明实现")
```

**实现 `start_turn` 的精确步骤**（严格照做，按 design D12）：

1. `await svc.set_chat_repo()`
2. `if streaming_manager.is_running(session_id): raise TurnBusy`
3. `redis = svc.chat_manager._redis`；`lock_held = False`；若 `redis is not None`：`try: lock_held = await acquire_session_lock(redis, session_id)` `except Exception: logger.warning(...)`（跳过锁），`else:` 若 `not lock_held: raise TurnBusy`
4. **把 3–8 步包进 `try/except`**，任何异常时先释放已取的锁（`if lock_held: await release_session_lock(redis, session_id)`）再 `raise`（**预留释放**要求，见 design D12）
5. `await svc.save_session_async(session_id, title if title is not None else query[:20], kb_id, user_id)`
6. `await svc.save_user_async(session_id, kb_id, query)`
7. `try: subscription, launch_ctx = await svc.agent_service.stream_chat(kb_id, session_id, query, deep_thinking, agent=agent)` `except Exception as e:` → 记 `logger.exception`，释放锁，**返回**一个产 `SSEErrorEvent(str(e))` + `SSEDoneEvent(trace_id=...)` 的轻量 async generator 的 `TurnHandle`（**不抛**，保站点 200+SSE）
8. `partial_holder = {"text": "", "sources": []}`；`signal = abort_signal if abort_signal is not None else asyncio.Event()`；`ctx = launch_ctx["ctx"]`；`ctx.abort_signal = signal`
9. `user_id = current_user_id.get()`（**移入本文件后需 import `current_user_id`**；站点由中间件注入，通道由调用方 set）
10. 定义 `answer_builder`（逐字搬自 `api/chat.py` 的 `_stream_rag_response.answer_builder`）
11. `task = asyncio.create_task(_run_with_finalize(svc, launch_ctx["session_id"], launch_ctx["kb_id"], partial_holder, answer_builder, streaming_manager, signal, release_lock_cb, ctx, trace_id=current_trace_id.get() or ""))`
12. `streaming_manager.register(launch_ctx["session_id"], task, signal)`；`task.add_done_callback(...)` 两条（释放锁 + 注销），逐字搬自 `api/chat.py`
13. `events = _subscribe_events(launch_ctx["session_id"], streaming_manager, max_idle=None)`
14. `return TurnHandle(session_id=launch_ctx["session_id"], events=events)`

其中 `release_lock_cb` 与 `_acquire/释放` 的包装逐字搬自 `api/chat.py`（含 `nonlocal lock_held` 的幂等释放），只是把 `_release_session_lock(_redis, sid)` 换成 `release_session_lock(...)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_turn_runner.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/turn_runner.py tests/services/test_turn_runner.py
git commit -m "feat(services): 新增 start_turn 单一编排入口（下沉自 api/chat，D12）"
```

---

### Task 4: `api/chat.py` 改为调用 `start_turn`（站点行为不变）

**Files:**
- Modify: `src/api/chat.py`（删已搬走的编排，改为薄层）
- Test: `tests/api/test_chat_stream*.py`（既有用例，不改断言）

**Interfaces:**
- Consumes: `src.services.turn_runner.start_turn` / `TurnBusy`（Task 3）
- Produces: `POST /api/chat/stream` 行为不变（409 / SSE 序列 / 响应头）

- [ ] **Step 1: 先跑既有用例建立基线**

Run: `POSTGRES_HOST=localhost pytest tests/api/ -q`
Expected: 记录通过与失败清单（作为"行为不变"的对照）

- [ ] **Step 2: 改写 `api/chat.py`**

把 `_stream_rag_response` / `_run_with_finalize` / `_acquire_session_lock` / `_release_session_lock` **全部删除**（已分别下沉到 Task 2/3），端点内改为：

```python
@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatStreamRequest, svc: AppService = Depends(get_app_service)):
    """流式 RAG 问答端点 — 返回 SSE 事件流（编排已下沉 services，见 D12）。"""
    session_id = body.session_id
    kb_id = body.kb_id
    query = body.query
    deep_thinking = body.deep_thinking
    agent = body.agent
    user_id = getattr(request.state, "user_id", "") if request else ""

    try:
        handle = await start_turn(
            svc,
            session_id=session_id,
            kb_id=kb_id,
            query=query,
            user_id=user_id,
            deep_thinking=deep_thinking,
            agent=agent,
        )
    except TurnBusy:
        raise HTTPException(409, "当前会话正在处理中")

    async def _frames() -> AsyncGenerator[str, None]:
        async for event in handle.events:
            yield to_sse(event)

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

> ⚠ 若既有用例/前端依赖"请求开始即落 `user` 消息"或 409 的**特定触发顺序**（注册表预检在前、Redis 锁在后），需在 Task 3 的 `start_turn` 内保持同样顺序——本阶段以 Step 1 的基线为准，出现差异即回 Task 3 调整，不得改站点断言。

- [ ] **Step 3: 跑既有用例确认无回归**

Run: `POSTGRES_HOST=localhost pytest tests/api/ -q`
Expected: 与 Step 1 基线**逐条一致**

- [ ] **Step 4: 全量回归**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 与创建 worktree 时的基线一致（环境性缺夹具失败除外）

- [ ] **Step 5: 前端 SSE 冒烟（人工）**

在 worktree 用 `scripts/dev-worktree.sh up --slot 2` 起本地后端，浏览器打开对话页发一条消息，确认：流式逐字出现、引用正常、结束无悬挂。记录结果。

- [ ] **Step 6: 提交**

```bash
git add src/api/chat.py
git commit -m "refactor(api): chat_stream 改为调用 services.start_turn（站点行为不变）"
```

---

## 后续阶段（指针，本文件不展开）

| 阶段 | 内容 | 依赖 | 备注 |
|---|---|---|---|
| 2 | 桥接 handler：会话 UUIDv5、msgid 去重、trace_id、喂入 `start_turn`、澄清回填 | 阶段 1 | `src/channels/wecom/{handler,dedup}.py` |
| 3 | 投影层 `WeComPresenter`（快照/节流/上限/保活/引用/兜底/footer） | 阶段 2 | **依赖 Spike E1/E7/E10 结论**；若 E1 反证为追加，须先改投影 spec 再实施 |
| 4 | 驱动可靠性补丁：认证等待、被顶停重连、`_drivers` 语义拆分 | 阶段 2 | `long_connection.py` + `wecom_service.py`；依赖 Spike E3/E4 |
| 5 | Spike 验证与校正（按你的要求后置） | 阶段 1–4 | 组 0 的 E1–E11 与**确认点 A**；结论回写 design/spec |

> **风险提示**：阶段 3/4 建立在 E1（快照语义）、E3（认证等待）、E4（被顶）、E7（6min/保活）、E10（trace 承载）之上。先实现、后 Spike 时，这些点**必须**留出"按结论校正"的窗口——design 已为每条给了退化分支（D5 变更点、D9 footer 强制开启、D16 兜底）。

---

## 自检结论

- **Spec 覆盖**：本阶段对应 design **D12/D20** 与 spec「入站消息喂入站点同款 Agent 管线」的"闸门/失败语义"两条；其余需求属阶段 2–4（见上表指针），无遗漏。
- **占位符扫描**：Task 3 的 `_run_with_finalize` 与 `answer_builder` 标记为"逐字搬移"并给出**唯一来源**（`api/chat.py` 现有实现）与精确改名清单——这是搬移指令而非占位符；`start_turn` 主体给出 14 步精确步骤。
- **类型一致性**：`TurnBusy` / `TurnHandle` / `start_turn` 签名在 Task 3 定义、Task 4 消费，一致；`acquire_session_lock` / `release_session_lock` 在 Task 2 定义、Task 3 消费，一致。
