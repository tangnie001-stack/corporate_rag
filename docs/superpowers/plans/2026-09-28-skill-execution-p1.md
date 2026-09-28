# skill-execution-and-delegation P1 实施计划（加载语义 · 预加载 · 委派路径隔离 · 标记剥离 · 委派域 trace）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 skill 的加载语义（缺省 inline、正文超限自动改 fork）、修掉预加载把子代理 prompt 注进主 agent 的错配、让委派路径真正拥有子上下文隔离、剥离确认标记但保留问题文本、并让子代理的工具调用出现在 Langfuse trace 上。

**Architecture:** P1 是**不改子代理能力边界**的那一半（子代理仍零工具）。五项改动互不依赖，可任意顺序执行；其中"委派路径子上下文隔离"（Task 4）与"委派域 trace"（Task 6/7）共用同一个执行器入口，但分别独立可测。P2（工具面继承、通用委派、预算、引用链策略）**不在本计划内**——完成本计划全部验收后由 `tasks.md` §0.2 的前提确认决定是否推进。

**Tech Stack:** Python 3.11+ / pytest / LangGraph（`astream_events` v2）/ Langfuse v2（命令式 span）/ dataclass。

**Spec:** `docs/openspec/changes/skill-execution-and-delegation/`（`proposal.md` / `design.md` 的 D1·D2·D3·D5·D11·D15 / `tasks.md` §1 §2 §4 §6 §7 §11 的 P1 部分 / `specs/{skill-registry,agent-preset,delegate-task,llm-tracing}/spec.md` 里对应 requirement）。执行者应同时读 spec 与本计划。

## Global Constraints

以下为本项目**每一条改动都隐含满足**的要求，逐字来自 `CLAUDE.md` 与 `docs/agents/rules.md`：

- **层间调用规则**：`api/` 不得直接调用 `infra/` 或 `config/`（必须经 `services/`）；`agents/` 不得 import `services/`（`services/` 在 `agents/` 之上，反向即越界）。
- **常量集中**：新增阈值/文案不得散落在业务代码。`settings.py` 放环境变量；`const.py` 放固定阈值与事件/节点常量；`SSEInteractionTexts` 放用户可见文案。与既有 `SkillContext` 同族的枚举常量随其放在 `models.py`。
- **注释标准**：所有函数写 docstring；`dataclass` 每个字段加行内注释（来源/范围/用途）；**写当前状态，不写变更历史**；注释陈述契约，不写推理过程。
- **代码风格**：不用三元表达式；写完整 if/else。类型不确定的值不用 `getattr(x, "attr", default)` 兜底，用显式 `is not None` / `isinstance` 判断。
- **日志**：事件消息英文 k=v + `[层名]` 前缀；**每个新事件必须在 `src/core/log_events.py` 与 `src/core/log_event_specs.py` 两处同名登记**，字段集与 `log_event(...)` 实参严格一致（import 期有硬断言）。完整规范见 `docs/agents/logging-rules.md`。
- **测试命令**：宿主侧必须加前缀 —— `POSTGRES_HOST=localhost pytest <path> -v`。
- **文件规模红线**：单文件 > 400 行须拆分；单函数 > 80 行须拆子函数。
- **本阶段硬约束**：**不得修改 `skills/` 下任何 SKILL.md**（正文、frontmatter、description 一律不动）。这五项改动全部通过代码实现，不靠改内容库。
- **不做**：不实现 P2 的任何条目（不改 `fork_tools.py`、不加 `allowed-tools` 收窄、不动 `DelegateTaskArgs.skill`、不加预算、不改 `FORK_DEFAULT_EXECUTOR_PROMPT` 的 `[n]` 指示）。

---

## File Structure

| 文件 | 本计划中的职责 |
|---|---|
| `src/agents/skills/models.py` | 新增 `ContextSource` 常量类；`SkillRecord` 追加 `context_source` 字段；修正 `context` 字段注释 |
| `src/agents/skills/loader.py` | `_resolve_context` 改缺省 inline + 返回来源；Task 2 增超限自动 fork；`_parse` 传 `context_source` 并记 `skill resolved`；修正模块 docstring |
| `src/config/const.py` | `INLINE_PROMPT_MAX_CHARS` 注释补"同时是承载方式开关"语义 |
| `src/core/log_events.py` / `log_event_specs.py` | 登记新事件 `skill resolved`（两处同名） |
| `src/services/agent_service.py` | `_preload_skills_text` 去掉 `fork_body` 回落 |
| `src/agents/skills/delegate_task.py` | 委派分支自建 `DelegateRun`；停止原因/`delegate_id` 读写改 `run` 侧；返回前剥确认标记前缀 |
| `src/agents/graph/verify/confirm_gate.py` | 新增 `strip_confirm_marker_prefix`（剥前缀、留问题文本） |
| `src/infra/llm/tool_trace.py` | 采集器参数化（`scope` / `parent_span` / `name_prefix`）+ 委派父 span 的开合 API |
| `src/agents/skills/executor.py` | `_run_fork` 开合委派父 span，并把 collector 传给事件消费方 |
| `src/agents/skills/fork_stream.py` | `consume_fork_events` 消费子代理事件时同步喂给 collector |
| `docs/agents/{logging-rules,glossary,requirements_pool}.md` | P1 文档同步 |

**测试文件**（与源码一一对应）：`tests/agents/skills/test_skill_loader.py`、`tests/agents/skills/test_first_batch_skills.py`、`tests/services/test_preset_skill_preload.py`、`tests/agents/skills/test_delegate_task.py`、`tests/agents/graph/test_confirm_gate.py`、`tests/infra/llm/test_tool_trace.py`、`tests/agents/skills/test_fork_stream_trace.py`（新建）。

---

## Task 1: `context` 缺省取 inline + `context_source` 字段 + `skill resolved` 事件

**Files:**
- Modify: `src/agents/skills/models.py`（`SkillContext` 之后新增 `ContextSource`；`SkillRecord` docstring 第 30 行那句 + 字段末尾追加）
- Modify: `src/agents/skills/loader.py:8`（模块 docstring）、`:89`（`_parse` 调用点）、`:152-166`（`_resolve_context`）
- Modify: `src/config/const.py:94-96`（`INLINE_PROMPT_MAX_CHARS` 注释）
- Modify: `src/core/log_events.py`（`Event` 新增 `SKILL_RESOLVED`）
- Modify: `src/core/log_event_specs.py`（`EVENT_SPECS` 新增 `"skill resolved"`）
- Test: `tests/agents/skills/test_skill_loader.py`

**Interfaces:**
- Consumes: `SkillContext`（`src/agents/skills/models.py`）
- Produces:
  - `class ContextSource`（`models.py`）：`DEFAULT = "default"` / `EXPLICIT = "explicit"` / `AUTO_OVERSIZE = "auto_oversize"`
  - `SkillRecord.context_source: str = ContextSource.DEFAULT`（**追加在字段列表末尾**，带默认值——设为必填或插中段会引爆 `loader.py` 与测试里约 16 处 `SkillRecord(...)` 构造）
  - `SkillLoader._resolve_context(self, meta: dict) -> tuple[str, str]` 返回 `(context, context_source)`。**Task 2 会把签名改成 `(self, meta, body)`**，本任务先只做缺省值与来源。
  - `Event.SKILL_RESOLVED = "skill resolved"`

- [ ] **Step 1: 写失败测试**

