# skill-execution-and-delegation P2 实施计划（子代理工具面 · 引用链 · 通用委派 · 会话级预算）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 fork 子代理从"零工具只读隔离体"升级为"默认继承执行者的**只读**工具面、声明只收窄"的完整 agent，并配套引用链策略（按路径给 `[n]` 指示）、通用委派（`skill` 可选）与会话级预算闸门。

**Architecture:** P2 是**改变子代理能力边界**的那一半。四处改动围绕同一条链路：`select_fork_tools`（工具面口径反转 + 只读收窄 + 禁用集三档）→ `SkillExecutor`（接线、按路径给引用指示）→ `delegate_task`（预算闸门 + 通用委派）→ 新模块 `src/chat/delegate_budget.py`（进程内会话级计数）。P1 已把**前置条件**（委派路径子上下文隔离 D15、委派域 trace D11）落到位，故本阶段一落地子代理检索即落子池、不污染主池。

**Tech Stack:** Python 3.11+ / pytest / LangChain `@tool` / dataclass / 进程内单例（同 `task_registry`）。

**Spec:** `docs/openspec/changes/skill-execution-and-delegation/`（`proposal.md` / `design.md` 的 **D6·D7·D8·D10·D16** / `tasks.md` §3 §3b §5 与 §11 的 P2 部分 / `specs/{delegate-task,llm-tracing}.spec.md` 的对应 requirement）。执行者应同时读 spec 与本计划。

## Global Constraints

以下为本项目**每一条改动都隐含满足**的要求，逐字来自 `CLAUDE.md` 与 `docs/agents/rules.md`：

- **层间调用规则**：`api/` 不得直接调用 `infra/` 或 `config/`（必须经 `services/`）；`agents/` 不得 import `services/`（`services/` 在 `agents/` 之上，反向即越界）。`agents/` 读 `chat/` 已有先例（`delegate_task.py` 已 import `src.chat.task_registry`）。
- **常量集中**：新增阈值/文案不得散落在业务代码。`settings.py` 放环境变量；`const.py` 放固定阈值与事件/节点常量；用户可见文案放 `SSEInteractionTexts`；纯 LLM 提示词放 `prompts/`。
- **注释标准**：所有函数写 docstring；`dataclass` 每个字段加行内注释（来源/范围/用途）；**写当前状态，不写变更历史**；注释陈述契约，不写推理过程。
- **代码风格**：**不用三元表达式**（写完整 if/else）；类型不确定的值不用 `getattr(x, "attr", default)` 兜底，用显式 `is not None` / `isinstance` 判断。
- **日志**：事件消息英文 k=v + `[层名]` 前缀；**每个新事件必须在 `src/core/log_events.py` 与 `src/core/log_event_specs.py` 两处同名登记**，字段集与 `log_event(...)` 实参一致。**新 reason 取值**只需在既有事件下补文档（见 `docs/agents/logging-rules.md`）。
- **测试命令**：宿主侧必须加前缀 —— `POSTGRES_HOST=localhost pytest <path> -v`。
- **文件规模红线**：单文件 > 400 行须拆分；单函数 > 80 行须拆子函数。
- **观测/失败纪律**：任何"辅助能力"（trace、预算、看板）失败都不得阻断对话——记 warning 后降级，绝不向上抛异常。
- **本阶段硬约束**：**不得修改 `skills/` 下任何 SKILL.md**；P2 只通过代码实现，不靠改内容库。
- **不做**：不改 D1/D2/D3/D5/D11/D15（P1 已完成）；**不实现嵌套深度上限**（子代理不持有 `delegate_task`，深度恒为 1，由禁用集硬保证）。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/config/const.py` | 新增 `FORK_EXCLUSIVE_TOOL_PREFIXES`、`DELEGATE_VIA_*`、`DELEGATE_GENERIC_SKILL_NAME`、`SSEInteractionTexts` 两条新文案 | Modify |
| `src/agents/skills/fork_tools.py` | `select_fork_tools` 四步新口径 + 空表 fail-closed | Rewrite |
| `src/agents/skills/executor.py` | `_fork_tools` 接线 readonly_map；`_executor_system_prompt` 按 `via` 分支；`_build_sub_agent`/`_run_fork` 透传 `via`；支持无 record | Modify |
| `src/agents/skills/loader.py` | 删除 `_warn_fork_without_tools`（语义作废） | Modify |
| `src/agents/skills/delegate_run.py` | `DelegateRun` 新增 `via` 字段 | Modify |
| `src/agents/skills/delegate_task.py` | `skill` 可选 + 通用分支 + 预算闸门 + 事件/看板占位 | Modify |
| `src/agents/graph/skill_direct.py` | 两处 `DelegateRun(...)` 填 `via=direct` | Modify |
| `src/config/prompts/__init__.py` | 默认执行者 prompt 去掉无条件"不标 `[n]`"；新增两条按路径的引用指示 | Modify |
| `src/chat/delegate_budget.py` | **新建**：进程内会话级委派计数 + TTL 惰性清理 | Create |
| `src/chat/manager.py` | `clear_history_async` 里挂钩预算复位 | Modify |
| `src/config/settings.py` | 新增 `DELEGATE_MAX_PER_SESSION` | Modify |
| `pyproject.toml` | 删掉已失效的 `filterwarnings` ignore（对应被删的 loader warning） | Modify |
| `docs/agents/{glossary,prompt-ownership,logging-rules}.md` | 两路径 `[n]` 策略、预算语义与 `budget_exhausted` | Modify |
| 测试 | 见各任务 | Modify/Create |

**已知既有缺陷（不在本阶段范围，勿顺手修）**：`rag_tools ↔ ask_tools` 链上存在**潜伏循环导入** —— 在裸 Python 进程里**先** import `src.agents.tools.rag_tools` 会 `ImportError`；在 pytest 下不触发（`tests/conftest.py:77` 先 import `AppService` 已打破环）。手工试验时请先 `import src.agents.skills.delegate_task`。

---

## Task 1: 工具面新口径 —— 继承只读面 · 禁用集三档 · 空表 fail-closed（design D7/D8）

**Files:**
- Modify: `src/config/const.py`（`FORK_FORBIDDEN_TOOLS` 之后新增前缀常量）
- Rewrite: `src/agents/skills/fork_tools.py`
- Test: `tests/agents/skills/test_fork_tools.py`（**整体重写**，旧 7 条断言的是"空=零工具"，正是本任务要反转的语义）

**Interfaces:**
- Consumes: `readonly_map()`（`src/agents/tools/readonly.py`，返回 `dict[str, bool]` 副本）；`FORK_FORBIDDEN_TOOLS`（`const.py`）
- Produces: `select_fork_tools(allowed: list[str], available: list, executor_tools: list[str] | None = None, tool_readonly: dict[str, bool] | None = None) -> list`

**口径（顺序即语义，勿调换）**：① 先减禁用集（`FORK_FORBIDDEN_TOOLS` + `FORK_EXCLUSIVE_TOOL_PREFIXES`，**优先于白名单**）→ ② 非只读工具默认不下发，仅当 `allowed` **显式声明**时放行；表中缺项按非只读 → ③ `allowed` 非空则收窄为交集（**空 = 不收窄**）→ ④ `executor_tools` 非空再收窄。

> **对 spec 公式的一处澄清**：`specs/delegate-task/spec.md` 的公式行写作"`− 非只读工具` … `∩ allowed-tools`"，字面顺序**无法**实现同一条 requirement 的规约句「**显式声明可放行非只读工具**（白名单退为例外通道）」与场景「新增的是非只读工具 → 仅在某个 skill 的 `allowed-tools` 显式声明后才下发」。**规约句与场景是权威**，故实现按上面的四步（把"显式声明"作为写类的例外通道）。实施者**不要**按公式字面把写类工具无条件减掉。

- [ ] **Step 1: 加常量**

`src/config/const.py`：在 `FORK_FORBIDDEN_TOOLS = ("ask_user", "delegate_task")` 那两行注释**之后**追加：

```python
# fork 子代理永不可持有的主 agent 专属工具**前缀**（design D8 第三档）：
# task_*（task_create/get/list/update/output/stop）是主 agent 的执行面（任务看板），
# 子代理是被委派的执行体，持有它会让子代理看见并改写主 agent 的任务编排。
# 与上面两项分列而不合并：三档理由不同（语义禁止 / 防递归 / 角色专属）。
# 用前缀而非逐名枚举：将来新增 task_* 工具时自动被挡，不必同步这份清单。
FORK_EXCLUSIVE_TOOL_PREFIXES = ("task_",)
```

- [ ] **Step 2: 写失败测试（整体重写该文件）**

`tests/agents/skills/test_fork_tools.py` 全文替换为：

```python
"""fork 子代理工具面：继承只读面 − 禁用集 − 非只读（除非显式声明）∩ 声明收窄。"""

