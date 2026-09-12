# turn-provenance-observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) — dispatch a fresh implementer per task, review between tasks, fix loop on rejection. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"每轮由哪个智能体、按哪本技能手册产生"这件事既对用户可见（SSE 来源声明 + 历史回放）、又对运维可查（温度/人设/分派/prompt 组成/消息构成的日志）。

**Architecture:** 复用既有 SSE `status` 事件承载两条来源声明（新增专用 `stage` 值），同时写入 `events_log` 与缓冲两个 sink，使其随 `process` 列持久化、由既有回放分支重建；技能模式/名单/显示名经 `RequestContext` 从服务层传到 `_run_generation`；日志侧新增 5 个登记事件 + `model turn` 扩 3 字段；前端补一个 `attachHistoryModelNote`，用既有 `model_name` 列在回放时重建模型标注。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph / Loguru（`src/core/logging.py` 注册表驱动）/ pytest / 原生 JS（`deploy/nginx/html/chat.html`）

**Spec:** `docs/openspec/changes/turn-provenance-observability/`（`proposal.md` / `design.md` / `specs/` / `tasks.md`；**设计决策 D1-D16 与 Risks 以 `design.md` 为准**）

## Global Constraints

- 中文注释 / docstring；dataclass 每个字段必须加行内注释（来源、范围、用途）
- 不用三元表达式（`a if cond else b`），写完整 if/else
- 类型不确定不用 `getattr(x, "attr", default)` 隐式兜底，用显式判断
- 硬编码集中管理：常量/文案/阈值只放 `src/config/`（`const.py` 的 `SSEInteractionTexts` 承载用户可见文案与 stage 常量）
- 日志一律走 `core_logging.log_event(Event.X, **fields)`；**不手写前缀/级别**（由 `EVENT_SPECS` 决定）；事件必须在 `Event` 枚举与 `EVENT_SPECS` **两处同名登记**（import 期硬校验）
- **`EventSpec.level` 只允许 `info`/`warning`/`error`**（`src/core/log_events.py:25`，无 `debug`）；**同一条事件只能有一个级别** → 需要"降级"的场景一律改用**调用点守卫不记**
- 单文件 > 400 行必须拆分；单函数 > 80 行必须拆分子函数
- 测试 mock 外部依赖（LLM / DB / Redis），不发起真实网络调用
- 改公共方法签名或响应结构时，同步受影响测试断言与 `docs/agents/api_contract.md`
- 质量门禁：`pytest tests/ -q` 全绿、`ruff check .` 无错、`pyright src/` 不新增 error

## 执行基线（2026-09-12 确认）

- **执行方式**：Subagent-Driven（每 Task fresh implementer + reviewer；A 选项）
- **实机验证**：包含在 Task 9（playwright 端到端 + 真实 trace 复核；A 选项）。若届时活服务不可用，降级为代码级验证并在 `docs/agents/cookbook.md` 登记遗留验证步骤
- **分支与提交**：当前分支，不新建 worktree；开工前先落一个 docs 基线 commit（change 产物 + 本 plan）；**每个 Task 一个 commit**
- **顺序**：Task 1 → 9 严格顺序执行（T2/T3 改签名，T4/T5 依赖其结果）

---

### Task 1: 常量、文案与事件登记

**Files:**
- Modify: `src/config/const.py`（`SSEInteractionTexts`：stage 常量约 203-208 行区、文案约 276-279 行区）
- Modify: `src/core/log_events.py`（`Event` 枚举末尾，约 187 行后）
- Modify: `src/core/log_event_specs.py`（`EVENT_SPECS`：`model turn` 约 80-93 行、字典末尾约 334 行前）
- Test: `tests/core/test_log_events.py`（追加）、`tests/config/test_turn_provenance_texts.py`（新建）

**Interfaces:**
- Produces（后续所有 Task 依赖这些确切名字）：
  - `SSEInteractionTexts.STAGE_TURN_AGENT: str = "turn_agent"`
  - `SSEInteractionTexts.STAGE_TURN_SKILL: str = "turn_skill"`
  - `SSEInteractionTexts.AGENT_IN_USE_TMPL: str = "当前使用了 {agent}"`
  - `SSEInteractionTexts.SKILLS_LOADED_TMPL: str = "成功加载 skills：{skills}"`
  - `SSEInteractionTexts.SKILL_IN_USE_TMPL: str = "使用技能：/{skill}（子代理执行）"`
  - `Event.AGENT_RESOLVED / Event.PROMPT_ASSEMBLED / Event.PROMPT_MESSAGES / Event.SKILL_INJECTED / Event.SKILL_DISPATCH`

- [ ] **Step 1: 写失败测试（文案与 stage 常量）**

新建 `tests/config/test_turn_provenance_texts.py`：

```python
"""来源声明的文案与 stage 常量（turn-provenance-observability design D2/D4/D9）。"""

from src.config.const import SSEInteractionTexts


def test_agent_in_use_template():
    """智能体声明文案：模板可填展示名。"""
    assert SSEInteractionTexts.AGENT_IN_USE_TMPL.format(agent="财务专家") == (
        "当前使用了 财务专家"
    )


def test_skills_loaded_template_uses_fullwidth_colon_and_ideographic_comma():
    """技能声明文案：全角冒号 + 顿号（与 UNKNOWN_SKILL_PREFIX 的列表标点一致）。"""
    assert SSEInteractionTexts.SKILLS_LOADED_TMPL.format(skills="a、b、c") == (
        "成功加载 skills：a、b、c"
    )


def test_skill_in_use_template_is_fork_wording():
    """fork 轮措辞：称"使用/执行"而非"加载"（fork 不注入上下文）。"""
    assert SSEInteractionTexts.SKILL_IN_USE_TMPL.format(skill="finance-analyst") == (
        "使用技能：/finance-analyst（子代理执行）"
    )


def test_turn_stages_are_dedicated():
    """来源声明的 stage 不得复用工具态/思考态（否则前端触发正文旁白重分类）。"""
    reserved = {
        SSEInteractionTexts.STAGE_RETRIEVE,
        SSEInteractionTexts.STAGE_WEB_SEARCH,
        SSEInteractionTexts.STAGE_AGENT,
    }
    turn_stages = {
        SSEInteractionTexts.STAGE_TURN_AGENT,
        SSEInteractionTexts.STAGE_TURN_SKILL,
    }
    assert not (turn_stages & reserved)
    assert len(turn_stages) == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/config/test_turn_provenance_texts.py -q`
Expected: FAIL（`AttributeError: STAGE_TURN_AGENT`）

- [ ] **Step 3: 加常量与文案**

`src/config/const.py`：在 `STAGE_RETRIEVE`（`:208`）之后追加新段：

```python
    # ── 每轮来源声明 stage（turn-provenance）──
    # 本轮生效智能体声明专用 stage：不得复用 retrieve/web_search（前端
    # renderStatusTag 对这两者会触发"待定正文归为旁白"重分类）
    STAGE_TURN_AGENT: str = "turn_agent"

    # 本轮技能动作声明专用 stage：同上约束
    STAGE_TURN_SKILL: str = "turn_skill"
```

在 `SKILL_DIRECT_CTX_UNAVAILABLE`（`:278`）之后、`# ── 直出轮确认门文案 ──`（`:280`）之前追加：

