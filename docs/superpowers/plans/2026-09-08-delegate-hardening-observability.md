# delegate-hardening-observability Implementation Plan（core，先行）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 fork 子代理（delegate_task + skill）执行对用户与日志可观测：过程增量 SSE 进"领域专家分析"折叠区、轮次结构化日志、thinking 跟随请求级 deep_thinking、idle/total/turn/cancel 分层防失控，并落地长 fork 的 SSE 主流续流前提（根治旧"静默→收流→卡死"）。

**Architecture:** ① SSE 主流订阅空闲与任务存活解耦（`max_idle=None`），前端干净 EOF 未收终态时按 `lastSeq` resume；② `RequestContext` 增 `deep_thinking`/`delegate_id`/`fork_stop_reason`，executor 消费 `sub_agent.astream_events(v2)` 聚合增量并转发 delegate 事件到 `ctx.clarify_channel`，用 `wait_for`+事件空闲检查实现三层防失控；③ `agent_service._convert_event` 共享转换器处理 delegate dict → 新增 `SSEDelegateEvent`（含 from_payload/SSEEvent 联合链式注册）+ `[delegate]` 日志前缀/事件；④ 前端 AI 气泡内新增 delegate-progress 折叠区。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph astream_events v2 / asyncio / 原生 JS（无框架）。仓库规则见 CLAUDE.md。

**Spec:** `docs/openspec/changes/delegate-hardening-observability/`（proposal/design.md/specs/{sse-stream-resilience,delegate-progress-observability,delegate-execution-controls}/spec.md/tasks.md）
**前端设计稿:** `docs/design/pages/chat-delegate-progress-2026-09-07.md`（前端任务须读）

## Global Constraints

以下约束对该 change 的所有 task 隐式生效（来自 spec / CLAUDE.md / change design）：

- 事件/日志链一条线改完，缺任一步即 import/resume 崩：`log_events.LOG_PREFIXES` + `Event` 成员 + `log_event_specs.EVENT_SPECS`（import 期 `_validate_registry` 断言）→ `sse.py` 序列化 `to_sse` / `from_payload` / `SSEEvent` 联合。改动需一并补 `tests/utils/test_sse_roundtrip.py` 的 CASES。
- 防污染不变量：scope=delegate 事件永不写入主 `full_answer`/主 token 流/落库；保留 `var_child_runnable_config` 隔离；既有泄漏回归 `test_fork_subagent_events_do_not_leak_to_outer_stream` 保留并改造。
- 中断原因词表只用 `DelegateStopReason`（`normal/idle/total/turn/failed/cancelled`，const 定义），契约/文档/前端文案不得另起原因词。
- fork 中断（idle/total/turn）统一返回 `SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT` 给主 agent；取消（cancelled）走 `asyncio.CancelledError` 传播（`_run_with_finalize` 取消收尾路径）。
- 常量进 `src/config/`：`settings.py` 放环境可调参数，`const.py` 放固定阈值/文案/枚举；SSE 交互文案统一挂 `SSEInteractionTexts` class。
- 实现前先读 CLAUDE.md 的「规则」「代码注释标准」；dataclass 字段必须行内注释（来源/范围/用途）；函数必须 docstring。
- 质量门禁：`pytest tests/ -v` 全绿、`ruff check .` 无错误、`pyright src/` 不新增 error、无遗留 `print()`/TODO。
- 提交粒度 = 本 plan 的 Task（conventional commits，如 `feat(streaming): ...` / `feat(skills): ...`）。
- 依赖顺序：本 change（core）**先于** task-board 合并（共享 `sse.py`/`from_payload`/`LOG_PREFIXES`/`EventSpec`/`chat.html`/`api_contract.md` 文件链）；chat-temperature-policy 无共享文件，可随时并行。合并前勿让 task-board 改动落地同一批文件。

---

### Task A: SSE 主流空闲收流与任务存活解耦

**Files:**
- Modify: `src/chat/streaming.py:22-59`（`_subscribe_events` 支持 `max_idle=None`）
- Modify: `src/services/agent_service.py:646`（`stream_chat` 主 POST 订阅传 `max_idle=None`）
- Test: `tests/chat/test_streaming.py`（追加两个测试）

**Interfaces:**
- Consumes: `StreamingRunManager`（`streaming.py` 现类）、`SSEErrorEvent`、`SSEInteractionTexts.RESUME_TIMEOUT_TEXT`
- Produces: `_subscribe_events(session_id, manager, after_seq=0, max_idle: float | None = 180.0)` —— `max_idle=None` 表示不按空闲收流（终态由任务生命周期提供，`has_terminal()` 为 True 即 return）

- [ ] **Step 1: 写失败测试**

`tests/chat/test_streaming.py` 现有 fixture `mgr` 已提供 StreamingRunManager（见文件头 fixture）。追加：

```python
async def test_subscribe_without_idle_keeps_flowing_until_terminal(mgr):
    """主 POST 流：max_idle=None 时长静默不断流，终态到达后正常返回。"""
    from src.chat.streaming import _subscribe_events
    from src.config.const import SSEInteractionTexts

    mgr.add_event("s1", "status", {"stage": "agent", "message": "thinking"})  # 非终态
    collected = []
    consumer = asyncio.create_task(_collect(_subscribe_events("s1", mgr, max_idle=None), collected))
    await asyncio.sleep(0.9)  # 远大于旧 0.3*loop 的判定节拍，仍不应因空闲收流
    # 预置的 status 事件会被消费者首轮消费，故不能断言空——只断言期间无超时错误/终态
    assert not any(
        isinstance(ev, SSEErrorEvent)
        and ev.error == SSEInteractionTexts.RESUME_TIMEOUT_TEXT
        for ev in collected
    )
    mgr.add_event("s1", "done", {"trace_id": ""})
    await asyncio.wait_for(consumer, timeout=2)
    assert any(getattr(ev, "type", "") == "done" for ev in collected)
    assert not any(
        isinstance(ev, SSEErrorEvent) and ev.error == SSEInteractionTexts.RESUME_TIMEOUT_TEXT
        for ev in collected
    )


async def test_subscribe_with_idle_still_times_out_when_no_terminal(mgr):
    """resume 端点（保留默认 max_idle）：无终态仍按空闲返回续传超时错误。"""
    from src.chat.streaming import _subscribe_events
    from src.config.const import SSEInteractionTexts
    from src.utils.sse import SSEErrorEvent

    mgr.add_event("s1", "status", {"stage": "agent", "message": "thinking"})
    events = []
    async for ev in _subscribe_events("s1", mgr, max_idle=0.6):
        events.append(ev)
    assert any(isinstance(ev, SSEErrorEvent) and ev.error == SSEInteractionTexts.RESUME_TIMEOUT_TEXT for ev in events)
```

需要的辅助 `_collect`（同文件模块级，取事件直到生成器结束）:

```python
async def _collect(agen, into):
    async for ev in agen:
        into.append(ev)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/chat/test_streaming.py -v`
Expected: FAIL —— `_subscribe_events(..., max_idle=None)` 内 `idle_loops * 0.3 > max_idle` 对 None 抛 `TypeError`（或第一个测试未按期结束）。

- [ ] **Step 3: 修改 `_subscribe_events`**

`src/chat/streaming.py`：

```python
async def _subscribe_events(
    session_id: str,
    manager: StreamingRunManager,
    after_seq: int = 0,
    max_idle: float | None = 180.0,
) -> AsyncGenerator[SSEEvent, None]:
    """SSE 消费者（事件对象版）：回放缓冲中 seq>after_seq 的事件并 tail 新事件直到终态。

    Args:
        session_id: 会话 ID
        manager: StreamingRunManager
        after_seq: 起始 seq（新一轮 POST 为 0，同页重连为 lastSeq）
        max_idle: tail 空闲超时秒数（无新事件超时返回续传超时 error）；
            None = 不按空闲收流，终态由任务生命周期（done/error）提供，
            仅在 manager.has_terminal() 为 True 时结束。主 POST 流用 None，
            resume 端点保留默认值 180 作"无任务僵尸续接"兜底。

    Yields:
        SSEEvent: 从缓冲还原的事件对象（token / citation / status / error / done），
            已注入 seq 属性，序列化时 to_sse 会将其写入 data（缓冲 payload 不变）
    """
    emitted = after_seq
    idle_loops = 0
    while True:
        pending = manager.get_events_since(session_id, emitted)
        if pending:
            idle_loops = 0
            for seq, etype, payload in pending:
                emitted = max(emitted, seq)
                event = from_payload(etype, payload)
                # 注入帧序列号，to_sse 序列化进 data（缓冲 payload 不变）
                event.seq = seq
                yield event
        else:
            if manager.has_terminal(session_id):
                return
            idle_loops += 1
            if max_idle is not None and idle_loops * 0.3 > max_idle:
                yield SSEErrorEvent(SSEInteractionTexts.RESUME_TIMEOUT_TEXT)
                return
            await asyncio.sleep(0.3)
```

（改动点：签名 `max_idle: float | None = 180.0`；空闲判定加 `max_idle is not None and` 前缀。）

- [ ] **Step 4: 主 POST 订阅传 `max_idle=None`**

`src/services/agent_service.py` 末行（现 646 行）`return _subscribe_events(session_id, streaming_manager), launch_context` 改为：

```python
        # 主 POST 订阅不按 180s 空闲收流（长静默由任务生命周期收口，含 ask_user
        # 等待、fork 长跑等合法静默）；resume 端点（sessions/events）保留空闲兜底
        return _subscribe_events(session_id, streaming_manager, max_idle=None), launch_context
```

确认兼容性：`_run_with_finalize`（`src/api/chat.py:152-249`）三支路径（正常 done / 取消 done(cancelled) / 异常 error）均已写终态，故主 POST 流 `has_terminal()` 终会为 True，不会无限挂起。

- [ ] **Step 5: 运行全部相关测试**

Run: `pytest tests/chat/test_streaming.py tests/services/test_agent_service.py tests/api/test_streaming_endpoints.py -v`
Expected: PASS（含既有注册表/缓冲生命周期测试）。

- [ ] **Step 6: 提交**

```bash
git add src/chat/streaming.py src/services/agent_service.py tests/chat/test_streaming.py
git commit -m "feat(streaming): decouple main POST SSE subscription from idle cutoff"
```

---

### Task B: 前端干净 EOF 自动续接（onClose → resume）

**Files:**
- Modify: `deploy/nginx/html/chat.html:1538-1639`（`buildStreamHandlers` 增加 `onClose`）
- Test: `tests/...` 无——本 Task 是纯前端行为，后续由 playwright 冒烟覆盖（Task E / 验收）

**Interfaces:**
- Consumes: 既有 `fetchStream`（`chat.html:1488-1516`，干净 EOF 已调用 `handlers.onClose && handlers.onClose()`）、`state.current`/`STATE.STREAMING`/`state.lastSeq`、`resumeStream(sessionId, afterSeq)`（`chat.html:1648`）
- Produces: `buildStreamHandlers()` 返回对象新增 `onClose` 回调

- [ ] **Step 1: 在 `buildStreamHandlers` 返回值中新增 `onClose`**

`src/.../chat.html` `buildStreamHandlers`（约 1635 行 `onError: (e) => { onStreamError(e); }` 之后）追加：