在 `tests/agents/skills/test_skill_loader.py` 中：把既有的 `test_missing_context_defaults_to_fork`（约 `:131`）**整个替换**为下面两个测试，并追加第三个。注意 import 行要加 `ContextSource`。

```python
def test_missing_context_defaults_to_inline(tmp_path):
    """未声明 context → 取 inline（与上游一致：不写即 inline）。"""
    _write_skill(tmp_path, "finance-qa", "description: 财务问答\n", "先检索再作答。")
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.context == SkillContext.INLINE
    assert rec.inline_prompt is not None
    assert rec.fork_body is None
    assert rec.context_source == ContextSource.DEFAULT


def test_explicit_fork_records_source(tmp_path):
    """显式声明 context 时来源记为 explicit。"""
    _write_skill(tmp_path, "analyst", "description: 财务专家\ncontext: fork\n", "正文")
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.FORK
    assert rec.context_source == ContextSource.EXPLICIT


def test_skill_resolved_event_logged(tmp_path, monkeypatch):
    """加载每个 skill 时记 skill resolved，字段含 context 与来源，供无 E2E 时判定承载方式。"""
    logged: list[dict] = []
    monkeypatch.setattr(
        "src.agents.skills.loader.core_logging.log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    _write_skill(tmp_path, "finance-qa", "description: 财务问答\n", "先检索再作答。")
    SkillLoader(tmp_path).load_all()
    assert len(logged) == 1
    item = logged[0]
    assert item["event"] == Event.SKILL_RESOLVED
    assert item["skill"] == "finance-qa"
    assert item["context"] == SkillContext.INLINE
    assert item["context_source"] == ContextSource.DEFAULT
    assert item["body_chars"] > 0  # 正文含换行，别断言精确长度
```

同时在文件头部 import 区补：

```python
from src.agents.skills.models import ContextSource, SkillContext
from src.core.log_events import Event
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_skill_loader.py -v -k "inline or source or resolved"`
Expected: FAIL —— `ImportError: cannot import name 'ContextSource'`（字段与常量都还不存在）。

- [ ] **Step 3: 加 `ContextSource` 与 `SkillRecord.context_source`**

在 `src/agents/skills/models.py` 的 `SkillContext` 类**之后**插入：

```python
class ContextSource:
    """`context` 取值的来源（写入 SkillRecord.context_source）。

    与 SkillContext 同类且同处：两者都属于 skill 的"执行形态"语义。
    """

    DEFAULT: str = "default"  # frontmatter 未声明，按缺省取 inline
    EXPLICIT: str = "explicit"  # frontmatter 显式声明
    AUTO_OVERSIZE: str = "auto_oversize"  # 未声明但正文超预算，加载期自动改用 fork
```

在 `SkillRecord` 的 docstring 里把 `context` 那行改为（并追加 `context_source` 一行）：

```
        context: 执行形态（inline|fork；未声明取 inline，正文超预算时自动改 fork，见 context_source）
        ...
        context_source: context 取值的来源（default|explicit|auto_oversize）
```

并在字段列表**末尾**（`source_path` 之后）追加：

```python
    context_source: str = ContextSource.DEFAULT  # context 取值来源（default|explicit|auto_oversize）
```

- [ ] **Step 4: 改 `_resolve_context` 与 `_parse`，并记新事件**

`src/agents/skills/loader.py`：

1) 模块 docstring 第 8 行（现为 `- context 未声明取 fork；非法值抛错 → 该 skill 跳过加载（同非法 name）`）改为：

```
- context 未声明取 inline（与上游一致）；正文超 INLINE_PROMPT_MAX_CHARS 时自动改用 fork
- 非法值抛错 → 该 skill 跳过加载（同非法 name）
```

2) 文件头补 import：

```python
from src.agents.skills.models import ContextSource, SkillContext, SkillRecord
from src.config.const import CAPABILITY_NAME_PATTERN, DEPRECATED_SKILL_FIELDS
from src.core import logging as core_logging
from src.core.log_events import Event
```

3) `_resolve_context`（`:152-166`）整体替换为：

```python
    def _resolve_context(self, meta: dict) -> tuple[str, str]:
        """解析 context 与其来源；非法值抛 ValueError 由 load_all 跳过该 skill。

        未声明取 inline（与上游一致：不写即 inline）。正文超预算的情形由超限自动
        判定处理（该判定需要正文长度，届时本方法增 body 形参）。

        非法值不回落任一模式，而是抛错让 load_all 跳过该 skill（与非法 name 同款）：
        降级到 inline 是静默失效，降级到 fork 会让作者以为声明生效；拼错应立即暴露。

        Returns:
            (context, context_source)：context 取 inline|fork；来源见 ContextSource
        """
        declared = meta.get("context")
        if declared is None:
            return SkillContext.INLINE, ContextSource.DEFAULT
        if declared not in (SkillContext.INLINE, SkillContext.FORK):
            raise ValueError(f"context 非法值 {declared!r}（仅允许 inline / fork）")
        return declared, ContextSource.EXPLICIT
```

4) `_parse` 中把 `context = self._resolve_context(meta)`（`:89`）改为 `context, context_source = self._resolve_context(meta)`；在 `return SkillRecord(...)` 之前插入：

```python
        core_logging.log_event(
            Event.SKILL_RESOLVED,
            skill=name,
            context=context,
            context_source=context_source,
            body_chars=len(body),
        )
```

并在 `SkillRecord(...)` 的实参里追加 `context_source=context_source,`。

5) `src/config/const.py:94-96` 的注释改为：

```python
INLINE_PROMPT_MAX_CHARS = (
    500  # inline skill 正文规模上限（字符，防上下文累积膨胀）；未声明 context 时超出即自动改用 fork，显式 inline 时仅记 warning
)
```

6) `src/core/log_events.py`：在 `SKILL_PRELOAD_SKIP = "skill preload skip"`（`:87`）附近加

```python
    SKILL_RESOLVED = "skill resolved"
```

7) `src/core/log_event_specs.py`：在 `"skill preload skip"` 那组（`:152`）附近加

```python
    "skill resolved": EventSpec(
        "skill resolved",
        "session",
        "info",
        ("skill", "context", "context_source", "body_chars"),
    ),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_skill_loader.py tests/core/test_log_events.py -v`
Expected: PASS（含既有的 `test_invalid_context_skips_skill` —— 非法值口径未变）。

