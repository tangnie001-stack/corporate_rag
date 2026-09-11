# 智能体/技能执行层 Implementation Plan（Plan 2 / 4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 fork 子代理携带 `allowed-tools` 工具独立执行、由会话智能体担任执行者，并让 `/xxx` 触发的 fork **直出**（主 agent 零 LLM 轮）在图上成立且引用链不断。

**Architecture:** 三件事一起做：① **隔离**——每次委派生成独立 `RequestContext`（子代理的检索与引用编号不污染主 agent），委派状态从 `ctx` 单值改为每次调用一个 `DelegateRun` 实例（修并发串号）；② **换框架**——`create_react_agent(tools=[], prompt=…)` → `langchain.agents.create_agent(tools=执行者∩allowed-tools, system_prompt=执行者人设)`，skill 正文改为初始 user message；③ **直出**——`workflow.py` 的入口从 `set_entry_point("agent")` 改为 `START` 条件边，新增 `skill_direct` 节点，`verify` 的判据输入统一走 `AgentState`（`VerifyInputs`），使"零 LLM 轮"与"引用/校验不空转"同时成立。

**Tech Stack:** Python 3.11+ / LangGraph 1.2.9（`START` 已可用）/ LangChain 1.3.11（`langchain.agents.create_agent`）/ pytest。**无新增依赖。**

**Spec:** `docs/openspec/changes/session-agent-and-skill-invocation/`（proposal.md / design.md / specs/ / tasks.md）——本计划实现其中的 4.1–4.7、4.9–4.11（4.8 见 `## 预检扫描` 的 R6）

## Global Constraints

- 注释与文档一律**中文**；每个函数写 docstring；每个 dataclass 字段加**行内注释**（来源/范围/用途）。
- **不用三元表达式**，写完整 `if/else`。
- **类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底**，用 `isinstance` / `x.attr if x is not None else default` 显式判断。
- 常量/阈值/文案集中到 `src/config/`（`settings.py` 运行参数 / `const.py` 事件与固定阈值 / `SSEInteractionTexts` 用户可见文案）。
- 单文件 < 400 行；单函数 < 80 行。（`src/agents/skills/executor.py` 现 441 行 —— 本计划改动它时**必须拆分**，不得继续加长。）
- 日志：事件消息**英文 k=v** + `[层名]` 前缀；新增事件先登记 `src/core/log_events.py` 的 `Event` + `EVENT_SPECS` 再启用。
- 测试 **mock 外部依赖**，不发真实网络调用；测试输出保持**无未断言 warning**。
- 门禁：`pytest tests/ -v` 全绿、`ruff check .` 无错、`pyright src/` 不新增 error、`python -m src.cli.check_docs` 0 error。**只格式化本次改动的文件**（`ruff format` 传具体路径，切勿 `ruff format .`）。
- **部署单 worker**，流式状态留在进程内，不假设多 worker。
- 契约变更须同步 `docs/agents/api_contract.md`（仅公共签名/响应结构）与受影响测试断言。

## 预检扫描

### A. 任务对 / 接缝

| pair | produces → consumes | 结论 |
|---|---|---|
| 4.2 → 4.5 | T3 放开工具（`tools=执行者∩allowed-tools`）需要的工具集 ← T4 的注入 seam | **循环依赖风险**：`make_rag_tools` 造 `delegate_task` → 需要 `SkillExecutor` → 需要工具集。**必须先做 seam（R2：把 4.5 提到 4.2 之前）** |
| 4.1 → 4.2 | 子代理独立 ctx ← executor 在执行期把 `current_request_ctx` 切到子 ctx | 一致；**工具读 `current_request_ctx.get()`，故必须 ContextVar set/reset**（R3） |
| 4.1 → 4.9 | 子 ctx 的 `tool_contexts` ← 直出路径的 citations 来源 | **接口不足**：`executor.execute()` 现只返回 `str` → 需返回/回传引用池（R4：改由 `DelegateRun` 承载） |
| 4.9 → 4.11 | 直出池写进 state ← verify/format 读 state | 一致（D24/D26 已定） |
| 4.10 → 4.11 | 入口分派 + 新 state 字段 ← 直出节点写 `answer`/判据 | **需明确**：`VerifyInputs` 由谁写（直出节点）与谁读（verify）——见 R5 |
| 4.11 → 既有 verify | verify/两条护栏现读 `current_request_ctx`（主 ctx） | **必须改判据来源**，否则直出轮空转（D26 ②）；本计划统一改读 `VerifyInputs`（R5） |
| 4.6 → 4.11 | 确认门"不通过"→ 不再进 verify 重跑 | 一致（D26 ④）；**确认门只在直出轮有意义**（R7） |
| 4.3 → 4.4 | 执行者预设（含 `maxTurns`）← 执行者选择 | **跨计划依赖**：`AgentPresetRegistry` 尚未装配进 `AgentService`（change 的 3.4 属 Plan 3）→ R1 |
| 4.8 → Plan 3 | 预设 `skills:` 预加载需"会话首轮 + 隐藏消息注入" | **归属错位**：预加载的时机/持久化在会话层 → 移到 Plan 3（R6） |
| 4.10 → Plan 3 | 直出机制 ← `/xxx` 前缀解析触发 | 机制可独立测试（测试直接注入初始 state），E2E 触发等 Plan 3（R8） |
| 4.1/4.2/4.4/4.9 | 四者都改 `executor.py` | **单文件多任务**：按 T2→T3→T5→T7 顺序推进，每步自洽可测（R9） |

### B. 单任务自洽性

| task | 检查 | 结论 |
|---|---|---|
| T1 `RequestContext.child()` + `DelegateRun` | 字段继承/共享边界（共享 `clarify_channel`/`abort_signal`，独立引用池与计数）与 D7/D14 自洽 | 一致 |
| T2 独立 ctx 生效 | 需在 fork 执行期 set ContextVar；`_consume_fork_events` 的 `ctx.delegate_id` 读取改由 `DelegateRun` 提供 | 一致（R4 依赖） |
| T3 `create_agent` 替换 | `create_agent` 参数含 `system_prompt`（非 `prompt`）；工具需 `BaseTool.name` 取交集；`middleware=[]` | 一致 —— **实测确认参数名**（见 R3 的验证命令） |
| T4 工具 seam | 需 `ToolProvider` 可调用对象注入；`executor.py` 已 441 行 → 本任务顺带拆分 | 一致（红线要求） |
| T5 执行者选择 + `maxTurns` | 依赖 `AgentPresetRegistry`；`executor` 现无 preset 句柄 | 一致（R1 先补最小装配） |
| T6 确认门 | 复用 `ask_user`/`pending_asks` 单槽；与 verify 重跑互斥 | 一致（R7） |
| T7 引用池回传 | `DelegateRun.ctx.tool_contexts` → state；`format` 不变（已读 state） | 一致 |
| T8 入口分派 + 直出节点 + verify 判据 | `START` 可用（实测 `__start__`）；`route_verify` 需新增回 `skill_direct` 的路由 | 一致 |
| T9 收口 | 事件登记 + `defensive-patterns` + 文档 + 门禁 | 一致 |

### C. Rulings（预检）

- **R1（跨计划，必须先解决）**：4.3/4.4 需要 `AgentPresetRegistry` 装配进 `AgentService`（change 的 3.4 原属 Plan 3）→ 本计划**纳入最小装配**（构造 registry + 注入 `SkillExecutor`），并把 change 的 3.4 标注为"由 Plan 2 提前完成"。**若判断有误**：Plan 3 重复装配一次（去重即可，成本低）。
- **R2**：把 4.5（打破循环依赖）**提到 4.2 之前**，否则 4.2 无法装配工具。**若判断有误**：返工顺序，非结构性问题。
- **R3**：子代理隔离**必须**用 `current_request_ctx.set(child_ctx)` + `finally reset` 实现（工具经 ContextVar 读 ctx，无法靠传参隔离）。**若判断有误**：子代理检索写进主池 → 主引用编号被污染（D7 承诺失效）。
- **R4**：`executor.execute()` 的返回契约从 `str` 扩为"文本 + 子代理引用池"，由**调用方传入的 `DelegateRun` 承载**（`run.result_text` / `run.ctx.tool_contexts`），避免改动所有返回值消费点。**若判断有误**：改回简单返回并在直出节点另取 ctx（多一次接线）。
- **R5**：verify 的判据来源**统一改为 `AgentState`**（新增 `VerifyInputs` 快照：引用池 + `temporal_years` + `missing_years` + `web_confirmed` + `verify_ask_count` + `web_guided`），**常规轮由 `agent_finalize` 从主 ctx 写、直出轮由直出节点从子 ctx 写**。这把 D26 的"按轮次选择来源"落地为"两轮共用同一来源、无需分支"。**若判断有误**：verify 需要在两处维护判据来源（分支更脆）。
- **R6**：4.8（预设预绑定 skill 预加载）**移出本计划**到 Plan 3（属会话层时机/持久化）。**若判断有误**：Plan 3 少一项，补做即可。
- **R7**：确认门**只在直出轮生效**——委派路径下主 agent 自己就能 `ask_user`，加门是重复机制的。change 的 `delegate-task`「确认门」Requirement 需加一条限定。**若判断有误**：委派路径缺"缺授权"兜底（主 agent 本就有 `ask_user`，影响小）。
- **R8**：本计划**不接 `/xxx` 触发**（Plan 3）；直出路径用"直接注入初始 state"的图级测试覆盖。**若判断有误**：E2E 覆盖延后到 Plan 3（不影响机制正确性）。
- **R9**：`executor.py` 441 行超红线 → **T4 顺带拆分**（按职责拆为"fork 执行"与"事件消费"两块，或抽出 `fork_stream.py`）。**若判断有误**：拆分方案可调整，功能不受影响。