```js
    onClose: () => {
      // 干净 EOF：主 POST 流若在 STREAMING 态未收终态（done/error），说明服务端
      // 因长静默/代理掐断收流但任务仍存活——按 lastSeq 自动续接（终态必达前不卡死输入框）。
      // 已收终态（state.current 已被 done/error 切回 IDLE）的 EOF 不续接，正常收尾。
      if (state.current === STATE.STREAMING) {
        streamRetryCount = 0;
        resumeStream(state.sessionId, state.lastSeq);
      }
    },
```

注意：`onClose` 与 `onError` 会先后触发时，`onError` 内的 `onStreamError` 已做指数退避重连，`state.current` 仍 STREAMING；此处仅在 `state.current === STATE.STREAMING` 时续接，避免与 onError 重试竞态重复——`onStreamError` 设置 `state.current = STATE.STREAMING` 后调度退避重连；若 EOF 先来而 onError 后到（abort 场景），`resumeStream` 内部会先 abort 旧 controller，行为收敛。保持现状的 retryTimer 清理逻辑不变。

- [ ] **Step 2: 静态自检**

Run: `node --check` 不可用（HTML 内嵌）→ 人工确认括号闭合、`onClose` 与 `onError` 同级；打开 `docs/design/pages/chat-delegate-progress-2026-09-07.md` 对照（sse-stream-resilience：无新增视觉）。

- [ ] **Step 3: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "fix(chat): resume stream on clean EOF when no terminal received"
```

---

### Task C: 上下文与配置准备

**Files:**
- Modify: `src/infra/llm/request_context.py:21-56`（`RequestContext` 增字段）
- Modify: `src/config/settings.py`（Skill 委派分区追加可调默认）
- Modify: `src/config/const.py:80-94`（delegate 护栏区；新增 `DelegateStopReason`、默认 turn 上限、中断文案；**不删旧常量**，删除统一放 Task I）
- Modify: `src/services/agent_service.py:634-636`（`stream_chat` 建 ctx 处注入 `ctx.deep_thinking`）
- Test: `tests/config/test_delegate_const.py`、`tests/infra/llm/test_request_context.py`、`tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `RequestContext.deep_thinking: bool`、`RequestContext.delegate_id: str`、`RequestContext.fork_stop_reason: str | None`
  - `settings.DELEGATE_MAX_IDLE_S: float`（默认 60）、`settings.DELEGATE_TOTAL_TIMEOUT_S: float`（默认 240）、`settings.DELEGATE_TOTAL_TIMEOUT_THINKING_S: float`（默认 600）
  - `const.DelegateStopReason`（str Enum：`NORMAL/IDLE/TOTAL/TURN/FAILED/CANCELLED`）、`const.DELEGATE_DEFAULT_MAX_TURNS = 5`
  - `SSEInteractionTexts.DELEGATE_INTERRUPT_TEXT: str = "领域专家分析中断 · {reason}"`、`SSEInteractionTexts.DELEGATE_REASON_TEXT: dict[str, str]`（idle→"空闲超时"、total→"超时"、turn→"轮次上限"、failed→"失败"、cancelled→"已取消"）
  - 移除 `const.DELEGATE_TIMEOUT`（原 120 单值总超时被 total 分档取代）

- [ ] **Step 1: 改 `RequestContext`**

`src/infra/llm/request_context.py` dataclass 中追加字段（带行内注释）：

```python
    deep_thinking: bool = False  # 请求级深思考开关（来源：chat/stream 请求入口 deep_thinking 参数；范围：请求内只读；用途：fork 未声明 thinking 时决定 enable_thinking）
    delegate_id: str = ""  # 当前活跃委派 id（来源：delegate_task fork 分支生成短 uuid；范围：单次委派期间有效；用途：delegate start/增量/end 事件与日志贯穿标识；无活跃委派为空串）
    fork_stop_reason: str | None = None  # 最近一次 fork 停止原因（来源：executor 中断时写入 DelegateStopReason 值；范围：委派期间有效；用途：delegate_task 终态区分 normal 与中断、task 注册表终态；None=正常完成或未执行）
```

- [ ] **Step 2: 改 `settings.py`**

在 `# ====== Skill 委派 ======` 分区（`SKILLS_DIR` 之后，约 228 行）追加：

```python
# fork 子代理事件级空闲阈值（秒）：超时无任一增量事件（reasoning/content/工具）即断，
# 正常长思考为流式增量不误杀，仅完全静默才断
DELEGATE_MAX_IDLE_S: float = float(os.getenv("DELEGATE_MAX_IDLE_S", "60"))
# fork 子代理总时长保险丝（秒，deep_thinking=false 档）；deep_thinking=true 走下一条
DELEGATE_TOTAL_TIMEOUT_S: float = float(os.getenv("DELEGATE_TOTAL_TIMEOUT_S", "240"))
# fork 子代理总时长保险丝（秒，deep_thinking=true 档）：深思考长题放宽
DELEGATE_TOTAL_TIMEOUT_THINKING_S: float = float(
    os.getenv("DELEGATE_TOTAL_TIMEOUT_THINKING_S", "600")
)
```

- [ ] **Step 3: 改 `const.py`**

① delegate 护栏区（现 85-87 行 `DELEGATE_TIMEOUT`）**旁**新增（注意：本 Task 不删除 `DELEGATE_TIMEOUT`——executor 在 Task E 才移除其引用，统一删除与 `STAGE_DELEGATE`/`DELEGATE_STATUS_*` 一道放 Task I）：

```python
DELEGATE_DEFAULT_MAX_TURNS = 5  # fork 零工具默认 turn 上限（防御；开放工具后由 skill max_iterations 覆盖）
```

② 同区新增枚举（类外定义，供 executor / delegate_task / task-board 共用）：

```python
class DelegateStopReason(str, Enum):
    """fork 结束原因统一枚举（delegate end ok/reason 与 task 注册表终态共用词表）。

    取值：normal=正常完成 / idle=流空闲超时 / total=总时长保险丝 /
    turn=轮次上限 / failed=执行异常 / cancelled=请求取消。
    契约/文档/前端文案不得另起原因词（spec delegate-execution-controls）。
    """

    NORMAL = "normal"
    IDLE = "idle"
    TOTAL = "total"
    TURN = "turn"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

文件头补 `from enum import Enum`。旧 `DELEGATE_STATUS_START/END`、`STAGE_DELEGATE`、`DELEGATE_TIMEOUT` 的删除统一放 **Task I**（届时 executor/agent_service 已改完，无任何引用；本 Task 与 Task D 均保留旧常量避免中间态 import 崩）。

③ `SSEInteractionTexts` 内（delegate 文案区，保留 `DELEGATE_TIMEOUT_TEXT`）追加：

```python
    # fork 中断/委派终态文案：中断时以 {reason} 填 DELEGATE_REASON_TEXT 的中文短词
    DELEGATE_INTERRUPT_TEXT: str = "领域专家分析中断 · {reason}"
    # DelegateStopReason → 前端可读中文短词（双通道：文字 + 颜色）
    DELEGATE_REASON_TEXT: dict[str, str] = {
        "idle": "空闲超时",
        "total": "超时",
        "turn": "轮次上限",
        "failed": "失败",
        "cancelled": "已取消",
    }
```

④ 更新注释「delegate 护栏」来源说明为三层防失控 change。

- [ ] **Step 4: 改 `tests/config/test_delegate_const.py`**

既有断言（`DELEGATE_TIMEOUT > 0` 等）**保留**，仅追加枚举与文案断言：

```python
def test_delegate_stop_reason_enum():
    from src.config.const import DelegateStopReason

    assert [r.value for r in DelegateStopReason] == [
        "normal", "idle", "total", "turn", "failed", "cancelled",
    ]


def test_delegate_interrupt_texts():
    from src.config.const import DelegateStopReason, SSEInteractionTexts

    assert DelegateStopReason.NORMAL.value == "normal"
    msg = SSEInteractionTexts.DELEGATE_INTERRUPT_TEXT.format(
        reason=SSEInteractionTexts.DELEGATE_REASON_TEXT["idle"]
    )
    assert msg.startswith("领域专家分析中断")
    assert "空闲超时" in msg
    # 已知原因词均可取到文案（缺词会 KeyError，堵住枚举外字符串）
    for r in DelegateStopReason:
        if r is not DelegateStopReason.NORMAL:
            assert SSEInteractionTexts.DELEGATE_REASON_TEXT[r.value]
```

- [ ] **Step 5: 改 `tests/infra/llm/test_request_context.py`**

追加：

```python
def test_request_context_new_fields_defaults():
    from src.infra.llm.request_context import RequestContext

    ctx = RequestContext(session_id="s1")
    assert ctx.deep_thinking is False
    assert ctx.delegate_id == ""
    assert ctx.fork_stop_reason is None
```

- [ ] **Step 6: stream_chat 入口注入 `ctx.deep_thinking`（B4 关键——漏则 thinking 跟随/600s 档线上不可达）**

`src/services/agent_service.py` `stream_chat` 建 ctx 后（现 634-636 行 `ctx = RequestContext(...)` / `ctx.kb_id = kb_id` / `ctx.kb_bound = bool(kb_id)`）追加：

```python
        ctx.deep_thinking = deep_thinking  # fork thinking 跟随的请求级来源（executor 读取）
```

`tests/services/test_agent_service.py` 追加（复用文件头部 `_make_service()` fixture，stream_chat 不触发图调用，仅需 `service._graph = Mock()`）：

```python
@pytest.mark.asyncio
async def test_stream_chat_sets_ctx_deep_thinking():
    """deep_thinking 应写入 launch_ctx 的 RequestContext（fork thinking 跟随来源）。"""
    service, _ = _make_service()
    service._graph = Mock()
    agen, launch_ctx = await service.stream_chat("", "s1", "q", deep_thinking=True)
    assert launch_ctx["ctx"].deep_thinking is True
    assert launch_ctx["deep_thinking"] is True
    # 默认 False 档
    agen2, launch_ctx2 = await service.stream_chat("", "s1", "q")
    assert launch_ctx2["ctx"].deep_thinking is False