```python
    # ── 每轮来源声明文案（turn-provenance）──
    # 生效智能体声明：{agent}=预设展示名（display_name，缺失回落 name）
    AGENT_IN_USE_TMPL: str = "当前使用了 {agent}"
    # 本轮技能声明：{skills}=成功解析的技能名列表（顿号连接；与 UNKNOWN_SKILL_PREFIX 同标点）
    SKILLS_LOADED_TMPL: str = "成功加载 skills：{skills}"
    # fork 轮技能声明：{skill}=fork 技能名（fork 不注入上下文，故称"使用"而非"加载"）
    SKILL_IN_USE_TMPL: str = "使用技能：/{skill}（子代理执行）"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/config/test_turn_provenance_texts.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: 写失败测试（事件登记）**

在 `tests/core/test_log_events.py` 末尾追加：

```python
def test_turn_provenance_events_registered():
    """来源与观测事件两处同名登记，且前缀/级别合法（design D11）。"""
    expected = {
        "agent resolved": ("session", "info"),
        "prompt assembled": ("llm", "info"),
        "prompt messages": ("agent", "info"),
        "skill injected": ("session", "info"),
        "skill dispatch": ("session", "info"),
    }
    for name, (prefix, level) in expected.items():
        member = le.Event(name)
        assert member.value == name
        spec = le.EVENT_SPECS[name]
        assert spec.prefix == prefix
        assert spec.level == level


def test_model_turn_spec_includes_temperature_fields():
    """model turn 扩 temperature / temp_source / kb_bound（design D11 #1）。"""
    fields = le.EVENT_SPECS["model turn"].fields
    assert "temperature" in fields
    assert "temp_source" in fields
    assert "kb_bound" in fields
```

- [ ] **Step 6: 运行测试确认失败**

Run: `pytest tests/core/test_log_events.py -q`
Expected: FAIL（`ValueError: 'agent resolved' is not a valid Event`）

- [ ] **Step 7: 登记 5 个新事件 + 扩 model turn 字段**

`src/core/log_events.py`：在 `FORK_CONFIRM_UNCONFIRMED`（约 `:187`）之后追加新段：

```python
    # [session] 本轮生效智能体与来源（turn-provenance；两者全空时不记）
    AGENT_RESOLVED = "agent resolved"
    # [llm] system prompt 组成（人设来源、条件注入、system 段数）
    PROMPT_ASSEMBLED = "prompt assembled"
    # [agent] 首轮组装的消息构成（system / 注入 / 历史三段条数）
    PROMPT_MESSAGES = "prompt messages"
    # [session] 技能正文成功注入（inline 命令触发 / 首轮预设预加载）
    SKILL_INJECTED = "skill injected"
    # [session] 命令形态分派结果（kind=plain 不记，避免每轮噪声）
    SKILL_DISPATCH = "skill dispatch"
```

`src/core/log_event_specs.py`：在 `model turn`（`:80-93`）的 fields 元组末尾追加三项（保留原有顺序）：

```python
    "model turn": EventSpec(
        "model turn",
        "agent",
        "info",
        (
            "model",
            "usage_in",
            "usage_out",
            "usage_estimated",
            "fallback",
            "latency_ms",
            "iteration",
            "temperature",
            "temp_source",
            "kb_bound",
        ),
    ),
```

在字典末尾（`"fork confirm unconfirmed"` 条目之后、闭合 `}` 之前）追加：

```python
    # [session/llm/agent] 每轮来源与 prompt 观测（turn-provenance-observability）
    "agent resolved": EventSpec(
        "agent resolved",
        "session",
        "info",
        (
            "requested",
            "bound",
            "effective",
            "source",
            "persona_applied",
        ),
    ),
    "prompt assembled": EventSpec(
        "prompt assembled",
        "llm",
        "info",
        (
            "persona_source",
            "kb_bound",
            "has_skills",
            "discipline_injected",
            "delegate_injected",
            "system_msgs",
        ),
    ),
    "prompt messages": EventSpec(
        "prompt messages",
        "agent",
        "info",
        ("system_msgs", "injected_msgs", "history_msgs"),
    ),
    "skill injected": EventSpec(
        "skill injected",
        "session",
        "info",
        ("skill", "mode", "chars", "source"),
    ),
    "skill dispatch": EventSpec(
        "skill dispatch",
        "session",
        "info",
        ("kind", "skill", "context", "direct_skill"),
    ),
```

- [ ] **Step 8: 运行测试确认通过 + 全量门禁**

Run: `pytest tests/core/test_log_events.py tests/config/test_turn_provenance_texts.py -q`
Expected: PASS

Run: `pytest tests/ -q && ruff check .`
Expected: 全绿、无 lint 错误（import 期 `_validate_registry` 已强制两处一致）

- [ ] **Step 9: 提交**

```bash
git add src/config/const.py src/core/log_events.py src/core/log_event_specs.py tests/core/test_log_events.py tests/config/test_turn_provenance_texts.py
git commit -m "feat(logging): 登记来源声明 stage/文案与 5 个观测事件，扩 model turn 温度字段"
```

---

### Task 2: 预加载回传技能名名单

**Files:**
- Modify: `src/services/agent_service.py`（`_preload_skills_text` `:704-729`、`_preload_if_first_round` `:731-750`、调用点 `:863`）
- Test: `tests/services/test_preset_skill_preload.py`（更新 6 处既有断言）

**Interfaces:**
- Consumes: `Event.SKILL_PRELOAD_SKIP`（既有）
- Produces:
  - `_preload_skills_text(self, skill_names: list[str]) -> tuple[str, list[str]]`（正文, 成功解析的技能名列表——去重、保序）
  - `_preload_if_first_round(self, effective_agent: str, history: list) -> tuple[str, list[str]]`

- [ ] **Step 1: 先看现状断言**

Run: `grep -n "_preload_skills_text\|_preload_if_first_round" tests/services/test_preset_skill_preload.py src/services/agent_service.py`
Expected: 测试中约 6 处（`:29/:42/:48` 与 `:69/:70/:81`）、服务中 3 处（`:704/:731/:863`）

- [ ] **Step 2: 写失败测试（新签名）**

在 `tests/services/test_preset_skill_preload.py` **新增**（不改既有用例，先让新用例红）：

```python
def test_preload_returns_text_and_resolved_names():
    """返回 (正文, 成功解析名单)：去重、保序、查不到的不入名单（design D14）。"""
    svc = _service(
        {
            "a": _Record("a", inline="方法论 A"),
            "b": _Record("b", inline="方法论 B"),
        }
    )
    text, names = svc._preload_skills_text(["a", "b", "ghost", "a"])
    assert names == ["a", "b"]
    assert "方法论 A" in text and "方法论 B" in text
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/services/test_preset_skill_preload.py::test_preload_returns_text_and_resolved_names -q`
Expected: FAIL（`ValueError: not enough values to unpack`）

- [ ] **Step 4: 改实现**

`_preload_skills_text` 改为：

```python
    def _preload_skills_text(self, skill_names: list[str]) -> tuple[str, list[str]]:
        """按预设声明的 skills 顺序渲染并拼接各技能正文（隐藏注入用）。

        Args:
            skill_names: 预设 frontmatter 的 skills 列表（声明顺序即渲染顺序）

        Returns:
            (用 "\\n\\n" 拼接的正文, 成功解析的技能名列表——去重、按声明顺序保序；
            全部查不到时正文为空串、名单为空列表)
        """
        parts: list[str] = []
        resolved: list[str] = []
        for name in skill_names:
            if self._skill_registry is None:
                break
            record = self._skill_registry.get(name)
            if record is None:
                core_logging.log_event(
                    Event.SKILL_PRELOAD_SKIP, skill=name, reason="not_found"
                )
                continue
            body = record.inline_prompt
            if not body:
                body = record.fork_body
            if not body:
                continue
            parts.append(render_skill_body(body, ""))
            if name not in resolved:
                resolved.append(name)
        return "\n\n".join(parts), resolved