from unittest.mock import MagicMock

import pytest
from langchain_core.tools import tool

from src.agents.skills.fork_tools import select_fork_tools
from src.agents.tools.readonly import readonly_map
from src.agents.tools.task_tools import make_task_tools
from src.config.const import FORK_EXCLUSIVE_TOOL_PREFIXES, FORK_FORBIDDEN_TOOLS


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


@tool("delegate_task")
def _delegate(skill: str) -> str:
    """委派。"""
    return skill


@tool("task_create")
def _task_create(title: str) -> str:
    """建任务。"""
    return title


@tool("write_doc")
def _write_doc(text: str) -> str:
    """写文件（写类工具的代表）。"""
    return text


# 只读表：检索/交互类只读（生产注册点即如此）；task_* 与 write_doc 不在表里
_RO = {"retrieve_kb": True, "search_web": True, "ask_user": True, "delegate_task": True}


def test_empty_allowed_inherits_readonly_face():
    """allowed-tools 为空 → 不收窄，继承只读面（D7 反转了"空 = 零工具"的旧语义）。"""
    picked = select_fork_tools([], [_retrieve, _search], None, _RO)
    assert [t.name for t in picked] == ["retrieve_kb", "search_web"]


def test_forbidden_tools_never_handed_over_even_when_declared():
    """禁用集优先于白名单：即使显式声明（且表里标只读）也不下发。"""
    picked = select_fork_tools(
        ["ask_user", "delegate_task"], [_ask, _delegate], None, _RO
    )
    assert picked == []


def test_task_prefix_blocked_even_when_readonly_and_declared():
    """task_* 前缀硬挡：表里标只读 + 显式声明 也不下发（D8 角色专属）。"""
    picked = select_fork_tools(
        ["task_create"], [_task_create], None, {**_RO, "task_create": True}
    )
    assert picked == []


def test_write_tool_not_handed_over_by_default():
    """非只读工具默认不下发。"""
    assert select_fork_tools([], [_write_doc], None, {**_RO, "write_doc": False}) == []


def test_write_tool_handed_over_when_explicitly_declared():
    """白名单退为例外通道：显式声明可放行非只读工具。"""
    picked = select_fork_tools(
        ["write_doc"], [_write_doc], None, {**_RO, "write_doc": False}
    )
    assert [t.name for t in picked] == ["write_doc"]


def test_missing_in_readonly_map_is_treated_as_write():
    """表中缺项按非只读处理（fail-safe）：未显式声明则不下发。"""
    assert select_fork_tools([], [_write_doc], None, _RO) == []


def test_allowed_narrows_to_intersection():
    """声明了白名单 → 收窄为交集。"""
    picked = select_fork_tools(["retrieve_kb"], [_retrieve, _search], None, _RO)
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_executor_tools_narrows_further():
    """执行者预设 tools 再收窄（更窄者胜）。"""
    picked = select_fork_tools(
        ["retrieve_kb", "search_web"], [_retrieve, _search], ["search_web"], _RO
    )
    assert [t.name for t in picked] == ["search_web"]


def test_unknown_declared_name_is_ignored():
    """白名单里引用不存在的工具 → 忽略（不抛）。"""
    assert select_fork_tools(["ghost"], [_retrieve], None, _RO) == []


def test_empty_readonly_map_fails_closed():
    """空表 fail-closed：一个都不下发（与双轴推导的 fail-open 极性相反，有意为之）。"""
    with pytest.warns(UserWarning, match="fail-closed"):
        assert select_fork_tools([], [_retrieve, _search], None, {}) == []


def test_real_tool_pool_guard():
    """守卫：域是**真实工具池**（`make_rag_tools` + `make_task_tools`），不是 ToolRegistry
    —— `task_*` 不进注册表，遍历注册表会漏掉 D8 最担心的对象。

    以**空 allowed-tools** 装配时：结果不得含任何非只读工具、不得含禁用集成员。
    """
    from src.agents.tools.rag_tools import make_rag_tools

    pool = [
        *(
            make_rag_tools(
                MagicMock(), MagicMock(), MagicMock(), delegate_task=_delegate
            )
            or []
        ),
        *make_task_tools(),
    ]
    pool_names = {t.name for t in pool}
    assert "task_create" in pool_names, "池里应含 D8 最担心的 task_* 对象"
    assert "retrieve_kb" in pool_names

    readonly = readonly_map()
    assert readonly, "真实注册路径下只读表应已填充（build_graph 期注册，早于请求）"

    picked = select_fork_tools([], pool, None, readonly)
    picked_names = {t.name for t in picked}
    for name in picked_names:
        assert name not in FORK_FORBIDDEN_TOOLS, f"{name} 属禁用集却下发了"
        for prefix in FORK_EXCLUSIVE_TOOL_PREFIXES:
            assert not name.startswith(prefix), f"{name} 属主 agent 专属类却下发了"
        assert readonly.get(name) is True, f"{name} 非只读却进了子代理工具面"
    # D7 的交付：只读检索工具确实进入了子代理工具面
    assert "retrieve_kb" in picked_names
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_fork_tools.py -v`
Expected: FAIL —— 旧实现下 `test_empty_allowed_inherits_readonly_face` 得 `[]`、`test_write_tool_handed_over_when_explicitly_declared` 得 `[]`、三条 `pytest.warns`/前缀断言因签名不符报 `TypeError: select_fork_tools() takes from 1 to 3 positional arguments but 4 were given`。

- [ ] **Step 4: 重写实现**

`src/agents/skills/fork_tools.py` 全文替换为：

```python
"""fork 子代理的工具面筛选 —— 主 agent 工具池 ∩ 只读约束 ∩ 声明收窄（design D7/D8）。

口径（顺序即语义，勿调换）：
1. **先减禁用集**：`FORK_FORBIDDEN_TOOLS`（ask_user / delegate_task）与
   `FORK_EXCLUSIVE_TOOL_PREFIXES`（task_*）永不下发，**即使 skill 显式声明**
   （禁用集优先于白名单）。
2. **再按只读性筛**：非只读工具默认不下发；仅当 skill 在 `allowed-tools` 里**显式声明**
   该工具时才放行（白名单退为"写权限的例外通道"）。表中**缺项**按非只读处理（fail-safe）。
3. **`allowed-tools` 是收窄项**：不声明即**不收窄**（继承只读面）；声明了才取交集。
4. 执行者预设声明 `tools` 时再取交集。

**空表极性（design D7，第二轮评审指出）**：`readonly_map()` 为空（工具尚未注册）时 fork 侧
**fail-closed**——一个都不下发。这与 `invocation.derive_invocation_flags` 对空表的
**fail-open** 极性**相反且都是有意为之**：同一张表的两个消费者失败代价不同——双轴推导空表时
不锁只是少了一层保护，而 fork 侧空表时"按只读放行"会把写权限下发给子代理。
**不得为"统一"而改掉任一侧的极。**
"""

import warnings

from langchain_core.tools import BaseTool

from src.config.const import FORK_EXCLUSIVE_TOOL_PREFIXES, FORK_FORBIDDEN_TOOLS