```

> 说明：`_collect_events`（test_agent_service.py:132-200）已消费 `launch_ctx["deep_thinking"]` 进 `_run_generation` 的 initial_state；`ctx.deep_thinking` 是**新增**的 fork 消费通道，二者并存（state 通道管主 agent enable_thinking，ctx 通道管 fork thinking 跟随与 total 档）。

- [ ] **Step 7: 运行测试**

Run: `pytest tests/config/test_delegate_const.py tests/infra/llm/test_request_context.py tests/services/test_agent_service.py tests/chat/ -v`
Expected: PASS（`DELEGATE_TIMEOUT` 尚未删除，executor 仍引用，无 import 破坏；Task I 再做清理）。

- [ ] **Step 8: 提交**

```bash
git add src/infra/llm/request_context.py src/config/settings.py src/config/const.py src/services/agent_service.py tests/config/test_delegate_const.py tests/infra/llm/test_request_context.py tests/services/test_agent_service.py
git commit -m "feat(config): add request deep_thinking/delegate fields and fork guard settings"
```

---

### Task D: 事件链——`[delegate]` 前缀、delegate 事件注册（sse/log_events 同步）

> 一条链（Event/EventSpec/LOG_PREFIXES + sse 序列化/from_payload/SSEEvent 联合）一次改完，任一漏改即 import/resume 崩。

**Files:**
- Modify: `src/core/log_events.py:18-20,38-172`（LOG_PREFIXES 增 `delegate`；Event 增三成员）
- Modify: `src/core/log_event_specs.py:29-286`（EVENT_SPECS 登记三条）
- Modify: `src/utils/sse.py`（新 `SSEDelegateEvent` + to_sse case + from_payload 分支 + SSEEvent 联合）
- Test: `tests/utils/test_sse_roundtrip.py`、`tests/utils/test_sse.py`

**Interfaces:**
- Produces:
  - Event: `DELEGATE_START = "delegate start"`、`DELEGATE_MODEL_TURN = "delegate model turn"`、`DELEGATE_END = "delegate end"`
  - prefix `delegate`（EventSpec prefix）
  - `SSEDelegateEvent`（type="delegate"）字段：`delegate_id: str`、`skill: str = ""`、`action: str`（"start"|"delta"|"end"）、`kind: str = ""`（"thinking"|"content"，delta 用）、`delta: str = ""`、`ok: bool = True`（end 用）、`reason: str = ""`（end 用，DelegateStopReason 值）、`seq`

- [ ] **Step 1: 写失败测试（roundtrip + 未知类型）**

`tests/utils/test_sse_roundtrip.py` 的 CASES 追加：

```python
    SSEDelegateEvent(
        delegate_id="d1a2b3c4",
        skill="finance-analyst",
        action="start",
    ),
    SSEDelegateEvent(
        delegate_id="d1a2b3c4",
        skill="finance-analyst",
        action="delta",
        kind="thinking",
        delta="先梳理营收口径…",
    ),
    SSEDelegateEvent(
        delegate_id="d1a2b3c4",
        skill="finance-analyst",
        action="end",
        ok=False,
        reason="idle",
    ),
```

import 加 `SSEDelegateEvent`。

`tests/utils/test_sse.py` 追加未知类型仍 raise：

```python
def test_from_payload_unknown_delegate_ok():
    from src.utils.sse import from_payload

    ev = from_payload("delegate", {"delegate_id": "d", "action": "delta", "kind": "thinking", "delta": "x"})
    assert ev.action == "delta"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_sse_roundtrip.py tests/utils/test_sse.py -v`
Expected: FAIL（ImportError: SSEDelegateEvent not defined / unknown sse event type）。

- [ ] **Step 3: 实现 `SSEDelegateEvent` + 序列化链**

`src/utils/sse.py`：

在 `SSEReasoningDeltaEvent` 后新增 dataclass：

```python
@dataclass
class SSEDelegateEvent:
    """fork 子代理过程事件（start/delta/end 一体）。

    action=start：委派开始（含 delegate_id/skill）；
    action=delta：过程增量（kind=thinking|content，delta=增量文本）；
    action=end：委派结束（ok 区分正常/中断，reason 取 DelegateStopReason 值，
    normal 时 ok=True、reason=""）。
    """

    delegate_id: str  # 本次委派唯一 id（短 uuid，贯穿 start/增量/end 与 task execution 条目）
    action: str = "delta"  # start | delta | end
    skill: str = ""  # 命中的 skill 名
    kind: str = ""  # delta 用：thinking（思考增量） | content（正文增量）
    delta: str = ""  # delta 用：增量文本
    ok: bool = True  # end 用：正常完成
    reason: str = ""  # end 用：中断原因（DelegateStopReason 值，非 normal 时非空）
    type: str = "delegate"  # SSE 事件名（event: delegate）
    seq: int | None = field(
        default=None, compare=False, repr=False
    )  # SSE 帧序列号（消费者注入；None 不序列化，不参与相等比较）

    def payload_for_buffer(self) -> dict:
        """返回与 to_sse 的 data: 同构的缓冲 payload。"""
        return {
            "delegate_id": self.delegate_id,
            "action": self.action,
            "skill": self.skill,
            "kind": self.kind,
            "delta": self.delta,
            "ok": self.ok,
            "reason": self.reason,
        }
```

`SSEEvent` 联合追加 `| SSEDelegateEvent`。模块底部加序列化函数与 `to_sse` case：

```python
def sse_delegate(event: SSEDelegateEvent) -> str:
    """构建 delegate 事件（fork 子代理过程增量/start/end）。"""
    data: dict = {
        "delegate_id": event.delegate_id,
        "action": event.action,
        "skill": event.skill,
        "kind": event.kind,
        "delta": event.delta,
        "ok": event.ok,
        "reason": event.reason,
    }
    if event.seq is not None:
        data["seq"] = event.seq
    return f"event: delegate\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

`to_sse` 内（`case SSEReasoningDeltaEvent` 前）追加：

```python
        case SSEDelegateEvent(
            delegate_id=did,
            action=action,
            skill=skill,
            kind=kind,
            delta=delta,
            ok=ok,
            reason=reason,
            seq=seq,
        ):
            return sse_delegate(
                SSEDelegateEvent(
                    delegate_id=did,
                    action=action,
                    skill=skill,
                    kind=kind,
                    delta=delta,
                    ok=ok,
                    reason=reason,
                    seq=seq,
                )
            )
```

`from_payload` 内（`if etype == "model_info"` 后、`raise` 前）追加：

```python
    if etype == "delegate":
        return SSEDelegateEvent(
            delegate_id=payload["delegate_id"],
            action=payload.get("action", "delta"),
            skill=payload.get("skill", ""),
            kind=payload.get("kind", ""),
            delta=payload.get("delta", ""),
            ok=payload.get("ok", True),
            reason=payload.get("reason", ""),
        )
```


- [ ] **Step 4: 实现日志事件注册链**

`src/core/log_events.py`：

`LOG_PREFIXES` 增 `"delegate"`：

```python
LOG_PREFIXES: frozenset[str] = frozenset(
    {"retrieval", "verify", "agent", "session", "db", "llm", "cli", "app", "delegate"}
)
```

`Event` 枚举在 `MODEL_TURN` 之后（[agent] 区末尾）或新增注释分组（值唯一即可，前缀在 spec 表决定）追加：

```python
    # [delegate] fork 子代理委派（delegate-hardening-observability）
    DELEGATE_START = "delegate start"
    DELEGATE_MODEL_TURN = "delegate model turn"
    DELEGATE_END = "delegate end"
```

`src/core/log_event_specs.py` `EVENT_SPECS` 追加：

```python
    # [delegate] fork 子代理委派（delegate-hardening-observability）
    "delegate start": EventSpec(
        "delegate start",
        "delegate",
        "info",
        ("delegate_id", "skill", "thinking", "task_len"),
    ),
    "delegate model turn": EventSpec(
        "delegate model turn",
        "delegate",
        "info",
        ("delegate_id", "model", "usage_in", "usage_out", "usage_estimated", "latency_ms"),
    ),
    "delegate end": EventSpec(
        "delegate end",
        "delegate",
        "info",
        ("delegate_id", "ok", "reason", "elapsed_ms", "result_len"),
    ),
```

（import 期 `_validate_registry` 将自动断言 Event/EventSpec/prefix 一致。）

- [ ] **Step 5: 运行测试**

Run: `pytest tests/utils/test_sse_roundtrip.py tests/utils/test_sse.py -v`
Expected: PASS。并确认 `python -c "import src.core.log_events"` 无 AssertionError。

- [ ] **Step 6: 提交**

```bash
git add src/core/log_events.py src/core/log_event_specs.py src/utils/sse.py tests/utils/test_sse_roundtrip.py tests/utils/test_sse.py
git commit -m "feat(sse): add delegate event type and [delegate] log registry chain"
```

---

### Task E: `SkillRecord.max_iterations` 消费点与 executor 防失控骨架（含 thinking 继承）

> 本 Task 建 executor 新签名骨架；Task F 填入 astream 消费与三层防失控细节。为避免中间态过大，两步合成一个 Task：一次完成 executor astream 重构（TDD）。

**Files:**
- Modify: `src/agents/skills/executor.py`（大改：_resolve_fork_llm thinking 跟随 + astream 消费 + 三层防失控 + delegate delta 推送 + 日志）
- Modify: `src/agents/skills/delegate_task.py:78-97`（fork 分支 delegate 事件 start/end + delegate_id + 日志）
- Test: `tests/agents/skills/test_skill_executor.py`、`tests/agents/skills/test_delegate_task.py`

**Interfaces:**
- Consumes: `current_request_ctx`、`ctx.deep_thinking/delegate_id/fork_stop_reason/abort_signal/clarify_channel`、`settings.DELEGATE_MAX_IDLE_S/DELEGATE_TOTAL_TIMEOUT_S/DELEGATE_TOTAL_TIMEOUT_THINKING_S`、`const.DelegateStopReason/DELEGATE_DEFAULT_MAX_TURNS`、`Event.DELEGATE_START/MODEL_TURN/END`、`estimate_usage`（`src/rag/stream.py:14`）
- Produces:
  - `SkillExecutor._resolve_fork_llm(record)`：record.thinking 为 None 且 ctx 存在时 extra_body.enable_thinking=ctx.deep_thinking（不再无条件复用主实例）
  - `SkillExecutor._run_fork(record, task) -> str`：仍返回 str；中断收敛为 timeout 文本或 CancelledError
  - `SkillExecutor._consume_fork_events(sub_agent, record, task, ctx, max_turns) -> str`
  - delegate 事件 dict 协议（经 clarify_channel 投递）：`{"type": "delegate", "action": "start"|"delta"|"end", "delegate_id", "skill", "kind", "delta", "ok", "reason"}`

- [ ] **Step 1: 写 executor 失败测试（逐段），先替换模块 helper**

测试文件 `tests/agents/skills/test_skill_executor.py` 改造思路：既有用例用 `fake_sub.ainvoke` mock，全部改为 `astream_events` 返回 async generator。**公共 import**：下述新测试大量使用 `RequestContext`/`current_request_ctx`（src.infra.llm.request_context）与 `AIMessageChunk`（langchain_core.messages），既有文件已 import `AIMessageChunk`；`RequestContext/current_request_ctx` 建议文件顶部一次性 import（与 `tests/agents/skills/test_delegate_task.py` 的做法一致）。文件级新增 helper：
```python
def _event(kind, chunk=None, output=None):
    """构造 langgraph v2 事件 dict（fake astream_events 事件源元素）。"""
    data = {}
    if chunk is not None:
        data["chunk"] = chunk
    if output is not None:
        data["output"] = output
    return {"event": kind, "name": "agent", "metadata": {"langgraph_node": "agent"}, "data": data}


def _agen(*items):
    async def gen():
        for it in items:
            yield it
    return gen()


def _fake_sub_agent(*items):
    """astream_events 版 fake sub-agent（替换旧 fake_sub.ainvoke mock）。"""
    fake = MagicMock()
    fake.astream_events = lambda *a, **k: _agen(*items)
    return fake
```

改造 `test_fork_reuses_main_llm_when_model_empty`：