```

`_preload_if_first_round` 改为（仅改返回类型与提前返回，判定逻辑不变）：

```python
    def _preload_if_first_round(
        self, effective_agent: str, history: list
    ) -> tuple[str, list[str]]:
        """首轮预加载判定：仅在首轮、已绑定预设且该预设声明 skills 时给出待注入正文。

        Args:
            effective_agent: 本会话生效的智能体名（空=未绑定）
            history: 本轮的历史消息（若本轮已有 inline `/xxx` 注入，T5 已往其中追加条目→非空）

        Returns:
            (预加载正文, 成功解析的技能名列表)；任一条件不满足返回 ("", [])
        """
        if history:
            return "", []
        if not effective_agent:
            return "", []
        if self._preset_registry is None:
            return "", []
        preset = self._preset_registry.get(effective_agent)
        if preset is None or not preset.skills:
            return "", []
        return self._preload_skills_text(preset.skills)
```

- [ ] **Step 5: 同步调用点**

`src/services/agent_service.py:863` 附近改为（`skill_action`/`loaded_skills` 在 Task 4 落，本步只改解包）：

```python
    if direct_skill == "":
        preload_text, preload_names = self._preload_if_first_round(
            effective_agent, history
        )
        if preload_text:
            preload_entry = await self._inject_skill_message(
                session_id, kb_id, preload_text
            )
            history = history + [preload_entry]
            launch_context["history"] = history
```

- [ ] **Step 6: 同步既有 6 处断言**

Run: `pytest tests/services/test_preset_skill_preload.py -q`
按失败清单逐处把「返回值直接与字符串比较」改为「解包后断言」。模式：

```python
    text, names = svc._preload_if_first_round("finance-expert", [])
    assert text == "方法论 A"
    assert names == ["a"]
```

（非首轮/无预设的场景：`assert text == ""` 且 `assert names == []`）

- [ ] **Step 7: 运行确认全通过**

Run: `pytest tests/services/test_preset_skill_preload.py -q`
Expected: PASS（全部用例）

- [ ] **Step 8: 提交**

```bash
git add src/services/agent_service.py tests/services/test_preset_skill_preload.py
git commit -m "refactor(agent): 预加载回传成功解析的技能名名单，供来源声明使用"
```

---

### Task 3: 智能体解析返回结构化结果

**Files:**
- Modify: `src/services/agent_service.py`（新增 `AgentResolution` dataclass；`_resolve_session_agent` `:876-912`；调用点 `:809-814`）
- Test: `tests/services/test_agent_service.py`（新增五分支用例）

**Interfaces:**
- Consumes: `_chat_manager.get_session_agent_async` / `bind_session_agent_async`、`self._preset_registry`、`Event.AGENT_BIND_IGNORED`（均既有）
- Produces:
  - `AgentResolution`（frozen dataclass）：`.effective: str`、`.source: str`（`bound|new_bound|ignored|unregistered|none`）
  - `_resolve_session_agent(self, session_id, requested, bound="") -> AgentResolution`

- [ ] **Step 1: 检查既有调用点**

Run: `grep -rn "_resolve_session_agent" src/ tests/`
Expected: 服务内 1 处（`stream_chat` 约 `:810`）；若测试中有调用，一并记录待改

- [ ] **Step 2: 写失败测试**

在 `tests/services/test_agent_service.py` 末尾追加：

```python
class TestResolveSessionAgentSource:
    """_resolve_session_agent 五分支的生效值与来源枚举（design D11）。"""

    @pytest.mark.asyncio
    async def test_bound_matching_request_keeps_bound(self):
        service, _ = _make_service()
        result = await service._resolve_session_agent(
            "s1", "finance-expert", bound="finance-expert"
        )
        assert result.effective == "finance-expert"
        assert result.source == "bound"

    @pytest.mark.asyncio
    async def test_bound_conflicting_request_is_ignored(self):
        service, _ = _make_service()
        result = await service._resolve_session_agent(
            "s1", "legal-expert", bound="finance-expert"
        )
        assert result.effective == "finance-expert"
        assert result.source == "ignored"

    @pytest.mark.asyncio
    async def test_no_request_no_bound_is_none(self):
        service, _ = _make_service()
        result = await service._resolve_session_agent("s1", "")
        assert result.effective == ""
        assert result.source == "none"

    @pytest.mark.asyncio
    async def test_unregistered_request_degrades_to_empty(self):
        service, _ = _make_service()
        service._preset_registry = Mock()
        service._preset_registry.get = Mock(return_value=None)
        result = await service._resolve_session_agent("s1", "ghost")
        assert result.effective == ""
        assert result.source == "unregistered"

    @pytest.mark.asyncio
    async def test_new_request_binds_as_new_bound(self):
        service, chat_manager = _make_service()
        service._preset_registry = Mock()
        service._preset_registry.get = Mock(return_value=Mock())
        result = await service._resolve_session_agent("s1", "finance-expert")
        assert result.effective == "finance-expert"
        assert result.source == "new_bound"
        chat_manager.bind_session_agent_async.assert_awaited_once_with(
            "s1", "finance-expert"
        )
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::TestResolveSessionAgentSource -q`
Expected: FAIL（`AttributeError: 'str' object has no attribute 'effective'`）

- [ ] **Step 4: 加 dataclass 并改造函数**

在 `src/services/agent_service.py` 的 `_record_event` 定义之前追加（文件已 import `dataclass`；若否补 `from dataclasses import dataclass`）：

```python
@dataclass(frozen=True)
class AgentResolution:
    """会话智能体解析结果（design D11：来源由解析方给出，调用方不重推分支）。

    effective: 本轮生效的智能体名（空=未绑定或未注册降级）
    source: 解析来源枚举 —— bound（沿用绑定）/ new_bound（首次绑定）/
        ignored（请求与会话绑定不一致，已忽略）/ unregistered（请求名未注册，
        降级为空）/ none（请求与绑定都为空）
    """

    effective: str  # 生效智能体名，来源：_resolve_session_agent；范围：本轮请求；用途：ctx.agent + agent resolved 日志
    source: str  # 来源枚举值，来源：同上；范围：同上；用途：agent resolved 日志与单测断言