- [ ] **Step 6: 跑 skill 相关全套，确认没有其他测试因缺省值翻转而挂**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ tests/services/test_preset_skill_preload.py -v`
Expected: 若有测试依赖"未声明取 fork"，此处会暴露——**记录下来但不要在本任务修**（Task 2 会引入超限自动 fork，届时按预期行为统一处置）。

- [ ] **Step 7: Commit**

```bash
git add src/agents/skills/models.py src/agents/skills/loader.py src/config/const.py src/core/log_events.py src/core/log_event_specs.py tests/agents/skills/test_skill_loader.py
git commit -m "feat(skill): context 缺省取 inline，新增 context_source 与 skill resolved 事件"
```

---

## Task 2: 正文超 `INLINE_PROMPT_MAX_CHARS` 时自动改用 fork

**Files:**
- Modify: `src/agents/skills/loader.py`（`_resolve_context` 签名加 `body`、加超限分支；`_parse` 传 `body`）
- Test: `tests/agents/skills/test_skill_loader.py`、`tests/agents/skills/test_first_batch_skills.py`

**Interfaces:**
- Consumes: Task 1 的 `ContextSource`、`SkillRecord.context_source`
- Produces: `SkillLoader._resolve_context(self, meta: dict, body: str) -> tuple[str, str]`（**签名变化**：新增 `body` 形参）

- [ ] **Step 1: 写失败测试**

在 `tests/agents/skills/test_skill_loader.py` 追加（`_write_skill` 已存在）：

```python
def test_oversize_body_auto_forks_with_warning(tmp_path):
    """未声明 context 且正文超预算 → 自动按 fork，来源记为 auto_oversize。"""
    long_body = "字" * (INLINE_PROMPT_MAX_CHARS + 1)
    _write_skill(tmp_path, "long-skill", "description: 长文方法论\n", long_body)
    with pytest.warns(UserWarning, match="已自动按 fork"):
        rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.FORK
    assert rec.context_source == ContextSource.AUTO_OVERSIZE
    assert rec.fork_body == long_body
    assert rec.inline_prompt is None


def test_short_body_without_context_stays_inline(tmp_path):
    """未声明 context 且正文未超预算 → 保持 inline（边界内侧）。"""
    _write_skill(
        tmp_path, "short-skill", "description: 短方法论\n",
        "字" * INLINE_PROMPT_MAX_CHARS,
    )
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.INLINE
    assert rec.context_source == ContextSource.DEFAULT


def test_explicit_inline_oversize_keeps_inline_with_warning(tmp_path):
    """显式声明优先：显式 inline 且超预算 → 仍按 inline，只记 warning。"""
    long_body = "字" * (INLINE_PROMPT_MAX_CHARS + 1)
    _write_skill(
        tmp_path, "force-inline", "description: 强制 inline\ncontext: inline\n", long_body
    )
    with pytest.warns(UserWarning, match="仍按 inline 处理"):
        rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.INLINE
    assert rec.context_source == ContextSource.EXPLICIT


def test_explicit_fork_short_body_stays_fork(tmp_path):
    """显式 fork 与正文长度无关。"""
    _write_skill(tmp_path, "short-fork", "description: 短 fork\ncontext: fork\n", "正文")
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.FORK
    assert rec.context_source == ContextSource.EXPLICIT
```

文件头补 import：`from src.config.const import INLINE_PROMPT_MAX_CHARS`。

在 `tests/agents/skills/test_first_batch_skills.py` 追加（该文件已有 `SKILLS_DIR`）：

```python
def test_in_repo_long_skills_resolve_as_auto_oversize():
    """在库三份长文 skill 未声明 context → 全部落 auto_oversize（按 fork 承载）。

    这是 P1 的核心验收点：默认值翻转后，长文 skill 仍不占主 agent 历史预算。
    """
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    for name in ("financial-statement-analyzer", "competitive-landscape", "market-sizing-analysis"):
        rec = records[name]
        assert rec.context == SkillContext.FORK, name
        assert rec.context_source == ContextSource.AUTO_OVERSIZE, name
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_skill_loader.py -v -k "oversize or short_body or auto_oversize"`
Expected: FAIL —— 超限的会被判成 inline、`context_source` 是 `default`。

- [ ] **Step 3: 实现超限自动判定**

`src/agents/skills/loader.py` 的 `_resolve_context` 整体替换为：

```python
    def _resolve_context(self, meta: dict, body: str) -> tuple[str, str]:
        """解析 context 与其来源；非法值抛 ValueError 由 load_all 跳过该 skill。

        未声明取 inline（与上游一致：不写即 inline）；但正文超 INLINE_PROMPT_MAX_CHARS
        时自动改用 fork —— 正文注入后会留在 messages 历史并挤占历史预算，fork 的隔离
        让长文不占主对话。该自动判定使外部来源的 skill 无需人工补 frontmatter。

        显式声明永远优先：显式 inline 且超预算只记 warning（超限守卫测试承担失败），
        显式 fork 与正文长度无关。

        Returns:
            (context, context_source)
        """
        declared = meta.get("context")
        if declared is None:
            if len(body) > INLINE_PROMPT_MAX_CHARS:
                warnings.warn(
                    f"skill 正文 {len(body)} 字符超出 inline 预算"
                    f"（INLINE_PROMPT_MAX_CHARS={INLINE_PROMPT_MAX_CHARS}），已自动按 fork 处理；"
                    "如确需 inline 请显式声明 context: inline 并精简正文"
                )
                return SkillContext.FORK, ContextSource.AUTO_OVERSIZE
            return SkillContext.INLINE, ContextSource.DEFAULT
        if declared not in (SkillContext.INLINE, SkillContext.FORK):
            raise ValueError(f"context 非法值 {declared!r}（仅允许 inline / fork）")
        if declared == SkillContext.INLINE and len(body) > INLINE_PROMPT_MAX_CHARS:
            warnings.warn(
                f"skill 显式声明 context: inline 但正文 {len(body)} 字符超出预算"
                f"（{INLINE_PROMPT_MAX_CHARS}），仍按 inline 处理"
            )
        return declared, ContextSource.EXPLICIT