```python
@pytest.mark.asyncio
async def test_fork_reuses_main_llm_when_model_empty():
    """fork 且未声明 model/thinking：复用主 agent llm 实例；经 astream_events 聚合结果。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    rec = _record(
        context=SkillContext.FORK,
        agent_prompt="你是财务建模专家",
        inline_prompt=None,
        model=None,
        thinking=None,
    )
    chunk = AIMessageChunk(content="分析结果：营收下降 20%")
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=chunk),
        _event("on_chat_model_end", output=AIMessage(content="分析结果：营收下降 20%")),
    )
    with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub) as mock_create:
        out = await exe.execute(rec, task="分析年报")

    args, kwargs = mock_create.call_args
    assert kwargs["tools"] == []
    assert kwargs["prompt"] == "你是财务建模专家"
    assert args[0] is main_llm  # 无 model/thinking → 复用主实例
    assert "分析结果" in out
```

新增 thinking 跟随（spec delegate-execution-controls：skill 未声明 thinking 跟随请求级 deep_thinking）测试：

```python
@pytest.mark.asyncio
async def test_fork_thinking_follows_ctx_deep_thinking_true():
    """thinking 未声明且 ctx.deep_thinking=True：新建 llm 带 enable_thinking=True。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    ctx = RequestContext(session_id="s1")
    ctx.deep_thinking = True
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub),
            patch("src.agents.skills.executor.get_llm", return_value=fake_llm) as mock_get_llm,
        ):
            main_llm = MagicMock()
            main_llm.model_name = "main-model"
            exe = SkillExecutor(main_llm=main_llm)
            rec = _record(
                context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None,
                model=None, thinking=None,
            )
            await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    kwargs = mock_get_llm.call_args.kwargs
    assert kwargs["model"] == "main-model"
    assert kwargs["extra_body"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_fork_thinking_follows_ctx_deep_thinking_false():
    """thinking 未声明且 ctx.deep_thinking=False：新建 llm 带 enable_thinking=False（不落模型默认思考）。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    fake_llm = MagicMock()
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    ctx = RequestContext(session_id="s1")  # deep_thinking 默认 False
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub),
            patch("src.agents.skills.executor.get_llm", return_value=fake_llm) as mock_get_llm,
        ):
            main_llm = MagicMock()
            main_llm.model_name = "main-model"
            exe = SkillExecutor(main_llm=main_llm)
            rec = _record(
                context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None,
                model=None, thinking=None,
            )
            await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert mock_get_llm.call_args.kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_fork_thinking_none_without_ctx_reuses_main_llm():
    """无请求上下文时 thinking=None：维持原行为——复用主 agent llm（不新建）。"""
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="分析")),
        _event("on_chat_model_end", output=AIMessage(content="分析")),
    )
    with (
        patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub) as mock_create,
        patch("src.agents.skills.executor.get_llm") as mock_get_llm,
    ):
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None,
            model=None, thinking=None,
        )
        await exe.execute(rec, task="分析")
    mock_get_llm.assert_not_called()
    args, _kwargs = mock_create.call_args
    assert args[0] is main_llm
```

新增 total 超时测试（覆盖 wait_for 总保险丝）：

```python
@pytest.mark.asyncio
async def test_fork_total_timeout_interrupts_with_reason():
    """总时长超时（deep_thinking=False 档 240s，测试收紧）：中断并返回超时文案。"""
    import src.agents.skills.executor as exec_mod
    from src.config.const import SSEInteractionTexts

    async def _never(*a, **k):
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    fake = MagicMock()
    fake.astream_events = _never
    ctx = RequestContext(session_id="s1")  # deep_thinking=False → 走默认档
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.create_react_agent", return_value=fake),
            patch.object(exec_mod.settings, "DELEGATE_TOTAL_TIMEOUT_S", 0.05),
        ):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None)
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "total"
```

新增 idle 超时与 turn 上限测试：

```python
@pytest.mark.asyncio
async def test_fork_idle_timeout_interrupts():
    """事件级空闲超时：两事件间隔超 DELEGATE_MAX_IDLE_S → reason=idle。"""
    import src.agents.skills.executor as exec_mod
    from src.config.const import SSEInteractionTexts

    async def _slow_events(*a, **k):
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="思考片段"))
        await asyncio.sleep(5)  # 超过被收紧的空闲阈值
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="后续"))

    fake = MagicMock()
    fake.astream_events = _slow_events
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.create_react_agent", return_value=fake),
            patch.object(exec_mod.settings, "DELEGATE_MAX_IDLE_S", 0.1),
        ):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None)
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "idle"


@pytest.mark.asyncio
async def test_fork_turn_limit_interrupts():
    """turn 上限：模型 start 事件超 max_iterations（测试设 1）→ reason=turn。"""
    import src.agents.skills.executor as exec_mod
    from src.config.const import SSEInteractionTexts

    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_start"),  # 第二轮
        _event("on_chat_model_end", output=AIMessage(content="x")),
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(
                context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None,
                max_iterations=1,
            )
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    assert ctx.fork_stop_reason == "turn"


@pytest.mark.asyncio
async def test_fork_cancel_aborts_with_cancelled():
    """请求取消：ctx.abort_signal 置位 → fork 抛 CancelledError、reason=cancelled。"""
    import src.agents.skills.executor as exec_mod
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    ctx = RequestContext(session_id="s1")
    ctx.abort_signal.set()
    token = current_request_ctx.set(ctx)
    try:
        fake_sub = _fake_sub_agent(
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="a")),
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="b")),
        )
        with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None)
            with pytest.raises(asyncio.CancelledError):
                await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    assert ctx.fork_stop_reason == "cancelled"
```

新增 delta 推送与节流/截断/usage 测试（需 ctx.clarify_channel 有消费方或直接 peek）：

```python
def _drain_channel(ctx):
    out = []
    while not ctx.clarify_channel.empty():
        out.append(ctx.clarify_channel.get_nowait())
    return out


@pytest.mark.asyncio
async def test_fork_pushes_delegate_delta_events():
    """fork 增量经 ctx.clarify_channel 投 delegate delta（thinking/content）；带 delegate_id。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    chunk1 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "思考A"})
    chunk2 = AIMessageChunk(content="正文B")
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=chunk1),
        _event("on_chat_model_stream", chunk=chunk2),
        _event("on_chat_model_end", output=AIMessage(content="正文B", usage_metadata={"input_tokens": 10, "output_tokens": 5})),
    )
    ctx = RequestContext(session_id="s1")
    ctx.delegate_id = "abc123"
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
            exe = SkillExecutor(main_llm=MagicMock())
            rec = _record(context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None)
            out = await exe.execute(rec, task="分析")
    finally:
        current_request_ctx.reset(token)
    deltas = [it for it in _drain_channel(ctx) if it.get("type") == "delegate"]
    kinds = [it["kind"] for it in deltas if it["action"] == "delta"]
    assert "thinking" in kinds and "content" in kinds
    assert any(it.get("delegate_id") == "abc123" for it in deltas)
    assert "正文B" in out
```

（usage_metadata 结构：真实 AIMessage 为 object；此处 MagicMock 环境里传 dict 即可，executor 用 `.get()` 兼容 dict/None。若 executor 按属性访问，测试改用 `types.SimpleNamespace`。以 executor 实现为准调整本测试的 usage 载体。）

截断回归改造（`test_fork_result_truncated`）：fake 改为 `astream_events` 产出长文本 chunk：

```python
@pytest.mark.asyncio
async def test_fork_result_truncated():
    """fork 结果超 DELEGATE_RESULT_LIMIT → 截断并带总字数提示。"""
    long_text = "字" * 3000
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content=long_text)),
        _event("on_chat_model_end", output=AIMessage(content=long_text)),
    )
    with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(context=SkillContext.FORK, agent_prompt="人格", inline_prompt=None)
        out = await exe.execute(rec, task="分析")
    assert "3000" in out
    assert out.startswith(SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX[:10])
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_skill_executor.py -v`
Expected: 大量 FAIL（astream_events/ainvoke 不一致、ctx 字段、reason 等）。

- [ ] **Step 3: 实现 `_resolve_fork_llm` thinking 跟随**

`src/agents/skills/executor.py` 修改 `_resolve_fork_llm`（当前 125-148 行）：当 `record.model is None and record.thinking is None` 时不再无条件复用主实例——新增规则：thinking 未声明但 ctx 存在时新建实例携带 `enable_thinking=ctx.deep_thinking`：

```python
    def _resolve_fork_llm(self, record: SkillRecord):
        """解析 fork 子代理的 llm 实例（model/thinking/deep_thinking 消费）。

        Args:
            record: fork SkillRecord

        Returns:
            llm 实例：
            - 声明 model → get_llm(model=record.model, extra_body?) 新建
            - 未声明 model 但 (thinking 声明 或 ctx.deep_thinking 可及) →
              get_llm(model=主 agent model_name, extra_body.enable_thinking=...)
              新建（新建实例才能携带 enable_thinking）
            - 两者均不可得 → 复用主 agent llm 实例
            enable_thinking 取值优先级：record.thinking（显式声明）>
              ctx.deep_thinking（请求级，仅 thinking 未声明时跟随）> 模型默认
            例外兜底：需新建实例携带 enable_thinking，但 record.model 为空且主 agent
              model_name 不可得（测试替身/缺省）→ 退回复用主实例，防落到默认 LLM_MODEL
              造成与主模型漂移（thinking 跟随在该场景降级为模型默认）
        """
        ctx = current_request_ctx.get()
        thinking = record.thinking
        if thinking is None and ctx is not None:
            thinking = ctx.deep_thinking

        if (
            thinking is not None
            and record.model is None
            and self._main_model_name is None
        ):
            core_logging.log_event(
                Event.DELEGATE_SKIP,
                reason="no_main_model_name_thinking_fallback",
            )
            return self._main_llm

        if record.model is None and thinking is None:
            return self._main_llm

        kwargs: dict = {}
        if record.model is not None:
            kwargs["model"] = record.model
        elif self._main_model_name is not None:
            kwargs["model"] = self._main_model_name
        if thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": thinking}
        return get_llm(**kwargs)
```

- [ ] **Step 4: 实现 `_run_fork` + `_consume_fork_events`（三层防失控 + delta 推送 + 日志）**

`executor.py` import 更新：

```python
import asyncio
import time

from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt import create_react_agent

from src.agents.skills.models import SkillContext, SkillRecord
from src.config import settings
from src.config.const import (
    DELEGATE_DEFAULT_MAX_TURNS,
    DELEGATE_RESULT_LIMIT,
    DelegateStopReason,
    SSEInteractionTexts,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx
from src.models import get_llm  # 模块级 import：测试需 patch executor.get_llm
from src.rag.stream import estimate_usage
```

替换 `_run_fork` 与新增 `_consume_fork_events` / `_push_delegate`：