```

`_resolve_session_agent` 改为（分支条件与告警事件保持不变，只改返回值与 docstring 的 Returns）：

```python
    async def _resolve_session_agent(
        self, session_id: str, requested: str, bound: str = ""
    ) -> AgentResolution:
        """解析本会话生效的智能体名（bind-once，无 400）。

        Args:
            session_id: 会话 ID
            requested: 请求体传入的智能体名（可为空）
            bound: 会话已绑定的智能体名（空=未绑定）

        Returns:
            AgentResolution：已绑定一律生效绑定值（请求不一致 → 忽略 + warning），
            来源枚举见 dataclass docstring
        """
        if bound:
            if requested and requested != bound:
                core_logging.log_event(
                    Event.AGENT_BIND_IGNORED,
                    session_id=session_id,
                    bound=bound,
                    requested=requested,
                )
                return AgentResolution(effective=bound, source="ignored")
            return AgentResolution(effective=bound, source="bound")
        if not requested:
            return AgentResolution(effective="", source="none")
        if (
            self._preset_registry is None
            or self._preset_registry.get(requested) is None
        ):
            core_logging.log_event(
                Event.AGENT_BIND_IGNORED,
                session_id=session_id,
                bound="",
                requested=requested,
            )
            return AgentResolution(effective="", source="unregistered")
        await self._chat_manager.bind_session_agent_async(session_id, requested)
        return AgentResolution(effective=requested, source="new_bound")
```

调用点 `:809-814` 改为：

```python
        bound_raw = await self._chat_manager.get_session_agent_async(session_id)
        resolution = await self._resolve_session_agent(
            session_id, agent, bound=bound_raw
        )
        effective_agent = resolution.effective
        ctx.agent = effective_agent
        launch_context["agent"] = effective_agent
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -q`
Expected: PASS（含新增 5 例；若有其它用例断言旧返回值，一并改为 `.effective`）

- [ ] **Step 6: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "refactor(agent): _resolve_session_agent 返回结构化结果（生效值 + 来源枚举）"
```

---

### Task 4: RequestContext 承载本轮来源并填充

**Files:**
- Modify: `src/infra/llm/request_context.py`（在 `known_skill_names` 之后追加三字段）
- Modify: `src/services/agent_service.py`（`stream_chat` 填充，约 `:815-869`）
- Test: `tests/services/test_agent_service.py`（新增 ctx 字段断言）

**Interfaces:**
- Consumes: `AgentResolution`（Task 3）、`_preload_if_first_round` 的 `(text, names)`（Task 2）、`parse_prefix` 的 `PrefixParse`（既有）、`SkillContext.INLINE/FORK`（既有）
- Produces（Task 5 依赖）：
  - `RequestContext.agent_display_name: str`
  - `RequestContext.loaded_skills: list[str]`
  - `RequestContext.skill_action: str`（`none|inline|preload|fork|unknown`）

- [ ] **Step 1: 加字段**

`src/infra/llm/request_context.py`，在 `known_skill_names`（`:61-63`）之后追加：

```python
    agent_display_name: str = ""  # 生效智能体的展示名（来源：Plan 3 会话预设 display_name，缺失回落 name）；范围：请求内只读；用途：SSE 来源声明文案
    loaded_skills: list[str] = field(
        default_factory=list
    )  # 本轮成功加载的技能名（来源：inline 命中记录规范名 / 首轮预加载回传名单；范围：本轮；用途：SSE 技能声明一条列全；fork/unknown/none 留空）
    skill_action: str = (
        "none"  # 本轮技能动作模式（none|inline|preload|fork|unknown；来源：stream_chat 依 parse_prefix 三态与 record.context 判定）；用途：决定是否发技能声明与发哪条文案
    )
```

- [ ] **Step 2: 写失败测试**

在 `tests/services/test_agent_service.py` 末尾追加：

```python
class TestTurnProvenanceContext:
    """stream_chat 写入本轮来源承载字段（design D12/D13）。"""

    @pytest.mark.asyncio
    async def test_no_skill_round_keeps_none(self):
        service, _ = _make_service()
        _agen, launch = await service.stream_chat("", "s-none", "营收多少")
        ctx = launch["ctx"]
        assert ctx.skill_action == "none"
        assert ctx.loaded_skills == []
        assert ctx.agent_display_name == ""

    @pytest.mark.asyncio
    async def test_unknown_prefix_marks_unknown(self):
        service, _ = _make_service()
        service._skill_registry = Mock()
        service._skill_registry.user_visible = Mock(return_value=[])
        service._skill_registry.model_visible = Mock(return_value=[])
        _agen, launch = await service.stream_chat("", "s-unknown", "/ghost 任务")
        ctx = launch["ctx"]
        assert ctx.skill_action == "unknown"
        assert ctx.loaded_skills == []

    @pytest.mark.asyncio
    async def test_inline_prefix_marks_inline_with_canonical_name(self):
        service, _ = _make_service()
        record = Mock()
        record.name = "finance-qa"
        record.context = SkillContext.INLINE
        record.inline_prompt = "方法论"
        service._skill_registry = Mock()
        service._skill_registry.user_visible = Mock(return_value=[record])
        service._skill_registry.model_visible = Mock(return_value=[record])
        _agen, launch = await service.stream_chat("", "s-inline", "/finance-qa 任务")
        ctx = launch["ctx"]
        assert ctx.skill_action == "inline"
        assert ctx.loaded_skills == ["finance-qa"]

    @pytest.mark.asyncio
    async def test_agent_display_name_comes_from_preset(self):
        service, _ = _make_service()
        preset = Mock()
        preset.display_name = "财务专家"
        preset.system_prompt = "你是财务专家"
        service._preset_registry = Mock()
        service._preset_registry.get = Mock(return_value=preset)
        _agen, launch = await service.stream_chat("", "s-agent", "营收多少", agent="finance-expert")
        ctx = launch["ctx"]
        assert ctx.agent_display_name == "财务专家"
```

（顶部需补 `from src.agents.skills.models import SkillContext`；若 `stream_chat` 的签名顺序不同，以 `src/services/agent_service.py` 实际签名为准调整调用）

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::TestTurnProvenanceContext -q`
Expected: FAIL（`AttributeError: 'RequestContext' object has no attribute 'skill_action'`）

- [ ] **Step 4: 填充 ctx**

`src/services/agent_service.py`：在 `session_preset` 解析块（`:827-833`）之后补显示名：

```python
    if session_preset is not None:
        ctx.persona = session_preset.system_prompt
        ctx.agent_display_name = session_preset.display_name or session_preset.name
    else:
        ctx.persona = ""
        ctx.agent_display_name = ""
```

`/xxx` 分派段（`:835-856`）改为（新增 `skill_action` / `loaded_skills`）：

```python
    parsed = parse_prefix(query, known, self._skill_registry)
    direct_skill = ""
    effective_query = query
    skill_action = "none"
    loaded_skills: list[str] = []
    if parsed.kind == "known":
        record = parsed.record
        if record is not None and record.context == SkillContext.INLINE:
            # inline：渲染正文持久化注入，本轮即追加进 history，主 agent 单轮
            injected_text = render_skill_body(record.inline_prompt or "", parsed.task)
            entry = await self._inject_skill_message(session_id, kb_id, injected_text)
            history = history + [entry]
            effective_query = parsed.task
            skill_action = "inline"
            loaded_skills = [record.name]
        else:
            direct_skill = parsed.skill_name
            effective_query = parsed.task
            skill_action = "fork"
    elif parsed.kind == "unknown":
        # 未知前缀复用 skill_direct 的 fail-open 通道输出"不存在 + 可用列表"
        direct_skill = parsed.skill_name
        effective_query = parsed.task
        skill_action = "unknown"