---

### Task 1: `RequestContext.child()` 与 `DelegateRun`

**Files:**
- Modify: `src/infra/llm/request_context.py`（新增 `child()`）
- Create: `src/agents/skills/delegate_run.py`
- Test: `tests/infra/llm/test_request_context_child.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `RequestContext.child() -> RequestContext`：继承 `session_id`/`kb_id`/`kb_bound`/`deep_thinking`，**共享** `clarify_channel`/`abort_signal`，`tool_contexts` 与各计数/`temporal_*` **全新**
  - `DelegateRun(delegate_id: str, skill_name: str, ctx: RequestContext, stop_reason: str | None = None, result_text: str = "")`

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/test_request_context_child.py`：

```python
"""RequestContext.child() 隔离语义测试。"""

import asyncio

from src.infra.llm.request_context import RequestContext


def test_child_shares_channels_but_isolates_pools():
    """child 共享通道/取消信号，但引用池与计数全新。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True, deep_thinking=True)
    parent.tool_contexts.append(object())
    parent.temporal_years = [2024]
    parent.ask_count = 3

    child = parent.child()

    assert child.session_id == "s1"
    assert child.kb_id == "k1"
    assert child.kb_bound is True
    assert child.deep_thinking is True
    assert child.clarify_channel is parent.clarify_channel
    assert child.abort_signal is parent.abort_signal
    assert child.tool_contexts == []
    assert child.temporal_years == []
    assert child.ask_count == 0
    assert child.delegate_id == ""


def test_child_does_not_write_back():
    """写 child 不污染 parent（引用池隔离，D7）。"""
    parent = RequestContext(session_id="s1")
    child = parent.child()

    child.tool_contexts.append(object())
    child.missing_years.append(2025)

    assert parent.tool_contexts == []
    assert parent.missing_years == []


def test_child_has_independent_abort_signal_identity():
    """共享的是同一个 Event 对象（取消必须贯通主流程）。"""
    parent = RequestContext(session_id="s1")
    parent.abort_signal.set()

    assert parent.child().abort_signal.is_set()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/llm/test_request_context_child.py -v`
Expected: FAIL —— `AttributeError: 'RequestContext' object has no attribute 'child'`

- [ ] **Step 3: 实现 `child()`**

`src/infra/llm/request_context.py` 在 `RequestContext` 类内追加：

```python
    def child(self) -> "RequestContext":
        """派生一个子代理上下文：共享通道与取消信号，独立引用池与计数。

        用途：fork 子代理在自己的引用池里检索与编号，不污染主 agent（design D7/D24）。
        共享项：clarify_channel / abort_signal —— 委派事件必须能回到同一条 SSE 通道，
        取消必须能即时中断子代理；二者若各持一份，事件与取消都会断链。

        Returns:
            新的 RequestContext；tool_contexts / 各计数 / temporal_* / missing_years /
            web_* 全部为默认初值（子代理独立累计）。
        """
        return RequestContext(
            session_id=self.session_id,
            kb_id=self.kb_id,
            kb_bound=self.kb_bound,
            clarify_channel=self.clarify_channel,
            abort_signal=self.abort_signal,
            deep_thinking=self.deep_thinking,
        )
```

- [ ] **Step 4: 新建 `DelegateRun`**

`src/agents/skills/delegate_run.py`：

```python
"""单次 fork 委派的运行态 —— 取代 ctx 上的单值字段（design D14 并发安全）。

背景：一轮内多个 delegate_task 会被 ToolNode 以 asyncio.gather 并发调度；把
delegate_id / 停止原因写在 RequestContext 的单值字段上会互相覆盖，导致 SSE 增量、
任务看板与终态判定的串号。故每次委派一个独立实例，由调用方持有并逐层传递。
"""

from dataclasses import dataclass, field

from src.infra.llm.request_context import RequestContext


@dataclass
class DelegateRun:
    """一次 fork 委派的全部运行态（每次调用新建，不共享）。

    Attributes:
        delegate_id: 本次委派短 id（来源：delegate_task 生成；用途：事件/看板/日志贯穿）
        skill_name: 被调用的 skill 名（来源：命中的 SkillRecord；用途：事件与日志）
        ctx: 子代理的独立请求上下文（来源：主 ctx.child()；用途：隔离引用池与计数）
        stop_reason: 中断原因（来源：executor 中断时写入；用途：终态区分 normal 与中断）
        result_text: 子代理最终文本（来源：executor 聚合；用途：直出轮写 answer）
    """

    delegate_id: str  # 本次委派短 id
    skill_name: str  # 被调用的 skill 名
    ctx: RequestContext  # 子代理独立上下文
    stop_reason: str | None = None  # None=正常完成或未执行
    result_text: str = ""  # 子代理最终文本
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/infra/llm/test_request_context_child.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: 提交**

```bash
git add src/infra/llm/request_context.py src/agents/skills/delegate_run.py tests/infra/llm/test_request_context_child.py
git commit -m "feat(delegate): RequestContext.child() 与 DelegateRun，为委派隔离与并发安全打底"
```

---

### Task 2: fork 执行期切换到子上下文（工具写入子池）

**Files:**
- Modify: `src/agents/skills/executor.py`（`_run_fork` 接收 `DelegateRun`，set/reset ContextVar）
- Test: `tests/agents/skills/test_fork_context_isolation.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `RequestContext.child()` / `DelegateRun`
- Produces: `SkillExecutor.execute(record: SkillRecord, task: str, run: DelegateRun | None = None) -> str`（`run=None` 时保持旧行为：用主 ctx，不隔离——供既有 inline/无 ctx 路径复用）

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_fork_context_isolation.py`：

```python
"""fork 子代理在独立 RequestContext 内执行：工具写入子池，主池不受影响。"""

import asyncio

import pytest

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.infra.llm.request_context import RequestContext, current_request_ctx


class _FakeSubAgent:
    """替身子代理：执行期读 current_request_ctx 并写入一个检索上下文。"""

    def __init__(self):
        self.seen_ctx = None

    async def astream_events(self, inputs, config=None, version="v2"):
        ctx = current_request_ctx.get()
        self.seen_ctx = ctx
        if ctx is not None:
            ctx.tool_contexts.append(_FakeContext("子代理材料"))
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("子代理结论")}}


class _FakeContext:
    def __init__(self, content):
        self.content = content
        self.source = "f.csv"
        self.page = 1
        self.score = 0.9
        self.kind = "kb"
        self.tier = ""

    def to_prompt_text(self):
        return self.content


class _Chunk:
    def __init__(self, text):
        self.content = text
        self.tool_call_chunks = []