def _is_forbidden(name: str) -> bool:
    """禁用集判定：精确名（ask_user / delegate_task）+ 主 agent 专属前缀（task_*）。

    Args:
        name: 工具名

    Returns:
        True 表示该工具永不下发子代理（优先于白名单与只读放行）
    """
    if name in FORK_FORBIDDEN_TOOLS:
        return True
    for prefix in FORK_EXCLUSIVE_TOOL_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def select_fork_tools(
    allowed: list[str],
    available: list,
    executor_tools: list[str] | None = None,
    tool_readonly: dict[str, bool] | None = None,
) -> list:
    """按 design D7 口径筛选可交给子代理的工具（四步顺序见模块 docstring）。

    Args:
        allowed: skill 的 allowed-tools（**空 = 不收窄**；非空 = 收窄为交集，
            并作为非只读工具的例外放行通道）
        available: 本次启用的工具对象（LangChain BaseTool）
        executor_tools: 执行者预设声明的工具名（空/None = 不再收窄）
        tool_readonly: 工具名 -> 是否只读（readonly_map()）；空/None 视为表未填充

    Returns:
        过滤后的工具列表（保持 available 原顺序）；空表时返回空列表（fail-closed）
    """
    readonly = tool_readonly if tool_readonly is not None else {}
    if not readonly:
        warnings.warn(
            "工具只读表为空（工具尚未注册），fork 子代理工具面按 fail-closed 处理：不下发任何工具"
        )
        return []
    declared = set(allowed)
    executor_allowed = set(executor_tools) if executor_tools else set()
    picked = []
    for tool in available:
        if not isinstance(tool, BaseTool):
            continue
        if _is_forbidden(tool.name):
            continue
        is_readonly = readonly.get(tool.name, False)
        if not is_readonly and tool.name not in declared:
            continue
        if declared and tool.name not in declared:
            continue
        if executor_allowed and tool.name not in executor_allowed:
            continue
        picked.append(tool)
    return picked
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_fork_tools.py -v`
Expected: PASS（11 passed）；**输出无 warning 泄漏**（除 `test_empty_readonly_map_fails_closed` 由 `pytest.warns` 自行捕获）。

- [ ] **Step 6: 跑 skill 相关全套，确认无其它测试依赖旧口径**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ -q`
Expected: PASS。此时**允许**出现失败，但必须是"依赖旧零工具语义"的用例（见 Task 2 的 Step 2 一并处理）；把失败的用例名记进报告。若出现**与本任务无关**的失败，停下报告。

- [ ] **Step 7: Commit**

```bash
git add src/config/const.py src/agents/skills/fork_tools.py tests/agents/skills/test_fork_tools.py
git commit -m "feat(fork): 子代理工具面改为继承只读面 + 禁用集三档（D7/D8）"
```

---

## Task 2: executor 接线 + 作废 loader 的零工具 warning（task 3.4 / 3.7）

**Files:**
- Modify: `src/agents/skills/executor.py`（`_fork_tools`）
- Modify: `src/agents/skills/loader.py`（删 `_warn_fork_without_tools` 及其调用）
- Modify: `pyproject.toml`（删已失效的 `filterwarnings` ignore）
- Test: `tests/agents/skills/test_skill_executor.py`、`tests/agents/skills/test_skill_loader.py`

**Interfaces:**
- Consumes: Task 1 的 `select_fork_tools(..., tool_readonly=)`；`readonly_map()`
- Produces: `SkillExecutor._fork_tools(record, preset) -> list`（行为变化：只读表非空且未装配 provider 时记 warning）

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_executor.py`：文件顶部加两个 `@tool` 替身，并追加两条测试（该文件**没有**现成的 executor 工厂，既有写法是直接 `SkillExecutor(main_llm=MagicMock(), ...)`，见 `:93`；`_record(**overrides)` 助手已存在，见 `:29`）：

```python
@tool("retrieve_kb")
def _retrieve(query: str) -> str:
    """检索。"""
    return query


@tool("write_doc")
def _write_doc(text: str) -> str:
    """写文件（写类工具的代表）。"""
    return text


def test_fork_tools_uses_readonly_map(monkeypatch):
    """未声明 allowed-tools 时继承只读面（不再零工具），且非只读工具被挡。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(
        readonly_module, "_TOOL_READONLY", {"retrieve_kb": True, "write_doc": False}
    )
    exe = SkillExecutor(main_llm=MagicMock(), tool_provider=lambda: [_retrieve, _write_doc])
    picked = exe._fork_tools(_record(allowed_tools=[]), None)
    assert [t.name for t in picked] == ["retrieve_kb"]


def test_fork_tools_without_provider_warns_when_tools_registered(monkeypatch):
    """工具确实注册过却拿不到 provider → 记 warning（不再静默零工具）。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(readonly_module, "_TOOL_READONLY", {"retrieve_kb": True})
    exe = SkillExecutor(main_llm=MagicMock(), tool_provider=None)
    with pytest.warns(UserWarning, match="tool_provider"):
        assert exe._fork_tools(_record(allowed_tools=[]), None) == []
```

> `monkeypatch.setattr(readonly_module, "_TOOL_READONLY", {...})` 是**正确**的杠杆：`readonly_map()` 每次调用都读该模块级字典并返回副本，故补丁立即生效，且测试间互不污染。

`tests/agents/skills/test_skill_loader.py`：**删除**断言该 warning 的两个用例（约 `:57-81`，一个用 `pytest.warns(UserWarning, match="allowed-tools")` 断言命中、一个用 `not [...]` 断言不命中）。该 warning 本任务删除，留着这两个用例会直接失败。

> **不受影响的两处既有断言**（已核实，勿改）：`tests/agents/skills/test_fork_sub_agent_contract.py:39` 与 `tests/agents/skills/test_skill_executor.py:172` 断言的是 `captured["tools"] == []`，其前提是 `available`（provider 返回的池）为空——空池在新口径下仍得 `[]`，语义未变。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_skill_executor.py -v`
Expected: FAIL —— `_fork_tools` 现按 `record.allowed_tools`（空）返回 `[]`；无 provider 时无 warning。

- [ ] **Step 3: 改实现**

`src/agents/skills/executor.py` 的 `_fork_tools`（现状 `:316-326`）整体替换为：

```python
    def _fork_tools(self, record: SkillRecord, preset):
        """按 design D7 口径选子代理工具面（继承只读面 − 禁用集 ∩ 声明收窄）。

        只读表从进程级声明读取（`readonly_map()`）——工具在 `build_graph` 期注册，
        **早于任何请求**，故生产路径上此处恒非空；空表由 `select_fork_tools` 按
        fail-closed 处理。

        Args:
            record: fork SkillRecord（读 allowed_tools 作收窄项）
            preset: 执行者预设（读 tools 作再收窄）；None 表示不再收窄

        Returns:
            子代理工具列表。未装配 tool_provider 且**工具已注册过**时记 warning 并返回空
            （真实装配缺陷，不再静默零工具）；工具从未注册（离线/单测）时静默返回空。
        """
        if self._tool_provider is not None:
            available = self._tool_provider()
        else:
            available = []
            if readonly_map():
                # 表非空说明工具确实注册过，却拿不到 provider → 装配缺陷，必须可见
                warnings.warn(
                    "SkillExecutor 未装配 tool_provider，但工具已注册：fork 子代理工具面为空"
                )
        if preset is not None:
            executor_tools = preset.tools
        else:
            executor_tools = None
        return select_fork_tools(
            record.allowed_tools, available, executor_tools, readonly_map()
        )
```

同文件 import 区补：`import warnings`；`from src.agents.tools.readonly import readonly_map`。

`src/agents/skills/loader.py`：删除方法 `_warn_fork_without_tools`（现状 `:151-165`）**及其全部调用点**；同步删除 import 区不再使用的 `warnings`（若它有别的使用者则保留）。

- [ ] **Step 4: 清掉已失效的 filterwarnings**

`pyproject.toml` 的 `[tool.pytest.ini_options] filterwarnings` 里，删掉这一条及其上方那段注释（它描述的 warning 已不存在，留着就是失实注释）：

```toml
    # loader 对「fork 但未声明 allowed-tools」的提示性 warning。仓库现有唯一 fork skill
    # (financial-statement-analyzer) 属分析型，零工具是既定设计（材料由主 agent 预检索
    # 经 task 传入），因此凡加载真实 skills 目录的用例都会稳定命中，属预期噪声。
    # 断言该 warning 的用例（tests/agents/skills/test_skill_loader.py）用 pytest.warns
    # 自行捕获，pytest.warns 会覆盖本过滤器，不受影响。
    "ignore:.*未声明 allowed-tools",
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ tests/services/ -q`
Expected: PASS。