```

预加载段改为（**仅当返回非空才置 `preload`**，否则会发出空名单声明）：

```python
    if direct_skill == "":
        preload_text, preload_names = self._preload_if_first_round(
            effective_agent, history
        )
        if preload_text:
            preload_entry = await self._inject_skill_message(
                session_id, kb_id, preload_text
            )
            history = history + [preload_entry]
            launch_context["history"] = history
            skill_action = "preload"
            loaded_skills = preload_names
    ctx.skill_action = skill_action
    ctx.loaded_skills = loaded_skills
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/infra/llm/request_context.py src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat(agent): RequestContext 承载本轮技能模式/名单与智能体展示名"
```

---

### Task 5: 每轮发出两条来源声明

**Files:**
- Modify: `src/services/agent_service.py`（`_run_generation`，`agent_used` 同点 `:531-539`）
- Test: `tests/services/test_agent_service.py`（顺序/幂等/文案）、`tests/chat/test_process_log.py`（回归断言）

**Interfaces:**
- Consumes: `RequestContext.skill_action/loaded_skills/agent_display_name`（Task 4）、`SSEStatusEvent`、`_record_event`、`manager.add_event`（既有）
- Produces: 每轮两条 `status` 事件（`stage=turn_agent` / `turn_skill`），进入 `capture.events_log` 与 SSE 缓冲

- [ ] **Step 1: 写失败测试（顺序与文案）**

在 `tests/services/test_agent_service.py` 末尾追加：

```python
class TestTurnProvenanceStatus:
    """每轮开头两条来源声明的文案、顺序与幂等（design D7/D8）。"""

    @staticmethod
    def _service_with_binding(record=None, preset=None):
        service, _ = _make_service()
        if record is not None:
            service._skill_registry = Mock()
            service._skill_registry.user_visible = Mock(return_value=[record])
            service._skill_registry.model_visible = Mock(return_value=[record])
        if preset is not None:
            service._preset_registry = Mock()
            service._preset_registry.get = Mock(return_value=preset)
        return service

    @pytest.mark.asyncio
    async def test_agent_and_skill_declarations_precede_node_status():
        """顺序：agent_used → turn_agent → turn_skill → 首个节点状态行。"""
        record = Mock()
        record.name = "finance-qa"
        record.context = SkillContext.INLINE
        record.inline_prompt = "方法论"
        preset = Mock()
        preset.display_name = "财务专家"
        preset.system_prompt = "你是财务专家"
        preset.skills = []
        service = self._service_with_binding(record=record, preset=preset)
        service._chat_manager.get_session_agent_async.return_value = "finance-expert"

        async def fake_astream(*args, **kwargs):
            yield _chat_model_start_item()

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events, _ = await _collect_events(
            service, "", "session-prov", "/finance-qa 任务"
        )
        statuses = [e for e in events if isinstance(e, SSEStatusEvent)]
        assert [s.stage for s in statuses][:2] == [
            SSEInteractionTexts.STAGE_TURN_AGENT,
            SSEInteractionTexts.STAGE_TURN_SKILL,
        ]
        assert statuses[0].message == "当前使用了 财务专家"
        assert statuses[1].message == "成功加载 skills：finance-qa"

    @pytest.mark.asyncio
    async def test_no_declaration_when_unbound_and_plain():
        """未绑定智能体 + 普通文本 → 不产出任何来源声明。"""
        service, _ = _make_service()

        async def fake_astream(*args, **kwargs):
            yield _chat_model_start_item()

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events, _ = await _collect_events(service, "", "session-plain", "营收多少")
        stages = [
            e.stage for e in events if isinstance(e, SSEStatusEvent)
        ]
        assert SSEInteractionTexts.STAGE_TURN_AGENT not in stages
        assert SSEInteractionTexts.STAGE_TURN_SKILL not in stages
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/services/test_agent_service.py::TestTurnProvenanceStatus -q`
Expected: FAIL（断言 stage 列表不含 `turn_agent`）

- [ ] **Step 3: 发声明**

`src/services/agent_service.py` `_run_generation`，在 `agent_used`（`:533-534`）之后、`drain_task`（`:537`）之前插入：

```python
    # 每轮来源声明（design D1/D7）：复用 status，stage 用专用值；两条均写两个
    # sink（events_log 负责随 process 持久化与历史回放，manager 缓冲负责实时 SSE）。
    # 必须在创建 clarify drain 任务之前写入，否则并发 drain 会把 ask_user/delegate
    # 事件插到前面，破坏"来源声明先于澄清与委派"的顺序契约。
    if ctx.agent_display_name:
        agent_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_AGENT,
            message=SSEInteractionTexts.AGENT_IN_USE_TMPL.format(
                agent=ctx.agent_display_name
            ),
        )
        _record_event(capture, agent_decl)
        manager.add_event(
            session_id, agent_decl.type, agent_decl.payload_for_buffer()
        )
    if ctx.skill_action in ("inline", "preload") and ctx.loaded_skills:
        skill_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_SKILL,
            message=SSEInteractionTexts.SKILLS_LOADED_TMPL.format(
                skills="、".join(ctx.loaded_skills)
            ),
        )
        _record_event(capture, skill_decl)
        manager.add_event(
            session_id, skill_decl.type, skill_decl.payload_for_buffer()
        )
    elif ctx.skill_action == "fork" and direct_skill:
        fork_decl = SSEStatusEvent(
            stage=SSEInteractionTexts.STAGE_TURN_SKILL,
            message=SSEInteractionTexts.SKILL_IN_USE_TMPL.format(skill=direct_skill),
        )
        _record_event(capture, fork_decl)
        manager.add_event(
            session_id, fork_decl.type, fork_decl.payload_for_buffer()
        )
```

（`SSEStatusEvent` 已在 `agent_service.py:59` 的 `from src.utils.sse import (...)` 内，无需新增 import；构造签名 `SSEStatusEvent(stage, message, detail=None, type="status", seq=None)` 见 `src/utils/sse.py:16-25`，`payload_for_buffer()` 返回 `{"stage", "message"}` 且仅 `detail` 非空时才带该键）

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/services/test_agent_service.py -q`
Expected: PASS

- [ ] **Step 5: 写 process 回归断言**

在 `tests/chat/test_process_log.py` 中追加：

```python
def test_turn_provenance_statuses_survive_exclusion():
    """回归：来源声明（stage=turn_agent/turn_skill）不被 _EXCLUDED_TYPES 吞掉。

    design D6/Risks —— 若后续改动排除集，此断言必须变红。
    """
    events = [
        {
            "type": "status",
            "payload": {"stage": "turn_agent", "message": "当前使用了 财务专家"},
        },
        {
            "type": "status",
            "payload": {"stage": "turn_skill", "message": "成功加载 skills：finance-qa"},
        },
        {"type": "done", "payload": {}},
    ]
    result, _purified = build_process_events(events)
    kept = [e for e in result["events"] if e["type"] == "status"]
    assert [e["payload"]["stage"] for e in kept] == ["turn_agent", "turn_skill"]
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/chat/test_process_log.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py tests/chat/test_process_log.py
git commit -m "feat(sse): 每轮开头声明生效智能体与本轮技能，随 process 持久化可回放"
```

