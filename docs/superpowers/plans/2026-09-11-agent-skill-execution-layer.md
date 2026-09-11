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
- Modify: `src/agents/skills/executor.py`（`_resolve_executor(record)`）
- Modify: `src/services/agent_service.py`（构造 `AgentPresetRegistry` 并注入 executor + 会话 agent 名来源）
- Test: `tests/agents/skills/test_fork_executor_selection.py`（新建）

**Interfaces:**
- Consumes: Task 4 的 `_build_sub_agent(record, preset, task)`；`src/agents/presets/`（Plan 1 交付）
- Produces: 执行者优先级 `skill.agent` > 会话选定智能体 > 系统默认；`maxTurns` 取 `preset.max_turns`，空则既有 `DELEGATE_DEFAULT_MAX_TURNS`

- [ ] **Step 1: 写失败测试**（三条优先级 + maxTurns 回落）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现 `_resolve_executor`**：

```python
    def _resolve_executor(self, record: SkillRecord, session_agent: str) -> AgentPreset | None:
        """按优先级选执行者预设：skill.agent > 会话智能体 > None（系统默认）。"""
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
```
会话智能体名从 `current_request_ctx.get()` 上的新字段读取（Plan 3 会写它；本任务先在 ctx 上加 `agent: str = ""` 供 Plan 3 填充，读不到即空串 → 落系统默认）。

- [ ] **Step 4: 跑测试确认通过** → [ ] **Step 5: 提交**

---

### Task 6: 确认门（仅直出轮）与重跑预算互斥

**Files:**
- Create: `src/agents/graph/verify/confirm_gate.py`
- Modify: `src/agents/graph/verify/node.py`（直出轮先过确认门）
- Test: `tests/agents/graph/test_confirm_gate.py`（新建）

**Interfaces:**
- Consumes: 既有 `ask_user`/`pending_asks` 单槽与 `SSEInteractionTexts`
- Produces: `async def confirm_gate(state, run) -> dict | None`：检测到"需确认"信号 → 走 `ask_user` → 返回重跑指令（上限 1 次）；**任何不通过路径返回 `{"_needs_regenerate": False}` 且带标注**，不再进入 verify 重跑（R7）

- [ ] **Step 1~4: TDD 四个分支**（请求确认→答复→重跑；无信号直通；拒绝/超时→出结论+标注；预算不与 verify 叠加）
- [ ] **Step 5: 提交**

---

### Task 7: 直出路径的引用池回传（D24）