@pytest.mark.asyncio
async def test_fork_writes_child_pool_not_parent(monkeypatch):
    """子代理的检索上下文落在子池，主池保持为空。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    fake = _FakeSubAgent()

    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(executor, "_build_sub_agent", lambda record, preset: fake)
    monkeypatch.setattr(executor, "_fork_tools", lambda record, preset: [])
    record = SkillRecord(name="finance-analyst", description="d", context=SkillContext.FORK, fork_body="正文")
    run = DelegateRun(delegate_id="d1", skill_name=record.name, ctx=parent.child())

    try:
        await executor.execute(record, "任务", run)
    finally:
        current_request_ctx.reset(token)

    assert fake.seen_ctx is run.ctx
    assert len(run.ctx.tool_contexts) == 1
    assert parent.tool_contexts == []


@pytest.mark.asyncio
async def test_context_var_restored_after_fork(monkeypatch):
    """执行结束后 ContextVar 复位到主 ctx（不泄漏到后续节点）。"""
    parent = RequestContext(session_id="s1")
    token = current_request_ctx.set(parent)
    executor = SkillExecutor(main_llm=object())
    monkeypatch.setattr(executor, "_build_sub_agent", lambda record, preset: _FakeSubAgent())
    monkeypatch.setattr(executor, "_fork_tools", lambda record, preset: [])
    record = SkillRecord(name="x", description="d", context=SkillContext.FORK, fork_body="b")
    run = DelegateRun(delegate_id="d1", skill_name="x", ctx=parent.child())

    try:
        await executor.execute(record, "t", run)
        assert current_request_ctx.get() is parent
    finally:
        current_request_ctx.reset(token)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_fork_context_isolation.py -v`
Expected: FAIL —— `execute()` 不接受第三个参数（`TypeError`）

- [ ] **Step 3: 实现（含 Task 4 的 `_build_sub_agent` / `_fork_tools` seam 占位）**

`src/agents/skills/executor.py`：
- `execute(self, record, task, run=None)`：fork 分支 `await self._run_fork(record, task, run)`。
- `_run_fork(self, record, task, run)`：
  - `child_ctx = run.ctx if run is not None else current_request_ctx.get()`
  - 取 `llm = self._resolve_fork_llm(record)`、`sub_agent = self._build_sub_agent(record, preset=None)`（Task 4 接工具/provider，Task 5 接 preset）
  - 用 `token_ctx = current_request_ctx.set(child_ctx)` 包住 `_consume_fork_events(...)`，`finally: current_request_ctx.reset(token_ctx)`（在既有 `var_child_runnable_config` reset 之外再包一层）
  - 中断写 `run.stop_reason`（`run is not None` 时），否则回落写 `child_ctx.fork_stop_reason`（兼容旧路径）
  - 正常返回时把聚合文本写入 `run.result_text`

```python
    def _build_sub_agent(self, record: SkillRecord, preset):
        """构建子代理（Task 4/5 接入工具与人设；本任务先保持零工具 + fork_body）。"""
        fork_body = record.fork_body
        if fork_body is not None:
            fork_body = render_skill_body(fork_body, "")
        return create_react_agent(
            self._resolve_fork_llm(record),
            tools=[],
            prompt=fork_body,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_fork_context_isolation.py tests/agents/skills/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/skills/executor.py tests/agents/skills/test_fork_context_isolation.py
git commit -m "feat(delegate): fork 执行期切换子上下文，引用池与计数隔离主 agent"
```

---

### Task 3: 工具注入 seam（打破循环依赖）+ 拆分 `executor.py`

> 预检 R2：必须先于 Task 4（放开工具）落地。

**Files:**
- Create: `src/agents/skills/fork_tools.py`（工具筛选：执行者 tools ∩ allowed-tools）
- Modify: `src/agents/skills/executor.py`（构造参数加 `tool_provider`；拆出事件消费到 `fork_stream.py`）
- Modify: `src/services/agent_service.py`（装配处传入 provider）
- Test: `tests/agents/skills/test_fork_tools.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces:
  - `select_fork_tools(allowed: list[str], available: list, executor_tools: list[str] | None = None) -> list`
  - `SkillExecutor(main_llm, tool_provider: Callable[[], list] | None = None, preset_registry=None)`

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_fork_tools.py`：

```python
"""fork 工具筛选：allowed-tools ∩ 执行者 tools。"""

from langchain_core.tools import tool

from src.agents.skills.fork_tools import select_fork_tools


@tool("retrieve_kb")
def _retrieve(query: str) -> str:
    """检索。"""
    return query


@tool("search_web")
def _search(query: str) -> str:
    """联网。"""
    return query


@tool("ask_user")
def _ask(question: str) -> str:
    """追问。"""
    return question


def test_empty_allowed_means_zero_tools():
    """allowed-tools 为空 → 零工具（不继承全集，D7 的 v1 语义已升级为显式声明）。"""
    assert select_fork_tools([], [_retrieve, _search]) == []


def test_allowed_filters_available():
    """只保留 allowed 里的工具。"""
    picked = select_fork_tools(["retrieve_kb"], [_retrieve, _search])
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_executor_tools_narrows_further():
    """执行者预设 tools 与 allowed 求交集（更窄者胜）。"""
    picked = select_fork_tools(["retrieve_kb", "search_web"], [_retrieve, _search], ["search_web"])
    assert [t.name for t in picked] == ["search_web"]


def test_unknown_allowed_name_is_ignored():
    """allowed 里引用了不存在的工具 → 忽略（不抛）。"""
    assert select_fork_tools(["ghost"], [_retrieve]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_fork_tools.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.agents.skills.fork_tools`

- [ ] **Step 3: 实现 `fork_tools.py`**

```python
"""fork 子代理的工具筛选 —— 执行者 tools ∩ skill allowed-tools。

交集口径（design D7）：allowed-tools 为空 → 零工具；执行者预设声明 tools 时再收窄
到两者交集；名字对不上的白名单项忽略（不抛，避免一个笔误打断整次委派）。
"""

from langchain_core.tools import BaseTool


def select_fork_tools(
    allowed: list[str],
    available: list,
    executor_tools: list[str] | None = None,
) -> list:
    """按白名单筛选可交给子代理的工具。

    Args:
        allowed: skill 的 allowed-tools（空 = 零工具）
        available: 当前注册表可用的工具对象（LangChain BaseTool）
        executor_tools: 执行者预设声明的工具名（空/None = 不再收窄）

    Returns:
        过滤后的工具列表（保持 available 原顺序）
    """
    if not allowed:
        return []
    names = set(allowed)
    if executor_tools:
        names &= set(executor_tools)
    picked = []
    for tool in available:
        if not isinstance(tool, BaseTool):
            continue
        if tool.name in names:
            picked.append(tool)
    return picked
```

- [ ] **Step 4: 拆分 `executor.py`（红线）**

把事件消费整段（`_consume_fork_events` 及其内联逻辑）抽到新文件 `src/agents/skills/fork_stream.py`（函数 `consume_fork_events(sub_agent, run, task, max_turns) -> str`），`executor.py` 只留：构造、`execute`、`_render_inline`、`_build_sub_agent`、`_fork_tools`、`_resolve_fork_llm`、`_truncate`。目标：两个文件都 < 400 行。

- [ ] **Step 5: 装配点注入 provider**

`src/services/agent_service.py`：构造 `SkillExecutor` 时传入 `tool_provider`，返回**当前启用工具列表**的延迟求值（闭包持有 `rag_tools` 或 `ToolRegistry.enabled_tools`）。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/agents/skills/ -v && wc -l src/agents/skills/executor.py src/agents/skills/fork_stream.py`
Expected: PASS；两文件均 < 400 行

- [ ] **Step 7: 提交**

```bash
git add src/agents/skills/fork_tools.py src/agents/skills/fork_stream.py src/agents/skills/executor.py src/services/agent_service.py tests/agents/skills/test_fork_tools.py
git commit -m "refactor(delegate): fork 工具筛选与 provider 注入（打破循环依赖），executor 拆分"
```

---

### Task 4: 换 `create_agent`：执行者人设 + 工具交集 + skill 正文作 user message

**Files:**
- Modify: `src/agents/skills/executor.py`（`_build_sub_agent` 改用 `create_agent`）
- Modify: `src/config/const.py`（无占位符时的追加模板常量）
- Test: `tests/agents/skills/test_fork_sub_agent_contract.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `select_fork_tools` / `tool_provider`
- Produces: 子代理构造满足 D6：`system_prompt = 执行者人设`、初始 `HumanMessage = skill 正文（task 已注入）`、`tools = allowed ∩ 执行者`、`middleware=[]`

- [ ] **Step 1: 写失败测试**

```python
"""fork 子代理构造契约：system_prompt=人设、user message=skill 正文、tools=交集、middleware 空。"""

import pytest

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord


class _FakeAgent:
    pass


@pytest.mark.asyncio
async def test_sub_agent_uses_system_prompt_and_user_body(monkeypatch):
    captured = {}

    def _fake_create_agent(model, tools=None, system_prompt=None, middleware=None, **kw):
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        captured["middleware"] = middleware
        return _FakeAgent()

    monkeypatch.setattr("src.agents.skills.executor.create_agent", _fake_create_agent)
    executor = SkillExecutor(main_llm=object(), tool_provider=lambda: [])
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="按方法论分析。\n任务：$ARGUMENTS",
        allowed_tools=["retrieve_kb"],
    )
    executor._build_sub_agent(record, preset=None, task="腾讯2024")

    assert captured["system_prompt"]  # 执行者人设非空（无 preset → 系统默认人设）
    assert captured["tools"] == []  # available 为空 → 交集为空
    assert captured["middleware"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_fork_sub_agent_contract.py -v`
Expected: FAIL —— `_build_sub_agent` 尚无 `task` 形参与 `create_agent` 调用

- [ ] **Step 3: 实现**

```python
from langchain.agents import create_agent

    def _build_sub_agent(self, record: SkillRecord, preset, task: str):
        """构建 fork 子代理：system=执行者人设、user=skill 正文、tools=交集。

        Args:
            record: fork SkillRecord
            preset: 执行者 AgentPreset（None → 系统默认人设）
            task: 任务文本（渲染进 skill 正文的 $ARGUMENTS）

        Returns:
            create_agent 编译产物（astream_events 事件源）
        """
        body = record.fork_body or ""
        if ARGUMENTS_PLACEHOLDER not in body and TASK_PLACEHOLDER not in body:
            body = body + "\n" + FORK_TASK_APPEND_TMPL.format(task=task)
        user_content = render_skill_body(body, task)
        available = self._tool_provider() if self._tool_provider is not None else []
        executor_tools = preset.tools if preset is not None else None
        return create_agent(
            self._resolve_fork_llm(record),
            tools=select_fork_tools(record.allowed_tools, available, executor_tools),
            system_prompt=self._executor_system_prompt(preset),
            middleware=[],
        )
```
`_executor_system_prompt(preset)`：`preset.system_prompt`（有 preset）否则系统默认人设（`build_system_prompt(persona=None, kb_bound=…, has_skills=…)` 的产物——由 Task 9 与 Plan 3 的组装器对齐；本任务先返回 `PromptManager.get_base_system_prompt()` 优先、不可得时退回 `record` 无关的默认串）。**初始 user message 由 `fork_stream.consume_fork_events` 传 `HumanMessage(user_content)`** —— 与现有 `HumanMessage(content=task)` 的差别就是本任务要点。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/skills/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/skills/executor.py src/agents/skills/fork_stream.py src/config/const.py tests/agents/skills/test_fork_sub_agent_contract.py
git commit -m "feat(delegate): fork 子代理改用 create_agent，人设来自 agent、任务来自 skill"
```

---

### Task 5: 执行者选择顺序 + `maxTurns`（含 `AgentPresetRegistry` 最小装配）

> 预检 R1：本任务提前完成 change 的 3.4（装配 presets），Plan 3 只做会话层读取。

**Files:**
- Modify: `src/infra/llm/request_context.py`（新增 `agent: str = ""` 字段）
- Modify: `src/agents/skills/executor.py`（构造参数加 `preset_registry`；新增 `_resolve_executor` / `_fork_max_turns`；`_run_fork` 接线）
- Modify: `src/services/agent_service.py`（构造 `AgentPresetRegistry` 并注入）
- Test: `tests/agents/skills/test_fork_executor_selection.py`（新建）

**Interfaces:**
- Consumes（Plan 1 已交付，签名已实机核对）：
  - `AgentPresetLoader(agents_root: Path).load_all() -> list[AgentPreset]`
  - `AgentPresetRegistry(loader).reload_if_changed()`（同名冲突抛 `ValueError`，fail-fast）/ `.get(name) -> AgentPreset | None`（**未命中返回 None**，不抛）
  - `AgentPreset` 字段：`name` / `display_name` / `description` / `system_prompt` / `tools: list[str]` / `skills: list[str]` / `max_turns: int | None`（None=系统默认）/ `source_path`
  - **`src/agents/presets/__init__.py` 是空文件**（无 re-export）→ 必须从子模块导入
- Produces：
  - `SkillExecutor(main_llm, tool_provider: Callable[[], list] | None = None, preset_registry: AgentPresetRegistry | None = None)`
  - `_resolve_executor(record: SkillRecord, session_agent: str) -> AgentPreset | None`
  - `_fork_max_turns(preset: AgentPreset | None) -> int`

**关键事实：**
- `RequestContext` 现**没有**承载会话智能体名的字段 → 本任务新增 `agent: str = ""`（来源：会话绑定值，由 Plan 3 写入；本任务只读，读不到即空串 → 落系统默认）。
- `settings` 里只有 `SKILLS_DIR`，**没有** `AGENTS_DIR` → 预设根目录按 skills 的既有推导方式取 `Path(__file__).resolve().parents[2] / "agents"`（仓库顶层 `agents/`，现有 `agents/finance-expert.md` 可作 frontmatter 模板）。
- `_run_fork` 目前把 `preset=None` 硬编码给 `_build_sub_agent`。会话智能体名必须在**切子 ctx 之前**从主 ctx 读（`child()` 不复制 `agent`，本任务也不改 `child()`）。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_fork_executor_selection.py`：

```python
"""fork 执行者选择：skill.agent > 会话智能体 > 系统默认；maxTurns 回落。"""

from pathlib import Path

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import DELEGATE_DEFAULT_MAX_TURNS

_AGENT_MD = """---
name: {name}
description: {name} 预设
system_prompt: 你是{name}
maxTurns: {max_turns}
---