```python
    async def _run_fork(self, record: SkillRecord, task: str) -> str:
        """fork 执行：astream 级消费 + 三层防失控 + thinking 继承。

        Args:
            record: fork SkillRecord
            task: 任务描述（子代理初始 HumanMessage）

        Returns:
            子代理聚合纯文本（截断对齐原实现）；因 idle/total/turn 中断统一返回
            DELEGATE_TIMEOUT_TEXT 并把 reason 写入 ctx.fork_stop_reason；
            请求取消（abort_signal 置位）抛 asyncio.CancelledError（reason=cancelled），
            由调用方（delegate_task → 主任务）按取消路径收尾。

        Raises:
            asyncio.CancelledError: ctx.abort_signal 置位（请求取消）
        """
        llm = self._resolve_fork_llm(record)
        sub_agent = create_react_agent(
            llm,
            tools=[],  # 零工具硬保证（design D7）：防递归 + 不污染主 ctx
            prompt=record.agent_prompt,
        )
        ctx = current_request_ctx.get()
        max_turns = record.max_iterations or DELEGATE_DEFAULT_MAX_TURNS
        deep_thinking = ctx.deep_thinking if ctx is not None else False
        total_timeout = (
            settings.DELEGATE_TOTAL_TIMEOUT_THINKING_S
            if deep_thinking
            else settings.DELEGATE_TOTAL_TIMEOUT_S
        )
        # 隔离子代理回调传播：不 reset 会经 var_child_runnable_config 把外层
        # callback handler 传进 create_react_agent，子代理 LLM 事件泄漏到外层
        # graph.astream_events（SSE token 污染 + full_answer 累积子代理原文）。
        # reset 后子代理事件只走其自身 handler，由本方法显式接入（scope=delegate）。
        token = var_child_runnable_config.set(None)
        try:
            try:
                text = await asyncio.wait_for(
                    self._consume_fork_events(sub_agent, record, task, ctx, max_turns),
                    timeout=total_timeout,
                )
            except asyncio.TimeoutError:
                # 总时长保险丝（total）：wait_for 取消内层后在此收敛。
                # 注意：abort 引发的 CancelledError 不会被 wait_for 吞成 TimeoutError——
                # 非超时路径的任务自身 CancelledError 原样透传（Python 3.11 wait_for 语义），
                # 因此 cancel 会继续上抛到 delegate_task 的 except asyncio.CancelledError
                # （该 CancelledError 已在 _consume_fork_events 置 fork_stop_reason=cancelled）。
                # 端到端验证见 Task J；勿在此加 except CancelledError 防吞（会破坏取消语义）
                if ctx is not None:
                    ctx.fork_stop_reason = DelegateStopReason.TOTAL
                return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
            return self._truncate(text)
        finally:
            var_child_runnable_config.reset(token)

    async def _consume_fork_events(
        self, sub_agent, record: SkillRecord, task: str, ctx, max_turns: int
    ) -> str:
        """迭代子代理 astream_events(v2)：聚合正文/思考、防失控、转发 delegate delta。

        Args:
            sub_agent: create_react_agent 返回的子代理（astream_events 事件源）
            record: fork SkillRecord
            task: 子代理初始任务文本
            ctx: 当前请求上下文（可能为 None；None 时仅聚合不推送/不防失控）
            max_turns: turn 上限（skill max_iterations 或默认）

        Returns:
            聚合后的子代理最终正文纯文本（不含 reasoning）

        Raises:
            asyncio.CancelledError: ctx.abort_signal 置位（reason=cancelled）
        """
        idle_timeout = settings.DELEGATE_MAX_IDLE_S
        model_starts = 0
        model_turn_started: float | None = None
        last_activity = time.monotonic()
        content_parts: list[str] = []
        text_parts: list[str] = []  # content 全量累计（含中途 assistant 文本）
        last_flush = time.monotonic()
        pending_think = ""
        pending_content = ""
        delegate_id = ctx.delegate_id if ctx is not None else ""
        skill = record.name
        started_at = time.monotonic()

        config = {"tags": ["delegate"], "metadata": {"scope": "delegate"}}
        agen = sub_agent.astream_events(
            {"messages": [HumanMessage(content=task)]},
            config=config,
            version="v2",
        )
        # 请求级取消观察任务：与 __anext__ 竞速（FIRST_COMPLETED）。静默等待期间
        # abort 置位也能即时中断（若只靠每事件轮询，需等下一事件或 idle 60s 才感知，
        # 违背"置位即中断、原因=cancelled"语义，spec delegate-execution-controls）
        abort_task: asyncio.Task | None = None
        if ctx is not None:
            abort_task = asyncio.create_task(ctx.abort_signal.wait())
        try:
            while True:
                if ctx is not None and ctx.abort_signal.is_set():
                    ctx.fork_stop_reason = DelegateStopReason.CANCELLED
                    raise asyncio.CancelledError
                remaining = idle_timeout - (time.monotonic() - last_activity)
                if remaining <= 0:
                    # 事件级流空闲 watchdog：完全静默超时（正常长思考为流式增量不误杀）
                    if ctx is not None:
                        ctx.fork_stop_reason = DelegateStopReason.IDLE
                    return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                get_next = asyncio.ensure_future(agen.__anext__())
                wait_set: list = [get_next]
                if abort_task is not None:
                    wait_set.append(abort_task)
                done, pending = await asyncio.wait(
                    wait_set,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    # idle 到点（无事件且无 abort）：cancel 悬挂的 next/abort 后收场
                    for t in pending:
                        t.cancel()
                    if ctx is not None:
                        ctx.fork_stop_reason = DelegateStopReason.IDLE
                    return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                if abort_task is not None and abort_task in done:
                    # 请求取消优先于 idle/turn：原因=cancelled，抛 CancelledError 走主任务取消路径
                    if get_next in pending:
                        get_next.cancel()
                    ctx.fork_stop_reason = DelegateStopReason.CANCELLED
                    raise asyncio.CancelledError
                try:
                    ev = get_next.result()
                except StopAsyncIteration:
                    break  # 事件源正常收尾
                except asyncio.CancelledError:
                    raise

                last_activity = time.monotonic()
                kind = ev.get("event", "")
                data = ev.get("data") or {}
                if kind == "on_chat_model_start":
                    model_starts += 1
                    model_turn_started = time.monotonic()
                    if model_starts > max_turns:
                        if ctx is not None:
                            ctx.fork_stop_reason = DelegateStopReason.TURN
                        return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                    continue
                if kind == "on_chat_model_end":
                    await self._record_delegate_model_turn(
                        data, delegate_id, model_turn_started
                    )
                    continue
                if kind != "on_chat_model_stream":
                    continue
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                content = getattr(chunk, "content", "") or ""
                reasoning = (chunk.additional_kwargs or {}).get("reasoning_content", "")
                if reasoning:
                    pending_think += reasoning
                if content:
                    text_parts.append(content)
                    pending_content += content
                # 聚合节流（约 80ms 或单方向累积超 600 字一次 flush），防高频事件刷屏
                now = time.monotonic()
                if (now - last_flush) >= 0.08:
                    await self._flush_delegate(
                        ctx, delegate_id, skill, pending_think, pending_content
                    )
                    pending_think, pending_content = "", ""
                    last_flush = now
            # 结束：flush 残留
            await self._flush_delegate(
                ctx, delegate_id, skill, pending_think, pending_content
            )
            text = "".join(text_parts)
            return text
        finally:
            if abort_task is not None:
                abort_task.cancel()
            # 显式关闭子代理事件流：防悬空 asyncgen 依赖 GC/loop-shutdown
            # （正常 break / idle / turn 返回 / total 被外层 wait_for 取消 / abort raise 均达此）
            await agen.aclose()

    async def _flush_delegate(
        self, ctx, delegate_id: str, skill: str, think: str, content: str
    ) -> None:
        """把累积的思考/正文增量投递为 delegate delta 事件（空则跳过）。"""
        if ctx is None:
            return
        if think:
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "delta",
                    "delegate_id": delegate_id,
                    "skill": skill,
                    "kind": "thinking",
                    "delta": think,
                    "ok": True,
                    "reason": "",
                }
            )
        if content:
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "delta",
                    "delegate_id": delegate_id,
                    "skill": skill,
                    "kind": "content",
                    "delta": content,
                    "ok": True,
                    "reason": "",
                }
            )

    async def _record_delegate_model_turn(
        self, data: dict, delegate_id: str, turn_started: float | None
    ) -> None:
        """记录 delegate model turn 日志（usage 缺失走 estimate_usage 兜底并标注）。

        Args:
            data: on_chat_model_end 事件的 data（含 output）
            delegate_id: 本次委派 id（来自 ctx.delegate_id）
            turn_started: 本次模型调用起始 monotonic 时间（None = 无 on_chat_model_start，
                忽略 latency）
        """
        from langchain_core.messages import BaseMessage

        output = data.get("output")
        usage_in = 0
        usage_out = 0
        usage_estimated = False
        model = ""
        if isinstance(output, BaseMessage):
            meta = output.usage_metadata
            if isinstance(meta, dict) and (
                meta.get("input_tokens") or meta.get("output_tokens")
            ):
                usage_in = int(meta.get("input_tokens") or 0)
                usage_out = int(meta.get("output_tokens") or 0)
            else:
                est = estimate_usage([output], SkillExecutor._output_text(output))
                usage_in = est.prompt_tokens
                usage_out = est.completion_tokens
                usage_estimated = True
            resp_meta = output.response_metadata
            if isinstance(resp_meta, dict):
                model = resp_meta.get("model_name", "")
                if not isinstance(model, str) or not model:
                    model = resp_meta.get("model", "")
            if not isinstance(model, str):
                model = ""
        latency_ms = 0
        if turn_started is not None:
            latency_ms = int((time.monotonic() - turn_started) * 1000)
        core_logging.log_event(
            Event.DELEGATE_MODEL_TURN,
            delegate_id=delegate_id,
            model=model,
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated="true" if usage_estimated else "false",
            latency_ms=latency_ms,
        )

    @staticmethod
    def _output_text(output) -> str:
        """从 model end output（AIMessage）取文本 content 供 usage 估算。"""
        if hasattr(output, "content"):
            c = output.content
            if isinstance(c, str):
                return c
            return str(c)
        return ""
```

> 说明：`delegate end` 终态日志（含 ok/reason 语义最完整）由 `delegate_task` 的 finally 统一记录（Task G），executor 正常/中断均不重复记；`delegate model turn`（usage/latency/usage_estimated）已由上述 `_record_delegate_model_turn` 在 `on_chat_model_end` 分支记录。

`_run_fork` 保留截断调用 `self._truncate(text)`（含 `DELEGATE_RESULT_LIMIT`），`_last_message_text` 不再使用——保留引用检查后删除该私有方法（git 提交内删除）。模块 docstring 的超时段落同步更新。

- [ ] **Step 5: 运行 executor 测试并修复**

Run: `pytest tests/agents/skills/test_skill_executor.py -v`
Expected: 除已被 Step1 改写/新增的用例通过外，剩余旧用例（model override、thinking 声明、无 model_name 等）也改为 astream 事件源并通过。逐段修正：model override 用例的 fake_sub 换成 `_fake_sub_agent`；usage 载体按实现调整。**删除 `test_fork_timeout_returns_timeout_text`**（其 mock `ainvoke`+`patch(exec_mod.DELEGATE_TIMEOUT)` 已被新 `test_fork_total_timeout_interrupts_with_reason` 取代；`DELEGATE_TIMEOUT` 常量将在 Task I 删除）。

- [ ] **Step 6: 提交（后端执行器半程——前端 start/end 尚在 Task G）**