**判定规则（按实测结果二选一，不要靠猜）**：
- 若输出**无**新增 warning 泄漏 → 完成，不加任何 filter。
- 若新 warning 泄漏进**无关**用例（它们只是恰好构造了无 provider 的 executor）→ 在 `pyproject.toml` 的 `filterwarnings` 里加一条**窄匹配**条目，并写明它是"测试装配产物"而非设计噪声：

```toml
    # 无 tool_provider 的 executor（测试装配）在工具已注册时会发此 warning；
    # 生产路径由 build_graph 的 tool_sink 恒注入 provider，不会命中。
    # 断言该 warning 的用例（tests/agents/skills/test_skill_executor.py）用 pytest.warns
    # 自行捕获，pytest.warns 覆盖本过滤器，不受影响。
    "ignore:.*未装配 tool_provider",
```

- [ ] **Step 6: 跑全量确认无回归**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 全部 PASS（P1 基线 1289 + 本阶段新增）。

- [ ] **Step 7: Commit**

```bash
git add src/agents/skills/executor.py src/agents/skills/loader.py pyproject.toml tests/agents/skills/test_skill_executor.py tests/agents/skills/test_skill_loader.py
git commit -m "feat(fork): executor 按只读表装配工具面；作废零工具 warning（D7）"
```

---

## Task 3: 按路径的引用编号指示 + `DelegateRun.via`（design D16，与 §3 同批）

**Files:**
- Modify: `src/config/const.py`（`DELEGATE_VIA_*`）
- Modify: `src/agents/skills/delegate_run.py`（`via` 字段）
- Modify: `src/agents/graph/skill_direct.py`（两处 `DelegateRun(...)` 填 `via`）
- Modify: `src/agents/skills/delegate_task.py`（`DelegateRun(...)` 显式填 `via`）
- Modify: `src/config/prompts/__init__.py`（默认执行者 prompt + 两条按路径指示）
- Modify: `src/agents/skills/executor.py`（`_executor_system_prompt` 按 `via` 分支；`_build_sub_agent` 透传）
- Modify: `docs/agents/glossary.md`、`docs/agents/prompt-ownership.md`
- Test: `tests/agents/skills/test_executor_contract.py`（追加）+ **修正** `tests/agents/skills/test_executor_contract.py`、`tests/agents/skills/test_fork_sub_agent_contract.py` 里**已有的 4 条** prompt 断言

**Interfaces:**
- Consumes: `DelegateRun`（`delegate_id` / `skill_name` / `ctx`）
- Produces: `DELEGATE_VIA_DIRECT = "direct"` / `DELEGATE_VIA_DELEGATE = "delegate"`；`DelegateRun.via: str = DELEGATE_VIA_DELEGATE`；`SkillExecutor._executor_system_prompt(preset, via: str) -> str`；`FORK_DELEGATE_CITATION_INSTRUCTION` / `FORK_DIRECT_CITATION_INSTRUCTION`

**为什么必须给载体而不是猜路径**（第三轮评审）：两条路径**共用同一个执行器**，靠任务文本或正文内容猜路径必然出错；`DelegateRun` 是既有的"每次委派一个实例"载体，加一个字段即可。

- [ ] **Step 1: 加常量与字段**

`src/config/const.py` 追加：

```python
# 委派路径标识（DelegateRun.via）：执行器据此决定给子代理的引用编号指示（design D16）。
# direct = /xxx 直出（**没有主 agent 补标** → 子代理须自检索并自标 [n]）；
# delegate = 主 agent 委派（[n] 由主 agent 按自身检索来源补标 → 子代理不标）。
DELEGATE_VIA_DIRECT = "direct"
DELEGATE_VIA_DELEGATE = "delegate"
```

`src/agents/skills/delegate_run.py`：在 `@dataclass` 里、`result_text` **之后**追加字段（带默认值，既有构造不受影响）：

```python
    via: str = DELEGATE_VIA_DELEGATE  # 执行路径（DELEGATE_VIA_DIRECT|DELEGATE_VIA_DELEGATE；决定引用编号指示）
```

同文件 import 区补 `from src.config.const import DELEGATE_VIA_DELEGATE`。

- [ ] **Step 2: 写失败测试**

在 `tests/agents/skills/test_executor_contract.py` 追加（该文件主题即"执行契约随人设下发"，且已 import `FORK_EXECUTION_CONTRACT`；构造方式是直接 `SkillExecutor(main_llm=object())`，见其 `:9`——**该文件没有 `_executor()` 助手**）：

```python
def test_direct_path_asks_for_citations():
    """直出路径：子代理须自检索并自标 [n]（没有主 agent 补标）。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None, DELEGATE_VIA_DIRECT)
    assert "自行检索" in prompt
    assert "[n]" in prompt


def test_delegate_path_forbids_citations():
    """委派路径：子代理不得自标 [n]（编号由主 agent 统一补标）。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None, DELEGATE_VIA_DELEGATE)
    assert "不要标注引用编号 [n]" in prompt


def test_preset_persona_also_gets_path_citation_instruction():
    """预设人设同样受按路径的引用指示约束（指示统一追加在最末）。"""

    class _Preset:
        system_prompt = "你是财务专家。"

    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(_Preset(), DELEGATE_VIA_DIRECT)
    assert prompt.startswith("你是财务专家。")
    assert "自行检索" in prompt


def test_default_executor_prompt_has_no_unconditional_citation_rule():
    """默认人设里不得再留无条件的 [n] 禁令（那会与直出路径矛盾）。"""
    assert "不标注引用编号" not in FORK_DEFAULT_EXECUTOR_PROMPT
```

**同批必须修正的 4 条既有断言**（签名变化 + 返回串多了一段，不改就红）：

1. `test_executor_contract.py:10` → `exe._executor_system_prompt(None)` 补第二实参 `DELEGATE_VIA_DELEGATE`
2. `test_executor_contract.py:21` → `exe._executor_system_prompt(_Preset())` 同上
3. `test_fork_sub_agent_contract.py:75-76` → 现在是**等值断言** `== (FORK_DEFAULT_EXECUTOR_PROMPT + FORK_EXECUTION_CONTRACT)`，须改成 `== (FORK_DEFAULT_EXECUTOR_PROMPT + FORK_EXECUTION_CONTRACT + FORK_DELEGATE_CITATION_INSTRUCTION)`，并补 `DELEGATE_VIA_DELEGATE` 实参
4. `test_fork_sub_agent_contract.py:86` 附近（preset 人设那条）→ 同样补 `via` 实参，并按需断言引用指示已在末尾

同文件 import 区补 `from src.config.const import DELEGATE_VIA_DELEGATE, DELEGATE_VIA_DIRECT`（以及 `FORK_DELEGATE_CITATION_INSTRUCTION`，按需）。

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ -q -k "prompt"`
Expected: FAIL —— `_executor_system_prompt` 只接受 1 个参数；`FORK_DEFAULT_EXECUTOR_PROMPT` 仍含无条件禁令。

- [ ] **Step 4: 改实现**

`src/config/prompts/__init__.py`：把 `FORK_DEFAULT_EXECUTOR_PROMPT` 改为**去掉**无条件引用禁令，并新增两条按路径指示：

```python
FORK_DEFAULT_EXECUTOR_PROMPT: str = (
    "你是一个企业知识库智能助手，作为委派子代理执行被指派的具体任务。"
    "只依据任务给出的材料与方法论作答，不得编造；输出结构化分析文本。"
)