人设正文。
"""


def _registry(tmp_path: Path, specs: list[tuple[str, int]]) -> AgentPresetRegistry:
    """按 (name, maxTurns) 写临时预设文件并建立已加载的注册表。"""
    for name, max_turns in specs:
        (tmp_path / f"{name}.md").write_text(
            _AGENT_MD.format(name=name, max_turns=max_turns), encoding="utf-8"
        )
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    return registry


def _fork_record(**overrides) -> SkillRecord:
    """构造 fork SkillRecord，默认不声明 agent。"""
    defaults = {
        "name": "finance-analyst",
        "description": "d",
        "context": SkillContext.FORK,
        "fork_body": "任务：$ARGUMENTS",
        "allowed_tools": [],
        "agent": "",
        "source_path": Path("/tmp/finance-analyst/SKILL.md"),
    }
    defaults.update(overrides)
    return SkillRecord(**defaults)


def test_skill_agent_wins_over_session_agent(tmp_path):
    """skill.agent 命中时优先于会话选定智能体。"""
    exe = SkillExecutor(
        main_llm=object(),
        preset_registry=_registry(tmp_path, [("legal-expert", 3), ("finance-expert", 7)]),
    )
    preset = exe._resolve_executor(_fork_record(agent="legal-expert"), "finance-expert")
    assert preset is not None
    assert preset.name == "legal-expert"


def test_session_agent_used_when_skill_declares_none(tmp_path):
    """skill 未声明 agent → 用会话选定智能体。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    preset = exe._resolve_executor(_fork_record(), "finance-expert")
    assert preset is not None
    assert preset.name == "finance-expert"


def test_unknown_names_fall_back_to_system_default(tmp_path):
    """skill.agent 与会话智能体都查不到 → None（系统默认人设）。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    assert exe._resolve_executor(_fork_record(agent="ghost"), "ghost") is None


def test_no_registry_falls_back_to_system_default():
    """未装配 registry → 恒 None，不抛。"""
    exe = SkillExecutor(main_llm=object())
    assert exe._resolve_executor(_fork_record(agent="finance-expert"), "finance-expert") is None


def test_fork_max_turns_uses_preset_then_default(tmp_path):
    """maxTurns：preset 声明值优先；未声明或 preset 为 None 时用 DELEGATE_DEFAULT_MAX_TURNS。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    preset = exe._resolve_executor(_fork_record(), "finance-expert")

    assert exe._fork_max_turns(preset) == 7
    assert exe._fork_max_turns(None) == DELEGATE_DEFAULT_MAX_TURNS


def test_fork_max_turns_defaults_when_preset_omits_max_turns(tmp_path):
    """preset 未写 maxTurns（None）→ 回落 DELEGATE_DEFAULT_MAX_TURNS。"""
    (tmp_path / "bare.md").write_text(
        "---\nname: bare\ndescription: bare\nsystem_prompt: 你是 bare\n---\n\n正文。\n",
        encoding="utf-8",
    )
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    exe = SkillExecutor(main_llm=object(), preset_registry=registry)
    preset = exe._resolve_executor(_fork_record(), "bare")

    assert preset is not None
    assert preset.max_turns is None
    assert exe._fork_max_turns(preset) == DELEGATE_DEFAULT_MAX_TURNS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_fork_executor_selection.py -v`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'preset_registry'`

- [ ] **Step 3: 加 `RequestContext.agent`**

`src/infra/llm/request_context.py` 在 `deep_thinking` 字段附近追加（中文行内注释写清来源/范围/用途）：

```python
    agent: str = ""  # 会话绑定智能体名（来源：Plan 3 由 sessions.agent 写入；范围：请求内只读；用途：fork 执行者选择的第二优先级；空=未绑定→系统默认）
```

- [ ] **Step 4: 实现执行者选择与 maxTurns**

`src/agents/skills/executor.py`：

```python
from src.agents.presets.models import AgentPreset
from src.agents.presets.registry import AgentPresetRegistry
```

构造参数（R15 只加 `tool_provider`，本任务按 R1 补 `preset_registry`）：

```python
    def __init__(
        self,
        main_llm,
        tool_provider: Callable[[], list] | None = None,
        preset_registry: AgentPresetRegistry | None = None,
    ) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 未声明 model 时按 model_name 继承复用）
            tool_provider: 延迟求值的"当前启用工具"来源（None=子代理零工具）
            preset_registry: 智能体预设注册表（None=不做执行者选择，落系统默认人设）
        """
```