---

### Task 6: 补全 5 处日志

**Files:**
- Modify: `src/agents/graph/agent_node.py`（温度分档 `:153-167`、`MODEL_TURN` `:194-203`、`_initial_messages` `:62-121`）
- Modify: `src/services/agent_service.py`（`agent resolved` 约 `:833` 后、`skill dispatch` 约 `:857` 后、`skill injected` `:845`/`:865`）
- Modify: `src/rag/prompt.py`（`build_system_prompt` `:24-63`）
- Test: `tests/agents/graph/test_agent_node.py`、`tests/rag/test_prompt_layers.py`

**Interfaces:**
- Consumes: `AgentResolution`（Task 3）、`settings.LLM_TEMPERATURE` / `settings.NON_KB_MAIN_TEMPERATURE`（既有）、`Event.*`（Task 1）
- Produces: `model turn` 3 新字段、`agent resolved`、`prompt assembled`、`prompt messages`、`skill injected`、`skill dispatch`

- [ ] **Step 1: 写失败测试（温度三字段）**

在 `tests/agents/graph/test_agent_node.py` 追加（沿用该文件既有的 fake LLM / prompt_manager 替身；`caplog` 或直接断言 `Event.MODEL_TURN` 的 fields 均不可行——本项目日志走 loguru，故用 `mock.patch` 拦截）：

```python
@pytest.mark.asyncio
async def test_model_turn_logs_temperature_and_source(monkeypatch):
    """未绑 KB → 显式传非 KB 档；绑 KB → 沿用构造默认（design D11 #1）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.agents.graph.agent_node.core_logging.log_event", fake_log_event)

    # 未绑 KB（state.kb_id == ""）
    await _run_one_turn(kb_id="")
    model_turn = [c for c in calls if c["event"].value == "model turn"][-1]
    assert model_turn["temperature"] == settings.NON_KB_MAIN_TEMPERATURE
    assert model_turn["temp_source"] == "explicit"
    assert model_turn["kb_bound"] is False

    calls.clear()
    # 绑 KB
    await _run_one_turn(kb_id="kb1")
    model_turn = [c for c in calls if c["event"].value == "model turn"][-1]
    assert model_turn["temperature"] == settings.LLM_TEMPERATURE
    assert model_turn["temp_source"] == "default"
    assert model_turn["kb_bound"] is True
```

（`_run_one_turn` 为本任务新增的本地 helper：构造 `AgentState` + fake `llm.bind_tools` 返回可 `astream` 的替身，调用 `make_agent_model_node(...)` 产出的节点函数；若该文件已有等价 helper，直接复用其名字，不重复造）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_agent_node.py -q -k temperature`
Expected: FAIL（`KeyError: 'temperature'`）

- [ ] **Step 3: 温度单一真源 + 日志字段**

`src/agents/graph/agent_node.py` 温度分档段改为：

```python
        # 采样温度分档（chat-temperature-policy）：未绑 KB → 非 KB 档；绑 KB → 不传
        # temperature，沿用模型构造温度 LLM_TEMPERATURE。temperature 为单一真源：
        # 同一变量既用于 LLM 调用也用于日志（design D11 #1）
        if state.kb_id:
            temperature = settings.LLM_TEMPERATURE
            temp_source = "default"
            async for chunk in model.astream(
                messages, extra_body={"enable_thinking": state.deep_thinking}
            ):
                chunks.append(chunk)
        else:
            temperature = settings.NON_KB_MAIN_TEMPERATURE
            temp_source = "explicit"
            async for chunk in model.astream(
                messages,
                extra_body={"enable_thinking": state.deep_thinking},
                temperature=temperature,
            ):
                chunks.append(chunk)
```

`Event.MODEL_TURN` 调用追加三个字段：

```python
        core_logging.log_event(
            Event.MODEL_TURN,
            model=model_name,
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated=usage_estimated,
            fallback=False,
            latency_ms=int((time.monotonic() - turn_start) * 1000),
            iteration=iteration,
            temperature=temperature,
            temp_source=temp_source,
            kb_bound=bool(state.kb_id),
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_agent_node.py -q`
Expected: PASS

- [ ] **Step 5: 写失败测试（prompt assembled）**

在 `tests/rag/test_prompt_layers.py` 追加：

```python
def test_prompt_assembled_logged_with_injection_facts(monkeypatch):
    """build_system_prompt 记录人设来源与条件注入事实（design D11 #3/D15）。"""
    from src.core.log_events import Event

    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    pm = _pm(base="基础段正文")

    build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=True,
        prompt_manager=pm,
    )

    assembled = [c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED]
    assert len(assembled) == 1
    payload = assembled[0]
    assert payload["persona_source"] == "preset"
    assert payload["kb_bound"] is True
    assert payload["has_skills"] is True
    assert payload["discipline_injected"] is True
    assert payload["delegate_injected"] is True
    assert payload["system_msgs"] == 1


def test_prompt_assembled_reports_base_persona_and_no_discipline(monkeypatch):
    """未选 agent（persona 为空）→ 人设来源 base、检索纪律不注入。"""
    from src.core.log_events import Event

    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)

    from src.infra.llm.prompt_manager import PromptManager

    build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=PromptManager()
    )

    payload = [c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED][0]
    assert payload["persona_source"] == "base"
    assert payload["discipline_injected"] is False
    assert payload["delegate_injected"] is True
```

- [ ] **Step 6: 运行确认失败**

Run: `pytest tests/rag/test_prompt_layers.py -q -k assembled`
Expected: FAIL（`AttributeError: module 'src.rag.prompt' has no attribute 'core_logging'`）

- [ ] **Step 7: 实现 prompt assembled**

`src/rag/prompt.py` 顶部补 import（放在既有 import 之后）：

```python
from src.core import logging as core_logging
from src.core.log_events import Event
```

（该模块此前不依赖日志，这是本 change 对它的唯一改动；若与 `src.core.logging` 形成循环 import，改用具名 import 并在 `build_system_prompt` 内延迟 import，并在提交信息里说明）

`build_system_prompt` 主体改为（仅新增布尔捕获与末尾一条日志，追加顺序与判定条件**逐字不变**）：

```python
    if persona:
        base = persona
        persona_source = "preset"
    else:
        base = prompt_manager.get_base_system_prompt()
        persona_source = "base"
    # 环境约束层·检索纪律：仅当绑定 KB 且选定 agent（persona 非空）时注入。
    # persona 为空时人设层即 FINANCIAL_SYSTEM_PROMPT，其处理流程 2–9 已含"先检索后作答"，
    # 无条件注入会破坏"默认行为逐字不变（端到端快照）"需求。
    discipline_injected = False
    if kb_bound and persona and KB_BOUND_RETRIEVAL_DISCIPLINE not in base:
        base += KB_BOUND_RETRIEVAL_DISCIPLINE
        discipline_injected = True
    if INLINE_CITATION_INSTRUCTION not in base:
        base += INLINE_CITATION_INSTRUCTION
    delegate_injected = False
    if (has_skills or not persona) and DELEGATE_GUIDANCE_SECTION not in base:
        base += DELEGATE_GUIDANCE_SECTION
        delegate_injected = True
    messages: list[SystemMessage] = [SystemMessage(content=_with_current_date(base))]
    if not kb_bound:
        messages.append(SystemMessage(content=KB_UNBOUND_SYSTEM_PROMPT))
    core_logging.log_event(
        Event.PROMPT_ASSEMBLED,
        persona_source=persona_source,
        kb_bound=kb_bound,
        has_skills=has_skills,
        discipline_injected=discipline_injected,
        delegate_injected=delegate_injected,
        system_msgs=len(messages),
    )
    return messages