# 引用编号指示按**路径**分派（design D16）：两条路径的 [n] 策略**不同且有意为之**，
# 实施者不得"统一"它们。
# 委派路径：子代理检索落子池、不回流主池，[n] 由主 agent 按**自身**检索来源统一补标，
#   故子代理不得自标（否则编号语义会与主池错配）。
FORK_DELEGATE_CITATION_INSTRUCTION: str = (
    "\n\n引用编号：不要标注引用编号 [n]（本轮编号由编排方按主 agent 的检索来源统一补标）。"
)
# 直出路径（/xxx）：没有主 agent 补标，子代理须自行检索并自标 [n]，
#   其子引用池即本轮引用池。
FORK_DIRECT_CITATION_INSTRUCTION: str = (
    "\n\n引用编号：本轮没有上游补标——请先自行检索，并在句末标注与检索结果一致的 [n]"
    "（编号与你检索到的材料顺序一致）。"
)
```

`src/agents/skills/executor.py` 的 `_executor_system_prompt`（现状 `:298-314`）整体替换为：

```python
    def _executor_system_prompt(self, preset, via: str) -> str:
        """解析 fork 子代理 system prompt（执行者人设 + 执行契约 + 按路径的引用指示）。

        Args:
            preset: 执行者 AgentPreset；None 表示未选执行者预设
            via: 执行路径（DELEGATE_VIA_DIRECT|DELEGATE_VIA_DELEGATE），决定引用编号指示

        Returns:
            preset 非空且 system_prompt 非空时返回其人设，否则返回系统默认
            FORK_DEFAULT_EXECUTOR_PROMPT；两者都追加执行契约 FORK_EXECUTION_CONTRACT
            与按 via 选择的引用指示（统一放在最末，保证预设人设也受同一约束）。
            本层不构造 PromptManager、不拉 Langfuse。
        """
        base = FORK_DEFAULT_EXECUTOR_PROMPT
        if preset is not None and preset.system_prompt:
            base = preset.system_prompt
        if via == DELEGATE_VIA_DIRECT:
            citation = FORK_DIRECT_CITATION_INSTRUCTION
        else:
            citation = FORK_DELEGATE_CITATION_INSTRUCTION
        return base + FORK_EXECUTION_CONTRACT + citation
```

`_build_sub_agent`（`:278-296`）签名加 `via: str`，`system_prompt=self._executor_system_prompt(preset, via)`；`_run_fork` 里求 `via` 并透传：

```python
        if run is not None:
            via = run.via
        else:
            # run 为空只出现在无请求上下文的 fail-open 分支（委派路径），保持既有引用策略
            via = DELEGATE_VIA_DELEGATE
```

`src/agents/graph/skill_direct.py`：两处 `DelegateRun(...)`（`:92-96`、`:130-134`）各加 `via=DELEGATE_VIA_DIRECT,`；import 区补常量。
`src/agents/skills/delegate_task.py`：`DelegateRun(...)`（`:102-106` 一带）加 `via=DELEGATE_VIA_DELEGATE,`。

- [ ] **Step 5: 补文档**

`docs/agents/glossary.md`：给 `fork 执行` 或邻近条目补一段——**两条路径的 `[n]` 策略不同且有意为之**（委派路径由主 agent 补标、直出路径子代理自标），并注明载体是 `DelegateRun.via`。
`docs/agents/prompt-ownership.md`：在 `output-delegate-citation` 相关段补一句指针，指向 glossary 的同一说明（**不要复制正文**，一事一档）。

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ tests/agents/graph/test_direct_skill_round.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/config/const.py src/agents/skills/delegate_run.py src/agents/skills/delegate_task.py src/agents/skills/executor.py src/agents/graph/skill_direct.py src/config/prompts/__init__.py docs/agents/glossary.md docs/agents/prompt-ownership.md tests/agents/skills/
git commit -m "feat(fork): 按路径给子代理引用编号指示，DelegateRun 增 via（D16）"
```

---

## Task 4: 会话级委派预算计数器（design D10）

**Files:**
- Create: `src/chat/delegate_budget.py`
- Modify: `src/config/settings.py`
- Modify: `src/chat/manager.py`（`clear_history_async` 挂钩复位）
- Test: Create `tests/chat/test_delegate_budget.py`

**Interfaces:**
- Produces:
  - `class SessionDelegateBudget`：`check_and_incr(session_id: str, limit: int) -> bool`、`reset(session_id: str) -> None`、`used(session_id: str) -> int`、`sweep_expired() -> None`、`ttl_seconds`
  - 模块级单例 `delegate_budget`
  - `settings.DELEGATE_MAX_PER_SESSION: int`

**为什么放 `src/chat/`**：`agents/` 要读它（`delegate_task`），`services/` 也要读它（会话删除复位）。放 `services/` 会造成 `agents → services` 反向 import（层间规则禁止）；`src/chat/` 已被 `agents/` 引用的先例是 `task_registry`。

- [ ] **Step 1: 写失败测试**

`tests/chat/test_delegate_budget.py`（新建）：

```python
"""会话级委派预算：进程内计数、TTL 惰性清理、显式复位。"""

from src.chat.delegate_budget import SessionDelegateBudget


def test_under_limit_allows_and_counts():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 2) is True
    assert budget.check_and_incr("s1", 2) is True
    assert budget.used("s1") == 2


def test_over_limit_denies_without_increment():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 1) is True
    assert budget.check_and_incr("s1", 1) is False
    assert budget.used("s1") == 1


def test_sessions_are_isolated():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 1) is True
    assert budget.check_and_incr("s2", 1) is True


def test_reset_clears_session():
    budget = SessionDelegateBudget()
    budget.check_and_incr("s1", 1)
    budget.reset("s1")
    assert budget.used("s1") == 0
    assert budget.check_and_incr("s1", 1) is True


def test_sweep_expired_drops_stale_sessions(monkeypatch):
    budget = SessionDelegateBudget(ttl_seconds=10)
    budget.check_and_incr("s1", 5)
    monkeypatch.setattr("src.chat.delegate_budget.time.time", lambda: 10**9)
    budget.sweep_expired()
    assert budget.used("s1") == 0


def test_check_does_not_rollback_on_failure():
    """已发起即计数：没有回滚接口（design D10，取消/异常不回滚）。"""
    budget = SessionDelegateBudget()
    assert not hasattr(budget, "decrement")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/chat/test_delegate_budget.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.chat.delegate_budget'`。

- [ ] **Step 3: 建实现**

`src/chat/delegate_budget.py`（新建）：

```python
"""会话级委派预算 —— 进程内计数、TTL 惰性清理（design D10）。

用途：给"模型裁量的委派"（`delegate_task` 的定点与通用分支）加一道会话级闸门，
防失控刷子代理。用户显式 `/xxx` 直出走 `skill_direct`，**不经** `delegate_task`，
故天然不消耗预算。

约束与语义：
- **进程内**状态（与 `task_registry` / `streaming_manager` 同假设：生产单 worker，
  流式状态在进程内；见 docs/agents/defensive-patterns.md）。进程重启即清。
- **取消/异常不回滚**：已发起即计数（计数发生在子代理启动前），失败不退还额度——
  否则模型可以靠"发起即失败"绕过闸门。
- **只记不判**：本模块不读 settings，`limit` 由调用方传入（保持可测、无配置耦合）。
- **不设嵌套深度上限**（design D10）：子代理不持有 `delegate_task`（禁用集硬保证），
  委派深度恒为 1，故深度上限是没有对象的配置。**勿照抄外部实现的"5 层封顶"。**
"""

import time

BUDGET_TTL_SECONDS = 1800  # 条目 TTL（默认 30min），惰性清理（与 task_registry 同款）


class SessionDelegateBudget:
    """会话级委派计数（进程内，键为 session_id）。

    Attributes:
        ttl_seconds: 条目 TTL（秒）；`check_and_incr` 每次调用会顺带惰性清理
    """

    def __init__(self, ttl_seconds: float = BUDGET_TTL_SECONDS) -> None:
        self._counts: dict[str, int] = {}  # session_id -> 已发起委派次数
        self._touched: dict[str, float] = {}  # session_id -> 最近一次计数时间（unix 秒，供 TTL）
        self.ttl_seconds = ttl_seconds  # 条目 TTL（秒）

    def used(self, session_id: str) -> int:
        """返回该会话已发起的委派次数（未计数过为 0）。"""
        return self._counts.get(session_id, 0)

    def check_and_incr(self, session_id: str, limit: int) -> bool:
        """检查额度并占用一次。

        Args:
            session_id: 会话 id
            limit: 本次会话允许的委派次数上限

        Returns:
            True = 额度可用且已计数；False = 已触顶（**不**计数）
        """
        self.sweep_expired()
        current = self._counts.get(session_id, 0)
        if current >= limit:
            self._touched[session_id] = time.time()
            return False
        self._counts[session_id] = current + 1
        self._touched[session_id] = time.time()
        return True

    def reset(self, session_id: str) -> None:
        """清除该会话计数（会话删除/历史清空时调用）。"""
        self._counts.pop(session_id, None)
        self._touched.pop(session_id, None)

    def sweep_expired(self) -> None:
        """惰性清理：超过 TTL 未计数的会话整条删除（防进程内 dict 只增不减）。"""
        now = time.time()
        stale = [
            sid
            for sid, touched in self._touched.items()
            if now - touched > self.ttl_seconds
        ]
        for sid in stale:
            self.reset(sid)


# 模块级共享实例（与 task_registry 同生命周期假设）
delegate_budget = SessionDelegateBudget()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/chat/test_delegate_budget.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: 加配置并挂钩复位**

`src/config/settings.py`（与 `DELEGATE_TOTAL_TIMEOUT_S` 同区）追加：

```python
DELEGATE_MAX_PER_SESSION: int = int(os.getenv("DELEGATE_MAX_PER_SESSION", "50"))
"""每会话"模型裁量委派"次数上限（design D10）。

只对 `delegate_task` 计数；`/xxx` 直出不消耗。取值依据：P2 前提确认实测的
真实会话委派高水位为 3/会话（79 会话中仅 2 个有委派），50 约为其 16 倍，
宁宽不宁紧；配合 `delegate skip` 的命中率观测，上线一版数据后再收紧。
"""
```

`src/chat/manager.py` 的 `clear_history_async`（`:303`）在**方法体开头**挂钩：

```python
        delegate_budget.reset(session_id)