```bash
git add src/agents/skills/executor.py tests/agents/skills/test_skill_executor.py
git commit -m "feat(skills): stream fork via astream_events with idle/total/turn guards and thinking inheritance"
```

---

### Task G: delegate_task——delegate 事件 start/end + delegate_id + 终态语义

**Files:**
- Modify: `src/agents/skills/delegate_task.py:51-97`
- Modify: `src/services/agent_service.py:161-315`（`_convert_event`：delegate dict 分支；移除旧 status/delegate 子分支）
- Test: `tests/agents/skills/test_delegate_task.py`、`tests/services/test_agent_service.py:1028-1056`

**Interfaces:**
- Consumes: `uuid`、`const.DelegateStopReason`、`Event.DELEGATE_START/DELEGATE_END`、`current_request_ctx`
- Produces: delegate dict 事件（start/end）投递；fork 不再经旧 `status` dict；`ctx.fork_stop_reason` 每次 fork 前置位清除、finally 读取并复位

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_delegate_task.py` 改造 `test_fork_hit_pushes_start_and_end_status`：

```python
@pytest.mark.asyncio
async def test_fork_hit_pushes_delegate_start_end_with_id():
    """fork 命中：推 delegate start + end 事件（带 delegate_id；end ok=True/reason=''）。"""
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="专家分析")),
        _event("on_chat_model_end", output=AIMessage(content="专家分析")),
    )

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
            out = await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    assert "专家分析" in out
    items = []
    while not ctx.clarify_channel.empty():
        items.append(ctx.clarify_channel.get_nowait())
    delegate_items = [it for it in items if it.get("type") == "delegate"]
    actions = [it["action"] for it in delegate_items]
    # 注意：executor 会在 content chunk 到达时 flush 出 kind=content 的 delta，
    # 故中间可能存在 delta——只断言首 start、末 end
    assert actions[0] == "start" and actions[-1] == "end"
    start, end = delegate_items
    assert start["delegate_id"] and end["delegate_id"] == start["delegate_id"]
    assert start["skill"] == "finance-analyst"
    assert end["ok"] is True and end["reason"] == ""
    # 旧 status 通道不再投递
    assert not [it for it in items if it.get("type") == "status"]


@pytest.mark.asyncio
async def test_fork_interrupted_end_carries_reason():
    """fork 中断（idle）：delegate end 携带 ok=False/reason=idle（区分"完成"）。"""
    import src.agents.skills.executor as exec_mod
    from src.config.const import SSEInteractionTexts

    async def _slow(*a, **k):
        yield _event("on_chat_model_stream", chunk=AIMessageChunk(content="a"))
        await asyncio.sleep(5)

    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with (
            patch("src.agents.skills.executor.create_react_agent", return_value=MagicMock(astream_events=_slow)),
            patch.object(exec_mod.settings, "DELEGATE_MAX_IDLE_S", 0.1),
        ):
            out = await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
    items = []
    while not ctx.clarify_channel.empty():
        items.append(ctx.clarify_channel.get_nowait())
    end = [it for it in items if it.get("action") == "end"][-1]
    assert end["ok"] is False and end["reason"] == "idle"
```

（测试文件需补 **`import asyncio`**（用例用 `asyncio.sleep`/`pytest.raises(asyncio.CancelledError)`）及 `AIMessageChunk/AIMessage`，helper `_event`/`_fake_sub_agent`/`_drain_channel` 可从 `test_skill_executor.py` 复制——`_drain_channel` 定义于该文件 Task E Step 1 的 delta 测试段，Task J 测试也用到。不要 import `AIMessage` 到 delegate_task 生产代码——它不被使用。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_delegate_task.py -v`
Expected: FAIL（fork 仍推 status、无 delegate 事件）。

- [ ] **Step 3: 实现 delegate_task fork 分支**

`delegate_task.py` import 区**仅新增**以下（模块既有 `tool`/`BaseModel`/`Field`/`SkillExecutor`/`SkillContext`/`SkillRegistry`/`SSEInteractionTexts`/`current_request_ctx` 已导入，勿重复添加；**必须含 `import asyncio`**——fork 分支新代码用 `except asyncio.CancelledError`，该模块现状无 asyncio import，漏加即 NameError）：

```python
import asyncio
import time
import uuid

from src.config.const import DelegateStopReason
from src.core import logging as core_logging
from src.core.log_events import Event
```

`delegate_task` 内 fork 分支（现 78-97 行）整体替换：

```python
        ctx = current_request_ctx.get()
        if ctx is None:
            return await executor.execute(record, task)
        # fork 可观测（design D6/D8）：分配 delegate_id 贯穿 start/增量/end；
        # ctx.fork_stop_reason 由 executor 中断时写，finally 读取并复位
        delegate_id = uuid.uuid4().hex[:8]
        ctx.delegate_id = delegate_id
        ctx.fork_stop_reason = None
        await ctx.clarify_channel.put(
            {
                "type": "delegate",
                "action": "start",
                "delegate_id": delegate_id,
                "skill": record.name,
                "kind": "",
                "delta": "",
                "ok": True,
                "reason": "",
            }
        )
        core_logging.log_event(
            Event.DELEGATE_START,
            delegate_id=delegate_id,
            skill=record.name,
            thinking="true" if ctx.deep_thinking else "false",
            task_len=len(task),
        )
        started_at = time.monotonic()
        result_len = 0  # 正常路径更新为 len(out)；异常/取消早退保持 0（finally 记录用，勿用 dir() hack）
        stop_reason: str | None = None
        ok = True
        try:
            try:
                out = await executor.execute(record, task)
                result_len = len(out)
            except asyncio.CancelledError:
                stop_reason = DelegateStopReason.CANCELLED
                ok = False
                raise
            except Exception:  # noqa: BLE001  # 异常同样收敛为 failed 终态后上抛（ToolNode 转错误回喂）
                stop_reason = DelegateStopReason.FAILED
                ok = False
                raise
            # 正常返回但 executor 曾中断（idle/total/turn）→ reason 已写入 ctx
            stop_reason = ctx.fork_stop_reason
            ok = stop_reason is None
            return out
        finally:
            reason = DelegateStopReason.NORMAL if ok else (stop_reason or DelegateStopReason.FAILED)
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "end",
                    "delegate_id": delegate_id,
                    "skill": record.name,
                    "kind": "",
                    "delta": "",
                    "ok": ok,
                    "reason": reason.value,
                }
            )
            core_logging.log_event(
                Event.DELEGATE_END,
                delegate_id=delegate_id,
                ok="true" if ok else "false",
                reason=reason.value,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                result_len=result_len,
            )
            ctx.delegate_id = ""
            ctx.fork_stop_reason = None
```

> `delegate end` 终态日志只由 `delegate_task` finally 统一记录（含 ok/reason/elapsed/result_len，语义最完整）；executor 的 `_consume_fork_events` 正常结束不记 end 日志（Task E 已实现为只 flush 增量返回文本），因此无重复条目。中断路径（idle/total/turn）的 end 日志同样由 delegate_task finally 记 reason，executor 不重复记。

- [ ] **Step 4: `_convert_event` 支持 delegate dict（替换旧 status/delegate 分支）**

`src/services/agent_service.py` `_convert_event`（161-315 行）：

头部 import 增 `SSEDelegateEvent`（from src.utils.sse import 列表）。

① dict 分支：在 `if isinstance(item, dict) and item.get("type") == "status":` 块**前**插入 delegate 处理：

```python
    if isinstance(item, dict) and item.get("type") == "delegate":
        # delegate_task / executor 经 ctx.clarify_channel 投递的委派事件 dict →
        # SSEDelegateEvent（fork 过程增量 start/delta/end；不进主 token 流/full_answer，
        # 防污染不变量由"delegate 仅经本分支转 delegate 事件"保证）
        return [
            SSEDelegateEvent(
                delegate_id=item.get("delegate_id", ""),
                action=item.get("action", "delta"),
                skill=item.get("skill", ""),
                kind=item.get("kind", ""),
                delta=item.get("delta", ""),
                ok=bool(item.get("ok", True)),
                reason=item.get("reason", ""),
            )
        ]
```

② 删除旧 status/delegate 子分支（194-205 行整块 `if stage == SSEInteractionTexts.STAGE_DELEGATE: ...`），保留其余 stage 分支体（非 delegate status 返回 []）。同步：`agent_service.py` 若仍 import `SSEStatusEvent` 用于其他处则保留。

③ `_convert_event` 签名带 scope（spec：共享转换器）：

```python
def _convert_event(
    item: _QueueItem,
    capture: _StreamCapture | None = None,
    scope: str = "main",
) -> list[SSEEvent]:
    """把 queue 中的 item 转成 SSE 事件列表（空列表 = 无需产出）。

    scope 标识事件归属：main = 主图事件（LangGraph astream_events / 澄清通道），
    delegate = fork 子代理事件（executor/delegate_task 经 clarify_channel 投递，
    自带 type=delegate 标记）。delegate 转换不依赖 metadata.langgraph_node=="agent"
    判定，杜绝子代理事件被误当主 token。scope 供后续调用方显式区分，当前实现
    下 graph 事件仅在 scope=="main" 时转换。
    """
    if isinstance(item, dict) and item.get("type") == "delegate":
        return [...]  # 见①
    if scope != "main":
        return []
    ...
```

（`_dual_stream` / `_drain_clarify_channel` / `_run_generation` 调用 `_convert_event` 处不传 scope，维持默认 "main"。）

- [ ] **Step 5: 改造 `tests/services/test_agent_service.py` 的 status 转换测试**

现 1028-1056 行两测试整体替换为 delegate 转换测试：

```python
def test_convert_delegate_dict_to_sse_delegate_event():
    """_convert_event 把 delegate dict 转 SSEDelegateEvent（delta/end 语义）。"""
    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEDelegateEvent

    events = _convert_event(
        {"type": "delegate", "action": "delta", "delegate_id": "d1",
         "skill": "analyst", "kind": "thinking", "delta": "思考A", "ok": True, "reason": ""}
    )
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SSEDelegateEvent)
    assert ev.delegate_id == "d1" and ev.action == "delta"
    assert ev.kind == "thinking" and ev.delta == "思考A"

    end = _convert_event(
        {"type": "delegate", "action": "end", "delegate_id": "d1",
         "skill": "analyst", "kind": "", "delta": "", "ok": False, "reason": "idle"}
    )[0]
    assert end.ok is False and end.reason == "idle"


def test_convert_event_scope_not_main_ignores_graph_events():
    """scope != main 时 graph 事件不转换（显式 scope 隔离，防误归属）。"""
    from src.services.agent_service import _convert_event

    item = {"event": "on_chat_model_stream", "metadata": {"langgraph_node": "agent"},
            "data": {"chunk": type("C", (), {"content": "x", "additional_kwargs": {}})()}}
    assert _convert_event(item, scope="delegate") == []
```

- [ ] **Step 6: 运行测试**

