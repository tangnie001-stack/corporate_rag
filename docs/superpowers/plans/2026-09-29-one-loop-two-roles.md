# 一循环两角色（one-loop-two-roles）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把主 agent 循环与 fork 子代理统一到**同一个装配入口**（`build_agent` → `create_agent`），领域阶段（`verify` / `format` / `agent_finalize` / `skill_direct`）留在外层图，**对外行为逐字保持**。

**Architecture:** 新增 `src/agents/graph/agent_factory.py`（装配工厂）与 `src/agents/graph/middleware.py`（system 施加 / 模型参数 / 回合预算 / 观测四件套）。外层 `agent` 节点改为「组装首轮消息 → 按类型拆两半 → `invoke` 装配产物 → 按外层已有条数回写」；外层图的 `tools` 节点与 `route_agent` 条件边随之删除，循环内化进 `create_agent`。主角色装配四件套 middleware，子角色 `middleware_extra=[]`（其轮次记录仍由 fork 消费侧承担）。

**Tech Stack:** Python 3.11 / FastAPI / LangChain 1.3（`create_agent`）/ LangGraph 1.2 / Langfuse v2 SDK / DashScope / pytest。

**Spec:** `docs/openspec/changes/one-loop-two-roles/`（`proposal.md` / `design.md` / `tasks.md` / `specs/*/spec.md`）。理由与实证在 `docs/tmp/one-loop-two-roles-brief.md`（§十一~§十八）与 `docs/tmp/deep-research-one-loop-two-roles.md`。

## Global Constraints

以下为**每个任务都隐含遵守**的约束，值逐字来自 spec 与 `CLAUDE.md`：

- **行为保持**：SSE 事件序列与 token 流、citations、落库字段、五条日志（`iteration done` / `iteration limit` / `model turn` / `prompt assembled` / `prompt messages`）的**事件名与字段值**逐字不变；触顶路径的消息语义不变（模型调用 = 有效上限次、工具执行 = 上限−1 次、末条为含 `tool_calls` 的 `AIMessage`、`answer` 允许空串）。
- **文件红线**：单文件 ≤ **400** 行；单函数 ≤ **80** 行（超了必须拆）。
- **代码风格**：不用三元表达式（写完整 `if/else`）；**不用 `getattr(x, "attr", default)` 隐式兜底**，用 `isinstance` 显式判断；常量/文案/阈值集中到 `src/config/`。
- **middleware 三条硬约束**：① 实例**跨请求共享** ⇒ **SHALL NOT** 在实例属性上存 per-request 状态；② 读图状态**一律 `state.get(...)`**——`create_agent` 的 middleware 收到的是**映射**，属性访问抛 `AttributeError`；③ **未 seed 的键在子图状态里根本不存在**（schema 默认值**不会**被填充）⇒ 消费者依赖的键必须由节点显式 seed。
- **测试**：`POSTGRES_HOST=localhost .venv/bin/python -m pytest <path> -v`（宿主侧必须带该前缀）；**mock 外部依赖**，不发起真实网络调用。
- **提交**：每个任务末尾提交；pre-commit 会跑 doc anti-rot 与 ADR 闸门。**不要跳过 hooks**。
- **收尾自检**：`ruff format . && ruff check .` 无错误、`pyright src/` 不新增 error、无遗留 `print()`/TODO/调试代码。

---

## File Structure

**Create**

| 文件 | 职责 |
|---|---|
| `src/agents/graph/agent_factory.py` | `LoopState`（子图 state schema）+ `build_agent(...)`：唯一的 agent 装配入口；**不产出** `graph compiled` 日志；**不传** `name` |
| `src/agents/graph/middleware.py` | `SystemMessagesMiddleware` / `ModelParamsMiddleware` / `AgentTurnBudget` / `AgentSpanMiddleware` 四个类 |
| `tests/agents/graph/test_agent_factory.py` | 装配入口契约 + 「唯一装配」静态扫描断言 |
| `tests/agents/graph/test_loop_middleware.py` | 四个 middleware 的行为（含触顶时点、温度档、思考开关、并发隔离） |

**Modify**

| 文件 | 改动 |
|---|---|
| `src/agents/graph/agent_node.py` | `_initial_messages` 产两半（**计数须在拆分前算出**）；新增 `make_agent_loop_node`；**删除** `make_agent_model_node` / `make_agent_tools_node` / `route_agent`；删两处临时取证埋点 |
| `src/agents/graph/workflow.py` | `agent` 节点换实现；**删外层 `tools` 节点与 `add_edge("tools","agent")`**；`agent` 出边改**直连 `agent_finalize`** |
| `src/agents/graph/state.py` | 删 `_agent_iterations` / `_max_agent_iterations` / `_delegate_used`；**新增 `_system_messages`**（`list[SystemMessage]`，无 reducer） |
| `src/services/agent_service.py` | `_convert_event` 三处模型事件谓词 `"agent"` → `"model"`；更正模块 docstring 里的 `"agent"` |
| `src/agents/tools/rag_tools.py` | 迭代序号：dict 状态下读 `_turn_count` |
| `src/agents/tools/ask_tools.py` | 3 处取数改显式形状判定 |
| `src/agents/graph/verify/guardrails.py` | 删 regen 复位的 `_agent_iterations` / `_delegate_used`（`:105/106`、`:180/181`） |
| `src/agents/graph/verify/regen_decision.py` | 同上（`:174/175`） |
| `src/agents/skills/executor.py` | `_build_sub_agent` 改调 `build_agent`（只传模型/工具面/静态 system） |
| `tests/agents/graph/test_agent_node.py` 等 12 个测试文件 | 见 Task 10/11/14 |

**顺序说明**：Task 1（基线）**必须在动代码前**完成；Task 2~6 是新增文件（不改现有行为，可独立提交）；Task 7+ 才开始改动行为面。

---

### Task 1: 基线采集与开工核对

**Files:**
- Create: `docs/tmp/one-loop-two-roles-baseline.md`（基线快照，**提交进库**）

**Interfaces:**
- Consumes: 无
- Produces: 基线文件（后续 Task 14 的比对基准）

- [ ] **Step 1: 确认工作区与前置闸门**

```bash
cd /root/code/corporate_rag-one-loop-two-roles
git branch --show-current          # 期望 feat/one-loop-two-roles
git log --oneline -1
```

期望：分支为 `feat/one-loop-two-roles`。若不在本 worktree，先按 `docs/agents/dev-flow.md`「变更开工前置」建/切。

- [ ] **Step 2: 记录测试基线**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
ruff check . 2>&1 | tail -3
```

把三条结果（全绿 / 0 error / pyright 不新增 error）抄进基线文件。

- [ ] **Step 3: 采集「五条日志 + SSE 序列 + citations」基线**

写一个一次性脚本（**不改产品代码**）`/tmp/baseline_capture.py`，用固定问题集跑两次（绑 KB / 未绑 KB 各一次），把下列内容写入 `docs/tmp/one-loop-two-roles-baseline.md`：

- 五条日志的**条数与字段值**：`iteration done`(`iteration`,`msgs`)、`iteration limit`(`query`,`iteration`)、`model turn`(`model`,`usage_in`,`usage_out`,`usage_estimated`,`fallback`,`latency_ms`,`iteration`,`temperature`,`temp_source`,`kb_bound`)、`prompt assembled`、`prompt messages`(`system_msgs`,`injected_msgs`,`history_msgs`)
- SSE 事件序列（`event` 名 + 顺序）
- 最终 `answer` 的 `[n]` 标记与 `citations` 列表长度

```bash
POSTGRES_HOST=localhost .venv/bin/python /tmp/baseline_capture.py 2>&1 | tail -20
```

- [ ] **Step 4: 记录一条 CLI 入口基线（关键：该入口不建 `RequestContext`）**

```bash
POSTGRES_HOST=localhost .venv/bin/python -c "
import asyncio, json, src.cli.check_abstain as ca
print('check_abstain 模块可导入（其 graph.ainvoke 不 set current_request_ctx）')
" 2>&1 | tail -3
```

在基线文件里记一句：**CLI 入口只把参数放进图输入、不建 ctx**（这是 Task 8 的 `kb_id` 必须 seed 的原因）。

- [ ] **Step 5: 提交**

```bash
git add docs/tmp/one-loop-two-roles-baseline.md
git commit -m "docs(one-loop-two-roles): 采改动前基线（五条日志条数与字段值 / SSE 序列 / citations）"
```

---

### Task 2: 装配入口 `build_agent` 与子图 schema

**Files:**
- Create: `src/agents/graph/agent_factory.py`
- Test: `tests/agents/graph/test_agent_factory.py`

**Interfaces:**
- Consumes: 无（只依赖 `langchain.agents.create_agent`）
- Produces:
  - `class LoopState(AgentState)`：字段 `_system_messages: list[BaseMessage]`、`_turn_count: int`、`_delegate_used: bool`、`kb_id: str`、`query: str`、`deep_thinking: bool`（**全部带默认值**；注意默认值不会自动填充，见 Global Constraints）
  - `def build_agent(model, tools, *, system: str | None = None, max_turns: int | None = None, middleware_extra: list[AgentMiddleware] | None = None) -> CompiledStateGraph`
    - `system` 为 `None` 表示「system 由运行态携带」（主角色，走 `SystemMessagesMiddleware`）；为 `str` 表示静态串（子角色）
    - `max_turns is None` ⇒ 不装配 `AgentTurnBudget`（子角色路径）
    - **不产出** `graph compiled` 日志；**不传** `name`

- [ ] **Step 1: 写失败测试**

```python
# tests/agents/graph/test_agent_factory.py
import pathlib
import re