```
（同文件 import 区补 `from src.chat.delegate_budget import delegate_budget`。放在开头而非末尾：该方法有 early return，复位不能依赖走到末尾。）

- [ ] **Step 6: 跑测试确认通过（含复位挂钩）**

`tests/chat/test_chat_manager.py` 追加：

```python
async def test_clear_history_resets_delegate_budget():
    """会话历史清空 → 委派预算复位（design D10 的复位点）。"""
    from src.chat.delegate_budget import delegate_budget
    from src.chat.manager import ChatManager

    delegate_budget.check_and_incr("s-budget", 5)
    assert delegate_budget.used("s-budget") == 1
    manager = ChatManager(redis_url="redis://localhost:6379/0")
    await manager.clear_history_async("s-budget")
    assert delegate_budget.used("s-budget") == 0
```

> 构造方式照该文件既有写法（`ChatManager(redis_url=...)`，见 `tests/chat/test_chat_manager.py:71`）。因为复位放在 `clear_history_async` **方法体开头**，即使 Redis 不可达（该方法会先 `_ensure_redis_async()`）复位也已经发生——这正是不放末尾的原因。

Run: `POSTGRES_HOST=localhost pytest tests/chat/ -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/chat/delegate_budget.py src/chat/manager.py src/config/settings.py tests/chat/test_delegate_budget.py tests/chat/test_chat_manager.py
git commit -m "feat(delegate): 会话级委派预算计数器 + TTL + 会话删除复位（D10）"
```

---

## Task 5: 预算闸门在 `delegate_task` 生效（task 5.10）

**Files:**
- Modify: `src/agents/skills/delegate_task.py`
- Modify: `src/config/const.py`（`SSEInteractionTexts` 新文案）
- Modify: `docs/agents/logging-rules.md`（`delegate skip` 的 `reason` 取值补 `budget_exhausted`）
- Test: `tests/agents/skills/test_delegate_task.py`

**Interfaces:**
- Consumes: Task 4 的 `delegate_budget.check_and_incr(session_id, limit)`、`settings.DELEGATE_MAX_PER_SESSION`；既有 `Event.DELEGATE_SKIP`（`log_events.py:165`，字段集 `("reason", "skills_dir")`）
- Produces: 触顶时返回可读文案且**不抛异常**、不中断本轮

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_delegate_task.py` 追加（沿用该文件既有写法：`_record(...)` / `_FakeRegistry` / `make_delegate_task` / `RequestContext(session_id=...)` + `current_request_ctx.set/reset`，见 `:111-121`）：

```python
@pytest.mark.asyncio
async def test_budget_exhausted_returns_readable_reason(monkeypatch):
    """触顶：返回可读原因、不抛异常、**不启动子代理**（且提示不要再重试）。"""
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(delegate_budget, "_counts", {"s1": 50})
    monkeypatch.setattr(delegate_budget, "_touched", {"s1": 0.0})
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    tool = make_delegate_task(
        _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_agent",
            side_effect=AssertionError("触顶时不得启动子代理"),
        ):
            out = await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)
    assert "上限" in out
    assert "不要" in out


@pytest.mark.asyncio
async def test_inline_hit_does_not_consume_budget(monkeypatch):
    """inline 命中不启动子代理 → 不消耗预算。"""
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(delegate_budget, "_counts", {})
    monkeypatch.setattr(delegate_budget, "_touched", {})
    rec = _record("finance-qa", SkillContext.INLINE, "请按规则作答：{task}")
    tool = make_delegate_task(
        _FakeRegistry({"finance-qa": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await tool.ainvoke({"task": "2024营收多少", "skill": "finance-qa"})
    finally:
        current_request_ctx.reset(token)
    assert delegate_budget.used("s1") == 0


@pytest.mark.asyncio
async def test_budget_skip_logged(monkeypatch):
    """触顶记 delegate skip / reason=budget_exhausted。"""
    import src.agents.skills.delegate_task as dt_mod
    from src.chat.delegate_budget import delegate_budget

    monkeypatch.setattr(delegate_budget, "_counts", {"s1": 50})
    monkeypatch.setattr(delegate_budget, "_touched", {"s1": 0.0})
    captured: list[tuple] = []
    monkeypatch.setattr(
        dt_mod.core_logging, "log_event", lambda ev, **kw: captured.append((str(ev), kw))
    )
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    tool = make_delegate_task(
        _FakeRegistry({"finance-analyst": rec}), SkillExecutor(main_llm=MagicMock())
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        await tool.ainvoke({"task": "分析", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)
    assert ("delegate skip", {"reason": "budget_exhausted"}) in captured
```

> `_counts` / `_touched` 是 Task 4 定义的内部字段；直接置位比跑 50 次快得多，且与 `used()` / `check_and_incr()` 的语义一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py -v -k budget`
Expected: FAIL —— 目前无预算检查，子代理会被真实启动。

- [ ] **Step 3: 加文案与实现**

`src/config/const.py` 的 `SSEInteractionTexts` 内（`DELEGATE_UNKNOWN_SKILL` 附近）追加：

```python
    DELEGATE_BUDGET_EXHAUSTED: str = (
        "本轮委派已达会话上限（{limit} 次）。请改用你自己的能力作答，"
        "不要再重试委派。"
    )
```

`src/agents/skills/delegate_task.py`：在 **fork 分支**内、建 `DelegateRun` **之前**插入闸门（放在 `ctx is None` 判断**之后**，因为计数需要 `ctx.session_id`）：

```python
        if not delegate_budget.check_and_incr(
            ctx.session_id, settings.DELEGATE_MAX_PER_SESSION
        ):
            core_logging.log_event(
                Event.DELEGATE_SKIP,
                reason="budget_exhausted",
            )
            return SSEInteractionTexts.DELEGATE_BUDGET_EXHAUSTED.format(
                limit=settings.DELEGATE_MAX_PER_SESSION
            )
```

同文件 import 区补 `from src.chat.delegate_budget import delegate_budget`、`from src.config import settings`。

**注意（勿自行发明语义）**：① **inline 命中不消耗**（它在闸门之前就 return 了，天然不计数）；② **无请求上下文（`ctx is None`）的 fail-open 分支不计数**——没有 session 可归属，如实如此并在报告里写明；③ **取消/异常不回滚**（计数已在启动前发生）；④ **`/xxx` 直出不消耗**（走 `skill_direct`，不经本工具）。

**`skills_dir` 不要传**：既有 `delegate skip` 的两个调用点是 `agent_service.py:839`（`reason="registry_empty"`，无 `skills_dir`）与 `:843`（`reason="skills_dir_missing"`，带 `skills_dir`）——该字段只在"技能目录不存在"这一种 reason 下有意义，预算触顶与目录无关。

- [ ] **Step 4: 补日志文档**

`docs/agents/logging-rules.md` 的 `delegate skip` 条目下补 `reason` 取值 `budget_exhausted`（会话级委派预算触顶），与既有取值并列。

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/agents/skills/delegate_task.py src/config/const.py docs/agents/logging-rules.md tests/agents/skills/test_delegate_task.py
git commit -m "feat(delegate): 委派预算触顶返回可读原因并记 delegate skip（D10）"
```