```

- [ ] **Step 8: 运行确认通过**

Run: `pytest tests/rag/test_prompt_layers.py -q`
Expected: PASS（既有 7 例 + 新增 2 例）

- [ ] **Step 9: 写失败测试（prompt messages）**

在 `tests/agents/graph/test_agent_node.py` 追加：

```python
def test_prompt_messages_counts_three_segments(monkeypatch):
    """首轮组装后记录 system / 注入 / 历史三段条数（design D11 #4）。"""
    from src.agents.graph.agent_node import _initial_messages
    from src.core.log_events import Event
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr(
        "src.agents.graph.agent_node.core_logging.log_event", fake_log_event
    )
    ctx = RequestContext(session_id="s1")
    current_request_ctx.set(ctx)
    try:
        state = _make_state(
            query="营收多少",
            kb_id="",
            history=[
                ChatMessage(role="user", content="你好"),
                ChatMessage(role="assistant", content="你好"),
                ChatMessage(role="user", content=f"{SKILL_INJECTION_PREFIX}\n方法论"),
            ],
        )
        _initial_messages(state, _make_prompt_manager())
    finally:
        current_request_ctx.set(None)

    payload = [c for c in calls if c["event"] is Event.PROMPT_MESSAGES][0]
    assert payload["injected_msgs"] == 1
    assert payload["history_msgs"] == 2
    assert payload["system_msgs"] >= 1
```

（`_make_state` / `_make_prompt_manager` 用该文件既有构造方式；若已有等价 helper 直接复用）

- [ ] **Step 10: 运行确认失败**

Run: `pytest tests/agents/graph/test_agent_node.py -q -k prompt_messages`
Expected: FAIL（`IndexError`：未产出 `prompt messages`）

- [ ] **Step 11: 实现 prompt messages**

`src/agents/graph/agent_node.py` `_initial_messages` 末尾（插入注入消息之后）追加：

```python
    if injected:
        insert_at = 0
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage):
                insert_at = i + 1
        messages[insert_at:insert_at] = injected
    # 消息构成（design D11 #4）：system 段由 build_system_prompt 产出，注入段来自
    # SKILL_INJECTION_PREFIX 抽取，history 段为清洗后的普通历史（不含当前 query）
    core_logging.log_event(
        Event.PROMPT_MESSAGES,
        system_msgs=sum(1 for m in messages if isinstance(m, SystemMessage)),
        injected_msgs=len(injected),
        history_msgs=len(cleaned_normal),
    )
    return messages
```

- [ ] **Step 12: 运行确认通过**

Run: `pytest tests/agents/graph/test_agent_node.py -q`
Expected: PASS

- [ ] **Step 13: 实现 agent resolved / skill dispatch / skill injected**

`src/services/agent_service.py`：

(a) `agent resolved`——放在 `session_preset` 解析块之后（`persona_applied` 依赖它）：

```python
    # [session] 生效智能体与解析来源（design D11 #2）：请求与绑定都为空时不记，
    # 避免每轮噪声（log_event 级别由 EventSpec 固定，不能逐次降级为 debug）
    if agent or bound_raw:
        core_logging.log_event(
            Event.AGENT_RESOLVED,
            requested=agent,
            bound=bound_raw,
            effective=effective_agent,
            source=resolution.source,
            persona_applied=bool(ctx.persona),
        )
```

(b) `skill dispatch`——放在 `direct_skill` 确定之后（`launch_context` 赋值前后皆可，但须在 `parse_prefix` 之后）：

```python
    # [session] 命令形态分派结果（design D11 #6）：普通文本轮不记（每轮噪声）
    if parsed.kind != "plain":
        record_ctx = parsed.record
        context = "none"
        if record_ctx is not None:
            context = record_ctx.context
        core_logging.log_event(
            Event.SKILL_DISPATCH,
            kind=parsed.kind,
            skill=parsed.skill_name,
            context=context,
            direct_skill=direct_skill,
        )
```

(c) `skill injected`——两个注入点各一条：

```python
            # inline 注入（source=command）
            core_logging.log_event(
                Event.SKILL_INJECTED,
                skill=record.name,
                mode="inline",
                chars=len(injected_text),
                source="command",
            )
```

```python
        if preload_text:
            # 预设首轮预加载（source=preset）；多技能时 skill 取顿号连接名列表
            core_logging.log_event(
                Event.SKILL_INJECTED,
                skill="、".join(preload_names),
                mode="preload",
                chars=len(preload_text),
                source="preset",
            )
```

- [ ] **Step 14: 全量门禁**

Run: `pytest tests/ -q && ruff check . && pyright src/`
Expected: 测试全绿、ruff 无错、pyright 不新增 error

- [ ] **Step 15: 提交**

```bash
git add src/agents/graph/agent_node.py src/rag/prompt.py src/services/agent_service.py tests/agents/graph/test_agent_node.py tests/rag/test_prompt_layers.py
git commit -m "feat(logging): 补温度来源/智能体来源/prompt 组成与消息构成/技能注入与分派日志"
```

---

### Task 7: 前端历史回放重建模型标注

**Files:**
- Modify: `deploy/nginx/html/chat.html`（新增 `attachHistoryModelNote`；`loadSessionMessages` 调用点 `:3357` 附近）

**Interfaces:**
- Consumes: 历史消息对象的 `model_name` 字段（后端 `src/api/sessions.py:144` 已返回）、`escapeHtml`、`lastAiRow()`（均既有）
- Produces: `attachHistoryModelNote(row, modelName)`（无返回值）

- [ ] **Step 1: 加函数（放在 `attachHistoryCitations` 之后）**

```js
// 历史回放：用既有 model_name 列重建模型标注（design D16）。
// model_info 帧不入 process，故回放只能读列；is_fallback 未持久化，
// 回放不重建 fallback 标记（已知限制）。空/缺失（存量消息）直接跳过，
// 不补占位行——与来源声明的"缺则不渲染"口径一致。
function attachHistoryModelNote(row, modelName) {
  if (!row || !modelName) return;
  const div = document.createElement('div');
  div.className = 'model-note';
  div.innerHTML = `由 <b>${escapeHtml(modelName)}</b> 回答`;
  row.insertAdjacentElement('afterend', div);
}
```

- [ ] **Step 2: 接线**

在 `loadSessionMessages`（约 `:3332-3360`）里、与 `attachHistoryCitations(lastAiRow(), m.sources)`（约 `:3357`）同点之后追加：

```js
      attachHistoryModelNote(lastAiRow(), m.model_name);