```

`_parse` 中的调用点改为 `context, context_source = self._resolve_context(meta, body)`。

import 区补 `INLINE_PROMPT_MAX_CHARS`：

```python
from src.config.const import (
    CAPABILITY_NAME_PATTERN,
    DEPRECATED_SKILL_FIELDS,
    INLINE_PROMPT_MAX_CHARS,
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_skill_loader.py tests/agents/skills/test_first_batch_skills.py -v`
Expected: PASS。

- [ ] **Step 5: 跑 skill 全目录 + 预加载，登记受影响用例**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/ tests/services/ -v`
Expected: PASS。若 `tests/services/test_preset_skill_preload.py::test_preload_renders_in_declared_order` 之类出现失败，那是 Task 3 的预期改动，**记录并在 Task 3 修**。

- [ ] **Step 6: Commit**

```bash
git add src/agents/skills/loader.py tests/agents/skills/test_skill_loader.py tests/agents/skills/test_first_batch_skills.py
git commit -m "feat(skill): 正文超 inline 预算时自动按 fork 承载（显式声明优先）"
```

---

## Task 3: 预加载只注入 inline 正文

**Files:**
- Modify: `src/services/agent_service.py:876-905`（`_preload_skills_text`）
- Test: `tests/services/test_preset_skill_preload.py`

**Interfaces:**
- Consumes: Task 1/2 之后的 `SkillRecord`（`inline_prompt` / `fork_body` 语义不变）
- Produces: `AgentService._preload_skills_text(self, skill_names: list[str]) -> tuple[str, list[str]]` 签名**不变**；行为变化 = fork skill 被跳过并记 `skill preload skip`（`reason="not_inline"`）

- [ ] **Step 1: 写失败测试**

`tests/services/test_preset_skill_preload.py`：既有 `test_preload_renders_in_declared_order` 断言 `b`（fork）也被拼进正文——**该断言按新语义是错的**，改为两份都是 inline，并新增两个测试：

```python
def test_preload_renders_in_declared_order():
    """按 skills 声明顺序拼接（两份都是 inline 正文）。"""
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A：$ARGUMENTS"),
            "b": _Record("b", inline="方法论 B"),
        }
    )
    text, names = svc._preload_skills_text(["a", "b"])
    assert text.index("方法论 A") < text.index("方法论 B")
    assert names == ["a", "b"]


def test_preload_skips_fork_skill(monkeypatch):
    """fork skill 的正文是子代理 prompt，不得注入主 agent；记 warning 并跳过。"""
    logged: list[dict] = []
    monkeypatch.setattr(
        "src.services.agent_service.core_logging.log_event",
        lambda event, **fields: logged.append({"event": event, **fields}),
    )
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A"),
            "f": _Record("f", fork="子代理用的任务 prompt"),
        }
    )
    text, names = svc._preload_skills_text(["a", "f"])
    assert text == "方法论 A"
    assert names == ["a"]
    assert "子代理用的任务 prompt" not in text
    assert any(
        item.get("event") == Event.SKILL_PRELOAD_SKIP and item.get("reason") == "not_inline"
        for item in logged
    )


def test_preload_all_fork_returns_empty():
    """全部命中 fork skill → 什么都不注入。"""
    svc = _service({"f": _Record("f", fork="子代理 prompt")})
    text, names = svc._preload_skills_text(["f"])
    assert text == ""
    assert names == []
```

文件头 import 区补 `from src.core.log_events import Event`（`SKILL_INJECTION_PREFIX` 的既有 import 保留）。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_preset_skill_preload.py -v`
Expected: FAIL —— `test_preload_skips_fork_skill` 拿到 "方法论 A\n\n子代理用的任务 prompt"。

- [ ] **Step 3: 去掉 `fork_body` 回落**

`src/services/agent_service.py` 的 `_preload_skills_text`（`:876-905`）里，把

```python
            body = record.inline_prompt
            if not body:
                body = record.fork_body
            if not body:
                continue
```

替换为：

```python
            body = record.inline_prompt
            if not body:
                # fork skill 的正文是写给子代理的任务 prompt，注入主 agent 属语义错配
                # 且与 fork 的"正文不进主 agent 上下文"冲突；需要用 fork skill 时走委派
                core_logging.log_event(
                    Event.SKILL_PRELOAD_SKIP, skill=name, reason="not_inline"
                )
                continue
```

并把该方法 docstring 的 Returns 段补一句：

```
            只注入 inline 正文；fork skill 被跳过（记 skill preload skip）——
            fork 正文属于子代理 prompt，注入主 agent 是语义错配。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_preset_skill_preload.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_service.py tests/services/test_preset_skill_preload.py
git commit -m "fix(skill): 预加载只注入 inline 正文，fork 正文不再进主 agent"
```

---

## Task 4: 委派路径自建 `DelegateRun`，停止原因与 `delegate_id` 读写源一并搬走

**Files:**
- Modify: `src/agents/skills/delegate_task.py:92-193`
- Test: `tests/agents/skills/test_delegate_task.py`

**Interfaces:**
- Consumes: `DelegateRun(delegate_id: str, skill_name: str, ctx: RequestContext)`（`src/agents/skills/delegate_run.py`）；`RequestContext.child()`（无参，返回独立子上下文，**不复制 `tool_contexts`**）；`SkillExecutor.execute(record, task, run=None)`
- Produces: 委派路径（定点命中 fork skill）此后向执行器传 `run`；`delegate end` 的 `ok` / `reason` 取自 `run.stop_reason`

**背景（照做即可）**：现状三处调用都是 `executor.execute(record, task)`（`:90` inline、`:94` 无 ctx、`:135` fork），执行器因此走"用主 ctx、不隔离"分支（`executor.py:177-179`）。子代理当前零工具故不可见问题；P2 一旦给它工具，检索就会写进主引用池。**同时**：改写完成后执行器把中断原因写到 `run.ctx.fork_stop_reason` 与 `run.stop_reason`（`executor.py:223`），而 `:146` 仍读主 ctx → 恒 `None` → 中断被误记 `normal`。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_delegate_task.py` 追加（该文件已有 `_record` 与 `_FakeRegistry`）：

```python
class _StubExecutor:
    """替身执行器：记录收到的 run，并模拟子代理向自己的上下文写检索结果。"""

    def __init__(self, stop_reason: str | None = None):
        self.seen_run = None
        self.stop_reason = stop_reason

    async def execute(self, record, task, run=None):
        self.seen_run = run
        if run is not None:
            run.ctx.tool_contexts.append(_Ctx("子代理材料"))
            run.stop_reason = self.stop_reason
        return "子代理结论"


class _Ctx:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
async def test_delegate_passes_run_and_isolates_pool():
    """委派路径必须自建 DelegateRun：子代理写入落子池，主池保持为空。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    executor = _StubExecutor()
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    try:
        out = await tool.ainvoke({"task": "任务", "skill": "analyst"})
    finally:
        current_request_ctx.reset(token)
    assert out == "子代理结论"
    assert executor.seen_run is not None
    assert executor.seen_run.ctx is not parent  # 独立子上下文
    assert executor.seen_run.ctx.tool_contexts  # 写入落在子池
    assert parent.tool_contexts == []  # 主池未被污染


@pytest.mark.asyncio
async def test_delegate_end_reason_from_run_not_main_ctx():
    """中断原因必须从 run 读：executor 写在 run 上，读主 ctx 会恒 None 而误记 normal。"""
    parent = RequestContext(session_id="s1", kb_id="k1", kb_bound=True)
    token = current_request_ctx.set(parent)
    executor = _StubExecutor(stop_reason=DelegateStopReason.TURN)
    tool = make_delegate_task(
        _FakeRegistry({"analyst": _record("analyst", SkillContext.FORK, "正文")}),
        executor,
    )
    try:
        await tool.ainvoke({"task": "任务", "skill": "analyst"})
    finally:
        current_request_ctx.reset(token)
    events = []
    while not parent.clarify_channel.empty():
        events.append(parent.clarify_channel.get_nowait())
    end = [e for e in events if e.get("action") == "end"]
    assert len(end) == 1
    assert end[0]["ok"] is False
    assert end[0]["reason"] == DelegateStopReason.TURN.value
```

文件头 import 区补：

```python
from src.agents.skills.delegate_run import DelegateRun
from src.config.const import DelegateStopReason
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py -v -k "passes_run or from_run"`
Expected: 第一个测试 FAIL（`seen_run is None`）；第二个 FAIL（`ok` 为 `True`、`reason` 为空串）。

- [ ] **Step 3: 改 `delegate_task` 的 fork 分支**

`src/agents/skills/delegate_task.py`，把 `:92-99` 这段

```python
        ctx = current_request_ctx.get()
        if ctx is None:
            return await executor.execute(record, task)
        # fork 可观测（design D6/D8）：分配 delegate_id 贯穿 start/增量/end；
        # ctx.fork_stop_reason 由 executor 中断时写，finally 读取并复位
        delegate_id = uuid.uuid4().hex[:8]
        ctx.delegate_id = delegate_id
        ctx.fork_stop_reason = None
```

替换为：

```python
        ctx = current_request_ctx.get()
        if ctx is None:
            # 无请求上下文时没有父上下文可派生，按既有 fail-open 直接用主上下文执行
            return await executor.execute(record, task)
        # 每次委派独占运行态（design D15）：子代理的检索与引用编号落子池，不污染主池；
        # 停止原因由 executor 写 run，终态判定从这里读（不再走主 ctx 的单值字段）
        delegate_id = uuid.uuid4().hex[:8]
        run = DelegateRun(
            delegate_id=delegate_id,
            skill_name=record.name,
            ctx=ctx.child(),
        )
```

把 `:135` 的 `out = await executor.execute(record, task)` 改为 `out = await executor.execute(record, task, run)`。

把 `:146` 的 `stop_reason = ctx.fork_stop_reason` 改为：

```python
            # 正常返回但 executor 曾中断（idle/total/turn）→ 原因已写在 run 上
            stop_reason = run.stop_reason
```

把 `:192-193` 的 finally 尾部两行

```python
            ctx.delegate_id = ""
            ctx.fork_stop_reason = None
```

**整段删除**（`DelegateRun` 每次新建、不再复用，无需复位主 ctx 字段）。

`:126` 的 `thinking="true" if ctx.deep_thinking else "false"` 与 `:102-103` 的 `ctx.session_id` / `:110`、`:155` 的 `ctx.clarify_channel` **保持不变**——它们是会话/请求级共享，不属于委派运行态。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py tests/agents/skills/test_fork_context_isolation.py -v`
Expected: PASS。

- [ ] **Step 5: 跑委派与直出相关全套（回归面最大的一步）**

Run: `POSTGRES_HOST=localhost pytest tests/agents/ tests/services/ -v`
Expected: PASS。重点看 `test_direct_skill_round.py`（直出路径本来就用 `run`）、`test_direct_round_delivery.py`、`test_agent_service.py`。

- [ ] **Step 6: Commit**

```bash
git add src/agents/skills/delegate_task.py tests/agents/skills/test_delegate_task.py
git commit -m "fix(delegate): 委派路径自建 DelegateRun，停止原因改从 run 读"
```

---

## Task 5: 确认标记"剥前缀、保留问题文本"

**Files:**
- Modify: `src/agents/graph/verify/confirm_gate.py`（新增 `strip_confirm_marker_prefix`）
- Modify: `src/agents/skills/delegate_task.py`（返回前调用）
- Test: `tests/agents/graph/test_confirm_gate.py`、`tests/agents/skills/test_delegate_task.py`

**Interfaces:**
- Consumes: `FORK_CONFIRM_MARKER`（`src/config/const.py:74`，值 `"CONFIRM_REQUIRED:"`）；既有 `strip_confirm_marker`（**整行删除**，直出路径继续用它）
- Produces: `strip_confirm_marker_prefix(answer: str) -> str`

**为什么不能复用现成的**：`strip_confirm_marker`（`confirm_gate.py:37-44`）按行过滤、**整行删掉**，而问题文本就在那一行上。直出路径可以整行删（问题已被确认门取走问过用户）；委派路径没有确认门，删掉就等于把"子代理在等什么确认"静默吞掉，主 agent 拿到一个无提示的不完整答案。

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_confirm_gate.py` 追加：

```python
def test_strip_confirm_marker_prefix_keeps_question():
    """剥协议前缀但保留问题文本（委派路径用；整行删除会吞掉"在等什么确认"）。"""
    text = "先给结论。\nCONFIRM_REQUIRED: 请提供公司代码\n其余内容照旧。"
    out = strip_confirm_marker_prefix(text)
    assert "CONFIRM_REQUIRED" not in out
    assert "请提供公司代码" in out
    assert "先给结论。" in out
    assert "其余内容照旧。" in out


def test_strip_confirm_marker_prefix_noop_without_marker():
    """无标记时原样返回（只去首尾空白）。"""
    assert strip_confirm_marker_prefix("正常结论") == "正常结论"


def test_strip_confirm_marker_still_drops_whole_line():
    """既有的整行删除行为不变（直出路径依赖它）。"""
    text = "结论。\nCONFIRM_REQUIRED: 请提供公司代码\n尾注。"
    out = strip_confirm_marker(text)
    assert "请提供公司代码" not in out
    assert "结论。" in out
    assert "尾注。" in out
```

文件头 import 区补 `strip_confirm_marker_prefix`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/graph/test_confirm_gate.py -v`
Expected: FAIL —— `ImportError: cannot import name 'strip_confirm_marker_prefix'`。

- [ ] **Step 3: 实现新函数**

`src/agents/graph/verify/confirm_gate.py`，在 `strip_confirm_marker` 之后插入：

```python
def strip_confirm_marker_prefix(answer: str) -> str:
    """移除"需确认"标记的协议前缀，但保留该行上的问题文本。

    与 strip_confirm_marker 的分工：后者整行删除，适用于直出路径（问题已被确认门
    取走并经 ask_user 问过用户，正文里再留一行属内部协议串泄漏）；委派路径没有
    确认门，需要把"子代理在等什么确认"交给主 agent 决定是否提问，故只剥前缀。

    Args:
        answer: 子代理聚合文本

    Returns:
        剥掉 marker 前缀后的文本（无标记时原样返回）
    """
    kept: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(FORK_CONFIRM_MARKER):
            kept.append(stripped[len(FORK_CONFIRM_MARKER) :].strip())
        else:
            kept.append(line)
    return "\n".join(kept).strip()
```

- [ ] **Step 4: 在委派路径调用它**

`src/agents/skills/delegate_task.py`：import 区补 `from src.agents.graph.verify.confirm_gate import strip_confirm_marker_prefix`（放在既有 skills 内 import 之后，避免与 `src.agents.graph` 形成循环——`confirm_gate` 只依赖 `ask_tools` 与 `const`，无反向依赖）。

把 Task 4 改过的这段

```python
                out = await executor.execute(record, task, run)
                result_len = len(out)
```

改为：

```python
                out = await executor.execute(record, task, run)
                # 内部协议串不外泄；但保留问题文本——主 agent 据此自行决定是否向用户提问
                out = strip_confirm_marker_prefix(out)
                result_len = len(out)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/graph/test_confirm_gate.py tests/agents/skills/test_delegate_task.py tests/agents/graph/test_direct_skill_round.py -v`
Expected: PASS（直出路径的整行删除行为未变）。

- [ ] **Step 6: Commit**

```bash
git add src/agents/graph/verify/confirm_gate.py src/agents/skills/delegate_task.py tests/agents/graph/test_confirm_gate.py
git commit -m "fix(delegate): 委派路径剥确认标记前缀但保留问题文本"
```

---

## Task 6: `ToolTraceCollector` 参数化（`scope` / 父 span / 名前缀）

**Files:**
- Modify: `src/infra/llm/tool_trace.py`
- Test: `tests/infra/llm/test_tool_trace.py`

**Interfaces:**
- Consumes: `_FakeClient` / `_item(...)`（既有测试替身）
- Produces:
  - `ToolTraceCollector(enabled: bool, trace_id: str, client: Any = None, scope: str = "main", parent_span: Any = None, name_prefix: str = "")`
  - `ToolTraceCollector.open_delegate_span(delegate_id: str, skill: str) -> Any | None`：开委派父 span（名字 `delegate`，metadata 带 `delegate_id` / `skill`），返回 span；失败记 warning 并返回 `None`
  - `ToolTraceCollector.close()`：除既有收尾外，**同时关闭委派父 span**

**设计要点**：`_round` 是单值状态，故**主域每请求一个实例、委派域每次委派一个实例**（不可按 `scope` 共用一个实例——`scope=delegate` 是所有委派共享的取值）。入口过滤 `metadata["langgraph_node"] == "tools"` **无需放宽**（已实测：`create_agent` 产物同样有 `tools` 节点，且自定义 config 的 metadata 是合并而非覆盖）。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/test_tool_trace.py` 追加：

```python
def test_default_scope_is_main_unchanged():
    """缺省参数下行为与既有完全一致（回归）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)
    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1"))
    collector.consume(_item("on_tool_end", run_id="r1", output="ok"))
    collector.close()
    assert [s.name for s in client.spans] == ["tools", "retrieve_kb"]
    assert client.spans[0].parent_observation_id is None


def test_delegate_scope_prefixes_names_and_nests_under_parent():
    """委派域：名字带前缀，且挂在该次委派的父 span 之下。"""
    client = _FakeClient()
    collector = ToolTraceCollector(
        enabled=True, trace_id="t1", client=client, scope="delegate", name_prefix="delegate:"
    )
    parent = collector.open_delegate_span(delegate_id="d1", skill="analyst")
    assert parent is not None
    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1"))
    collector.consume(_item("on_tool_end", run_id="r1", output="ok"))
    collector.close()
    names = [s.name for s in client.spans]
    assert names == ["delegate", "delegate:tools", "delegate:retrieve_kb"]
    assert client.spans[1].parent_observation_id == parent.id  # round 挂在父下
    assert client.spans[2].parent_observation_id == client.spans[1].id  # 工具挂在 round 下


def test_delegate_span_failure_does_not_break_conversation():
    """观测失败不影响对话：父 span 建不出来时返回 None，工具 span 仍可产出。"""

    class _BrokenClient(_FakeClient):
        def span(self, **kwargs):
            if kwargs.get("name") == "delegate":
                raise RuntimeError("langfuse down")
            return super().span(**kwargs)

    collector = ToolTraceCollector(
        enabled=True, trace_id="t1", client=_BrokenClient(), scope="delegate"
    )
    assert collector.open_delegate_span(delegate_id="d1", skill="s") is None
    collector.close()  # 不得抛
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/test_tool_trace.py -v -k "delegate or default_scope"`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'scope'`。

- [ ] **Step 3: 参数化采集器**

`src/infra/llm/tool_trace.py`：

1) `__init__` 签名与字段（保留既有三个参数在前，新增三个在后）：

```python
    def __init__(
        self,
        enabled: bool,
        trace_id: str,
        client: Any = None,
        scope: str = "main",
        parent_span: Any = None,
        name_prefix: str = "",
    ) -> None:
        """初始化采集器。

        Args:
            enabled: 是否产出（取自 settings.LANGFUSE_ENABLE）
            trace_id: 本轮 trace id
            client: Langfuse 客户端；None 时惰性取单例
            scope: 事件归属域（main=主图；delegate=fork 子代理），仅用于委派父 span 的定位
            parent_span: 委派父 span（scope=delegate 时由 open_delegate_span 产出并回填）
            name_prefix: span 名前缀（委派域传 "delegate:"，便于在 trace 上区分归属）
        """
        self._enabled = enabled and bool(trace_id)
        self._trace_id = trace_id
        self._client = client
        self._scope = scope
        self._parent_span = parent_span
        self._name_prefix = name_prefix
        self._open: dict[str, Any] = {}  # run_id -> 尚未结束的工具 span
        self._round: Any = None  # 当前 tools 父 span
        self._delegate_span: Any = None  # 委派父 span（仅 scope=delegate）
```

2) 新增 `open_delegate_span`（放在"对外"区、`consume` 之前）：

```python
    def open_delegate_span(self, delegate_id: str, skill: str) -> Any | None:
        """开委派父 span（标注 delegate_id / skill），供该次委派的工具 span 挂靠。

        Args:
            delegate_id: 本次委派短 id
            skill: 被调用的 skill 名（通用委派传占位名）

        Returns:
            委派父 span；未启用或建 span 失败时返回 None（观测失败不得影响对话）
        """
        if not self._enabled:
            return None
        try:
            self._delegate_span = self._get_client().span(
                trace_id=self._trace_id,
                name=self._name_prefix + _DELEGATE_SPAN,
                start_time=_now(),
                metadata={"delegate_id": delegate_id, "skill": skill},
            )
        except Exception:
            logger.warning("[tool_trace] open delegate span failed", exc_info=True)
            return None
        self._parent_span = self._delegate_span
        return self._delegate_span
```

3) 文件顶部常量区加：

```python
_DELEGATE_SPAN = "delegate"  # 委派父 span 名（与 scope=delegate 对应）
```

4) `_open_round` 改为带前缀与父 span：

```python
    def _open_round(self) -> None:
        """开该轮的 tools 父 span（同一轮只开一次）。"""
        if self._round is not None:
            return
        parent_id = None
        if self._parent_span is not None:
            parent_id = self._parent_span.id
        try:
            self._round = self._get_client().span(
                trace_id=self._trace_id,
                parent_observation_id=parent_id,
                name=self._name_prefix + _TOOLS_NODE,
                start_time=_now(),
            )
        except Exception:  # 观测失败不得影响对话
            logger.warning("[tool_trace] open round span failed", exc_info=True)
```

5) `_on_tool_start` 里的 `name=str(item.get("name", ""))` 改为 `name=self._name_prefix + str(item.get("name", ""))`。

6) `close()` 在 `_close_round()` 之后追加委派父 span 的收尾：

```python
    def close(self) -> None:
        """收尾兜底：关闭所有未结束的 span（取消 / 异常路径必经）。"""
        for span in list(self._open.values()):
            self._end_span(span)
        self._open.clear()
        self._close_round()
        if self._delegate_span is not None:
            span, self._delegate_span = self._delegate_span, None
            self._parent_span = None
            self._end_span(span)
```

7) 模块 docstring 补一句委派域的说明：

```
委派域（scope=delegate）：每次委派一个实例，先 open_delegate_span 再 consume 子代理
事件流；span 名带 "delegate:" 前缀，父 span 标注 delegate_id / skill。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/infra/llm/test_tool_trace.py tests/services/test_run_generation_tracing.py -v`
Expected: PASS（既有主域用例是回归闸门）。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/tool_trace.py tests/infra/llm/test_tool_trace.py
git commit -m "feat(trace): ToolTraceCollector 参数化 scope/父 span/名前缀，支持委派域"
```

---

## Task 7: 委派父 span 开合 + 子代理事件流喂给采集器

**Files:**
- Modify: `src/agents/skills/executor.py`（`_run_fork`）
- Modify: `src/agents/skills/fork_stream.py`（`consume_fork_events` 及 `_handle_fork_event`）
- Test: `tests/agents/skills/test_fork_stream_trace.py`（新建）

**Interfaces:**
- Consumes: Task 6 的 `ToolTraceCollector(..., scope="delegate", name_prefix="delegate:")` + `open_delegate_span(delegate_id, skill)` + `close()`；`settings.LANGFUSE_ENABLE`；`src/infra/llm/trace_context.current_trace_id`
- Produces:
  - `consume_fork_events(sub_agent, run, user_content, skill_name, max_turns, trace_collector=None)`
  - `SkillExecutor._run_fork` 在 `run` 非 None 时开合委派父 span

**硬约束**：**不得改动** `executor.py:189` 的 `var_child_runnable_config.set(None)`——它隔离的是 SSE 那一路（防 token 污染与子代理原文累积）。trace 靠"显式喂事件"实现，不靠恢复配置继承。

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/skills/test_fork_stream_trace.py`：

```python
"""fork 子代理事件流 → 委派域 Langfuse span（父 span 标注 + 工具 span 挂靠）。"""

import pytest

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.fork_stream import consume_fork_events
from src.infra.llm.request_context import RequestContext


class _FakeSpan:
    def __init__(self, span_id, name, parent_id, metadata=None):
        self.id = span_id
        self.name = name
        self.parent_observation_id = parent_id
        self.metadata = metadata or {}
        self.ended = None

    def end(self, **kwargs):
        self.ended = kwargs


class _FakeClient:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def span(self, **kwargs):
        span = _FakeSpan(
            f"s{len(self.spans)}",
            kwargs.get("name", ""),
            kwargs.get("parent_observation_id"),
            kwargs.get("metadata"),
        )
        self.spans.append(span)
        return span


class _Chunk:
    def __init__(self, text):
        self.content = text
        self.additional_kwargs = {}


class _SubAgentWithTool:
    """替身子代理：先调用一次工具，再产出正文。"""

    async def astream_events(self, inputs, config=None, version="v2"):
        yield {
            "event": "on_chain_start",
            "name": "tools",
            "metadata": {"langgraph_node": "tools"},
        }
        yield {
            "event": "on_tool_start",
            "name": "retrieve_kb",
            "run_id": "r1",
            "metadata": {"langgraph_node": "tools"},
            "data": {"input": {"query": "营收"}},
        }
        yield {
            "event": "on_tool_end",
            "run_id": "r1",
            "metadata": {"langgraph_node": "tools"},
            "data": {"output": "检索结果"},
        }
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("子代理结论")}}