Run: `pytest tests/agents/skills/test_delegate_task.py tests/services/test_agent_service.py tests/utils/test_sse_roundtrip.py -v`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/agents/skills/delegate_task.py src/services/agent_service.py tests/agents/skills/test_delegate_task.py tests/services/test_agent_service.py
git commit -m "feat(delegate): emit delegate start/end events with delegate_id and stop reason"
```

---

### Task H: 前端 AI 气泡结构调整 + delegate-progress 折叠区

> 前置：先读 `docs/design/pages/chat-delegate-progress-2026-09-07.md` §1；产出用 `/frontend-design` skill 校准视觉（样式细节以设计稿与 MASTER 为准，本 Task 给结构与功能实现，颜色/间距可微调）。改动须回写设计文档（防腐，见 change tasks 5.0）。

**Files:**
- Modify: `deploy/nginx/html/chat.html`（CSS 追加；JS：ensureAiBubble/renderAiAnswer 结构、delegate handler、折叠区函数）
- Test: 无单元测试；验收见 Task M（playwright 冒烟）

**Interfaces:**
- Consumes: 既有 `buildStreamHandlers`、`ensureAiBubble`、`streamBubble`、`finalizeAnswer`、`.bubble-row.ai/.bubble-content`
- Produces: `delegate` SSE handler；`_delegateMap`（delegate_id → section）；`ensureAiBubble` 返回的 `.bubble-content` 归属新 `.bubble-main` wrapper

- [ ] **Step 1: CSS——bubble-main wrapper + delegate-progress 块**

`chat.html` `<style>` 内 `.bubble-row.ai .bubble-content` 规则后追加：

```css
  /* AI 气泡内容列包装：正文 + 分析过程折叠区同列（bubble-row 为 flex 横向，
     过程区须在其列内才不破坏布局） */
  .bubble-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
  .bubble-content:empty { display: none; }

  /* 领域专家分析过程折叠区（core change；视觉规格见 design/pages/chat-delegate-progress） */
  .delegate-progress { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .delegate-head {
    width: 100%; display: flex; align-items: center; gap: 8px;
    padding: 8px 12px; background: var(--surface-muted); border: none;
    cursor: pointer; font-family: inherit; font-size: 13px; color: var(--text);
  }
  .delegate-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .delegate-dot.running { background: var(--primary); animation: delegatePulse 1.2s ease-in-out infinite; }
  .delegate-dot.done { background: var(--text-secondary); }
  .delegate-dot.error { background: #EF4444; }
  @keyframes delegatePulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
  .delegate-title { flex: 1; text-align: left; font-weight: 500; }
  .delegate-status { font-size: 11px; color: var(--text-secondary); }
  .delegate-status.error { color: #EF4444; }
  .delegate-chevron { transition: transform 150ms; }
  .delegate-progress.closed .delegate-chevron { transform: rotate(-90deg); }
  .delegate-body { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
  .delegate-progress.closed .delegate-body { display: none; }
  .delegate-think { font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
  .delegate-think summary { cursor: pointer; user-select: none; display: flex; gap: 6px; align-items: center; }
  .delegate-think summary::before { content: ''; width: 3px; height: 12px; background: var(--primary-light); border-radius: 2px; }
  .delegate-think-body { margin: 6px 0 0; padding-left: 9px; border-left: 3px solid var(--primary-light); white-space: pre-wrap; word-break: break-word; }
  .delegate-content { font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
```

> `delegate-head` 语义化用 `<button aria-expanded>`（无障碍，见设计稿）。

- [ ] **Step 2: bubble-main wrapper——改 `ensureAiBubble` 与 `renderAiAnswer`**

`ensureAiBubble`（现 1010-1020 行）HTML 从两段改为含 wrapper：

```js
function ensureAiBubble() {
  if (streamBubble && streamBubble.isConnected) {
    return streamBubble;
  }
  const row = document.createElement('div');
  row.className = 'bubble-row ai';
  row.innerHTML = `
    <div class="bubble-avatar">🤖</div>
    <div class="bubble-main">
      <div class="bubble-content md"></div>
    </div>`;
  chatContainer.appendChild(row);
  streamBubble = row.querySelector('.bubble-content');
  return streamBubble;
}
```

`renderAiAnswer`（历史非流式，988-1001 行）同样套 `.bubble-main`：

```js
function renderAiAnswer(text, interrupted) {
  const div = document.createElement('div');
  div.className = 'bubble-row ai';
  let interruptedTag = '';
  if (interrupted) {
    interruptedTag = '<div class="interrupted-tag">（回答被中断）</div>';
  }
  div.innerHTML = `
    <div class="bubble-avatar">🤖</div>
    <div class="bubble-main">
      <div class="bubble-content md">${renderMarkdown(text)}${interruptedTag}</div>
    </div>`;
  chatContainer.appendChild(div);
  scrollToBottom();
}
```

`finalizeAnswer` 取 host 用 `streamBubble.closest('.bubble-row.ai')` 不变（CSS 后代选择器兼容）；`attachInlineCiteRefs` 内 `.bubble-content.md` 仍可 query。

- [ ] **Step 3: delegate 渲染模块——新增 `delegateSections` 状态与函数**

紧邻 Think 折叠行区（`renderReasoningDelta` 之后、`escapeHtml` 前）插入：

```js
// ── delegate 过程折叠区（core change；按 delegate_id 分节，一次回答多次委派不串流）──
// 中断原因 → 中文短词（与后端 const.SSEInteractionTexts.DELEGATE_REASON_TEXT 同步；
// 后端不随 SSE 下发行内文案，前端维护同一张表，勿只用裸 reason 码展示）
const DELEGATE_REASON_TEXT = {
  idle: '空闲超时',
  total: '超时',
  turn: '轮次上限',
  failed: '失败',
  cancelled: '已取消',
};
// delegate_id → { section, dot, statusEl, thinkBody, contentBody, closedByUser, finished }
const delegateSections = new Map();

function ensureDelegateSection(data) {
  const did = data.delegate_id;
  if (!did) return null;
  let sec = delegateSections.get(did);
  if (sec) return sec;
  const bubble = ensureAiBubble();           // 无正文时也先建 AI 行（正文到达后填充）
  const main = bubble.closest('.bubble-main');
  const section = document.createElement('section');
  section.className = 'delegate-progress';
  section.dataset.did = did;
  const title = escapeHtml(data.skill || '专家') + ' · 领域专家分析';
  section.innerHTML = `
    <button type="button" class="delegate-head" aria-expanded="true">
      <span class="delegate-dot running"></span>
      <span class="delegate-title">${title}</span>
      <span class="delegate-status">正在分析…</span>
      <svg class="delegate-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>
    <div class="delegate-body">
      <details class="delegate-think" open>
        <summary>思考过程</summary>
        <div class="delegate-think-body"></div>
      </details>
      <div class="delegate-content"></div>
    </div>`;
  main.appendChild(section);
  const head = section.querySelector('.delegate-head');
  head.addEventListener('click', () => {
    const closed = section.classList.toggle('closed');
    head.setAttribute('aria-expanded', String(!closed));
  });
  const s = {
    section,
    dot: section.querySelector('.delegate-dot'),
    status: section.querySelector('.delegate-status'),
    thinkBody: section.querySelector('.delegate-think-body'),
    contentBody: section.querySelector('.delegate-content'),
    thinkLen: 0,
    contentLen: 0,
    finished: false,
  };
  delegateSections.set(did, s);
  scrollToBottom();
  return s;
}

function appendDelegateDelta(data) {
  const s = delegateSections.get(data.delegate_id);
  if (!s || s.finished) {
    if (!s) console.warn('delegate delta without open section', data.delegate_id);
    return;
  }
  const delta = data.delta || '';
  if (!delta) return;
  if (data.kind === 'thinking') {
    s.thinkBody.textContent += delta;
    s.thinkLen += delta.length;
  } else {
    s.contentBody.textContent += delta;
    s.contentLen += delta.length;
  }
  scrollToBottom();
}

function finalizeDelegateSection(data) {
  const s = delegateSections.get(data.delegate_id);
  if (!s || s.finished) return;
  s.finished = true;
  const ok = data.ok !== false;
  const reason = data.reason || '';
  if (ok) {
    s.dot.classList.remove('running');
    s.dot.classList.add('done');
    s.status.textContent = '领域专家分析完成';
    s.status.classList.remove('error');
  } else {
    const text = DELEGATE_REASON_TEXT[reason] || reason;
    s.dot.classList.remove('running');
    s.dot.classList.add('error');
    s.status.textContent = '分析中断 · ' + (text || reason);
    s.status.classList.add('error');
  }
  scrollToBottom();
}

// 兜底：本轮收尾（done(cancelled)/error/EOF 终止）时仍未收到 delegate end 的节，
// 置为中断态——fork 取消时 end 事件可能因 drain task 先被 cancel 而丢失（H2），
// 避免前端永远停在"正在分析…"呼吸点
function finalizePendingDelegateSections(reason) {
  for (const [did, s] of delegateSections) {
    if (!s.finished) {
      s.finished = true;
      s.dot.classList.remove('running');
      s.dot.classList.add('error');
      const text = DELEGATE_REASON_TEXT[reason] || reason || '中断';
      s.status.textContent = '分析中断 · ' + text;
      s.status.classList.add('error');
    }
  }
}

function resetDelegateSections() {
  delegateSections.clear();
}
```

- [ ] **Step 4: buildStreamHandlers 增加 `delegate` handler**

`buildStreamHandlers()` 返回对象中（`abstention` 之后）加：

```js
    delegate: (data) => {
      try {
        state.lastSeq = (data.seq !== undefined) ? data.seq : state.lastSeq;
        if (data.action === 'start') {
          ensureDelegateSection(data);
        } else if (data.action === 'delta') {
          appendDelegateDelta(data);
        } else if (data.action === 'end') {
          finalizeDelegateSection(data);
        }
      } catch (err) { /* ignore */ }
    },
```

- [ ] **Step 5: 终态/新轮清理 + 未收 end 兜底（H2）**

① `finalizeAnswer()` 末尾追加 `finalizePendingDelegateSections('')`（此时仍 pending 的节属异常残留，给一个中断收尾）再 `resetDelegateSections();`（正文定型后不再接收本 delegate 增量）。

② `buildStreamHandlers` 的 `done` handler 中，`data.cancelled === true` 分支（恢复输入前）追加 `finalizePendingDelegateSections('cancelled');`；`error` handler 追 `finalizePendingDelegateSections('failed');`——覆盖"fork 取消时 delegate end 事件可能因 drain task 先被 cancel 而丢失"的场景（后端取消路径见 Task E/G）。

③ `showNewPage()` / 新轮 `startStream`（`state.lastSeq = 0` 处）确认已经 clear 的 DOM 场景：`showNewPage` 内 `chatContainer.innerHTML = ''` 已清 DOM；`resetDelegateSections` 在每次 finalize 调用已复位 map。resume 回放路径（刷新后）不重建过程区——`delegate` 事件回放会再触发 handler 重建 DOM 区，属运行期展示（D7），可接受；若刷新后从历史载入则无 delegate 事件，天然不重建。

- [ ] **Step 6: 静态自检 + 提交**

Run: 人工复查 HTML 结构闭合与事件名（`event: delegate` ↔ `handlers.delegate`）。
```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(chat): render delegate progress collapsible sections inside AI answer"
```

---

### Task I: 删除旧 delegate status 通道 + 清理常量与测试残留

**Files:**
- Modify: `src/config/const.py`（删除 `STAGE_DELEGATE`/`DELEGATE_STATUS_START`/`DELEGATE_STATUS_END`/`DELEGATE_TIMEOUT`）
- Modify: `src/agents/skills/executor.py`（docstring 超时段）
- Test: `tests/config/test_delegate_const.py`

- [ ] **Step 1: 清理 const 与测试**

删除 `const.py` 中 `DELEGATE_TIMEOUT`（80-94 行 delegate 护栏区，Task C 已保留用于过渡，现无引用）、`SSEInteractionTexts.STAGE_DELEGATE`、`DELEGATE_STATUS_START`、`DELEGATE_STATUS_END`。全局 grep 确认无残留引用：

Run: `rg "DELEGATE_TIMEOUT|STAGE_DELEGATE|DELEGATE_STATUS" src/ tests/`
Expected: 仅剩 `tests/config/test_delegate_const.py`。更新该文件断言（删 timeout/stage/start/end 断言，保留结果截断/文案等）。

- [ ] **Step 2: 运行回归**

Run: `pytest tests/config/ tests/agents/skills/ tests/services/test_agent_service.py tests/utils/ -v`
Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add src/config/const.py tests/config/test_delegate_const.py src/agents/skills/executor.py
git commit -m "refactor(delegate): remove superseded delegate status channel constants"
```

---

### Task J: fork 请求取消路径贯通（cancel 端点 → executor → delegate_task）

> 后端逻辑已在 Task E/G 实现（abort_signal 置位 → executor 抛 CancelledError、reason=cancelled；delegate_task except CancelledError re-raise）。本 Task 验证端到端行为并补单测确认 `cancel` 语义不被 ToolNode 吞掉。

**Files:**
- Test: `tests/agents/skills/test_skill_executor.py`（补一个真实 delegate_task 层取消用例，若 Task G 测试已覆盖可跳过并标注）
- Verify: `src/agents/skills/delegate_task.py`（CancelledError 不被 `except Exception` 捕获——CancelledError 继承 BaseException，已天然满足；代码审查确认）

- [ ] **Step 1: 审查代码确认 CancelledError 传播**

Run: `python -c "import asyncio; print(issubclass(asyncio.CancelledError, Exception))"`
Expected: False（确认 except Exception 捕获不到，`delegate_task` 的 `except asyncio.CancelledError` 分支必要且正确）。

- [ ] **Step 2: 补端到端单测（Task G 若已有 cancel 用例则标记本 Task 完成）**

若 Task G 未覆盖，在 `tests/agents/skills/test_delegate_task.py` 补：

```python
@pytest.mark.asyncio
async def test_cancel_during_fork_propagates_cancelled_with_end_reason():
    """取消传播：fork 中 abort 置位 → 抛 CancelledError，delegate end 已推 reason=cancelled。"""
    import src.agents.skills.executor as exec_mod
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    ctx = RequestContext(session_id="s1")
    ctx.abort_signal.set()
    token = current_request_ctx.set(ctx)
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)
    try:
        fake_sub = MagicMock(astream_events=lambda *a, **k: _agen(
            _event("on_chat_model_stream", chunk=AIMessageChunk(content="x")),
        ))
        with patch("src.agents.skills.executor.create_react_agent", return_value=fake_sub):
            with pytest.raises(asyncio.CancelledError):
                await tool.ainvoke({"task": "t", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)
    end = [it for it in _drain_channel(ctx) if it.get("action") == "end"][-1]
    assert end["ok"] is False and end["reason"] == "cancelled"
```

- [ ] **Step 3: 运行并提交**

```bash
pytest tests/agents/skills/test_delegate_task.py -v
git add tests/agents/skills/test_delegate_task.py
git commit -m "test(delegate): cancel propagates CancelledError with cancelled end reason"
```

---

### Task K: 防污染回归改造（scope 不变量）

**Files:**
- Modify: `tests/agents/skills/test_skill_executor.py`（`test_fork_subagent_events_do_not_leak_to_outer_stream` 加断言：delegate 增量/文本不进 full_answer）
- Verify: `src/agents/skills/executor.py`（`var_child_runnable_config.set(None)` 保留）

- [ ] **Step 1: 扩展现有泄漏回归**

在 `test_fork_subagent_events_do_not_leak_to_outer_stream`（现有，见文件）追加第三断言（sub-agent 经 ctx.clarify_channel 投递 delegate 事件，full_answer 不含其 delta）：

```python
    # 3. 子代理事件经 ctx.clarify_channel 走 delegate 通道：full_answer 仍不含过程原文
    #    （防污染不变量：scope=delegate 永不写主 token/full_answer/落库内容）
```

若该用例未设 ctx（无 RequestContext），在工具执行包一层 `current_request_ctx` set 并收集 clarify_channel，断言 channel 中 delegate delta 的 delta 文本未出现在 full_answer。

- [ ] **Step 2: 运行并提交**

```bash
pytest tests/agents/skills/test_skill_executor.py -v
git add tests/agents/skills/test_skill_executor.py
git commit -m "test(delegate): keep anti-pollution invariant covering delegate channel"
```

---

### Task L: 文档与登记（contract / 数据流 / 日志 / 术语）

> 知识防腐：文档同步后若设计稿/契约后续变更需更新。

**Files:**
- Modify: `docs/agents/logging-rules.md`（前缀主表登记 `[delegate]`）
- Modify: `docs/agents/api_contract.md`（SSE 事件表新增 delegate 行、委派状态阶段、续接语义）
- Modify: `docs/agents/data-flow.md`（delegate 事件链路）
- Modify: `docs/openspec/changes/agent-delegation-skills/...`（对 `delegate-observability` delta 标 SUPERSEDED，归档防冲突）
- Verify: `docs/agents/glossary.md`（已含 delegate_id/DelegateStopReason/end ok·reason 术语，缺则补）

- [ ] **Step 1: logging-rules.md 登记前缀**

按该文档「前缀主表（开放登记制）」加一行 `delegate`（fork 子代理委派事件）及三事件名（delegate start / delegate model turn / delegate end），并核对 `log_event_specs.py` 中 fields 一致。

- [ ] **Step 2: api_contract.md 同步**

读 `docs/agents/api_contract.md` 现有 SSE 事件表（2.3.1）后：
- 新增 `delegate` 事件行：`action=start|delta|end`、`delegate_id`、`skill`、`kind=thinking|content`、`delta`、`ok`、`reason`（end 语义：ok=true → 完成文案；ok=false 带 reason → "分析中断 · 原因"）
- 更新「delegate 状态阶段」：删除旧无条件 status 完成语义；示例帧换成 delegate 事件
- `2.4.4 sessions/events` 续接语义补充：主 POST 流 `max_idle=None`（不按 180s 空闲收流，终态由任务生命周期给）；resume 保留 180s 空闲错误兜底；前端 `onClose` → `lastSeq` resume

- [ ] **Step 3: data-flow.md / SUPERSEDED 标注**

- `docs/agents/data-flow.md` 补 delegate 事件双通道链路（graph 主循环 + clarify_channel delegate 通道），注明 scope 隔离
- 在 `docs/openspec/changes/agent-delegation-skills/` 的 `delegate-observability` delta（capability spec 或对应文件）标注 **SUPERSEDED by delegate-hardening-observability**（防归档时两批 ADDED 冲突，见 change proposal「与其它 change 的关系」）

- [ ] **Step 4: 提交**

```bash
git add docs/agents/logging-rules.md docs/agents/api_contract.md docs/agents/data-flow.md docs/agents/glossary.md docs/openspec/changes/agent-delegation-skills/
git commit -m "docs(delegate): register [delegate] prefix, delegate SSE contract, and SUPERSEDED marker"
```

---

### Task M: 冒烟与质量门禁（含 playwright）

**Files:**
- Verify: 全量测试 / lint / playwright 冒烟脚本

- [ ] **Step 1: 后端质量门禁**

Run:
```bash
pytest tests/ -v
ruff check .
pyright src/
```
Expected: 全绿（pyright 不引入新 error；存量误报除外）。

- [ ] **Step 2: 冒烟**

- 起服务（`docker compose up -d --build` 或本地 uvicorn）后委托一次 skill：确认前端出现 delegate-progress 节（thinking 折叠/正文流式、运行中呼吸点 → 完成文案）；主回答与 MySQL 落库无子代理过程文本
- 强制中断（delegate 长 idle 或点停止按钮）确认"分析中断 · 原因"、delegate end reason 可辨、输入框不卡死
- 长静默/断线：清空 EOF 未收终态 → `onClose` 自动 resume（lastSeq 续接）无重复卡片
- 一次回答多次委派：多节分列、互不串流
- playwright 冒烟用 `/playwright-cli` 或既有 e2e 目录模式（参考 `docs/agents/cookbook.md` 若有既有用例）

- [ ] **Step 3: openspec validate**

Run: `npx opsx validate delegate-hardening-observability`（如项目 opsx 命令不同以实际为准）Expected: valid。

---

## Self-Review 记录（写 plan 时执行）

- **Spec 覆盖**：
  - sse-stream-resilience 三条需求 → Task A（主流解耦）、Task B（EOF resume）、长任务流量维持由 delegate 聚合事件天然提供（A 中注明，心跳为可选未实现——符合 design D5 非必需）
  - delegate-execution-controls：thinking 跟随 → E；deep_thinking 注入 → C；idle/total/turn → E；abort_signal → E/J；枚举 → C；超时文案与日志 → E/G
  - delegate-progress-observability：scope 共享转换 → G；增量 SSE → E/G；折叠区 → H；delegate_id → G；完成/中断文案 → G+H；轮次日志 → E/G；防污染 → K
  - tasks.md 全部条目映射见各 Task 头；6.4 SUPERSEDED → L
- **Placeholder 扫描**：前端样式数值引用设计稿 token（视觉规格按设计稿执行，非占位符）；playwright 具体命令以既有 cookbook 为准，属验收步骤非代码占位。
- **Type 一致性**：`SSEDelegateEvent` 字段 `action/kind/delta/ok/reason` 在 sse.py（D）、delegate dict 协议（G）、executor dict（E）、_convert_event（G）、前端 handler（H）一致；`DelegateStopReason` 值在 const（C）、executor（E）、delegate_task（G）一致；settings 键名三处一致。
- **强模型评审修正记录**：①Task A 订阅测试断言不再要求 `collected == []`（预置 status 事件会被首轮消费）；②Task G 事件 action 断言改为首 start 末 end（content chunk 会产 delta）；③delegate_task import 补 `asyncio`；④**补 stream_chat 入口 set ctx.deep_thinking（Task C Step 6 + 测试）**——修复 thinking 跟随/600s 档线上不可达；⑤executor 等待改为 `__anext__` 与 abort 竞速（静默窗口即时感知取消）+ 显式 `agen.aclose()`；⑥前端内置 `DELEGATE_REASON_TEXT` 映射 + 未收 end 兜底 `finalizePendingDelegateSections`；⑦`_resolve_fork_llm` 增加"主 agent 无 model_name 需新建时退回复用主实例"兜底；⑧旧常量删除统一收到 Task I，消解 Task C 内部矛盾。与 openspec design D4 旧句（model/thinking 均未声明→复用主实例）的冲突已在 change design.md 同步修正。