**Files:**
- Modify: `src/agents/skills/delegate_run.py`（`result_text` 已含；`ctx` 提供引用池）
- Modify: `src/agents/graph/state.py`（`tool_contexts` 复用；无需新字段——由 Task 8 的直出节点写入）
- Test: `tests/agents/skills/test_fork_citation_pool.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 `DelegateRun`；既有 `format_node`（已读 `state.tool_contexts`）
- Produces: 直出轮 `format` 的引用池 = 子代理池（`run.ctx.tool_contexts`），不改写主 ctx

- [ ] **Step 1~3: TDD**：构造一个子代理在子池写入 2 条 + 答案含 `[1][2]` 的直出轮，断言 `citations` 非空（index 1/2）、`INVALID_CITATION` 信号**未**产生、主池仍为空。
- [ ] **Step 4: 提交**

---

### Task 8: 图入口分派 + `skill_direct` 节点 + verify 判据统一（D26）

**Files:**
- Modify: `src/agents/graph/state.py`（新增 `direct_skill: str = ""` 与 `verify_inputs: VerifyInputs | None = None`；新增 `VerifyInputs` dataclass）
- Create: `src/agents/graph/verify/inputs.py`（`VerifyInputs` + `verify_inputs_from_ctx(ctx)`）
- Modify: `src/agents/graph/workflow.py`（入口条件边 + `skill_direct` 节点 + `route_verify` 增回直出的路由）
- Modify: `src/agents/graph/agent_node.py`（`agent_finalize` 顺带写 `verify_inputs`）
- Modify: `src/agents/graph/verify/node.py` + `guardrails.py`（判据改读 `VerifyInputs`）
- Modify: `src/services/agent_service.py`（初始 state 注入 `direct_skill`——Plan 3 填值，本任务可由测试直接注入）
- Test: `tests/agents/graph/test_entry_dispatch.py`、`tests/agents/graph/test_direct_verify_source.py`（新建）

**Interfaces:**
- Consumes: Task 4/5 的 executor；Task 7 的引用池回传
- Produces: `route_entry(state) -> str`（`"agent"` | `"skill_direct"`）；`make_skill_direct_node(executor, preset_registry)`；`VerifyInputs(tool_contexts, temporal_years, missing_years, web_confirmed, verify_ask_count, web_guided)`

- [ ] **Step 1~6: TDD 要点**
  1. `route_entry`：`direct_skill` 非空 → `skill_direct`；空 → `agent`（既有行为不变）
  2. 直出节点：调 executor → 写 `answer` / `tool_contexts`(=子池) / `verify_inputs`(=子 ctx 快照)；**主 agent 轮次为 0**（用 sink 计数断言 `agent` 节点未被调用）
  3. `verify_node` 改用 `state.verify_inputs or verify_inputs_from_ctx(ctx)`；`agent_finalize` 常规轮写入该快照 → **常规轮行为逐字不变**（快照与旧 ctx 读取等价）
  4. `route_verify`：直出轮 `_needs_regenerate` → `skill_direct`；常规轮 → `agent`
  5. 护栏改吃 `VerifyInputs` 后，直出轮**不再静默跳过**（构造"子池有 kb 材料 + 答案无 `[n]`" → 断言护栏触发）
- [ ] **Step 7: 跑测试确认通过** → [ ] **Step 8: 提交**

---

### Task 9: 收口（事件登记 / 防复发 / 文档 / 门禁）

**Files:**
- Modify: `src/core/log_events.py`（登记 `delegate slot`… 等新事件）
- Modify: `docs/agents/defensive-patterns.md`（登记本次两类缺陷：活跃状态按 id 分槽、构造期快照进程级注册表）
- Modify: `docs/agents/api_contract.md`（`execute()` 签名与 `DelegateRun` 契约）
- Modify: `docs/agents/code-map.md`（`fork_stream.py` / `fork_tools.py` / `delegate_run.py` / `verify/inputs.py`）
- Modify: `docs/openspec/changes/session-agent-and-skill-invocation/tasks.md`（4.8 标注移入 Plan 3；3.4 标注由 Plan 2 完成；4.7 限定"确认门仅直出轮"）
- Test: 全量门禁

- [ ] **Step 1: 登记日志事件**（按"开放登记制"先 `Event` + `EVENT_SPECS` 再启用）
- [ ] **Step 2: 登记防御模式两条**（现象→规则格式）
- [ ] **Step 3: 契约/文档同步**（一事一档，别处链接不复制）
- [ ] **Step 4: 回写 change 的 tasks.md 分工标注**（R1/R6/R7 落地）
- [ ] **Step 5: 跑全量门禁**（`pytest tests/ -v` / `ruff check .` / `pyright src/` / `check_docs`）
- [ ] **Step 6: 提交**

---

## Self-Review

**1. Spec coverage（对照 change 的 4.x）**

| change 任务 | 覆盖 |
|---|---|
| 4.1 独立 RequestContext + `delegate_id` 分槽 | T1 + T2 |
| 4.2 `create_agent` + 人设/任务分离 + 工具交集 | T3（seam）+ T4 |
| 4.3 执行者选择顺序 | T5 |
| 4.4 `_resolve_fork_llm` 简化 + `maxTurns` | Plan 1 已做 thinking/常量部分；`maxTurns` 在 T5 |
| 4.5 打破循环依赖 | T3（按 R2 前置） |
| 4.6 确认门 | T6 |
| 4.7 测试 | 各任务内嵌 + T9 门禁 |
| 4.8 预加载 | **移出**（R6 → Plan 3） |
| 4.9 直出引用池并轨 | T7 |
| 4.10 图入口分派 | T8 |
| 4.11 直出节点 + verify 语义适配 | T8 |

**2. Placeholder scan**：Task 5–9 的部分步骤以要点+验收标准给出（未逐行贴码）——**这是本计划的已知缺口**：Task 5–9 在执行前需按 Plan 1 的粒度补全代码块与测试代码。执行时若某步无码可实现，先补步再实现。

**3. Type consistency**：`DelegateRun(delegate_id, skill_name, ctx, stop_reason, result_text)`（T1 定义 → T2/T5/T7/T8 消费）；`select_fork_tools(allowed, available, executor_tools=None)`（T3 定义 → T4 消费）；`VerifyInputs`（T8 定义并在 T8/T9 消费）；`SkillExecutor.execute(record, task, run=None)`（T2 定 → T5/T8 消费）。命名已核对一致。

**4. 阻塞与前置**：R1（preset 装配）在 T5 内完成；T8 依赖 T4/T5/T7；`/xxx` 触发属 Plan 3（R8）。