```python
    def _resolve_executor(
        self, record: SkillRecord, session_agent: str
    ) -> AgentPreset | None:
        """按优先级选执行者预设：skill.agent > 会话智能体 > None（系统默认）。

        Args:
            record: fork SkillRecord（其 agent 字段为最高优先级）
            session_agent: 会话绑定智能体名（空串=未绑定）

        Returns:
            命中的 AgentPreset；两处都查不到（或未装配 registry）返回 None
        """
        if self._preset_registry is None:
            return None
        if record.agent:
            preset = self._preset_registry.get(record.agent)
            if preset is not None:
                return preset
        if session_agent:
            preset = self._preset_registry.get(session_agent)
            if preset is not None:
                return preset
        return None

    def _fork_max_turns(self, preset: AgentPreset | None) -> int:
        """取 fork 子代理的 turn 上限：preset.max_turns 优先，缺省回落系统默认。"""
        if preset is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        if preset.max_turns is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        return preset.max_turns
```

`_run_fork` 里把 `preset=None` 换成解析结果（**在切子 ctx 之前读会话智能体名**）：

```python
        ctx = current_request_ctx.get()
        session_agent = ctx.agent if ctx is not None else ""
        preset = self._resolve_executor(record, session_agent)
        sub_agent = self._build_sub_agent(record, preset)
        max_turns = self._fork_max_turns(preset)
```
（`max_turns` 原为 `DELEGATE_DEFAULT_MAX_TURNS` 常量赋值，改为上面的调用；`ctx` 若已在函数内取过就复用，不要重复 `get()`。注意保持 `_run_fork` < 80 行。）

- [ ] **Step 5: 装配点构造 registry**

`src/services/agent_service.py`（`skills_dir` 推导之后、注册分支之前加 `agents_dir`；导入放本地 import 块）：

```python
        from src.agents.presets.loader import AgentPresetLoader
        from src.agents.presets.registry import AgentPresetRegistry

        agents_dir = Path(__file__).resolve().parents[2] / "agents"
```

```python
            if skill_registry.names():
                preset_registry = AgentPresetRegistry(AgentPresetLoader(agents_dir))
                preset_registry.reload_if_changed()  # 同名冲突 fail-fast（配置错误）
                skill_executor = SkillExecutor(
                    self._llm,
                    tool_provider=lambda: fork_tool_pool,
                    preset_registry=preset_registry,
                )
                delegate_task_tool = make_delegate_task(skill_registry, skill_executor)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_fork_executor_selection.py tests/agents/skills/ -v && pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/infra/llm/request_context.py src/agents/skills/executor.py src/services/agent_service.py tests/agents/skills/test_fork_executor_selection.py
git commit -m "feat(delegate): fork 执行者按 skill.agent > 会话智能体 > 系统默认选择，maxTurns 生效"
```

---

### Task 6: fork 工具硬约束（剔除交互/委派工具）

> **范围变更（R22，已确认）**：change 的 4.6「确认门」**移出本计划到 Plan 3** —— 确认门的触发方（`/xxx` 直出）与消费方都属 Plan 3，且它不服务本计划"引用链不断"的核心目标。但**前提约束必须在本计划落地**：子代理不得持有 `ask_user` / `delegate_task`，否则一个 skill 只要写 `allowed-tools: ask_user` 就能拿到交互工具，直接推翻 D18 的确认门设计（并且 `delegate_task` 会引入递归委派）。

**Files:**
- Modify: `src/config/const.py`（新增 `FORK_FORBIDDEN_TOOLS`）
- Modify: `src/agents/skills/fork_tools.py`（筛选时减去禁用集）
- Test: `tests/agents/skills/test_fork_tools.py`（追加用例）

**Interfaces:**
- Consumes: Task 3 的 `select_fork_tools(allowed, available, executor_tools=None)`
- Produces: `select_fork_tools` 的返回值恒不含 `FORK_FORBIDDEN_TOOLS` 中的工具

- [ ] **Step 1: 追加失败测试**

`tests/agents/skills/test_fork_tools.py` 追加：

```python
@tool("delegate_task")
def _delegate(skill: str, task: str) -> str:
    """委派。"""
    return skill


def test_ask_user_is_never_handed_to_sub_agent():
    """D18：子代理不持有面向用户的交互工具，即使 skill 显式声明也不给。"""
    assert select_fork_tools(["ask_user"], [_ask]) == []


def test_delegate_task_is_never_handed_to_sub_agent():
    """D7：子代理不得再委派（防递归），即使 skill 显式声明也不给。"""
    assert select_fork_tools(["delegate_task"], [_delegate]) == []


def test_forbidden_tools_do_not_block_allowed_readonly_tools():
    """禁用集只剔除自身，不影响同一白名单里的只读检索工具。"""
    picked = select_fork_tools(["ask_user", "retrieve_kb"], [_ask, _retrieve])
    assert [t.name for t in picked] == ["retrieve_kb"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_fork_tools.py -v`
Expected: FAIL —— 前两条断言当前会返回含 `ask_user` / `delegate_task` 的列表

- [ ] **Step 3: 实现**

`src/config/const.py`（放 `SKILL_TASK_PLACEHOLDERS` 附近，中文注释说明依据）：

```python
FORK_FORBIDDEN_TOOLS = ("ask_user", "delegate_task")
"""fork 子代理永不可持有的工具名。

ask_user：D18 —— 子代理不直接交互，需要确认时由编排层确认门代为询问。
delegate_task：D7 —— 子代理不再委派，防递归与上下文爆炸。
"""
```

`src/agents/skills/fork_tools.py`：把 `names` 的计算改为先建白名单再减去禁用集（用 `set.difference` 或 `for` 循环，不要三元）：

```python
    if not allowed:
        return []
    names = set(allowed)
    names -= set(FORK_FORBIDDEN_TOOLS)
    if not names:
        return []
    if executor_tools:
        names &= set(executor_tools)
```

（`names` 为空时提前返回是可选的优化；保留与否都行，但**不得**因此改变既有四条用例的语义。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_fork_tools.py tests/agents/skills/ -v`
Expected: PASS（含既有 4 条）

- [ ] **Step 5: 提交**

```bash
git add src/config/const.py src/agents/skills/fork_tools.py tests/agents/skills/test_fork_tools.py
git commit -m "fix(delegate): fork 工具剔除 ask_user/delegate_task，落实子代理不交互不递归"
```

---

### Task 7: verify 判据随材料走（材料载体 = AgentState）

> 预检 R5 + **R5-amended**：判据的**材料**统一走 `AgentState`，但**流程字段不走**。
> **为什么不把 `web_confirmed`/`verify_ask_count`/`web_guided` 也快照化**：这三个字段在同一次 verify 调用内被**写后读**（`regen_decision.py:79` 写 `ctx.web_confirmed = True` → `:87` 立刻读它决定分支；`ask_confirm.py:36` 自增 `verify_ask_count`），一旦改成读快照就会丢掉刚写入的值，行为改变。它们本就是请求级流程状态、且子代理从不写它们，留在主 ctx 是正确的。

**Files:**
- Modify: `src/agents/graph/state.py`（新增 `verify_temporal_years` 字段）
- Modify: `src/agents/graph/agent_node.py`（`agent_finalize` 顺带写该字段）
- Modify: `src/agents/graph/verify/node.py`（年份判据改读 `state`）
- Modify: `src/agents/graph/verify/guardrails.py`（引用池判据改读 `state.tool_contexts`）
- Test: `tests/agents/graph/test_verify_material_source.py`（新建）

**Interfaces:**
- Consumes: 既有 `AgentState.tool_contexts`（已由 `agent_finalize` 写入主 ctx 池；`format_node` 已在读）
- Produces:
  - `AgentState.verify_temporal_years: list[int]`
  - `_has_web_context(state) -> bool` / `_has_kb_context(state) -> bool`（签名由 `ctx` 改为 `state`）

**关键事实（已实机核对）：**
- `AgentState` 是 **`@dataclass`**（不是 TypedDict）→ 加字段无需 reducer。
- `state.tool_contexts` 已经是"本轮材料池"的既有载体：常规轮由 `agent_finalize`（`agent_node.py:205`）从主 ctx 写入；直出轮将由 Task 8 的直出节点写入子代理池。**因此引用池不需要新字段**，只需把护栏的读取源从 ctx 改成 state。
- 只有 `temporal_years` 需要新载体（verify 用它做年份完整性比对，`verify/node.py:44`）。
- `web_citation_guard` 里有一处 **写** ctx（`guardrails.py:98` `ctx.web_count = 0`）→ 该函数必须继续接收 `ctx`。
- `kb_citation_guardrail` 若无其它 ctx 用途，把 `ctx` 形参一并删除（避免留未使用参数），同步改 `verify/node.py` 的调用。

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_verify_material_source.py`：