@pytest.mark.asyncio
async def test_fork_events_feed_delegate_collector(monkeypatch):
    """消费子代理事件时同步喂给委派域采集器：父 span 标注 + 工具 span 挂其下。"""
    from src.infra.llm.tool_trace import ToolTraceCollector

    client = _FakeClient()
    collector = ToolTraceCollector(
        enabled=True, trace_id="t1", client=client, scope="delegate", name_prefix="delegate:"
    )
    run = DelegateRun(
        delegate_id="d1", skill_name="analyst", ctx=RequestContext(session_id="s1")
    )
    # 生产里由 _run_fork 先开委派父 span，再消费事件；此处照做
    parent = collector.open_delegate_span(run.delegate_id, "analyst")
    assert parent is not None
    text = await consume_fork_events(
        _SubAgentWithTool(), run, "任务", "analyst", max_turns=5, trace_collector=collector
    )
    collector.close()
    assert text == "子代理结论"
    names = [s.name for s in client.spans]
    assert names == ["delegate", "delegate:tools", "delegate:retrieve_kb"]
    assert client.spans[1].parent_observation_id == parent.id  # round 挂在委派父下
    assert client.spans[2].parent_observation_id == client.spans[1].id  # 工具挂在 round 下


@pytest.mark.asyncio
async def test_no_collector_is_noop():
    """不传采集器时行为与既有完全一致（既有路径不受影响）。"""
    run = DelegateRun(
        delegate_id="d1", skill_name="analyst", ctx=RequestContext(session_id="s1")
    )
    text = await consume_fork_events(_SubAgentWithTool(), run, "任务", "analyst", max_turns=5)
    assert text == "子代理结论"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_fork_stream_trace.py -v`
Expected: FAIL —— `TypeError: consume_fork_events() got an unexpected keyword argument 'trace_collector'`。

- [ ] **Step 3: 让事件流喂给采集器**

`src/agents/skills/fork_stream.py`：

1) `consume_fork_events` 签名加末位参数（默认 `None`，不影响既有调用）：

```python
async def consume_fork_events(
    sub_agent,
    run: DelegateRun | None,
    user_content: str,
    skill_name: str,
    max_turns: int,
    trace_collector=None,
) -> str:
```

docstring 的 Args 段补：

```
        trace_collector: 委派域工具 span 采集器；None 时不采集（既有路径）。
            采集靠显式喂事件，不靠恢复 LangChain 配置继承——事件流与 SSE 的隔离
            （executor 里的 var_child_runnable_config.set(None)）必须保持不变。