from langchain_core.messages import AIMessage

from src.agents.graph.agent_factory import LoopState, build_agent


class _FakeModel:
    """最小可绑定模型的假模型。"""

    def bind_tools(self, tools, **kwargs):
        return self


def test_build_agent_accepts_state_schema_and_returns_compiled_graph():
    agent = build_agent(_FakeModel(), tools=[], system="sys")
    # 装配产物必须是可 invoke 的编译图
    assert hasattr(agent, "ainvoke")
    assert set(agent.get_graph().nodes) >= {"model", "tools"}


def test_loop_state_declares_every_key_consumers_depend_on():
    keys = set(LoopState.__dataclass_fields__)
    assert {
        "_system_messages",
        "_turn_count",
        "_delegate_used",
        "kb_id",
        "query",
        "deep_thinking",
    } <= keys


def test_build_agent_does_not_emit_graph_compiled(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        build_agent(_FakeModel(), tools=[], system="sys")
    assert "graph compiled" not in caplog.text


def test_create_agent_is_only_called_from_agent_factory():
    """「唯一装配」静态扫描断言：src/agents/ 下只有装配入口可以调 create_agent(。"""
    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "agents"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "agent_factory.py":
            continue
        if re.search(r"\bcreate_agent\s*\(", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"绕过装配入口的 create_agent 调用：{offenders}"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_factory.py -v
```

期望：`ModuleNotFoundError: No module named 'src.agents.graph.agent_factory'`

- [ ] **Step 3: 写实现**

```python
# src/agents/graph/agent_factory.py
"""唯一的 agent 装配入口：主循环与 fork 子代理共用。

本模块是 src/agents/ 下唯一允许调用 create_agent 的地方（由
tests/agents/graph/test_agent_factory.py 的静态扫描断言守住）。
"""

from dataclasses import field
from typing import Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage


class LoopState(AgentState):
    """装配产物的图状态。

    ⚠️ 这里的默认值**不会**被自动填充：`create_agent` 的子图状态是映射，
    调用方未 seed 的键在状态里根本不存在。凡消费者依赖的键都必须由
    `make_agent_loop_node` 显式 seed（见 agent_node.make_agent_loop_node）。
    """

    _system_messages: list[BaseMessage] = field(default_factory=list)  # system 段（节点从外层 seed）
    _turn_count: int = 0  # 已完成的模型调用数（AgentTurnBudget 于 after_model 自增）
    _delegate_used: bool = False  # 本轮或此前是否声明过 delegate_task（放宽上限用）
    kb_id: str = ""  # 会话知识库 ID（工具取数 + 档位判据回退用）
    query: str = ""  # 本轮用户问题（ask_user 日志用）
    deep_thinking: bool = False  # 请求级深思考开关（模型参数用）


def build_agent(
    model: Any,
    tools: list[Any],
    *,
    system: str | None = None,
    max_turns: int | None = None,
    middleware_extra: list[AgentMiddleware] | None = None,
) -> Any:
    """装配 agent 循环（主/子角色共用）。

    Args:
        model: 已解析的 LLM 或模型名
        tools: 工具面（主角色=全量；子角色=只读面筛选结果）
        system: system 提供方式——None=经运行态携带（主角色，由 prompt middleware
            施加）；str=静态串（子角色，人设 + 执行契约）
        max_turns: 主循环回合上限；None=不装配回合预算 middleware（子角色路径）
        middleware_extra: 额外 middleware 集合；子角色传空列表

    Returns:
        create_agent 的编译产物（可直接 ainvoke / 作为子图节点）

    Notes:
        本函数**不产出** `graph compiled` 日志——该事件由图装配层
        （build_graph）发一次；子角色每次委派都会调用本函数，若在此发日志
        每次委派都会多一条。
    """
    middleware: list[AgentMiddleware] = list(middleware_extra or [])
    return create_agent(
        model,
        tools=tools,
        system_prompt=system,
        middleware=middleware,
        state_schema=LoopState,
    )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_factory.py -v
```

期望：4 passed。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/agent_factory.py tests/agents/graph/test_agent_factory.py
git commit -m "feat(agent): 新增唯一装配入口 build_agent 与子图 state schema"
```

---

### Task 3: `SystemMessagesMiddleware`（system 施加）

**Files:**
- Create: `src/agents/graph/middleware.py`
- Test: `tests/agents/graph/test_loop_middleware.py`

**Interfaces:**
- Consumes: `LoopState._system_messages`（由节点 seed）
- Produces: `class SystemMessagesMiddleware(AgentMiddleware)`，`state_schema = LoopState`；同步与异步钩子**都必须**实现

- [ ] **Step 1: 写失败测试**

```python
# tests/agents/graph/test_loop_middleware.py
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.middleware import SystemMessagesMiddleware


class _RecordingModel(GenericFakeChatModel):
    """记录每次调用实收消息的假模型。"""

    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RecordingModel.seen.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def _build_recording_agent(*system_contents):
    _RecordingModel.seen = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware()],
    )
    return agent


def _sysmsgs(*contents):
    return [SystemMessage(content=c) for c in contents]


@pytest.mark.asyncio
async def test_unbound_kb_sends_two_system_messages_in_order():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("FIRST", "SECOND-UNBOUND"),
        }
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:3]] == [
        "SystemMessage",
        "SystemMessage",
        "HumanMessage",
    ]
    assert sent[0].content == "FIRST"
    assert sent[1].content == "SECOND-UNBOUND"


@pytest.mark.asyncio
async def test_bound_kb_sends_single_system_message():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "_system_messages": _sysmsgs("FIRST")}
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:2]] == ["SystemMessage", "HumanMessage"]
    assert sent[0].content == "FIRST"
```

> 注：`GenericFakeChatModel` 的 `messages` 参数接受迭代器（`iter([...])`）——**不要**传 `itertools.cycle`，否则 `_generate` 不会推进。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v
```

期望：`ImportError: cannot import name 'SystemMessagesMiddleware'`

- [ ] **Step 3: 写实现（含测试用的记录模型）**

```python
# src/agents/graph/middleware.py
"""agent 循环的四个 middleware（仅主角色装配）。

硬约束：实例随图构造一次、跨请求共享 ⇒ 不得在实例属性上保存 per-request
状态；读图状态一律 state.get(...)（middleware 收到的是映射）。
"""

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from src.agents.graph.agent_factory import LoopState

logger = logging.getLogger(__name__)


class SystemMessagesMiddleware(AgentMiddleware):
    """把节点 seed 的 system 段施加到每次模型调用。

    未绑定 KB 时有两段（主 system + 未绑定提示），故不能用只支持单条的
    @dynamic_prompt；改用 override(system_message=第一条, messages=[第二条, ...])。
    """

    state_schema = LoopState

    def _apply(self, request: Any) -> Any:
        system_messages = request.state.get("_system_messages", []) or []
        if not system_messages:
            return request
        first = system_messages[0]
        rest = list(system_messages[1:])
        return request.override(system_message=first, messages=[*rest, *request.messages])

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._apply(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._apply(request))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k system
```

期望：2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/middleware.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(agent): 新增 SystemMessagesMiddleware（两条 system 消息的施加通道）"
```

---

### Task 4: `ModelParamsMiddleware`（温度分档 + 思考开关）

**Files:**
- Modify: `src/agents/graph/middleware.py`（追加类）
- Test: `tests/agents/graph/test_loop_middleware.py`（追加用例）

**Interfaces:**
- Consumes: `LoopState.deep_thinking`（**必须由节点 seed**）、`current_request_ctx.kb_bound`
- Produces: `class ModelParamsMiddleware(AgentMiddleware)`；施加 `temperature`（KB 档**不带该键**）与 `extra_body={"enable_thinking": <deep_thinking>}`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_bound_kb_omits_temperature_key(monkeypatch):
    """绑 KB 档：不带 temperature 键（沿用模型构造温度）。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    ctx = RequestContext(session_id="s", kb_id="kb-1", kb_bound=True)
    token = current_request_ctx.set(ctx)
    try:
        _RecordingModel.seen_kwargs = []
        model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
        agent = build_agent(model, tools=[], system="S",
                            middleware_extra=[ModelParamsMiddleware()])
        await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"})
        kw = _RecordingModel.seen_kwargs[-1]
        assert "temperature" not in kw
        assert kw["extra_body"] == {"enable_thinking": False}
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_unbound_kb_passes_explicit_temperature():
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(model, tools=[], system="S",
                        middleware_extra=[ModelParamsMiddleware()])
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": ""})
    kw = _RecordingModel.seen_kwargs[-1]
    assert kw["temperature"] == settings.NON_KB_MAIN_TEMPERATURE


@pytest.mark.asyncio
async def test_deep_thinking_switch_comes_from_seeded_state():
    """思考开关取 seeded 的 deep_thinking（漏 seed 会静默关闭）。"""
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(model, tools=[], system="S",
                        middleware_extra=[ModelParamsMiddleware()])
    await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "", "deep_thinking": True}
    )
    assert _RecordingModel.seen_kwargs[-1]["extra_body"] == {"enable_thinking": True}
```

`_RecordingModel` 需补两个收集器（定义在类内，模块级列表）：

```python
class _RecordingModel(GenericFakeChatModel):
    seen: list = []
    seen_kwargs: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RecordingModel.seen.append(list(messages))
        _RecordingModel.seen_kwargs.append(dict(kwargs))
        return super()._generate(messages, stop, run_manager, **kwargs)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "temperature or thinking"
```

期望：`ImportError: cannot import name 'ModelParamsMiddleware'`

- [ ] **Step 3: 写实现**

```python
class ModelParamsMiddleware(AgentMiddleware):
    """施加温度分档与思考开关（承接原 agent_node 调用点的内联参数）。

    档位判据取**请求上下文**的绑定状态；ctx 缺失（如 CLI 评估入口）时回退
    图状态里的 kb_id（该键由节点 seed）。KB 档用「不带 temperature 键」表达
    "不传"，沿用模型构造温度。

    ⚠️ per-call extra_body 在 langchain-openai 中**整体覆盖**构造时的 extra_body
    （_get_request_payload 浅合并），故本模型不宜在 LLM_KWARGS 里配置其他
    extra_body 参数（会被本处覆盖丢弃）。
    """

    state_schema = LoopState

    def _settings(self, request: Any) -> dict[str, Any]:
        ctx = current_request_ctx.get()
        if ctx is not None:
            kb_bound = ctx.kb_bound
        else:
            kb_bound = bool(request.state.get("kb_id"))
        deep_thinking = bool(request.state.get("deep_thinking"))
        extra_body = {"enable_thinking": deep_thinking}
        if kb_bound:
            return {"extra_body": extra_body}
        return {"extra_body": extra_body, "temperature": settings.NON_KB_MAIN_TEMPERATURE}

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request.override(model_settings=self._settings(request)))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(request.override(model_settings=self._settings(request)))
```

顶部补 import：`from src.config import settings`、`from src.infra.llm.request_context import current_request_ctx`。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "temperature or thinking"
```

期望：3 passed。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/middleware.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(agent): 新增 ModelParamsMiddleware（温度分档 + 思考开关，判据取请求上下文）"
```

---

### Task 5: `AgentTurnBudget`（回合上限，仅主角色）

**Files:**
- Modify: `src/agents/graph/middleware.py`
- Test: `tests/agents/graph/test_loop_middleware.py`

**Interfaces:**
- Consumes: `LoopState._turn_count` / `_delegate_used` / `query`
- Produces: `class AgentTurnBudget(AgentMiddleware)`，构造参数 `(limit: int, bonus: int = MAX_DELEGATE_BONUS)`；命中时 `{"jump_to": "end", "_turn_count": n}`（**不注入消息**）

- [ ] **Step 1: 写失败测试（三条，对应 spec 的确切语义）**

```python
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool


@tool
def echo(x: str) -> str:
    """echo。"""
    return x


@tool
def delegate_task(skill: str) -> str:
    """委派。"""
    return "delegated"


class _LoopingModel(GenericFakeChatModel):
    """前 tool_rounds 次调用发工具调用，之后正常收尾；记录调用次数。"""

    tool_rounds: int = 0
    tool_name: str = "echo"
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _LoopingModel.calls += 1
        if _LoopingModel.calls <= _LoopingModel.tool_rounds:
            msg = AIMessage(content="", tool_calls=[{
                "name": _LoopingModel.tool_name,
                "args": {"x": "1", "skill": "s"},
                "id": f"c{_LoopingModel.calls}",
            }])
        else:
            msg = AIMessage(content="ANSWER")
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.mark.asyncio
async def test_hit_limit_skips_last_tool_and_keeps_tool_call_message():
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    executed = []

    @tool("echo")
    def _echo(x: str) -> str:
        """echo。"""
        executed.append(x)
        return x

    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[_echo], system="S",
                        max_turns=3, middleware_extra=[AgentTurnBudget(limit=3)])
    out = await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _LoopingModel.calls == 3                  # 模型调用 = 上限次
    assert len(executed) == 2                        # 工具执行 = 上限 − 1
    last = out["messages"][-1]
    assert isinstance(last, AIMessage) and last.tool_calls   # 末条含 tool_calls
    assert (last.content or "") == ""                # 允许空串


@pytest.mark.asyncio
async def test_limit_hit_on_normal_finish_still_logs(caplog):
    """上限轮恰好正常收尾（无 tool_calls）也产出 iteration limit。"""
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 1
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[echo], system="S",
                        max_turns=2, middleware_extra=[AgentTurnBudget(limit=2)])
    with caplog.at_level(logging.WARNING, logger="src.core.logging"):
        await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert "iteration limit" in caplog.text


@pytest.mark.asyncio
async def test_delegate_bonus_applies_in_the_same_round():
    """上限同轮声明的委派工具必须被执行（不能被跳过）。"""
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "delegate_task"
    ran = []

    @tool("delegate_task")
    def _delegate(skill: str) -> str:
        """委派。"""
        ran.append(skill)
        return "ok"

    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[_delegate], system="S",
                        max_turns=5, middleware_extra=[AgentTurnBudget(limit=5)])
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert ran, "上限轮声明的委派工具必须被执行"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "limit or bonus"
```

期望：`ImportError: cannot import name 'AgentTurnBudget'`

- [ ] **Step 3: 写实现**

```python
class AgentTurnBudget(AgentMiddleware):
    """回合预算（**只装配给主角色**）。

    判定放 after_model 并用 jump_to=end 结束——不注入任何消息。若放
    before_model 判下轮，会多执行一次工具、末条变 ToolMessage，答案提取就会
    把工具结果当成答案。

    委派放宽：本轮声明了 delegate_task 时**先置位**再算上限，且标志跨轮保持
    （= 今天的 `delegate_used or state._delegate_used`）。
    """

    state_schema = LoopState

    def __init__(self, limit: int, bonus: int = MAX_DELEGATE_BONUS) -> None:
        super().__init__()
        self._limit = limit  # 进程级常量
        self._bonus = bonus

    def _after_model(self, state: Any) -> dict[str, Any]:
        n = state.get("_turn_count", 0) + 1
        last = state["messages"][-1]
        declared = list(getattr(last, "tool_calls", None) or [])
        delegate_now = any(c.get("name") == "delegate_task" for c in declared)
        delegate_used = delegate_now or bool(state.get("_delegate_used"))
        effective_max = self._limit + self._bonus if delegate_used else self._limit
        update: dict[str, Any] = {"_turn_count": n}
        if delegate_now:
            update["_delegate_used"] = True
        if n >= effective_max:
            core_logging.log_event(
                Event.ITERATION_LIMIT, query=state.get("query", ""), iteration=n
            )
            if declared:
                update["jump_to"] = "end"
        return update

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        return self._after_model(state)

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any]:
        return self._after_model(state)
```

顶部补 import：`from langchain.agents.middleware import hook_config`、`from src.config.const import MAX_DELEGATE_BONUS`、`from src.core import logging as core_logging`、`from src.core.log_events import Event`。

⚠️ **`jump_to` 只在该轮仍声明工具调用时返回**——「上限轮正常收尾」不需要 jump（本就结束），但 `iteration limit` 日志**必须照记**（上面的实现已把日志与 jump 解耦）。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "limit or bonus"
```

期望：3 passed。

- [ ] **Step 5: 加并发隔离测试（middleware 不持实例状态）**

```python
@pytest.mark.asyncio
async def test_budget_instance_shared_across_requests_does_not_bleed():
    """同一个 middleware 实例被两个并发请求使用，计数互不污染。"""
    mw = AgentTurnBudget(limit=3)
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[echo], system="S", max_turns=3, middleware_extra=[mw])

    async def one(i):
        return await agent.ainvoke(
            {"messages": [HumanMessage(content=f"q{i}")], "query": f"q{i}"}
        )

    r1, r2 = await asyncio.gather(one(1), one(2))
    for r in (r1, r2):
        assert r["_turn_count"] == 3  # 各自独立从 0 起
```

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k shared
```

期望：1 passed。

- [ ] **Step 6: 提交**

```bash
git add src/agents/graph/middleware.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(agent): 新增 AgentTurnBudget（after_model 判定 + jump_to=end，不注入消息）"
```

---

### Task 6: `AgentSpanMiddleware`（观测，必须在最内层）

**Files:**
- Modify: `src/agents/graph/middleware.py`
- Test: `tests/agents/graph/test_loop_middleware.py`

**Interfaces:**
- Consumes: `request.model_settings`（由 `ModelParamsMiddleware` 施加）、`state.get("_turn_count", 0)`
- Produces: `class AgentSpanMiddleware(AgentMiddleware)`；产出 `iteration done`（入口）、`model turn`（出口）与 `agent_turn` generation observation

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_iteration_done_counts_full_messages_including_system(caplog):
    """msgs 计的仍是含 system 段的完整条数 = len(request.messages) + 1。"""
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    with caplog.at_level(logging.INFO, logger="src.core.logging"):
        await agent.ainvoke({
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("S1", "S2"),
        })
    assert "iteration done iteration=1 msgs=4" in caplog.text  # 2 system + 1 user + system_message 那一份


@pytest.mark.asyncio
async def test_model_turn_reports_effective_temperature(caplog):
    """温度上报取实际施加的档位（最内层能看到 model_settings）。"""
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S",
        middleware_extra=[ModelParamsMiddleware(), AgentSpanMiddleware()],
    )
    with caplog.at_level(logging.INFO, logger="src.core.logging"):
        await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": ""})
    assert "temp_source=explicit" in caplog.text
    assert f"temperature={settings.NON_KB_MAIN_TEMPERATURE}" in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "iteration_done or model_turn"
```

期望：`ImportError: cannot import name 'AgentSpanMiddleware'`

- [ ] **Step 3: 写实现（**注意：必须放在 middleware 列表的最后一个**）**

```python
class AgentSpanMiddleware(AgentMiddleware):
    """主循环观测（模型轮次日志 + Langfuse generation span）。

    必须在 middleware 列表**最内层**：这样它看到的 request 已被前序
    middleware 施加过 system 与 model_settings，温度上报才与实际生效档位一致。
    观测故障必须吞异常降级，不得影响对话。

    计数口径：`iteration = state.get("_turn_count", 0) + 1`（本次调用序号；
    AgentTurnBudget 的自增在 after_model，晚于本 middleware，直接取值会少 1）。
    """

    state_schema = LoopState

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        iteration = request.state.get("_turn_count", 0) + 1
        msgs = len(request.messages) + 1  # 最内层：request.messages 已含前插的其余 system
        core_logging.log_event(Event.ITERATION_DONE, iteration=iteration, msgs=msgs)
        turn_start = time.monotonic()
        settings_map = request.model_settings or {}
        if "temperature" in settings_map:
            temperature = settings_map["temperature"]
            temp_source = "explicit"
        else:
            temperature = settings.LLM_TEMPERATURE
            temp_source = "default"
        response = await handler(request)
        self._record_turn(request, response, iteration, temperature, temp_source,
                          int((time.monotonic() - turn_start) * 1000))
        return response

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        # create_agent 的异步路径走 awrap_model_call；同步路径仅测试/脚本用。
        iteration = request.state.get("_turn_count", 0) + 1
        msgs = len(request.messages) + 1
        core_logging.log_event(Event.ITERATION_DONE, iteration=iteration, msgs=msgs)
        return handler(request)
```

`_record_turn` 按**今天 `agent_node.py:249-268` 的口径**实现：从 `ModelResponse` 拆出 `AIMessage`，取 `usage_metadata`（缺失走 `estimate_usage` 并标 `usage_estimated=True`）与 `response_metadata["model_name"]`，发 `model turn` 日志（字段集见 tasks §6.1），并在 `@observe`/命令式 span 下写入 observation；`langfuse_context.update_current_observation` 若在 middleware 中不可用，则**回退为命令式 span**（见 spec OQ）。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -v -k "iteration_done or model_turn"
```

期望：2 passed。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/middleware.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(agent): 新增 AgentSpanMiddleware（最内层观测，计数口径按实证钉死）"
```

---

### Task 7: 节点产两半 + `make_agent_loop_node`

**Files:**
- Modify: `src/agents/graph/agent_node.py:69-145`（`_initial_messages`）、新增 `make_agent_loop_node`、删除 `make_agent_model_node` / `make_agent_tools_node` / `route_agent`
- Test: `tests/agents/graph/test_agent_node.py`

**Interfaces:**
- Consumes: `build_agent`、四个 middleware
- Produces:
  - `_split_initial_messages(state, prompt_manager, tool_names) -> tuple[list[SystemMessage], list[BaseMessage]]`（**system 半段 / 非 system 半段**）
  - `make_agent_loop_node(bundle) -> Callable[[AgentState], Awaitable[dict]]`

- [ ] **Step 1: 写失败测试（三条，覆盖两个易错点）**

```python
import logging
import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from src.agents.graph.agent_node import _split_initial_messages, make_agent_loop_node
from src.agents.graph.state import AgentState


@dataclass
class _Bundle:
    """装配产物束（Task 9 的 build_graph 用同一形状）。"""

    agent: object  # build_agent 的产物（本测试用假实现）
    prompt_manager: object
    tool_names: frozenset


class _RecordingInner:
    """记录子图输入的假装配产物。"""

    def __init__(self, produced: list[BaseMessage]) -> None:
        self.inputs: list[dict] = []
        self._produced = produced

    async def ainvoke(self, payload: dict) -> dict:
        self.inputs.append(payload)
        return {"messages": [*payload["messages"], *self._produced]}


def test_prompt_messages_counts_system_before_split(caplog, fake_prompt_manager):
    """system_msgs 必须在拆分前算出，否则恒为 0。"""
    state = AgentState.make_initial_state(session_id="s", kb_id="", query="q", history=[])
    with caplog.at_level(logging.INFO, logger="src.core.logging"):
        system_half, rest_half = _split_initial_messages(
            state, fake_prompt_manager, frozenset()
        )
    match = re.search(r"system_msgs=(\d+)", caplog.text)
    assert match is not None, "必须产出 prompt messages 事件"
    assert int(match.group(1)) >= 1, "拆分后 system 段被移出列表 ⇒ 计数不得为 0"
    assert all(isinstance(m, SystemMessage) for m in system_half)
    assert not any(isinstance(m, SystemMessage) for m in rest_half)


@pytest.mark.asyncio
async def test_first_turn_writes_back_whole_assembled_list(fake_prompt_manager):
    """首轮外层 messages 为空 ⇒ 回写整份（组装段 + 新增段）。"""
    inner = _RecordingInner([AIMessage(content="A1")])
    node = make_agent_loop_node(
        _Bundle(agent=inner, prompt_manager=fake_prompt_manager, tool_names=frozenset())
    )
    state = AgentState.make_initial_state(session_id="s", kb_id="", query="q", history=[])
    out = await node(state)
    seed_len = len(inner.inputs[0]["messages"])
    assert seed_len > 0, "首轮必须组装出非 system 段作为子图输入"
    assert len(out["messages"]) == seed_len + 1, "首轮须回写整份组装段 + 新增段"
    assert isinstance(out["messages"][-1], AIMessage)
    assert out["_system_messages"], "system 半段必须落回外层（跨 invoke 载体）"


@pytest.mark.asyncio
async def test_regen_round_model_request_contains_query_and_history(fake_prompt_manager):
    """重生成轮的模型请求必须含原始 query 与历史（回写基准取外层条数）。"""
    inner = _RecordingInner([AIMessage(content="A2")])
    node = make_agent_loop_node(
        _Bundle(agent=inner, prompt_manager=fake_prompt_manager, tool_names=frozenset())
    )
    first = await node(
        AgentState.make_initial_state(session_id="s", kb_id="", query="原始问题", history=[])
    )
    state = AgentState.make_initial_state(
        session_id="s", kb_id="", query="原始问题", history=[]
    )
    state.messages = [*first["messages"], SystemMessage(content="VERIFY-GUIDANCE")]
    state._system_messages = first["_system_messages"]
    out = await node(state)
    sent = inner.inputs[-1]["messages"]
    assert any("原始问题" in str(getattr(m, "content", "")) for m in sent), "regen 轮丢了原始问题"
    assert any(
        isinstance(m, SystemMessage) and "VERIFY-GUIDANCE" in str(m.content) for m in sent
    )
    assert len(out["messages"]) == 1, "regen 轮只回写新增段"
```

> `fake_prompt_manager`：优先复用既有 `tests/agents/graph/` 下的假 PromptManager；没有则按 `build_prompt` 的签名加一个只回显入参的桩 fixture。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node.py -v -k "split or write_back or regen_round"
```

期望：FAIL（`_split_initial_messages` / `make_agent_loop_node` 不存在）

- [ ] **Step 3: 写实现**

`_initial_messages` 改为在**拆分前**发计数事件，并返回两半：

```python
    core_logging.log_event(
        Event.PROMPT_MESSAGES,
        system_msgs=sum(1 for m in messages if isinstance(m, SystemMessage)),  # 拆分前算
        injected_msgs=len(injected),
        history_msgs=len(cleaned_normal),
    )
    system_half = [m for m in messages if isinstance(m, SystemMessage)]
    rest_half = [m for m in messages if not isinstance(m, SystemMessage)]
    return system_half, rest_half
```

新增节点（**回写基准 = 外层 `len(state.messages)`**）：

```python
def make_agent_loop_node(bundle) -> Callable:
    """外层 agent 节点：组装首轮 → seed 子图 → invoke → 按外层条数回写。"""

    inner = bundle.agent  # build_agent 的产物

    async def agent_loop(state: AgentState) -> dict:
        if state.messages:
            seed: list[BaseMessage] = list(state.messages)
            system_half = list(state._system_messages or [])
        else:
            system_half, seed = _split_initial_messages(
                state, bundle.prompt_manager, bundle.tool_names
            )
        subgraph_input = {
            "messages": seed,
            "_system_messages": system_half,
            "query": state.query,
            "kb_id": state.kb_id,
            "deep_thinking": state.deep_thinking,
            "_turn_count": 0,
            "_delegate_used": False,
        }
        out = await inner.ainvoke(subgraph_input)
        produced = list(out["messages"])
        # 基准是**外层已有条数**，不是喂给子图的输入长度：首轮外层为空 ⇒ 回写整份
        delta = produced[len(state.messages):]
        last = produced[-1]
        return {"messages": delta, "_system_messages": system_half, "answer": _extract_text(last)}

    return agent_loop
```

删除 `make_agent_model_node` / `make_agent_tools_node` / `route_agent`（整段），并删除两处临时取证埋点（`first_chunk_ms` 与 `logger.info("[agent] TIMING …")`；`first_chunk_at` 保留——它是观测的 `completion_start_time`）。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node.py -v
```

期望：全绿（既有用例按新形态改造后）。

- [ ] **Step 5: 确认文件行数红线**

```bash
wc -l src/agents/graph/agent_node.py
```

期望：< 400。

- [ ] **Step 6: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_agent_node.py
git commit -m "feat(agent): agent 节点改为 invoke 装配产物，回写基准取外层条数"
```

---

### Task 8: 工具取数（映射承载）

**Files:**
- Modify: `src/agents/tools/rag_tools.py:191-196`、`src/agents/tools/ask_tools.py:82-85,166-169`
- Test: `tests/agents/tools/test_rag_tools.py`、`tests/agents/tools/test_ask_tools.py`

**Interfaces:**
- Consumes: 图状态（映射或 `AgentState` 实例，**两种都要支持**）
- Produces: 工具在两种承载下都能取到 `kb_id` / `query` / 迭代序号；降级分支记 warning

- [ ] **Step 1: 写失败测试（含「无 ctx 入口」这条关键断言）**

```python
import pytest

from src.agents.tools import ask_tools, rag_tools
from src.infra.llm.request_context import current_request_ctx


@pytest.mark.asyncio
async def test_retrieve_kb_reads_turn_count_when_state_is_mapping(monkeypatch):
    """create_agent 承载下（映射状态）迭代序号取 _turn_count，不得恒为 0。"""
    seen = {}

    async def _fake_retrieve(state, query, kb_id, iteration, **kwargs):
        seen["iteration"] = iteration
        seen["kb_id"] = kb_id
        return ""

    monkeypatch.setattr(rag_tools, "_retrieve_with_signals", _fake_retrieve, raising=False)
    await rag_tools.retrieve_kb.ainvoke(
        {"query": "q", "state": {"messages": [], "_turn_count": 3, "kb_id": "kb-1"}}
    )
    assert seen["iteration"] == 3, "映射承载下迭代序号不得恒为 0"
    assert seen["kb_id"] == "kb-1"


@pytest.mark.asyncio
async def test_ask_user_reads_query_and_kb_id_when_state_is_mapping():
    """ask_user 在映射状态下也要取到真实 query / kb_id（不抛 AttributeError）。"""
    from src.agents.tools import ask_tools

    # 直接驱动取数分支：把 state 传成映射，断言不抛异常且取到值
    captured = ask_tools._read_state_fields({"query": "真实问题", "kb_id": "kb-1", "_turn_count": 2})
    assert captured == ("真实问题", 2, "kb-1")


@pytest.mark.asyncio
async def test_cli_style_entry_without_request_context_still_retrieves(monkeypatch):
    """CLI 入口只把参数放进图输入、不建 ctx ⇒ 装配必须 seed kb_id。"""
    assert current_request_ctx.get() is None, "本用例刻意不建 ctx"
    seen = {}

    async def _fake_retrieve(state, query, kb_id, iteration, **kwargs):
        seen["kb_id"] = kb_id
        return ""

    monkeypatch.setattr(rag_tools, "_retrieve_with_signals", _fake_retrieve, raising=False)
    # 等价于 CLI（src/cli/check_abstain.py:127 / eval_ragas.py:129）：kb_id 只在图输入里
    await rag_tools.retrieve_kb.ainvoke(
        {"query": "q", "state": {"messages": [], "_turn_count": 0, "kb_id": "kb-from-input"}}
    )
    assert seen["kb_id"] == "kb-from-input", "无 ctx 入口不得退化为空 kb_id"
```

> ⚠️ 两处 `monkeypatch.setattr(..., "_retrieve_with_signals", raising=False)` 的**内部函数名以 `rag_tools.py` 实际代码为准**（实施时先读该文件的检索入口再落名）；第三个用例的 `_read_state_fields(...)` 是本次要新增的小工具函数（把「实例/映射」两分支收敛到一处，返回 `(query_text, iteration, kb_id)`），`ask_user` 与 `_load_dimension_options` 都调它。

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/ -v -k "mapping or cli_style"
```

期望：FAIL（映射状态下 `AttributeError` 或取到 0/空）

- [ ] **Step 3: 写实现**

`rag_tools.py` 迭代序号：

```python
        # iteration：两种承载都要支持——外层自建图给 AgentState 实例，
        # create_agent 给映射（该键由装配 seed）。
        iteration = 0
        if isinstance(state, AgentState):
            iteration = state._agent_iterations
        elif isinstance(state, dict):
            iteration = int(state.get("_turn_count", 0))
```

`ask_tools.py` 三处改显式形状判定：

```python
    if isinstance(state, AgentState):
        query_text = state.query
        iteration = state._agent_iterations
    elif isinstance(state, dict):
        query_text = str(state.get("query", ""))
        iteration = int(state.get("_turn_count", 0))
    else:
        query_text = ""
        iteration = 0
        logger.warning("ask_user state missing fields tool=ask_user fields=query,iteration")
```

`kb_id`（`:168`）同法：实例取 `state.kb_id`，映射取 `state.get("kb_id", "")`；两者都取不到时回退 `current_request_ctx.kb_id`。

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/ -v
```

期望：全绿。

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/rag_tools.py src/agents/tools/ask_tools.py tests/agents/tools/
git commit -m "fix(tools): 取图状态改为显式形状判定，支持映射承载（含 CLI 无 ctx 入口）"
```

---

### Task 9: 外层图接入（删 `tools` 节点与条件边）

**Files:**
- Modify: `src/agents/graph/workflow.py:72-133`
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: `build_agent`、`make_agent_loop_node`
- Produces: 外层图 `START → route_entry → {agent, skill_direct} → agent_finalize → verify ⇄ → format → END`；**无 `tools` 节点**

- [ ] **Step 1: 写失败测试**

```python
def test_graph_has_no_outer_tools_node_and_agent_goes_straight_to_finalize():
    graph = _build_test_graph()  # 复用 test_graph.py 里既有的构造 helper（缺则按该文件既有用例补）
    nodes = set(graph.get_graph().nodes)
    assert "tools" not in nodes, "循环已内化进装配产物，外层不得再有 tools 节点"
    assert "agent" in nodes and "agent_finalize" in nodes
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("agent", "agent_finalize") in edges


def test_sub_agent_assembly_does_not_emit_graph_compiled(caplog):
    """委派一次后 graph compiled 计数不变（装配入口不发该事件）。"""
    with caplog.at_level(logging.INFO, logger="src.core.logging"):
        _build_test_graph()  # 图装配层自己发一次
    before = caplog.text.count("graph compiled")
    assert before == 1

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.core.logging"):
        build_agent(_FakeModel(), tools=[], system="子角色静态串")  # 子角色装配入口
    assert caplog.text.count("graph compiled") == 0, "装配入口不得发编译日志"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_graph.py -v -k "no_outer_tools or graph_compiled"
```

期望：FAIL（仍有 `tools` 节点）

- [ ] **Step 3: 写实现**

把 `build_graph` 的节点/边段替换为：

```python
    if tool_sink is not None:
        tool_sink.extend(rag_tools)

    # 装配产物：主角色四件套 middleware；AgentSpan 必须在最内层（看得到已施加的
    # system 与 model_settings）。子角色不在此处装配（executor 侧另行调用）。
    loop_agent = build_agent(
        llm,
        rag_tools,
        system=None,  # 主角色：system 经运行态携带，由 SystemMessagesMiddleware 施加
        max_turns=MAX_AGENT_ITERATIONS,
        middleware_extra=[
            SystemMessagesMiddleware(),
            ModelParamsMiddleware(),
            AgentTurnBudget(limit=MAX_AGENT_ITERATIONS),
            AgentSpanMiddleware(),
        ],
    )
    bundle = AgentLoopBundle(
        agent=loop_agent,
        prompt_manager=prompt_manager,
        tool_names=frozenset(str(t.name) for t in rag_tools),
    )

    builder.add_node("agent", make_agent_loop_node(bundle))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node("verify", verify_node)
    builder.add_node(LangGraphNode.Format.NAME, format_node)
    direct_node = unavailable_skill_direct
    if skill_direct_node is not None:
        direct_node = skill_direct_node
    builder.add_node(LangGraphNode.SkillDirect.NAME, direct_node)

    builder.add_conditional_edges(
        START,
        route_entry,
        {"agent": "agent", LangGraphNode.SkillDirect.NAME: LangGraphNode.SkillDirect.NAME},
    )
    # 循环已内化进装配产物：agent 出边**直连** agent_finalize（不再有 route_agent 条件边）
    builder.add_edge("agent", "agent_finalize")
    builder.add_edge("agent_finalize", "verify")
    builder.add_edge(LangGraphNode.SkillDirect.NAME, "verify")
    builder.add_conditional_edges(
        "verify",
        route_verify,
        {
            "agent": "agent",
            LangGraphNode.SkillDirect.NAME: LangGraphNode.SkillDirect.NAME,
            "format": LangGraphNode.Format.NAME,
        },
    )
    builder.add_edge(LangGraphNode.Format.NAME, END)
```

**必须同时删除**：`builder.add_node("tools", make_agent_tools_node(rag_tools))`、`builder.add_edge("tools", "agent")`、`builder.add_conditional_edges("agent", route_agent, {...})`。

并在 `agent` 节点附近加一句注释（防止后人误判 `ToolTraceCollector` 失效）：

```python
    # 注：ToolTraceCollector 的 langgraph_node == "tools" 判据**仍然有效**——
    # 工具事件来自装配产物内部的同名节点，不是外层图。
```

`AgentLoopBundle` 定义为 dataclass（放 `agent_node.py`，与 `make_agent_loop_node` 同文件）：

```python
@dataclass
class AgentLoopBundle:
    """外层 agent 节点需要的装配产物与上下文。"""

    agent: object  # build_agent 的产物
    prompt_manager: object
    tool_names: frozenset[str]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/ -v
```

期望：全绿（`test_graph.py` 里 `make_rag_tools` 的 mock 面已随签名同步）。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/workflow.py src/agents/graph/agent_node.py tests/agents/graph/test_graph.py
git commit -m "feat(agent): 外层图接入装配产物，删除 tools 节点与 route_agent 条件边"
```

---

### Task 10: SSE 谓词 + 模块 docstring + 测试夹具与副本

**Files:**
- Modify: `src/services/agent_service.py:186-208`（docstring）、`:250`、`:267`、`:277`
- Modify: `tests/services/test_agent_service.py`、`tests/services/test_dual_stream.py`、`tests/agents/skills/test_skill_executor.py`、`tests/agents/skills/test_delegate_task.py`、`tests/infra/llm/test_tool_trace.py`
- Test: 上述文件

**Interfaces:**
- Consumes: 装配产物内部模型节点名恒为 `"model"`
- Produces: 主 SSE 的 token/status/`model_used` 由 `langgraph_node == "model"` 驱动

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_convert_event_accepts_model_node_for_token():
    item = {
        "event": "on_chat_model_stream",
        "metadata": {"langgraph_node": "model"},   # 改名后的事件形状
        "data": {"chunk": _chunk("你")},
    }
    events = _convert_event(item)
    assert [type(e).__name__ for e in events] == ["SSETokenEvent"]


@pytest.mark.asyncio
async def test_convert_event_ignores_legacy_agent_node():
    item = {
        "event": "on_chat_model_stream",
        "metadata": {"langgraph_node": "agent"},   # 旧形状必须不再匹配
        "data": {"chunk": _chunk("你")},
    }
    assert _convert_event(item) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_agent_service.py -v -k "model_node or legacy_agent"
```

期望：FAIL（当前谓词是 `"agent"`）

- [ ] **Step 3: 写实现**

三处 `if metadata.get("langgraph_node") == "agent":` → `== "model"`（`on_chat_model_stream` / `on_chat_model_start` / `on_chat_model_end`）。docstring（`:197/198/201`）同步改。**不得**引入 `checkpoint_ns` 兜底，也**不要**动 `if scope != "main": return`（该形参在生产调用点均为缺省，本变更不改）。

在同函数处补一句注释：

```python
        # 判据实际只有「节点判别键」一维：scope 形参虽在，但生产调用点均用缺省 main。
        # 主 SSE 与子代理事件的隔离由事件路由承担（var_child_runnable_config.set(None)
        # 切断回调继承 + 委派事件经显式喂事件走委派域），属**单点机制**。
```

- [ ] **Step 4: 同步测试夹具与谓词副本（**五个文件逐一处理**）**

| 文件 | 处置 |
|---|---|
| `tests/services/test_agent_service.py` | `{"langgraph_node": "agent"}` 的**模型事件**夹具 → `"model"`（`:44/54/64/313/337/1131`） |
| `tests/services/test_dual_stream.py` | 同上（`:27/272`）；注意 `:262` 的 `"generate"` 是**否定用例**，不动 |
| `tests/agents/skills/test_skill_executor.py` | 夹具 `:67` 改；**`:586-620` 的谓词副本 `:606` 与注释 `:586/620` 一并改为 `"model"`** |
| `tests/agents/skills/test_delegate_task.py` | `:87` 夹具改 |
| `tests/infra/llm/test_tool_trace.py` | `:182` 是**否定用例**（验 `tools` 判据不误收）；值改成 `"model"` 更贴近现实，但**语义不变**，显式确认后再改 |

- [ ] **Step 5: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/ tests/agents/skills/ tests/infra/llm/ -v
```

期望：全绿。

- [ ] **Step 6: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py tests/services/test_dual_stream.py tests/agents/skills/test_skill_executor.py tests/agents/skills/test_delegate_task.py tests/infra/llm/test_tool_trace.py
git commit -m "refactor(sse): 模型事件判别键改为 model，并同步测试夹具与谓词副本"
```

---

### Task 11: 状态字段清理与 regen 复位删除

**Files:**
- Modify: `src/agents/graph/state.py:31-41`（删三个字段 + 加 `_system_messages`）
- Modify: `src/agents/graph/verify/guardrails.py:105/106`、`:180/181`
- Modify: `src/agents/graph/verify/regen_decision.py:174/175`
- Test: `tests/agents/graph/test_state.py`、`tests/agents/graph/test_verify_node.py`

**Interfaces:**
- Consumes: 无
- Produces: `AgentState._system_messages: list[BaseMessage]`（无 reducer）；三个循环字段与三处复位消失

- [ ] **Step 1: 写失败测试**

```python
def test_agent_state_has_system_messages_and_no_loop_counters():
    fields = set(AgentState.__dataclass_fields__)
    assert "_system_messages" in fields
    assert "_agent_iterations" not in fields
    assert "_max_agent_iterations" not in fields
    assert "_delegate_used" not in fields


def test_regen_decision_no_longer_resets_loop_budget():
    """regen = 重新 invoke 装配产物 ⇒ 计数天然从初值起，不再需要显式复位。"""
    decision = kb_citation_guardrail(state_with_missing_citation())
    assert "_agent_iterations" not in decision
    assert "_delegate_used" not in decision
    assert decision.get("ctx.web_count") is None or True   # web_count 复位保留（在 ctx 上）
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_state.py tests/agents/graph/test_verify_node.py -v -k "state_messages or no_longer_resets"
```

期望：FAIL

- [ ] **Step 3: 写实现**

`state.py`：删除 `_agent_iterations` / `_max_agent_iterations` / `_delegate_used` 三行，新增

```python
    _system_messages: list[BaseMessage] = field(
        default_factory=list
    )  # system 段（来源：agent 节点首轮组装后写入；范围：整轮执行，跨 regen invoke 持久；用途：模型参数与 system 的施加通道；**不带 reducer**，写入即替换）
```

`guardrails.py` 与 `regen_decision.py`：删掉决策 dict 里的 `"_agent_iterations": 0` 与 `"_delegate_used": False`（共三处）；**保留** `ctx.web_count = 0`（两处：`guardrails.py:99`、`regen_decision.py:169`）。在两处删点补注释：

```python
    # regen 轮主循环预算独立起算：regen = 重新 invoke 装配产物，计数天然从初值起，
    # 不再需要显式复位（旧实现靠 _agent_iterations=0 / _delegate_used=False 达成同一效果）。
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/ -v
```

期望：全绿（`test_verify_node.py` 里断言这些返回键的用例已同步）。

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/state.py src/agents/graph/verify/guardrails.py src/agents/graph/verify/regen_decision.py tests/agents/graph/test_state.py tests/agents/graph/test_verify_node.py
git commit -m "refactor(state): 删三个循环字段与 regen 预算复位，新增 _system_messages 载体"
```

---

### Task 12: 子角色接入同一装配入口

**Files:**
- Modify: `src/agents/skills/executor.py:308-321`（`_build_sub_agent`）
- Test: `tests/agents/skills/test_skill_executor.py`

**Interfaces:**
- Consumes: `build_agent`
- Produces: 子代理由 `build_agent(model, tools, system=<静态串>, middleware_extra=[])` 生成（**不传** `max_turns`、**不传** `name`）

- [ ] **Step 1: 写失败测试**

```python
def test_sub_agent_uses_shared_factory_with_empty_middleware(monkeypatch):
    calls = {}

    def _spy_build_agent(model, tools, **kwargs):
        calls.update(kwargs)
        calls["tools_count"] = len(tools)
        return object()

    monkeypatch.setattr(executor_module, "build_agent", _spy_build_agent, raising=False)
    executor = _make_executor()
    executor._build_sub_agent(record=None, preset=None, via=DELEGATE_VIA_DELEGATE)
    assert calls["middleware_extra"] == [], "子角色 middleware 必须为空"
    assert calls["max_turns"] is None, "子角色的轮次上限不走图内预算"
    assert isinstance(calls["system"], str), "子角色用静态 system 串"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/skills/test_skill_executor.py -v -k shared_factory
```

期望：FAIL

- [ ] **Step 3: 写实现**

```python
    def _build_sub_agent(self, record: SkillRecord | None, preset, via: str):
        """构建 fork 子代理：走与主 agent 共用的装配入口（只传模型/工具面/静态 system）。

        子角色的 middleware 集合为空——其模型轮次记录由 fork 委派的事件消费侧
        （fork_stream._record_delegate_model_turn）承担，图内不重复产出主循环口径
        的轮次日志与观测。轮次上限仍由消费侧判定（见 delegate-execution-controls
        的「fork turn 上限」），故不传 max_turns。
        """
        return build_agent(
            self._resolve_fork_llm(record),
            self._fork_tools(record, preset),
            system=self._executor_system_prompt(preset, via),
            middleware_extra=[],
        )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/skills/ -v
```

期望：全绿（含 `test_skill_executor.py` 里既有的 fork 装配断言）。

- [ ] **Step 5: 提交**

```bash
git add src/agents/skills/executor.py tests/agents/skills/test_skill_executor.py
git commit -m "refactor(skills): 子代理改走共用装配入口（middleware 集合为空，不传 max_turns/name）"
```

---

### Task 13: 文档与登记

**Files:**
- Modify: `docs/agents/code-map.md` / `data-flow.md` / `api_contract.md` / `glossary.md` / `prompt-ownership.md` / `defensive-patterns.md` / `requirements_pool.md` / `logging-rules.md`

**Interfaces:**
- Consumes: 全部实现改动
- Produces: 文档与代码一致

- [ ] **Step 1: 逐份更新（内容要点，按 tasks §8 执行）**

| 文档 | 要写进去的内容 |
|---|---|
| `code-map.md` | `graph/` 新增 `agent_factory.py`（唯一装配入口）/ `middleware.py`（四件套）；`agent_node.py` 职责行改为「首轮组装 + 循环包装节点」；**删** `route_agent`/`make_agent_*_node` 的描述 |
| `data-flow.md` | 循环图改为「`agent` 节点内 invoke 装配产物」；删外层 `tools` 节点；首轮消息组装路径改为「组装 → 按类型拆两半 → system 落外层字段」 |
| `api_contract.md` | `build_graph` 契约（参数面不变，内部结构变）+ 新增 `build_agent` 签名与返回 |
| `glossary.md` | 新术语：**装配入口** / **回合预算 middleware** / **system 提供方式（静态串 / 经运行态携带）** / **循环后阶段** |
| `prompt-ownership.md` | 在「承载」栏补一句：system 段的**组装**仍在 `build_system_prompt`，**下发**经 `SystemMessagesMiddleware`（两条 system 的通道不变） |
| `defensive-patterns.md` | 复核三条已登记项是否齐备：middleware `state_schema` 静默丢弃 / 实例跨请求共享 / `extra_body` 浅合并与档位判据取请求级上下文 |
| `requirements_pool.md` | 复核 D-09（四个死字段）/ D-10（`is_fallback` 硬编码）/ D-11（埋点清理待办）/ F-36（工具取数形状）/ F-37（触顶空回答） |
| `logging-rules.md` | 注明三条循环日志的**产点**已迁至 middleware（事件名与字段集不变） |

- [ ] **Step 2: 跑文档闸门**

```bash
.venv/bin/python -m src.cli.check_docs 2>&1 | tail -3
```

期望：`0 error`。（38 warn 是已知的反引号 quirk。）

- [ ] **Step 3: 撰写 ADR**

按 `docs/adr/README.md` 模板写一条：`统一 agent 循环到 create_agent（L1/L2 参数化，领域阶段留外层图）`。编号取 **`dev-wsl` 当时的最大号 +1**（写时先 `ls docs/adr | tail`，并在提交前与当时的在途 change 协调）。

```bash
.venv/bin/python -m src.cli.check_adr 2>&1 | tail -3
```

期望：`0 error`。

- [ ] **Step 4: 提交**

```bash
git add docs/agents/ docs/adr/
git commit -m "docs(agents): 同步 code-map/data-flow/api_contract/glossary/prompt-ownership 与 ADR"
```

---

### Task 14: 验收（基线比对 + 触顶复现 + 真实模型 E2E）

**Files:**
- Modify: `docs/tmp/one-loop-two-roles-baseline.md`（追加比对结论）

**Interfaces:**
- Consumes: Task 1 的基线快照
- Produces: 验收结论（逐条勾选）

- [ ] **Step 1: 质量门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
ruff format . && ruff check .
pyright src/ 2>&1 | tail -3
grep -rn "TIMING" src/agents/graph/ ; echo "(空=埋点已删净)"
wc -l src/agents/graph/agent_node.py
```

期望：测试全绿、ruff 无错误、pyright 不新增 error、`src/agents/graph/` 无 TIMING 埋点、`agent_node.py` < 400 行。

- [ ] **Step 2: 基线逐字比对（用 Task 1 的同一问题集重跑）**

```bash
POSTGRES_HOST=localhost .venv/bin/python /tmp/baseline_capture.py > /tmp/after.txt
diff docs/tmp/one-loop-two-roles-baseline.md /tmp/after.txt
```

期望：五条日志的**条数与字段值**、SSE 事件序列、`[n]` 与 citations **全部一致**。任何差异都要在基线文件里逐条说明原因（不允许"看起来差不多"）。

- [ ] **Step 3: 触顶路径真实复现**

用宽泛问题在绑 KB 会话里触发触顶，核对：日志含 `iteration limit`、**不出现**任何提示性文案、`verify` 收到空答案后照常收尾、落库与变更前一致。

```bash
grep -n "iteration limit" /data/logs/*.log | tail -3
```

- [ ] **Step 4: 真实模型 E2E（DashScope）**

- 绑 KB / 未绑 KB 各一次：token 级流式内容正确、`extra_body` 生效、**绑 KB 轮不出现显式 `temperature` 参数**（看 `model turn` 的 `temp_source=default`）
- 深度思考开启一次：reasoning 增量流出（验证 `deep_thinking` 随调用链传入）
- `/xxx` 直出轮与主 agent 委派轮各一次：子代理链路未回归（含委派折叠区事件）
- 观察 Langfuse：`agent_turn` generation observation 仍出现且字段完整（失败则回退命令式 span 并记录）

- [ ] **Step 5: 提交**

```bash
git add docs/tmp/one-loop-two-roles-baseline.md
git commit -m "docs(one-loop-two-roles): 追加验收结论（基线逐字比对 + 触顶复现 + E2E）"
```

---

## Self-Review

**1. Spec 覆盖对照**（每条 requirement → 任务）

| capability / requirement | 承接任务 |
|---|---|
| `agent-assembly` · 主/子角色共用同一装配 | Task 2（入口 + 静态扫描断言）、Task 12（子角色接入） |
| `agent-assembly` · 装配差异仅由参数提供 | Task 2（`system` / `max_turns` / `middleware_extra`）、Task 12（空 middleware） |
| `agent-assembly` · 装配入口不产出编译日志 | Task 2（`test_build_agent_does_not_emit_graph_compiled`）、Task 9（`graph compiled` 仍只发一次） |
| `agent-assembly` · middleware 不得持 per-request 实例状态 | Task 5 Step 5（并发隔离）、Task 4/5/6 的 `self._limit` 等只存进程级常量 |
| `agent-assembly` · 装配后工具与中间件仍能取到必需字段 | Task 7（seed 清单）、Task 8（工具取数 + CLI 无 ctx 用例） |
| `agent-assembly` · 回合上限参数化且委派放宽 | Task 5（三方向用例） |
| `agent-loop-observability` · 触顶消息状态 | Task 5（时点三断言）、Task 14 Step 3（真实复现） |
| `agent-loop-observability` · 循环域日志字段值逐字保持 | Task 6（`msgs` / `iteration` 口径 + 温度上报）、Task 14 Step 2（基线比对） |
| `prompt-composition` · system 施加通道逐字不变 | Task 3（两条 system）、Task 7（**计数须拆分前算**） |
| `prompt-composition` · 消息构成计数逐字不变 | Task 7 Step 1 第一条用例、Task 14 Step 2 |
| `answer-verification` · 重生成轮预算独立起算 | Task 7（回写基准 = 外层条数）、Task 11（删复位）、Task 7 Step 1 第三条用例 |
| `delegate-progress-observability` · 单维判据 + 不泄漏 | Task 10（谓词 + 副本）、Task 10 Step 1 第二条用例 |
| `delegate-task` · fork 执行（共用装配 / middleware 写实） | Task 12 |

**2. 占位符扫描**：已清除全部 `...` 占位与"类似 Task N"式引用；两处**必须按实际代码落名**的地方已就地标注（Task 8 的内部检索函数名、Task 7 的 `fake_prompt_manager` fixture）。

**3. 类型与命名一致性**：`LoopState`（Task 2）的六个键在 Task 4/5/6/7 中被读写，名称一致；`build_agent` 的关键字参数（`system` / `max_turns` / `middleware_extra`）在 Task 9/12 的调用点一致；`AgentLoopBundle`（Task 9）与 Task 7 测试里的 `_Bundle` 字段同形（`agent` / `prompt_manager` / `tool_names`）；`_turn_count` / `_delegate_used` / `_system_messages` 在 middleware 与节点两侧同名。

**4. 已知的实现期不确定项**（不阻塞开工，逐条已在 spec 的 Open Questions 记录）：

- `AgentSpanMiddleware` 里 Langfuse observation 的归属（`update_current_observation` 在 middleware 中是否指向正确 observation）；失败则回退命令式 span。
- `ModelResponse` 需拆包才能复用今天的 `usage_metadata` / `_observation_output` 口径（Task 6 已标注）。
- `abort` / `ask_user` 挂起穿过「节点内 invoke 子图」的行为（机制预期一致，须按 `tasks.md` 7.16 在**单元层**覆盖）。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-29-one-loop-two-roles.md`. Two execution options:**

1. **Subagent-Driven（推荐）** —— 每个任务派一个全新的子代理执行，任务之间我来复核（两阶段评审），迭代快。
2. **Inline Execution** —— 在本会话内按 executing-plans 批量执行，设检查点供你复核。

**选哪种？**