```python
"""verify 的判据材料必须随 AgentState 走，而不是读主 ctx。

常规轮由 agent_finalize 把主 ctx 池快照进 state；直出轮由直出节点把子代理池写进 state。
故"主 ctx 池"与"判据材料"必须解耦：state 有材料 → 护栏生效；state 无材料 → 即使主 ctx
有材料也不生效（证明不再偷看主 ctx）。
"""

import pytest

from src.agents.graph.state import AgentState
from src.agents.graph.verify.node import verify_node
from src.infra.llm.request_context import RequestContext, current_request_ctx


class _KbContext:
    """最小 RAGContext 替身（只需 kind 供 _has_kb_context 判定）。"""

    def __init__(self, content: str = "2023 年营收 100 亿"):
        self.content = content
        self.source = "annual.pdf"
        self.page = 12
        self.score = 0.9
        self.kind = "kb"
        self.tier = ""

    def to_prompt_text(self) -> str:
        return self.content


def _state(**overrides) -> AgentState:
    """构造绑定 KB 的 state，判据材料缺省为空。"""
    defaults = {
        "session_id": "s1",
        "kb_id": "kb1",
        "query": "2024 年营收",
        "answer": "公司经营稳健，业务持续增长。",
        "tool_contexts": [],
        "verify_temporal_years": [],
    }
    defaults.update(overrides)
    return AgentState(**defaults)


@pytest.mark.asyncio
async def test_guardrail_uses_state_materials_not_main_ctx(monkeypatch):
    """直出轮形态：主 ctx 池为空、材料在 state → 护栏仍生效（不再空转）。"""
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(tool_contexts=[_KbContext()])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is True
    assert main_ctx.tool_contexts == []  # 主池不被改写（D7/D24）


@pytest.mark.asyncio
async def test_main_ctx_materials_are_not_read_any_more(monkeypatch):
    """反向证明：主 ctx 有材料但 state 没有 → 护栏不生效（判据只看 state）。"""
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    main_ctx.tool_contexts.append(_KbContext())
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(tool_contexts=[])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is False


@pytest.mark.asyncio
async def test_year_check_uses_state_verify_temporal_years(monkeypatch):
    """年份完整性判据来自 state.verify_temporal_years（不是主 ctx）。"""
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    main_ctx.temporal_years = [2024]  # 主 ctx 有年份，但 state 没有 → 不做完整性校验
    token = current_request_ctx.set(main_ctx)
    try:
        state = _state(answer="2023 年营收 100 亿。", tool_contexts=[])
        out = await verify_node(state)
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is False
```

> 说明：`verify_node` 需 `settings.VERIFY_ENABLED=True`（默认即 True）；若测试环境默认关闭，在文件顶部加 `monkeypatch.setenv` 或在用例内 `monkeypatch.setattr(settings, "VERIFY_ENABLED", True)`——实现者按实跑结果决定，并在报告里写明。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/graph/test_verify_material_source.py -v`
Expected: FAIL —— `test_guardrail_uses_state_materials_not_main_ctx` 当前会因主 ctx 池空而静默放行

- [ ] **Step 3: 加 state 字段并让 `agent_finalize` 写入**

`src/agents/graph/state.py` 在 `tool_contexts` 附近追加：

```python
    verify_temporal_years: list[int] = field(
        default_factory=list
    )  # verify 判据材料：本轮要求覆盖年份（来源：常规轮 agent_finalize 从主 ctx 写入 / 直出轮直出节点从子 ctx 写入；用途：年份完整性比对；空=不校验）
```

`src/agents/graph/agent_node.py` 的 `agent_finalize` 追加返回值（保持既有 `answer` / `tool_contexts` 不变）：

```python
    ctx = current_request_ctx.get()
    if ctx is not None:
        contexts = ctx.tool_contexts
        years = ctx.temporal_years
    else:
        contexts = []
        years = []
    return {"answer": answer, "tool_contexts": contexts, "verify_temporal_years": years}
```

- [ ] **Step 4: 护栏与 verify 改读 state**

`src/agents/graph/verify/guardrails.py`：
- `_has_web_context(ctx)` → `_has_web_context(state: AgentState) -> bool`，体内 `for c in state.tool_contexts`。
- `_has_kb_context(ctx)` → `_has_kb_context(state: AgentState) -> bool`，同上。
- `web_citation_guard(state, ctx)`：`ctx` **保留**（`ctx.web_count = 0` 的写）；内部判定改用 `_has_web_context(state)`。
- `kb_citation_guardrail(state, ctx)`：若 `ctx` 除 `_has_kb_context` 外再无用途 → **删除 `ctx` 形参**，内部改用 `_has_kb_context(state)`。
- docstring 同步（判据来源从"当前请求上下文"改为"AgentState 承载的本轮材料"）。

`src/agents/graph/verify/node.py`：
- `required = state.verify_temporal_years`（原来的 `ctx.temporal_years if ctx is not None else []` 删除）。
- `ctx` 仍保留（`decide_missing_web` 需要 `web_confirmed` / `web_guided`）。
- 按 Step 4 的结论调整 `kb_citation_guardrail` 调用参数。

- [ ] **Step 5: 跑测试确认通过（含既有 verify 测试回归）**

Run: `pytest tests/agents/graph/test_verify_material_source.py tests/agents/graph/ -v && pytest tests/ -q`
Expected: PASS —— **既有 verify / 护栏测试必须逐条不变通过**（常规轮行为等价）

- [ ] **Step 6: 提交**

```bash
git add src/agents/graph/state.py src/agents/graph/agent_node.py src/agents/graph/verify/node.py src/agents/graph/verify/guardrails.py tests/agents/graph/test_verify_material_source.py
git commit -m "refactor(verify): 判据材料改由 AgentState 承载，直出轮不再空转（D26）"
```

---

### Task 8: 图入口分派 + `skill_direct` 节点 + 直出引用池（D26/D24）

**Files:**
- Create: `src/agents/graph/skill_direct.py`（`route_entry` + `make_skill_direct_node` + 未装配兜底节点）
- Modify: `src/config/const.py`（`LangGraphNode` 内加 `SkillDirect.NAME`；`SSEInteractionTexts` 加两条直出兜底文案）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（登记 `SKILL_DIRECT_SKIP`；**先登记再启用**）
- Modify: `src/agents/graph/state.py`（新增 `direct_skill: str = ""`）
- Modify: `src/agents/graph/workflow.py`（`START` 条件入口 + `skill_direct` 节点注册 + `skill_direct → verify` 边 + `route_verify` 增回直出的路由）
- Modify: `src/services/agent_service.py`（把 `skill_registry` / `skill_executor` 传入 `build_graph`）
- Test: `tests/agents/graph/test_entry_dispatch.py`、`tests/agents/graph/test_direct_skill_round.py`（新建）

**Interfaces:**
- Consumes: Task 5 的 executor（`execute(record, task, run)`）/ Task 3 的 `make_delegate_task`；Task 7 的 `verify_temporal_years` 载体；`SkillRegistry.get(name) -> SkillRecord | None`（registry.py:57，**未命中返回 None**）、`SkillRegistry.reload_if_changed()`
- Produces:
  - `route_entry(state: AgentState) -> str`（`"agent"` | `"skill_direct"`）
  - `make_skill_direct_node(skill_registry, executor) -> Callable[[AgentState], Awaitable[dict]]`
  - `build_graph(..., skill_direct_node=None)`

**关键事实（已实机核对）：**
- `langgraph.graph.START` 可用（`__start__`）；现图用 `builder.set_entry_point("agent")`。
- `LangGraphNode` 是"每个节点一个嵌套类、`NAME` 为注册名"的结构（`state.py:73-85`）→ 新增 `class SkillDirect: NAME: str = "skill_direct"`。
- `format_node` 读 `state.tool_contexts`，越界 `[n]` 会打 `Signal.INVALID_CITATION`（`nodes.py:79-90`）→ 直出轮只要把子代理池写进 `state.tool_contexts`，引用链与信号就都正确。
- 直出节点**不做** delegate start/end SSE 事件（`delegate_task.py` 才有）——见本任务末尾的 R23 说明。

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_entry_dispatch.py`：

```python
"""图入口分派：direct_skill 非空 → skill_direct；空 → 常规 agent 轮。"""

from src.agents.graph.skill_direct import route_entry
from src.agents.graph.state import AgentState


def test_entry_routes_direct_when_skill_selected():
    """命令行直出：direct_skill 非空 → skill_direct。"""
    assert route_entry(AgentState(session_id="s1", direct_skill="finance-analyst")) == "skill_direct"


def test_entry_routes_agent_when_no_skill():
    """常规轮：direct_skill 为空 → agent（既有行为不变）。"""
    assert route_entry(AgentState(session_id="s1")) == "agent"
```

`tests/agents/graph/test_direct_skill_round.py`：