```

2) `_handle_fork_event` 签名加末位参数并转发：

```python
async def _handle_fork_event(
    ev, ctx, delegate_id: str, skill: str, state: _ForkStreamState, trace_collector=None
) -> str | None:
```

在函数体第一行 `state.last_activity = time.monotonic()` **之前**插入：

```python
    if trace_collector is not None:
        trace_collector.consume(ev)
```

并在 `consume_fork_events` 的循环里把调用改为：

```python
            stop_text = await _handle_fork_event(
                ev, ctx, delegate_id, skill_name, state, trace_collector
            )
```

- [ ] **Step 4: 在 `_run_fork` 开合委派父 span**

`src/agents/skills/executor.py`：

1) import 区补：

```python
from src.config import settings
from src.infra.llm.tool_trace import ToolTraceCollector
from src.infra.llm.trace_context import current_trace_id
```

（`settings` 已在 `executor.py:43` import，**勿重复添加**。）

2) 在 `_run_fork` 里，`token = var_child_runnable_config.set(None)` **之前**插入：

```python
        # 委派域 trace（design D11）：父 span 标注 delegate_id / skill，其下挂子代理的工具 span。
        # 每委派一个实例——_round 是单值状态，共用实例会跨流串台。
        trace_collector = ToolTraceCollector(
            enabled=settings.LANGFUSE_ENABLE,
            trace_id=current_trace_id.get() or "",
            scope="delegate",
            name_prefix="delegate:",
        )
        if run is not None:
            trace_collector.open_delegate_span(run.delegate_id, record.name)