---

## Task 6: 通用委派 —— `skill` 可选（design D6）

**Files:**
- Modify: `src/agents/skills/delegate_task.py`（参数可选 + 通用分支 + 事件/看板占位）
- Modify: `src/agents/skills/executor.py`（`execute` / `_run_fork` / `_render_fork_task` / `_resolve_executor` / `_fork_tools` 支持无 record）
- Modify: `src/config/const.py`（`DELEGATE_GENERIC_SKILL_NAME`、`SSEInteractionTexts.DELEGATE_GENERIC_TITLE`）
- Test: `tests/agents/skills/test_delegate_task.py`、`tests/agents/skills/test_skill_executor.py`

**Interfaces:**
- Produces: `DelegateTaskArgs.skill: str | None = None`；`SkillExecutor.execute(record: SkillRecord | None, task: str, run: DelegateRun | None = None) -> str`

**承载性约束（预检扫描发现，必须遵守）**：Task 5 已把预算闸门插在 `delegate_task` 的 fork 分支里（`ctx is None` 判断之后）。本任务重构同一函数、拆出通用分支时，**两条分支必须共用同一处闸门**（或各自显式调用同一次 `check_and_incr`）——**不得**把通用分支写成绕过闸门的独立早退路径。通用委派同样是"模型裁量的委派"，按 design D10 必须消耗预算。**并在本任务的测试里加一条断言**：通用委派（省略 `skill`）同样消耗预算（触顶时被拒）。

- [ ] **Step 1: 加常量**

`src/config/const.py` 追加：

```python
# 通用委派（省略 skill）在事件/trace 上的 skill 占位。用 ASCII 以保证日志值安全
# （logging-rules：事件消息英文 k=v；中文仅限用户可见文案）。
DELEGATE_GENERIC_SKILL_NAME = "(generic)"
```

`SSEInteractionTexts` 追加用户可见标题：

```python
    DELEGATE_GENERIC_TITLE: str = "通用分析任务"
```

- [ ] **Step 2: 写失败测试**

```python
@pytest.mark.asyncio
async def test_generic_delegation_without_skill(monkeypatch):
    """省略 skill → 通用委派：不查注册表、task 直接作子代理输入、事件与看板仍有条目。"""
    import src.agents.skills.delegate_task as dt_mod

    board = SessionTaskRegistry(on_change=None)  # 不写真实 buffer，防污染
    tool = make_delegate_task(
        _FakeRegistry({}), SkillExecutor(main_llm=MagicMock())  # 空注册表：若去查就会走未知 skill
    )
    fake_sub = _fake_sub_agent(
        _event("on_chat_model_start"),
        _event("on_chat_model_stream", chunk=AIMessageChunk(content="结论")),
        _event("on_chat_model_end", output=AIMessage(content="结论")),
    )
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch.object(dt_mod, "task_registry", board), patch(
            "src.agents.skills.executor.create_agent", return_value=fake_sub
        ):
            out = await tool.ainvoke({"task": "帮我查一下某公司近三年的营收"})
            items = board.list_session("s1")
    finally:
        current_request_ctx.reset(token)
    assert "结论" in out
    assert items[0].title == SSEInteractionTexts.DELEGATE_GENERIC_TITLE


@pytest.mark.asyncio
async def test_generic_delegation_inherits_readonly_tools(monkeypatch):
    """通用委派同样继承只读工具面（含 retrieve_kb），且不含写类/禁用集。"""
    from src.agents.tools import readonly as readonly_module

    monkeypatch.setattr(
        readonly_module, "_TOOL_READONLY", {"retrieve_kb": True, "write_doc": False}
    )

    @tool("retrieve_kb")
    def _rk(query: str) -> str:
        """检索。"""
        return query

    @tool("write_doc")
    def _wd(text: str) -> str:
        """写。"""
        return text

    captured: dict = {}

    def _fake_create_agent(*args, **kwargs):
        captured.update(kwargs)
        return _fake_sub_agent(_event("on_chat_model_end", output=AIMessage(content="ok")))

    executor = SkillExecutor(main_llm=MagicMock(), tool_provider=lambda: [_rk, _wd])
    tool = make_delegate_task(_FakeRegistry({}), executor)
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_agent", side_effect=_fake_create_agent
        ):
            await tool.ainvoke({"task": "查营收"})
    finally:
        current_request_ctx.reset(token)
    assert [t.name for t in captured["tools"]] == ["retrieve_kb"]
```

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py -v -k generic`
Expected: FAIL —— `DelegateTaskArgs.skill` 必填，缺参报校验错误。

- [ ] **Step 4: 改实现**

`DelegateTaskArgs`：

```python
class DelegateTaskArgs(BaseModel):
    """delegate_task 工具参数（LLM 可见的入参契约）。"""

    task: str = Field(description="要委派的任务描述（深度任务可把已到手的材料一并放入）")
    skill: str | None = Field(
        default=None,
        description=(
            "要调用的 skill 名（可用列表见工具描述）。"
            "省略即通用委派：不加载任何 skill 正文，由通用子代理直接完成任务。"
        ),
    )