```python
"""直出轮整链：主 agent 零 LLM 轮、子代理材料进 state、引用不丢。"""

import pytest

from src.agents.graph.skill_direct import make_skill_direct_node
from src.agents.graph.state import AgentState
from src.agents.graph.workflow import build_graph
from src.agents.skills.models import SkillContext, SkillRecord
from src.infra.llm.request_context import RequestContext, current_request_ctx


class _KbContext:
    """最小 RAGContext 替身。"""

    def __init__(self, content: str, page: int):
        self.content = content
        self.source = "annual.pdf"
        self.page = page
        self.score = 0.9
        self.kind = "kb"
        self.tier = ""

    def to_prompt_text(self) -> str:
        return self.content


class _FakeRegistry:
    """只实现直出节点用到的 get / reload。"""

    def __init__(self, record):
        self._record = record

    def reload_if_changed(self) -> None:
        return None

    def get(self, name):
        if name == self._record.name:
            return self._record
        return None


class _FakeExecutor:
    """替身：在子池写 2 条材料并返回带 [1][2] 的答案。"""

    def __init__(self):
        self.seen_run = None

    async def execute(self, record, task, run=None):
        self.seen_run = run
        run.ctx.tool_contexts.append(_KbContext("2024 年营收 1000 亿", 12))
        run.ctx.tool_contexts.append(_KbContext("2023 年营收 900 亿", 13))
        run.ctx.temporal_years.append(2024)
        return "公司 2024 年营收 1000 亿[1]，同比增至 900 亿[2]。"


def _graph(fake_executor, record, **kwargs):
    """构建带直出节点的最小图（tools 传空避免真实检索）。"""
    node = make_skill_direct_node(_FakeRegistry(record), fake_executor)
    return build_graph(
        vector_store=None,
        bm25=None,
        llm=None,
        reranker=None,
        prompt_manager=None,
        tools=[],
        skill_direct_node=node,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_direct_round_keeps_child_citations_and_zero_agent_rounds():
    """直出轮：主 agent 0 轮、citations 落在子代理池、主池不被污染。"""
    record = SkillRecord(
        name="finance-analyst",
        description="d",
        context=SkillContext.FORK,
        fork_body="任务：$ARGUMENTS",
        allowed_tools=[],
    )
    fake = _FakeExecutor()
    graph = _graph(fake, record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        out = await graph.ainvoke(
            AgentState(
                session_id="s1",
                kb_id="kb1",
                query="2024 年营收",
                direct_skill=record.name,
            )
        )
    finally:
        current_request_ctx.reset(token)

    assert out["_agent_iterations"] == 0  # 主 agent 一轮都没跑
    assert [c["index"] for c in out["citations"]] == [1, 2]
    assert out["answer"].startswith("公司 2024 年营收")
    assert main_ctx.tool_contexts == []  # 主池保持为空（D7/D24）
    assert main_ctx.temporal_years == []


@pytest.mark.asyncio
async def test_unknown_or_inline_skill_falls_open():
    """direct_skill 命中不到 / 非 fork → 兜底文案，不抛、不空转。"""
    record = SkillRecord(
        name="finance-qa",
        description="d",
        context=SkillContext.INLINE,
        inline_prompt="方法论 $ARGUMENTS",
    )
    graph = _graph(_FakeExecutor(), record)
    main_ctx = RequestContext(session_id="s1", kb_id="kb1", kb_bound=True)
    token = current_request_ctx.set(main_ctx)
    try:
        out = await graph.ainvoke(
            AgentState(session_id="s1", kb_id="kb1", query="q", direct_skill="finance-qa")
        )
    finally:
        current_request_ctx.reset(token)

    assert out["_needs_regenerate"] is False
    assert out["citations"] == []
```

> 实现者注意：`build_graph` 现有签名已含 `tools=None` 覆盖参数（`workflow.py:63`）；`vector_store=None` 等占位参数只有在 `tools` 已给出时才不会被使用。若 `build_graph` 对 `llm`/`prompt_manager` 有非 None 断言，改用轻量替身对象并在报告里写明。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/graph/test_entry_dispatch.py tests/agents/graph/test_direct_skill_round.py -v`
Expected: FAIL —— `ModuleNotFoundError: src.agents.graph.skill_direct`

- [ ] **Step 3: 常量与事件登记（先登记再启用）**

`src/config/const.py`：
```python
    class SkillDirect:
        NAME: str = "skill_direct"  # 命令行直出节点（/xxx 命中 fork skill）
```
`SSEInteractionTexts` 追加两条用户可见文案：
```python
    SKILL_DIRECT_UNAVAILABLE: str = "该技能不可直接执行，请去掉前缀后重试。"
    SKILL_DIRECT_CTX_UNAVAILABLE: str = "Error: 请求上下文不可用"
```
`src/core/log_events.py`：`Event` 加 `SKILL_DIRECT_SKIP = "skill_direct_skip"`；`src/core/log_event_specs.py` 加对应 `EventSpec`（prefix 用 `"agent"`，level `"info"`，fields `("skill", "reason")`）——两处必须同名，否则 import 期 `AssertionError`。

- [ ] **Step 4: `state.py` 加字段**

```python
    direct_skill: str = ""  # 本轮命令行直出的 fork skill 名（来源：AgentService 解析 /xxx 后注入初始 state，Plan 3 填值；用途：入口分派与重生成目标；空=常规轮）
```

- [ ] **Step 5: 新建 `src/agents/graph/skill_direct.py`**

```python
"""命令行直出节点（D22/D24/D26）。

/xxx 命中 fork skill 时，主 agent 零 LLM 轮：直接跑 fork 子代理，把子代理的
answer 与"本轮材料"（引用池 + 要求覆盖年份）搬进 AgentState，再交给 verify/format。
子代理跑在自己的 RequestContext 里，主 ctx 不被写入（D7/D24）。
"""

import uuid

from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.models import SkillContext
from src.config.const import SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx


def route_entry(state: AgentState) -> str:
    """入口分派：命中命令行直出 → skill_direct；其余 → 常规 agent 轮。"""
    if state.direct_skill:
        return LangGraphNode.SkillDirect.NAME
    return "agent"


def make_skill_direct_node(skill_registry, executor):
    """构造直出节点。

    Args:
        skill_registry: SkillRegistry（懒重载后按名解析 SkillRecord）
        executor: SkillExecutor（经 execute(record, task, run) 跑 fork 子代理）

    Returns:
        图节点函数：写 answer / tool_contexts / verify_temporal_years
    """

    async def skill_direct(state: AgentState) -> dict:
        main_ctx = current_request_ctx.get()
        if main_ctx is None:
            return {
                "answer": SSEInteractionTexts.SKILL_DIRECT_CTX_UNAVAILABLE,
                "_needs_regenerate": False,
            }
        skill_registry.reload_if_changed()
        record = skill_registry.get(state.direct_skill)
        if record is None or record.context != SkillContext.FORK:
            core_logging.log_event(
                Event.SKILL_DIRECT_SKIP, skill=state.direct_skill, reason="not_fork"
            )
            return {
                "answer": SSEInteractionTexts.SKILL_DIRECT_UNAVAILABLE,
                "_needs_regenerate": False,
            }
        run = DelegateRun(
            delegate_id=uuid.uuid4().hex[:8], skill_name=record.name, ctx=main_ctx.child()
        )
        text = await executor.execute(record, state.query, run)
        return {
            "answer": text,
            "tool_contexts": run.ctx.tool_contexts,
            "verify_temporal_years": run.ctx.temporal_years,
        }

    return skill_direct


def unavailable_skill_direct(state: AgentState) -> dict:
    """未装配 executor 时的兜底直出节点（fail-open，让 verify/format 正常收尾）。"""
    return {
        "answer": SSEInteractionTexts.SKILL_DIRECT_UNAVAILABLE,
        "_needs_regenerate": False,
    }
```

- [ ] **Step 6: `workflow.py` 接入入口与路由**

- 追加形参 `skill_direct_node=None`，导入 `from langgraph.graph import END, START, StateGraph`。
- 注册节点（**恒注册**，避免"条件边映射到不存在的节点"）：
```python
    builder.add_node(
        LangGraphNode.SkillDirect.NAME,
        skill_direct_node if skill_direct_node is not None else unavailable_skill_direct,
    )
```
- 入口改为条件边，并删掉 `builder.set_entry_point("agent")`：
```python
    builder.add_conditional_edges(
        START,
        route_entry,
        {"agent": "agent", LangGraphNode.SkillDirect.NAME: LangGraphNode.SkillDirect.NAME},
    )
```
- 直出节点 → verify：
```python
    builder.add_edge(LangGraphNode.SkillDirect.NAME, "verify")
```
- `route_verify` 增回直出的分支：
```python
def route_verify(state: AgentState) -> str:
    if state._needs_regenerate:
        if state.direct_skill:
            return LangGraphNode.SkillDirect.NAME
        return "agent"
    return LangGraphNode.Format.NAME
```
- docstring 同步（入口/直出节点/回直出）。

- [ ] **Step 7: `agent_service` 传入直出节点**

`skill_registry` / `skill_executor` 都在 `__init__` 里已存在（skills 分支内）；把它们提到分支外可见的位置（未命中时置 `None`），再：
```python
        skill_direct_node = None
        if skill_registry is not None and skill_executor is not None:
            skill_direct_node = make_skill_direct_node(skill_registry, skill_executor)
        self._graph = build_graph(..., skill_direct_node=skill_direct_node)