```

3) 把 `consume_fork_events(sub_agent, run, user_content, record.name, max_turns)` 调用改为：

```python
                text = await asyncio.wait_for(
                    consume_fork_events(
                        sub_agent,
                        run,
                        user_content,
                        record.name,
                        max_turns,
                        trace_collector,
                    ),
                    timeout=total_timeout,
                )
```

4) 在既有 `finally` 块（`:219-225`）里、`current_request_ctx.reset(token_ctx)` **之前**插入：

```python
            # 委派父 span 必须关：取消/异常路径不关会留下悬空 span
            trace_collector.close()
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_fork_stream_trace.py tests/agents/skills/ tests/services/test_run_generation_tracing.py -v`
Expected: PASS。

- [ ] **Step 6: 目视确认中断路径也会关 span**

临时把 `tests/agents/skills/test_fork_stream_trace.py` 里的 `_SubAgentWithTool` 改成抛出 `RuntimeError`，跑一次确认 `collector.close()` 仍被调用（span 的 `ended` 非 None），确认后**改回**。

- [ ] **Step 7: Commit**

```bash
git add src/agents/skills/executor.py src/agents/skills/fork_stream.py tests/agents/skills/test_fork_stream_trace.py
git commit -m "feat(trace): 委派父 span 开合 + 子代理事件流接入委派域采集器"
```

---

## Task 8: P1 文档同步

**Files:**
- Modify: `docs/agents/logging-rules.md`
- Modify: `docs/agents/glossary.md`
- Modify: `docs/agents/requirements_pool.md`

**Interfaces:** 无代码接口；本任务不许改代码。

- [ ] **Step 1: 登记新事件与 skip reason**

`docs/agents/logging-rules.md`：

- 前缀主表/事件清单里登记 `skill resolved`（`[session]`，info，字段 `skill` / `context` / `context_source` / `body_chars`）。
- 在 `skill preload skip` 条目下补新的 `reason` 取值：`not_inline`（命中的 skill 是 fork，正文属子代理 prompt，不注入主 agent）。

- [ ] **Step 2: 补术语**

`docs/agents/glossary.md` 的「技能委派（主从委派）」表里补一行：

| 术语 | 定义 | 常见错误 |
|---|---|---|
| `context_source` | skill 的 `context` 取值来源：`default`（未声明→inline）/ `explicit`（frontmatter 显式）/ `auto_oversize`（未声明但正文超 `INLINE_PROMPT_MAX_CHARS`，加载期自动改 fork） | ❌ 以为"未声明"就是固定某一侧；长文 skill 的承载方式随正文长度自动变化 |

同时修正该表里与本次改动冲突的表述（若提到"未声明取 fork"，改为 inline 缺省 + 超限自动 fork）。

- [ ] **Step 3: 收口需求池**

`docs/agents/requirements_pool.md`：

- F-13 条目补一句收口：默认值已由 `skill-execution-and-delegation` 的 P1 改为 inline，长文由加载期超限自动改 fork 承载，**不再依赖作者手写 `context: fork`**。
- 追加一条待办：`context_source` 是否需要暴露到 `/skills` 面板（当前只进 SkillRecord 与加载日志）。

- [ ] **Step 4: 一事一档自检**

确认三处改动没有复制别处正文；若有新增归属内容，按 CLAUDE.md 的「文档组织」表登记。

- [ ] **Step 5: Commit**

```bash
git add docs/agents/logging-rules.md docs/agents/glossary.md docs/agents/requirements_pool.md
git commit -m "docs(agents): P1 同步——skill resolved 事件、context_source 术语、F-13 收口"
```

---

## Task 9: P1 验收

**Files:** 无代码改动（本任务是执行与判定）。

- [ ] **Step 1: 全量质量门禁**

Run: `POSTGRES_HOST=localhost pytest tests/ -v`
Expected: 全部 PASS。

- [ ] **Step 2: lint 与类型**

Run: `ruff format . && ruff check . && pyright src/`
Expected: `ruff` 无错误；`pyright` 不新增 error（存量第三方误报不算）。

- [ ] **Step 3: 内容库零改动核验**

Run:
```bash
for n in financial-statement-analyzer competitive-landscape market-sizing-analysis; do
  diff -rq "$HOME/.agents/skills/$n" "skills/$n" && echo "$n identical"