```

- [ ] **Step 3: 手动校验**

切换到一个历史会话，确认：

1. 每个 AI 气泡行之后出现「由 {模型名} 回答」；
2. 与实时路径位置一致（气泡行之后、引用栏同级）；
3. `model_name` 为空的历史消息不出现空行、不报错；
4. 多个历史轮各自的标注落在各自气泡之后（不串轮）；
5. 浏览器控制台无新增 error。

- [ ] **Step 4: 提交**

```bash
git add deploy/nginx/html/chat.html
git commit -m "feat(ui): 历史回放用 model_name 列重建模型标注"
```

---

### Task 8: 文档同步

**Files:**
- Modify: `docs/agents/api_contract.md`
- Modify: `docs/agents/logging-rules.md`
- Modify: `docs/agents/data-flow.md`（如链路有变化；否则跳过并在提交信息说明）

- [ ] **Step 1: 登记接口契约**

`docs/agents/api_contract.md` 追加/更新：

- `status` 新增 `stage` 取值 `turn_agent` / `turn_skill`：每轮**至多各一条**，顺序为「`agent_used` → `turn_agent` → `turn_skill` → 首个节点状态行」，且**先于同轮的 ask_user/delegate**；
- 语义：声明**本轮触发**（意图），结果（fork 中断/降级、失败原因）由既有通道呈现，**声明不撤回不改写**；
- **文案不是结构化真源**：消费端取生效智能体必须用 `agent_used`，**不得**解析来源声明文本；前端顶栏继续走 `agent_used`（评审约束）；
- 条件不产出：未绑定智能体 → 无 `turn_agent`；本轮无技能动作/命令失败 → 无 `turn_skill`（不输出空行）；
- 历史消息 `model_name` 字段被前端回放消费（模型标注），空值不渲染。

- [ ] **Step 2: 登记日志事件**

`docs/agents/logging-rules.md` 前缀主表/事件登记处追加：

- 新事件：`agent resolved`（session）、`prompt assembled`（llm）、`prompt messages`（agent）、`skill injected`（session）、`skill dispatch`（session）；`model turn` 扩 `temperature`/`temp_source`/`kb_bound`；
- 说明**噪声控制用调用点守卫**：`agent resolved` 在请求与绑定都为空时不记、`skill dispatch` 在 `kind=plain` 时不记——因 `EventSpec.level` 只允许 `info/warning/error` 且**调用点不能逐次降级**，故不用 debug；
- 说明**不再扩 `iteration done`**（消息构成由 `prompt messages` 在组装点承载），避免后人再提。

- [ ] **Step 3: 数据流（如需）**

若 `docs/agents/data-flow.md` 记录了 SSE 事件链路或 prompt 组装链路，补上两条来源声明与 `prompt messages` 的位置；若无相关章节则跳过并在提交信息写明"无链路变化，跳过"。

- [ ] **Step 4: 文档门禁**

Run: `python -m src.cli.check_docs`
Expected: 0 error

- [ ] **Step 5: 提交**

```bash
git add docs/agents/api_contract.md docs/agents/logging-rules.md docs/agents/data-flow.md
git commit -m "docs: 登记来源声明流契约、5 个新日志事件与噪声控制口径"
```

---

### Task 9: 门禁与实机验证

**Files:** 无代码改动（仅验证与必要的修复）

- [ ] **Step 1: 全量门禁**

Run: `pytest tests/ -q`
Expected: 全绿（0 failed）

Run: `ruff check .`
Expected: 无错误

Run: `pyright src/`
Expected: 不新增 error（存量第三方误报不计）

Run: `python -m src.cli.check_docs`
Expected: 0 error

- [ ] **Step 2: 启动活服务并登录**

```bash
docker compose restart app
```

浏览器（playwright-cli）打开聊天页，按既有方式登录（`/api/auth/login` 自动注册任意账号口令）。

- [ ] **Step 3: 实机验证——来源声明与顺序**

在**新对话**页选「财务专家」，发一轮普通问题。
Expected: 过程区**最前**出现两条状态行——`当前使用了 财务专家` → 该轮技能声明（若预设声明了 skills），且都在"正在思考..."之前。

- [ ] **Step 4: 实机验证——inline 技能声明的名单与标点**

输入 `/finance-qa 任务` 发送（技能名以 `skills/` 目录实际存在者为替换）。
Expected: 一条 `成功加载 skills：<顿号连接的名单>`；无空名单、无重复行。

- [ ] **Step 5: 实机验证——刷新/切会话回放**

刷新页面，再切换到另一个会话后切回。
Expected: 两条来源声明**与模型标注**都重建；标注位置在 AI 气泡行之后、与引用栏同级。

- [ ] **Step 6: 实机验证——fork 轮措辞**

发一轮 `/finance-analyst 任务`（或任一 `context: fork` 技能）。
Expected: 技能声明为 `使用技能：/<name>（子代理执行）`；随后出现委派折叠区。

- [ ] **Step 7: 实机验证——空模型名与未知 stage 容忍**

- 找到一条 `model_name` 为空的存量/异常历史消息（或临时构造），Expected: 不渲染空标注行、不报错；
- Expected: 两条来源声明作为普通状态行渲染、**不改变正文/旁白判定**（正文仍只出现在气泡里），顶栏仍显示生效智能体；
- 浏览器控制台 Expected: 无新增 error。

- [ ] **Step 8: 真实 trace 复核**

用一轮真实请求的 trace_id 检索容器日志：

```bash
docker compose exec app sh -c 'grep "<trace_id>" /data/logs/app_$(date +%F).log'
```

Expected: 能从日志直接读出

- 每次 LLM 调用的 `temperature` + `temp_source` + `kb_bound`；
- `agent resolved`（requested/bound/effective/source/persona_applied）；
- `prompt assembled`（persona_source/条件注入/system_msgs）与 `prompt messages`（三段条数）；
- `skill injected`（skill/mode/chars/source）与 `skill dispatch`（kind/skill/context/direct_skill，且普通文本轮无该行）。

- [ ] **Step 9: 收尾**

若 Step 2-8 中有环境不可用导致降级，在 `docs/agents/cookbook.md` 按协议登记遗留验证步骤；否则不新增条目。

```bash
git status --short   # 确认无遗留未提交改动
```

---

## Self-Review（计划自检）

- **Spec 覆盖**：`turn-provenance` R1/R2/R3/R4 → Task 4/5；`streaming-run` R1 → Task 5；`chat-harness-ui` R1 → Task 5（前端零改动，回放分支既有）、R2(MODIFIED 消息流样式) → Task 7；`agent-loop-observability` R1/R2/R3 → Task 6；契约文档 → Task 8；实机 → Task 9。**无未覆盖 Requirement。**
- **`tasks.md` 覆盖**：1.1-1.4→T1；2.1-2.5→T2/T3/T4；3.1-3.3→T5；4.1-4.7→T6；5.1-5.7→T2/T3/T4/T5/T6（5.6 后端用例落在 T5 Step 5 的 `tests/chat/test_process_log.py`）；6.1-6.3→T7；7.1-7.4→T8；8.1-8.3→T9。
- **类型一致**：`AgentResolution.effective/source`、`_preload_*(...) -> tuple[str, list[str]]`、`RequestContext.skill_action/loaded_skills/agent_display_name`、`Event.PROMPT_MESSAGES` 在跨任务引用处名称一致。
- **已知残余**：`is_fallback` 不回放（Task 7 注释 + spec 场景登记）；`skill injected` 在 preload 多技能时 `skill` 为顿号连接串（Task 6 Step 13 注释说明）。