```
（`make_skill_direct_node` 从 `src.agents.graph.skill_direct` 本地导入。`direct_skill` 的**填值**属 Plan 3，本任务不接线。）

- [ ] **Step 8: 跑测试确认通过**

Run: `pytest tests/agents/graph/ -v && pytest tests/ -q`
Expected: PASS —— 既有图测试全部不变通过（`direct_skill=""` 时入口行为与 `set_entry_point("agent")` 等价）

- [ ] **Step 9: 提交**

```bash
git add src/agents/graph/skill_direct.py src/agents/graph/state.py src/agents/graph/workflow.py src/config/const.py src/core/log_events.py src/core/log_event_specs.py src/services/agent_service.py tests/agents/graph/test_entry_dispatch.py tests/agents/graph/test_direct_skill_round.py
git commit -m "feat(graph): 图入口分派 + skill_direct 直出节点，引用池与校验判据随材料走（D24/D26）"
```

> **R23（记录，不在本计划实现）**：直出路径**不发** `delegate` start/end SSE 事件（子代理 delta 会经共享 `clarify_channel` 流出，但没有"开始/结束"包裹）。前端卡片需要 start/end 时由 Plan 3 补（触发方与 UI 契约都在那里）。**若判断有误**：直出轮 UI 只有增量、没有卡片头尾，观感缺一截，Plan 3 补 20 行即可。

---

### Task 9: 收口（事件登记 / 防复发 / 部署 / 文档 / 门禁）

**Files:**
- Modify: `docker-compose.override.yml`（挂载 `agents/`）
- Modify: `docs/agents/defensive-patterns.md`（登记本次两类缺陷）
- Modify: `docs/agents/api_contract.md`（`SkillExecutor.execute` 签名与 `DelegateRun` 契约）
- Modify: `docs/agents/code-map.md`（`fork_stream.py` / `fork_tools.py` / `delegate_run.py` / `skill_direct.py` / `presets/`）
- Modify: `docs/agents/glossary.md`（如需：直出节点、执行者选择术语）
- Modify: `docs/openspec/changes/session-agent-and-skill-invocation/tasks.md`（分工标注回写）
- Test: 全量门禁

- [ ] **Step 1: 开发环境挂载 `agents/`（R20）**

`docker-compose.override.yml` 的 app volumes 追加一行（与 `skills/` 对齐）：
```yaml
      - /mnt/d/code/demo/AIAgent/corporate_rag/agents:/app/agents
```
验证：`docker compose config | grep -A 8 "app:"` 能看到该挂载；若容器在运行，`docker compose up -d --force-recreate app` 后 `docker compose exec app ls /app/agents` 能列出 `finance-expert.md`。

- [ ] **Step 2: 登记防御模式两条**

`docs/agents/defensive-patterns.md` 按"现象 → 规则"格式追加到对应章节（并发 / Prompt 或新章节）：
1. **进程级注册表不得在构造期快照**：现象——`SkillLoader` 在构造时快照 `readonly_map()`，而生产构造 loader 早于工具注册 → fail-safe 永不触发（Plan 1 最终评审发现）；规则——需要进程级事实时在**解析/使用期**惰性读取，不在构造期缓存。
2. **活跃状态按调用分槽，不挂在共享上下文单值字段上**：现象——一轮内多个 `delegate_task` 被 `asyncio.gather` 并发调度，把 `delegate_id`/停止原因写在共享 `RequestContext` 单值字段上会互相覆盖（串号）；规则——每次调用一个独立实例（`DelegateRun`）并由调用方逐层传递；若必须落 ctx，落**该次调用的独立子 ctx**。

- [ ] **Step 3: 契约 / 文档同步（一事一档，别处链接不复制）**

- `docs/agents/api_contract.md`：`SkillExecutor.execute(record, task, run=None) -> str` 的 `run` 语义（承载子 ctx / delegate_id / stop_reason / result_text）；`DelegateRun` 字段表；`select_fork_tools` 的交集口径与禁用集；`make_skill_direct_node` 的入参。
- `docs/agents/code-map.md`：新增模块登记（`fork_stream.py` / `fork_tools.py` / `delegate_run.py` / `skill_direct.py` / `presets/`）。
- `docs/agents/glossary.md`：按需补"直出（skill_direct）/ 执行者选择"术语，**不复制** design.md 正文，只留一句话 + 指针。

- [ ] **Step 4: 回写 change 的 tasks.md 分工标注**

`docs/openspec/changes/session-agent-and-skill-invocation/tasks.md`：
- 3.4 标注「由 Plan 2 提前完成」；
- 4.6（确认门）标注「移出 Plan 2 → Plan 3」（R22）；
- 4.7 补一句限定「确认门四个分支的测试随 4.6 进 Plan 3」；
- 4.11 的"按轮次选择来源"改述为「材料判据统一由 `AgentState` 承载（R5-amended）：`tool_contexts` 复用 + `verify_temporal_years` 新增；流程字段（`web_confirmed`/`verify_ask_count`/`web_guided`）仍读主 ctx」。

- [ ] **Step 5: 跑全量门禁**

```
pytest tests/ -v
ruff check .
pyright src/
python -m src.cli.check_docs
```

- [ ] **Step 6: 提交**

```bash
git add docker-compose.override.yml docs/agents/defensive-patterns.md docs/agents/api_contract.md docs/agents/code-map.md docs/agents/glossary.md docs/openspec/changes/session-agent-and-skill-invocation/tasks.md
git commit -m "docs(delegate): 收口执行层——防御模式/契约/文档/分工标注/开发挂载"
```


---

## Self-Review

**1. Spec coverage（对照 change 的 4.x，T1–T5 已交付，T6–T9 已按真实形状补全）**

| change 任务 | 覆盖 | 状态 |
|---|---|---|
| 4.1 独立 RequestContext + `delegate_id` 分槽 | T1 + T2 | ✅ 已交付 |
| 4.2 `create_agent` + 人设/任务分离 + 工具交集 | T3（seam）+ T4 | ✅ 已交付 |
| 4.3 执行者选择顺序 | T5 | ✅ 已交付 |
| 4.4 `_resolve_fork_llm` 简化 + `maxTurns` | Plan 1 已做 thinking/常量；`maxTurns` 在 T5 | ✅ 已交付 |
| 4.5 打破循环依赖 | T3（按 R2 前置） | ✅ 已交付 |
| 4.6 确认门 | **移出**（R22 → Plan 3）；本计划只落"子代理不持有交互/委派工具"的前提约束（T6） | ⏭ 移出 |
| 4.7 测试 | 各任务内嵌 + T9 门禁 | 进行中 |
| 4.8 预加载 | **移出**（R6 → Plan 3） | ⏭ 移出 |
| 4.9 直出引用池并轨 | T8（`state.tool_contexts` = 子代理池） | 待做 |
| 4.10 图入口分派 | T8（`route_entry` + `START` 条件边） | 待做 |
| 4.11 直出节点 + verify 语义适配 | T7（判据材料随 state）+ T8（直出节点 + `route_verify` 回直出） | 待做 |

**2. Placeholder scan**：T1–T5 已按"逐行可粘贴"粒度交付并落地；T6–T9 已按同一粒度补齐（关键代码块 + 完整测试代码 + 精确行号前提）。已知的**刻意留白**：
- T8 的"直出轮不发 delegate start/end 事件"是 R23 的显式延后（Plan 3 补）。
- T8 测试里 `build_graph(vector_store=None, ...)` 的最小图构造若与既有签名断言冲突，允许改用轻量替身，但必须在报告里写明。

**3. Type consistency（对照已落地代码）**：
- `DelegateRun(delegate_id, skill_name, ctx, stop_reason=None, result_text="")`（T1 定义 → T2/T8 消费）✅
- `select_fork_tools(allowed, available, executor_tools=None)`（T3 定义 → T4 消费；T6 追加禁用集）✅
- `SkillExecutor(main_llm, tool_provider=None, preset_registry=None)` / `.execute(record, task, run=None)`（T2/T3/T5 定，T8 消费）✅
- `consume_fork_events(sub_agent, run, user_content, skill_name, max_turns)`（T3/T4 定）✅
- `build_graph(..., tools=None, tool_sink=None, skill_direct_node=None)`（T3 定 tool_sink，T8 加 skill_direct_node）✅
- `AgentState.direct_skill: str` / `AgentState.verify_temporal_years: list[int]`（T8 / T7 定，互相消费）✅
- 节点名 `LangGraphNode.SkillDirect.NAME`（T8 定，workflow/route_verify 消费）✅

**4. 阻塞与前置**：R1（preset 装配）在 T5 内完成 ✅；T7 不依赖 T6；T8 依赖 T4/T5/T7；`/xxx` 触发属 Plan 3（R8）；确认门属 Plan 3（R22）。**T6 → T7 → T8 → T9 为串行**（T7/T8 同改 `state.py`，T8 消费 T7 的字段）。