done
```
Expected: 三份均只有 SKILL.md 的 description 尾句差异（**P1 未新增任何差异**）；`references/`、`examples/` 逐字相同。若出现新的差异，说明本阶段违规改了内容库，必须回退。

- [ ] **Step 4: 承载方式核验（P1 核心验收）**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_first_batch_skills.py -v -k auto_oversize`
Expected: PASS —— 三份长文 skill 均落 `context_source=auto_oversize` 且 `context=fork`。

再启动一次服务，在启动/加载日志里确认出现三行 `[session] skill resolved skill=... context=fork context_source=auto_oversize body_chars=...`，且**没有**任何 skill 记 `context_source=default` 且正文超限（那会是本阶段的 bug）。

- [ ] **Step 5: 隔离核验（替身，不可用真实子代理）**

Run: `POSTGRES_HOST=localhost pytest tests/agents/skills/test_delegate_task.py -v -k "passes_run or from_run"`
Expected: PASS。**注意**：P1 阶段子代理仍零工具，真实子代理不会写任何引用池——所以"主池为空"必须靠**替身执行器**证明（Step 1 已覆盖），不能靠人工 E2E 观察。

- [ ] **Step 6: trace 目视（P1 的预期形态）**

跑一轮 `/xxx` 命中 fork skill 的会话（任一在库长文 skill），在 Langfuse 上确认：

- 该 trace 出现名为 **`delegate`** 的父 span，metadata 含 `delegate_id` 与 `skill`；
- **其下不应有工具 span**（P1 阶段子代理零工具，工具 span 属 P2）；
- 主 agent 的工具 span 结构（`tools` → 各工具名）与改动前一致。

若父 span 未出现，先查 `LANGFUSE_ENABLE` 是否开启（未开启时采集器整体断电，属预期）。

- [ ] **Step 7: 标记剥离的人工对拍**

构造一次子代理返回 `CONFIRM_REQUIRED: <问题>` 的委派（可用替身或临时提示词），确认回到主 agent 的工具结果里**没有协议前缀**、但**问题文本仍在**。

- [ ] **Step 8: 规格校验与阶段结论**

Run: `openspec validate skill-execution-and-delegation`
Expected: `is valid`。

把 `docs/openspec/changes/skill-execution-and-delegation/tasks.md` 的 §0 闸门逐条对照：

- §0.1 的 P1 条目（§1/§2/§4/§6/§7）与 **§11 的 P1 验收清单（11.3–11.6）** 是否全过；
- §0.3 **P1 代码需过 code review** —— 派独立 reviewer 审本阶段实现（架构评审评的是提案，本步评的是代码）；
- 通过后再进入 §0.2 的 P2 前提确认。**未通过前不得开始 P2 任何条目。**

- [ ] **Step 9: Commit（如有验收过程产生的临时改动或记录）**

```bash
git status --short
# 只提交本阶段应有的改动；临时探针/试验代码不得入库
```

---

## Self-Review（作者自查记录）

**1. Spec 覆盖**（逐条对 P1 的 spec 面）：

| spec requirement | 落点 |
|---|---|
| `skill-registry`「SkillRecord 运行时对象」：`context` 未声明取 inline / 非法值抛错跳过 / `context_source` 三态 | Task 1、Task 2 |
| `skill-registry`「正文按 context 解析」+「长内容的建模去向」（超限记 warning、长文落 fork） | Task 2 |
| `agent-preset`「预加载 skill」只注入 inline 正文 + fork 被跳过记 warning | Task 3 |
| `delegate-task`「委派路径的子上下文隔离与并发分槽」（含 `ctx is None` 豁免） | Task 4（豁免分支保持既有 `:94` early-return 不动） |
| `delegate-task`「子代理确认标记在委派路径不泄漏」（剥前缀、留文本） | Task 5 |
| `llm-tracing`「子代理的工具调用记为 trace span（委派域）」（父 span 带 `delegate_id`/`skill`、`delegate:` 前缀、显式喂事件、观测故障不阻断、不悬空、域间不串台、SSE 隔离不变） | Task 6、Task 7 |

**2. 占位符扫描**：无 "TBD"/"适当处理"/"类似 Task N"。每个代码步骤都给了可粘贴的完整实现与断言。

**3. 类型与命名一致性**：`ContextSource.DEFAULT/EXPLICIT/AUTO_OVERSIZE`、`SkillRecord.context_source`、`_resolve_context(meta[, body])`、`ToolTraceCollector(scope=/parent_span=/name_prefix=)`、`open_delegate_span(delegate_id, skill)`、`consume_fork_events(..., trace_collector=None)`、`strip_confirm_marker_prefix` —— 全计划内命名一致，与 Task 6/7 的测试断言一致。

**4. 已知的跨任务签名变化**（执行者注意）：`_resolve_context` 在 Task 1 是 `(meta)`、Task 2 变成 `(meta, body)`；`consume_fork_events` 在 Task 7 末位新增带默认值的形参，既有调用点无需改动即可通过。