```

工具 body：`skill_registry.reload_if_changed()` 与 `record = skill_registry.get(skill)` 改为**仅在 `skill` 非空时**执行；命中 inline 仍走原路；`record is None` 且 `skill` 非空 → 保持既有"未知 skill + 可用列表"返回；`skill` 为空 → 通用分支（与 fork 分支共用后续全部逻辑，只是 `record` 为 `None`、`skill_name` 用占位、看板标题用 `DELEGATE_GENERIC_TITLE`）。

`src/agents/skills/executor.py`：
- `execute` 的 `record` 形参改 `SkillRecord | None`；`None` 时直接走 `_run_fork`（无 inline 可能）。
- `_render_fork_task(record, task)` → `record is None` 时**直接返回 `task`**（无正文可渲染）。
- `_fork_tools(record_or_none, preset)` → `record is None` 时 `allowed = []`（不收窄，仅继承只读面）。
- `_resolve_executor(record_or_none, session_agent)` → `record is None` 时跳过 `record.agent` 分支，只用 `session_agent`。
- `_run_fork` 里所有 `record.xxx` 访问按上述分支改写，别用 `getattr` 兜底（显式 `is not None`）。

`src/agents/skills/delegate_task.py`：事件与看板的 `skill` / 标题在两处按是否有 record 取值。

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ -q`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/agents/skills/delegate_task.py src/agents/skills/executor.py src/config/const.py tests/agents/skills/
git commit -m "feat(delegate): skill 参数可选，新增通用委派（D6）"
```

---

## Task 7: P2 验收（tasks §11 的 11.7–11.14）

**Files:** 无代码改动（本任务是执行与判定）。

- [ ] **Step 1: 全量质量门禁**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 全部 PASS（P1 基线 1289 + 本阶段新增）。**不要**跑全仓 `ruff format .`（本仓已知陷阱：会误改 `docs/**` 内嵌代码块）。格式检查用：

Run: `ruff check . && ruff format --check src/ tests/ && pyright src/`
Expected: `ruff` 无错误；`pyright` 不新增 error。

- [ ] **Step 2: 内容库零改动核对**

Run:
```bash
for n in financial-statement-analyzer competitive-landscape market-sizing-analysis; do
  diff -rq "$HOME/.agents/skills/$n" "skills/$n" && echo "$n identical"
done
```
Expected: 仍**只有** `SKILL.md` 的 description 尾句差异（及 `market-sizing-analysis` 的已知空行差异）——**P2 未新增任何差异**。

- [ ] **Step 3: 11.7 直出路径子代理自检索（**P2 的关键检查点**）**

跑一轮 `/competitive-landscape <问题>`（活体，worktree 手工 uvicorn，**须覆写 `LANGFUSE_HOST=http://127.0.0.1:3000`**；`.env` 里是容器名 `langfuse-web:3000`，宿主解析不了）。核对三件事**同时成立**：
1. 子代理的检索写入**子**池（`tool_contexts` 非空）；
2. Langfuse 上 `delegate` 父 span **之下出现了 `delegate:` 前缀的工具 span**（P1 时该处为空）；
3. 最终答案带 `[n]`，且本轮 `citations` 与之一一对应。

若第 1 条不成立（子代理拿到工具却**不自检索**）→ 这是 §0.2 复核列明的**唯一真风险**：D16 的直出路径失效，**停下并升级为完整架构评审**，不要在实施里自行改设计。

- [ ] **Step 4: 11.8 主 agent 委派路径引用不受污染**

主 agent 委派一次（定点或通用），断言主 agent 答案的 `[n]` **仅**来自主 agent 自身检索（子代理检索落子池、不回流）。

- [ ] **Step 5: 11.9 写权限须显式声明**

用替身造一个非只读工具：① 未声明时**不**进入子代理工具面；② 某 skill 在 `allowed-tools` 显式声明后**才**下发。

- [ ] **Step 6: 11.10 通用委派**

主 agent 对 skill 未覆盖的任务发起通用委派（省略 `skill`），子代理自行检索并返回结果；事件与看板条目仍可逐次关联。

- [ ] **Step 7: 11.11 预算**

① 触顶后被拒、返回可读原因、本轮照常收尾；② `/xxx` 直出**不消耗**预算；③ 会话删除后计数归零；④ `delegate skip` / `reason=budget_exhausted` 落日志。

- [ ] **Step 8: 11.12 确认标记**

委派路径的确认标记被**剥前缀但保留问题文本**（P1 已覆盖，本阶段回归即可）。

- [ ] **Step 9: 11.13 委派域 trace**

Langfuse 上委派父 span **之下出现子代理的工具 span**（`delegate:` 前缀）；多工具轮与并发委派均不串台（各挂各的父 span）。

- [ ] **Step 10: 11.14 规格校验**

Run: `openspec validate skill-execution-and-delegation`
Expected: valid。**归档（`openspec-sync-specs`）在本阶段全部验收通过后进行**——`archive` 是整包动作，四份 delta spec 一次性同步进主规格。

- [ ] **Step 11: 收尾**

```bash
git status --short   # 只提交本阶段应有的改动；临时探针/试验代码不得入库
```
按项目常规派**独立 reviewer** 审本阶段实现（架构评审评的是提案，实现另需评审）。

---

## Self-Review（作者自查记录）

**1. Spec 覆盖**：§3 的 3.1–3.7 → Task 1（3.1/3.2/3.3/3.6）+ Task 2（3.4/3.7）；§3b 的 3.8–3.10 → Task 3；§5 的 5.1–5.5 → Task 6，5.6–5.9/5.11 → Task 4，5.10 → Task 5；§11 的 11.7–11.14 → Task 7。**无遗漏**。

**2. 占位符扫描**：无 TBD/TODO、"类似 Task N"、或要求"自己写测试"的步骤。初稿里三处对测试助手名的**推测**已按仓库实况**逐一定正**（这是本次自查抓到的主要缺陷，见下）。

**2b. 自查修正的四处推测错误**（初稿写错、已改）：
1. `Task 5` 初稿的日志调用带 `skills_dir=str(skill_registry.skills_dir)` —— **`SkillRegistry` 没有 `skills_dir` 属性**；既有两个调用点中只有 `reason="skills_dir_missing"` 才带该字段（`agent_service.py:843`），预算触顶与目录无关 → 已删掉该实参。
2. `Task 2` 初稿用 `_executor(tool_provider=...)` —— 该文件**没有** executor 工厂，既有写法是直接 `SkillExecutor(main_llm=MagicMock(), ...)`（`:93`）→ 已改为直接构造。
3. `Task 5` 初稿用 `_make_tool_with_ctx()` —— 该文件**没有**此助手；既有写法是 `_record(...)` + `_FakeRegistry` + `make_delegate_task(...)` + `RequestContext(session_id=...)` 配 `current_request_ctx.set/reset`（`:111-121`）→ 已按实况重写三条测试。
4. `Task 4 Step 6` 初稿写 `ChatManager(...)` —— 既有写法是 `ChatManager(redis_url="redis://localhost:6379/0")`（`tests/chat/test_chat_manager.py:71`）→ 已写实。

**2c. 自查发现的计划外必改项**：`tests/agents/skills/test_skill_loader.py:57-81` 有**两个用例**断言 `Task 2` 要删除的那个 warning（一个 `pytest.warns` 命中、一个断言不命中）→ 已在 Task 2 明写删除。`pyproject.toml` 的那条 `filterwarnings` ignore 正对应同一 warning → 已写明同批删除。

**2d. 执行前预检扫描追加修正的三处**（派单前对全计划做的成对/自洽扫描，详见 P2 台账）：
1. **Task 3 漏列必须同步修正的既有断言** —— `_executor_system_prompt` 签名变化（加 `via`）会影响 **4 条**既有断言：`test_executor_contract.py:10`、`:21`，`test_fork_sub_agent_contract.py:75-76`（**等值断言**，还须计入新增的引用指示）、`:86` 附近。已在 Task 3 的 Files 与步骤里写实。
2. **Task 3 的测试助手名错**：初稿用 `_executor()`，但两个候选文件都**没有**该助手（实况是 `SkillExecutor(main_llm=object())` 直接构造）→ 已改为直接构造，并把追加测试落到 `test_executor_contract.py`（主题一致且已 import `FORK_EXECUTION_CONTRACT`）。
3. **Task 6 会与 Task 5 的闸门位置重叠**：T5 把预算闸门插在 fork 分支内、T6 随后拆分通用分支 —— 若 T6 让通用分支早退绕过闸门，通用委派即可绕过预算 → 已在 Task 6 写明"两条分支必须共用同一处闸门"并在其测试里加断言。

**1b. 执行前预检扫描（P2）**：成对共享文件/接口与逐任务自洽的完整表见 P2 台账 `.superpowers/sdd/2026-09-28-skill-execution-p2/progress.md`。除 2d 的三处外，扫描结论：`delegate_task.py` 被 T3/T5/T6 **三个任务**依次修改（顺序严格，且已识别 T5↔T6 的闸门覆盖风险）；`executor.py` 被 T2/T3/T6 依次修改（`_fork_tools` 的两次演进已按序声明）；`const.py` 被 T1/T3/T5/T6 在**不同区段**追加常量（无冲突）；`test_delegate_task.py:356` 显式传 `skill`，改可选后仍通过，无需修正。

**3. 类型与命名一致性**：`select_fork_tools(allowed, available, executor_tools=None, tool_readonly=None)` 在 Task 1 定义、Task 2 消费，位置参数与关键字名一致；`DELEGATE_VIA_DIRECT/DELEGATE_VIA_DELEGATE` 在 Task 3 定义并使用；`delegate_budget.check_and_incr(session_id, limit)` 在 Task 4 定义、Task 5 消费；`DELEGATE_GENERIC_SKILL_NAME` / `DELEGATE_GENERIC_TITLE` 在 Task 6 定义并使用。跨任务签名变化（`_executor_system_prompt` 加 `via`、`_build_sub_agent` 加 `via`、`execute` 的 `record` 可空）已在其 Interfaces 块显式标注。

**4. 已实测确认的事实**（避免臆造）：
- 真实工具池 10 件：`ask_user, delegate_task, retrieve_kb, search_web, task_create, task_get, task_list, task_output, task_stop, task_update`
- `readonly_map()` 实际只有 4 条且全 True：`retrieve_kb, ask_user, search_web, delegate_task` → **`task_*` 不在表里**（缺项按写类已自动被挡），故 D8 的 `task_*` 排除集的真实作用是"挡住 skill **显式声明**"（这正是 Task 1 的 `test_task_prefix_blocked_even_when_readonly_and_declared`）
- 空 `allowed` + 真实只读表 → 子代理得到 `retrieve_kb` + `search_web`（D7 的交付）
- `pyproject.toml` 有一条 `filterwarnings` ignore 正对应 Task 2 要删的 loader warning → **必须同批删除**（否则留下描述不存在行为的注释）
- `delegate skip` 事件已存在（`log_events.py:165`，字段 `("reason", "skills_dir")`），故 `budget_exhausted` 只需补文档
- 全仓**不存在 `/clear`**，预算复位的唯一实际路径是 `manager.clear_history_async`（`src/chat/manager.py:303`，经 `app_service.py:244` 的会话删除）
