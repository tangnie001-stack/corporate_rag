# 智能体/技能契约层 Implementation Plan（Plan 1 / 4）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 SKILL.md / agent preset 的磁盘契约、双轴调用控制与两个注册表改造成主流形态，并保持既有行为不漂移。

**Architecture:** 纯契约层改造：`SkillRecord`/`SkillLoader`/`SkillRegistry` 三点同步演进（删私有字段、加双轴与 `agent`、`agent_prompt`→`fork_body`、`allowed-tools` 改逗号分隔、名称限 ASCII slug）；新增 `src/agents/presets/` 三个文件（模型 / 加载器 / 注册表）承载智能体预设；`ToolEntry` 增加 `readonly` 作为双轴默认推导的事实来源。**不改图**；对 `SkillExecutor` 只做"字段被删导致的最小适配"（改名取用 + 复用既有默认常量），**不放开工具、不换 `create_agent`**——那属于 Plan 2。

**Tech Stack:** Python 3.11+ / dataclass / PyYAML / pytest / ruff / pyright；无需新增依赖。

**Spec:** `docs/openspec/changes/session-agent-and-skill-invocation/`（proposal.md / design.md / specs/ / tasks.md）

## Global Constraints

- 注释与文档一律**中文**；每个函数必须写 docstring；每个 dataclass 字段必须加**行内注释**（说明来源、范围、用途）。
- **不用三元表达式**（`a if cond else b`），写完整 `if/else` 结构。
- **类型不确定的值不用 `getattr(x, "attr", default)` 隐式兜底**，用 `x.attr if x is not None else default` 或 `isinstance` 显式判断。
- 常量/阈值/文案**不得散落在业务代码**：环境变量与可配置阈值 → `src/config/settings.py`；LLM 提示词 → `src/config/prompts.py`；事件/节点常量/固定阈值/用户可见文案 → `src/config/const.py`（用户可见文案统一放 `SSEInteractionTexts` class）。
- 单文件 < 400 行；单函数 < 80 行。
- 日志：事件消息**英文 k=v** + `[层名]` 前缀（本计划用 `[agent]` 或 `[delegate]`）；新增事件必须先登记 `src/core/log_events.py` 的 `Event` 枚举与 `EVENT_SPECS`（`docs/agents/logging-rules.md` 是格式唯一归属，事件不抄录）。
- 测试 **mock 外部依赖**，不发起真实网络调用。
- 质量门禁：`pytest tests/ -v` 全绿、`ruff format . && ruff check . --fix` 无错、`pyright src/` 不新增 error。
- **部署形态单 worker**（不假设多 worker）。
- 契约变更须同步：`docs/agents/api_contract.md`（仅公共方法签名/响应结构）、受影响测试断言（`tests/` 里的硬编码结构）。

## 前置阻塞项（已关闭）

以下问题在本次评审中发现，**已回填进 change**，不阻塞本计划：

- **P1**：D22「fork 直出、主 agent 0 LLM 轮」缺**图入口分派** → 已落为 **design D26** + tasks 4.10、4.11 + `agent-service` spec 4 个 Scenario。
- **P2**：直出路径下 `verify` 三处依赖失效（年份完整性读主 ctx → 静默跳过；两条引用护栏读主 `ctx.tool_contexts` → 静默跳过；重生成路由回主 agent 而非子代理）→ 同上 D26 覆盖。
- **P4**：`DEFAULT_SUBAGENT_MAX_TURNS` 与既有 `DELEGATE_DEFAULT_MAX_TURNS`（`src/config/const.py:89`）重复 → 已改为**复用既有常量**（tasks 4.4 / design 已结案行）。

**Plan 1 不受它们影响**：本计划只改契约层与注册表，不涉及图入口、verify 判据与常量新增。

---

### Task 1: `SkillRecord` 契约改造

**Files:**
- Modify: `src/agents/skills/models.py`
- Modify: `src/agents/skills/executor.py`（字段被删导致的最小适配：`agent_prompt`→`fork_body`、去掉 `thinking`/`max_iterations` 引用）
- Test: `tests/agents/skills/test_skill_models.py`
- Test: `tests/agents/skills/test_skill_executor.py`（若断言了被删字段）

**Interfaces:**
- Consumes: 无
- Produces: `SkillRecord` 新字段集 —— `name: str` / `description: str` / `context: str` / `inline_prompt: str | None` / `fork_body: str | None` / `agent: str | None` / `model: str | None` / `allowed_tools: list[str]` / `user_invocable: bool` / `disable_model_invocation: bool` / `source_path: Path`（**删除** `thinking`、`max_iterations`；`agent_prompt` **改名** `fork_body`）

- [ ] **Step 1: 写失败测试**

在 `tests/agents/skills/test_skill_models.py` 末尾追加：

```python
def test_skill_record_has_dual_axis_and_agent_fields():
    """SkillRecord 含双轴与 agent 字段，且不再有 thinking/max_iterations/agent_prompt。"""
    import dataclasses

    from src.agents.skills.models import SkillRecord

    field_names = {f.name for f in dataclasses.fields(SkillRecord)}
    assert "user_invocable" in field_names
    assert "disable_model_invocation" in field_names
    assert "agent" in field_names
    assert "fork_body" in field_names
    assert "thinking" not in field_names
    assert "max_iterations" not in field_names
    assert "agent_prompt" not in field_names


def test_skill_record_dual_axis_defaults_are_open():
    """未显式赋值时双轴默认开放（推导在 loader 层覆写）。"""
    from src.agents.skills.models import SkillRecord

    record = SkillRecord(name="finance-qa", description="财务问答")
    assert record.user_invocable is True
    assert record.disable_model_invocation is False
    assert record.fork_body is None
    assert record.agent is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_skill_models.py -v`
Expected: FAIL —— `assert "user_invocable" in field_names` 失败（字段尚不存在）

- [ ] **Step 3: 改 `SkillRecord`（最小实现）**

`src/agents/skills/models.py` 全文替换为：

```python
"""Skill 内容模型 — SkillRecord（skill 文件解析后的运行时对象）。

skill = 磁盘声明式能力文件（skills/<name>/SKILL.md）：frontmatter 声明元数据、
正文按 context 语义存为 inline_prompt（主 agent 注入执行）或 fork_body
（fork 子代理的 user message 任务内容）。skill 是内容/配置而非代码。
"""

from dataclasses import dataclass, field
from pathlib import Path


class SkillContext:
    """skill 执行上下文常量（frontmatter context 字段取值）。

    分工：inline = 指令注入主 agent 上下文、主 agent 自己执行；
    fork = 生成独立子代理（create_agent）隔离执行。
    """

    INLINE: str = "inline"  # inline 执行形态
    FORK: str = "fork"  # fork 执行形态


@dataclass
class SkillRecord:
    """单个 skill 的运行时对象（由 SkillLoader 解析 SKILL.md 产出）。

    Attributes:
        name: skill 名（ASCII slug，frontmatter name 或目录名；LLM 委派匹配依据）
        description: whenToUse 一句话描述（委派匹配与前端展示依据）
        context: 执行形态（inline|fork，非法值回落 inline）
        inline_prompt: context=inline 时正文（短方法论，≤500 字，可含 $ARGUMENTS）
        fork_body: context=fork 时正文（子代理的 user message 任务内容）
        agent: fork 执行者预设名（空=None 时按会话选定智能体或系统默认）
        model: fork 覆盖模型（空=None 继承主 agent llm）
        allowed_tools: fork 子代理工具白名单（逗号分隔字符串解析而来）
        user_invocable: 是否允许用户 `/xxx` 调用（frontmatter 显式值或按工具只读性推导）
        disable_model_invocation: 是否禁止模型自动 delegate_task 调用（同上）
        source_path: SKILL.md 文件绝对路径（懒重载 signature 用）
    """

    name: str  # skill 名（ASCII slug）
    description: str  # whenToUse 一句话描述
    context: str = SkillContext.INLINE  # inline|fork
    inline_prompt: str | None = None  # context=inline 时正文（短方法论）
    fork_body: str | None = None  # context=fork 时正文（子代理 user message）
    agent: str | None = None  # fork 执行者预设名，None=按会话选定智能体
    model: str | None = None  # fork 覆盖模型，None=继承主 agent llm
    allowed_tools: list[str] = field(
        default_factory=list
    )  # fork 子代理工具白名单（逗号分隔字符串解析）
    user_invocable: bool = True  # 允许用户 /xxx 调用
    disable_model_invocation: bool = False  # 禁止模型自动调用
    source_path: Path = field(default_factory=Path)  # SKILL.md 绝对路径
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_skill_models.py -v`
Expected: PASS（本文件全部用例）

- [ ] **Step 5: 适配执行器的字段引用（不放开工具、不换框架）**

Task 1 删字段会让 `SkillExecutor` 引用到不存在的属性（**仓库会 import/运行时报错**），故本步做最小适配，保证树保持绿色：

`src/agents/skills/executor.py:127`：

```python
        max_turns = DELEGATE_DEFAULT_MAX_TURNS
```

（原为 `record.max_iterations or DELEGATE_DEFAULT_MAX_TURNS`；`max_iterations` 已删，执行者轮次后续由 `AgentPreset.max_turns` 提供——Plan 2 接手。）

`src/agents/skills/executor.py` 中 `create_react_agent(llm, tools=[], prompt=record.agent_prompt)`：

```python
        sub_agent = create_react_agent(
            llm,
            tools=[],  # 零工具硬保证（design D7）：防递归 + 不污染主 ctx
            prompt=record.fork_body,
        )
```

（`agent_prompt` → `fork_body`；**本轮仍保持零工具与 `create_react_agent`**，框架替换是 Plan 2。）

`_resolve_fork_llm` 中对 `record.thinking` 的引用：删掉该分支，思考一律跟随 `ctx.deep_thinking`（与 tasks 4.4 同向，但因字段删除必须现在做）。

- [ ] **Step 6: 跑全量测试确认树仍绿**

Run: `pytest tests/ -v`
Expected: PASS（若 `tests/agents/skills/test_skill_executor.py` 断言了 `thinking` / `max_iterations` / `agent_prompt`，一并改为新行为断言）

- [ ] **Step 7: 提交**

```bash
git add src/agents/skills/models.py src/agents/skills/executor.py tests/agents/skills/test_skill_models.py tests/agents/skills/test_skill_executor.py
git commit -m "refactor(skills): SkillRecord 删 thinking/max_iterations、agent_prompt 改名 fork_body、加双轴与 agent（含 executor 最小适配）"
```

---

### Task 2: `SkillLoader` 解析改造（ASCII 名称 / 逗号分隔 allowed-tools / 废弃字段告警）

**Files:**
- Modify: `src/agents/skills/loader.py`
- Modify: `src/config/const.py`（新增名称正则常量）
- Test: `tests/agents/skills/test_skill_loader.py`

**Interfaces:**
- Consumes: Task 1 的 `SkillRecord`（`fork_body` / `agent` / `allowed_tools: list[str]`）
- Produces:
  - `src/config/const.py`: `CAPABILITY_NAME_PATTERN: re.Pattern[str]`（`^[A-Za-z0-9][A-Za-z0-9_-]*$`）
  - `SkillLoader(skills_root: Path, tool_readonly: dict[str, bool] | None = None)`
  - `SkillLoader.load_all() -> list[SkillRecord]`（名称非法 / 解析失败 → 记 warning 并跳过）

- [ ] **Step 1: 写失败测试**

在 `tests/agents/skills/test_skill_loader.py` 末尾追加：

```python
def test_allowed_tools_comma_separated_string_is_split(tmp_path):
    """allowed-tools 用逗号分隔字符串书写，内部转 list。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "finance-qa"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: finance-qa\n"
        "description: 财务问答\n"
        "allowed-tools: retrieve_kb, search_web\n"
        "---\n"
        "先检索后答。\n",
        encoding="utf-8",
    )

    records = SkillLoader(tmp_path).load_all()

    assert len(records) == 1
    assert records[0].allowed_tools == ["retrieve_kb", "search_web"]


def test_non_ascii_name_is_skipped_with_warning(tmp_path):
    """非 ASCII slug 名称记 warning 并跳过（不注册）。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "财报分析"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 财报分析\ndescription: 中文名\n---\n正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="非法"):
        records = SkillLoader(tmp_path).load_all()

    assert records == []


def test_deprecated_fields_are_ignored_with_warning(tmp_path):
    """thinking / max-iterations 不再被识别，忽略并记 warning，不写入记录。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "legacy-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: legacy-skill\n"
        "description: 遗留字段\n"
        "thinking: true\n"
        "max-iterations: 3\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="已废弃"):
        records = SkillLoader(tmp_path).load_all()

    assert len(records) == 1
    assert records[0].name == "legacy-skill"


def test_agent_field_is_parsed(tmp_path):
    """frontmatter agent 字段解析为 fork 执行者名。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "deep-analysis"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deep-analysis\n"
        "description: 深度分析\n"
        "context: fork\n"
        "agent: finance-expert\n"
        "---\n"
        "任务：$ARGUMENTS\n",
        encoding="utf-8",
    )

    records = SkillLoader(tmp_path).load_all()

    assert records[0].agent == "finance-expert"
    assert records[0].fork_body is not None


def test_explicit_dual_axis_fields_are_parsed(tmp_path):
    """显式声明 user-invocable false 时原样写入记录。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "report-publish"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: report-publish\n"
        "description: 发布报告\n"
        "user-invocable: false\n"
        "disable-model-invocation: true\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    records = SkillLoader(tmp_path).load_all()

    assert records[0].user_invocable is False
    assert records[0].disable_model_invocation is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_skill_loader.py -v -k "comma or non_ascii or deprecated or agent_field or dual_axis"`
Expected: FAIL —— `allowed_tools == ["retrieve_kb", "search_web"]` 失败（当前对字符串返回 `[]`），`fork_body` AttributeError

- [ ] **Step 3: 新增名称正则常量**

`src/config/const.py` 顶部若未 import `re` 则加 `import re`，并在 `INLINE_PROMPT_MAX_CHARS` 之后追加：

```python
# skill / agent preset 名允许的字符集（ASCII slug）：/xxx 命令天然是 ASCII 惯例，
# 非 ASCII 名会让 `/财报分析` 落进"非命令形态"分支被静默当普通文本（design D15）
CAPABILITY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DEPRECATED_SKILL_FIELDS = (
    "thinking",
    "max-iterations",
)  # 已废弃的 skill frontmatter 字段（读到时忽略并记 warning）
```

- [ ] **Step 4: 改 `SkillLoader`（最小实现）**

`src/agents/skills/loader.py` 全文替换为：

```python
"""SkillLoader — 扫描 skills/<name>/SKILL.md 并解析 frontmatter + 正文。

SKILL.md 结构：YAML frontmatter（--- 包裹）+ 正文。frontmatter 字段：
name/description/context/model/allowed-tools/agent/user-invocable/disable-model-invocation。
解析规则：
- name 缺省用目录名；description 缺省用正文首段
- name 必须是 ASCII slug（CAPABILITY_NAME_PATTERN），否则记 warning 并跳过该 skill
- context 非法值回落 inline 并记 warning（fail-open，不阻塞加载）
- allowed-tools 用逗号分隔字符串书写，内部转 list
- 正文按 context 存 inline_prompt（inline）或 fork_body（fork）
- thinking / max-iterations 已废弃：忽略并记 warning
- 只扫一层 skills/<name>/SKILL.md，不递归（目录即 skill 边界）
"""

import warnings
from pathlib import Path

import yaml

from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import DEPRECATED_SKILL_FIELDS, CAPABILITY_NAME_PATTERN


class SkillLoader:
    """从 skills_root 扫描并解析全部 SKILL.md 为 SkillRecord。"""

    def __init__(self, skills_root: Path) -> None:
        """初始化加载器。

        Args:
            skills_root: skills 内容库根目录（含 <name>/SKILL.md 子目录）
        """
        self.skills_root = skills_root

    def load_all(self) -> list[SkillRecord]:
        """扫描 skills_root 下全部 skill，解析为 SkillRecord 列表。

        Returns:
            解析成功的 SkillRecord 列表；目录不存在或为空时返回空列表。
            单文件解析失败（YAML 非法 / 字段类型不符 / 名称非法 / 读取 IO 错误）
            记 warning 并跳过（fail-open，坏 skill 不拖垮整体）。
        """
        records: list[SkillRecord] = []
        if not self.skills_root.exists():
            return records
        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            path = skill_dir / "SKILL.md"
            if not path.exists():
                continue
            try:
                records.append(self._parse(path, skill_dir.name))
            except (yaml.YAMLError, ValueError, OSError) as exc:
                warnings.warn(f"skill {skill_dir.name} 解析失败，已跳过: {exc}")
        return records

    def _parse(self, path: Path, fallback_name: str) -> SkillRecord:
        """解析单个 SKILL.md。

        Args:
            path: SKILL.md 文件路径
            fallback_name: frontmatter 缺 name 时用的目录名

        Returns:
            SkillRecord（source_path 为 path 绝对路径）

        Raises:
            yaml.YAMLError: frontmatter YAML 无法解析
            ValueError: frontmatter 非 dict / 字段类型不符 / 名称为空或非 ASCII slug
            OSError: 读取 SKILL.md 文件失败（读错误由 load_all 捕获并跳过）
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        self._warn_deprecated_fields(meta)
        name = self._resolve_name(meta, fallback_name)
        description = self._resolve_description(meta, body)
        context = self._resolve_context(meta, name)
        inline_prompt, fork_body = self._resolve_body(context, body)
        return SkillRecord(
            name=name,
            description=description,
            context=context,
            inline_prompt=inline_prompt,
            fork_body=fork_body,
            agent=self._resolve_optional_str(meta, "agent"),
            model=self._resolve_optional_str(meta, "model"),
            allowed_tools=self._resolve_allowed_tools(meta),
            user_invocable=self._resolve_bool(meta, "user-invocable", True),
            disable_model_invocation=self._resolve_bool(
                meta, "disable-model-invocation", False
            ),
            source_path=path.resolve(),
        )

    def _resolve_name(self, meta: dict, fallback_name: str) -> str:
        """解析并校验 skill 名（ASCII slug 约束）。"""
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            name = fallback_name
        if not CAPABILITY_NAME_PATTERN.match(name):
            raise ValueError(
                f"名称非法（仅允许 ASCII slug：字母数字/下划线/连字符，且不以连字符开头）: {name!r}"
            )
        return name

    def _warn_deprecated_fields(self, meta: dict) -> None:
        """读到时忽略已废弃字段并记 warning（不写入记录）。"""
        for key in DEPRECATED_SKILL_FIELDS:
            if key in meta:
                warnings.warn(
                    f"skill frontmatter 的 {key} 已废弃，已忽略（迭代上限改由执行者 maxTurns 控制）"
                )

    def _resolve_description(self, meta: dict, body: str) -> str:
        """description 缺省时用正文首段兜底。"""
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            return self._first_paragraph(body)
        return description

    def _resolve_context(self, meta: dict, name: str) -> str:
        """context 非法值回落 inline 并记 warning。"""
        context = meta.get("context", SkillContext.INLINE)
        if context not in (SkillContext.INLINE, SkillContext.FORK):
            warnings.warn(f"skill {name} context 非法值 {context!r}，回落 inline")
            return SkillContext.INLINE
        return context

    def _resolve_body(self, context: str, body: str) -> tuple[str | None, str | None]:
        """按 context 把正文落到 inline_prompt 或 fork_body。"""
        if context == SkillContext.INLINE:
            return body, None
        return None, body

    def _resolve_optional_str(self, meta: dict, key: str) -> str | None:
        """解析可选的字符串字段（非字符串或空串视为未声明）。"""
        value = meta.get(key)
        if not isinstance(value, str) or not value:
            return None
        return value

    def _resolve_bool(self, meta: dict, key: str, default: bool) -> bool:
        """解析可选的布尔字段（非布尔视为未声明，回落 default）。"""
        value = meta.get(key)
        if not isinstance(value, bool):
            return default
        return value

    def _resolve_allowed_tools(self, meta: dict) -> list[str]:
        """解析 allowed-tools：支持逗号分隔字符串（主流写法）与 YAML 列表（兼容）。"""
        value = meta.get("allowed-tools")
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            items = [str(part).strip() for part in value]
        else:
            items = []
        return [item for item in items if item]

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 SKILL.md 拆成 (frontmatter dict, 正文)。

        Args:
            raw: 文件全文

        Returns:
            (frontmatter dict, 正文 str)；无 frontmatter 时返回 (空 dict, 全文)

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
            ValueError: frontmatter 不是 mapping
        """
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                meta = yaml.safe_load("\n".join(lines[1:i]))
                body = "\n".join(lines[i + 1 :]).strip()
                if meta is None:
                    return {}, body
                if not isinstance(meta, dict):
                    raise ValueError("frontmatter 必须是 mapping")
                return meta, body
        # 没有闭合 ---：按无 frontmatter 处理（正文全文）
        return {}, raw

    def _first_paragraph(self, body: str) -> str:
        """取正文首段作 description 兜底（按空行切分取第一段，截断到 200 字）。"""
        if not body:
            return ""
        paragraph = body.split("\n\n")[0].replace("\n", " ").strip()
        return paragraph[:200]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/agents/skills/test_skill_loader.py -v`
Expected: PASS（含既有用例；若既有用例断言 `agent_prompt` / `thinking` / `max_iterations`，在本步一并改为新字段名——它们已被 Task 1 改名）

- [ ] **Step 6: 提交**

```bash
git add src/agents/skills/loader.py src/config/const.py tests/agents/skills/test_skill_loader.py
git commit -m "feat(skills): loader 支持逗号分隔 allowed-tools、agent 字段与 ASCII 名称护栏，废弃字段告警"
```

---

### Task 3: `ToolEntry.readonly`（双轴推导的事实来源）

**Files:**
- Create: `src/agents/tools/readonly.py`（工具只读声明的**单一事实来源**）
- Modify: `src/agents/tools/registry.py`
- Modify: `src/agents/tools/rag_tools.py`（注册点显式声明）
- Test: `tests/agents/tools/test_registry.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `src/agents/tools/readonly.py`: `declare_readonly(name: str, readonly: bool) -> None` / `readonly_map() -> dict[str, bool]`
  - `ToolEntry.readonly: bool`；`ToolRegistry.register(name, fn, deps=None, enabled=True, readonly=True)`；`ToolRegistry.readonly_map() -> dict[str, bool]`

> **为什么要有 `readonly.py`**：工具只读性的事实写在**注册点**（`rag_tools.py`），但 skill 加载发生在 `AgentService.__init__`，那里**没有 `ToolRegistry` 实例**（工具在 `build_graph → make_rag_tools` 内部才构造）。若把只读表另抄一份到 `config`，就制造了双事实来源（与本 change D19 取消 catalog 的理由同源）。故用一个进程级声明表做单一来源：注册点 `declare_readonly(...)`，消费方 `readonly_map()`。该模式与项目既有的进程级 `pending_asks` 注册表一致。

- [ ] **Step 1: 写失败测试**

在 `tests/agents/tools/test_registry.py` 末尾追加：

```python
def test_tool_entry_readonly_defaults_true():
    """未显式声明时 readonly 默认 True（现有工具均只读）。"""
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("retrieve_kb", lambda: None)

    assert registry.readonly_map() == {"retrieve_kb": True}


def test_tool_entry_readonly_explicit_false():
    """写类工具显式声明 readonly=False。"""
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("publish_report", lambda: None, readonly=False)

    assert registry.readonly_map() == {"publish_report": False}


def test_register_declares_readonly_into_single_source():
    """注册时把只读性写入进程级声明表（供 skill 加载侧读取）。"""
    from src.agents.tools.readonly import readonly_map
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("publish_report", lambda: None, readonly=False)

    assert readonly_map()["publish_report"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/tools/test_registry.py -v -k readonly`
Expected: FAIL —— `AttributeError: 'ToolRegistry' object has no attribute 'readonly_map'`（`ModuleNotFoundError: src.agents.tools.readonly` 亦可）

- [ ] **Step 3: 新建声明表**

新建 `src/agents/tools/readonly.py`：

```python
"""工具只读声明的单一事实来源（进程级）。

工具只读性的事实写在**注册点**（rag_tools/web_tools 等），而 skill 加载发生在
AgentService 构造期——那时工具尚未构造（工具在 build_graph → make_rag_tools
内部创建），拿不到 ToolRegistry 实例。故用进程级声明表衔接两侧：
注册点 declare_readonly(...)，消费方（skill 双轴推导）readonly_map()。

与项目既有的进程级 pending_asks 注册表同构（进程内共享、启动期写入、运行期只读）。
"""

_TOOL_READONLY: dict[str, bool] = {}


def declare_readonly(name: str, readonly: bool) -> None:
    """登记工具只读性（由 ToolRegistry.register 转发调用）。

    Args:
        name: 工具名
        readonly: True=只读无外部副作用；False=写/改/删/发/外部调用
    """
    _TOOL_READONLY[name] = readonly


def readonly_map() -> dict[str, bool]:
    """返回 工具名 -> readonly 的映射副本（供 skill 双轴默认推导）。

    返回副本而非内部引用，避免调用方误改单一来源。
    """
    return dict(_TOOL_READONLY)
```

- [ ] **Step 4: 实现 `readonly` 并转发声明**

`src/agents/tools/registry.py`：给 `ToolEntry` 加字段、改 `register`、加 `readonly_map`：

```python
@dataclass
class ToolEntry:
    """注册表条目。

    fn: 可调用工具（LangChain tool 或装饰器产物）
    deps: 依赖注入 dict（工具闭包需要的外部依赖）
    enabled: 是否启用（停用不出现在 LLM 可见列表）
    readonly: 是否只读无外部副作用（skill 双轴默认推导的事实来源，True=只读）
    """

    fn: Any
    deps: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    readonly: bool = True
```

```python
    def register(
        self,
        name: str,
        fn: Any,
        deps: dict | None = None,
        enabled: bool = True,
        readonly: bool = True,
    ) -> None:
        """注册一个工具（同时把只读性写入进程级声明表）。

        Args:
            name: 工具名（LLM 可见）
            fn: 工具可调用对象
            deps: 依赖注入 dict
            enabled: 初始是否启用
            readonly: 是否只读无外部副作用（写/改/删/发/外部调用应传 False）
        """
        self._entries[name] = ToolEntry(
            fn=fn, deps=deps or {}, enabled=enabled, readonly=readonly
        )
        declare_readonly(name, readonly)

    def readonly_map(self) -> dict[str, bool]:
        """返回本注册表内 工具名 -> readonly 映射。"""
        return {name: entry.readonly for name, entry in self._entries.items()}
```

文件头 import 加 `from src.agents.tools.readonly import declare_readonly`。

`src/agents/tools/rag_tools.py` 的注册块显式声明只读（把事实写在注册点）：

```python
    registry.register("retrieve_kb", retrieve_kb, readonly=True)
    registry.register("ask_user", ask_user, readonly=True)
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web

        registry.register("search_web", search_web, readonly=True)
    if delegate_task is not None:
        registry.register("delegate_task", delegate_task, readonly=True)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/agents/tools/ -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agents/tools/readonly.py src/agents/tools/registry.py src/agents/tools/rag_tools.py tests/agents/tools/test_registry.py
git commit -m "feat(tools): ToolEntry 增加 readonly，注册点声明写入进程级单一来源"
```

---

### Task 4: 双轴默认推导（fail-safe）+ 加载期护栏

**Files:**
- Create: `src/agents/skills/invocation.py`
- Modify: `src/agents/skills/loader.py`（`tool_readonly` 缺省读进程级声明表，推导并覆写双轴）
- Test: `tests/agents/skills/test_skill_invocation.py`

**Interfaces:**
- Consumes: Task 3 的 `src/agents/tools/readonly.py: readonly_map()`；Task 2 的 `SkillLoader(skills_root)` 与 `SkillRecord.allowed_tools`
- Produces:
  - `src/agents/skills/invocation.py`: `derive_invocation_flags(allowed_tools: list[str], tool_readonly: dict[str, bool]) -> tuple[bool, bool]`（返回 `(user_invocable, disable_model_invocation)`）
  - `SkillLoader(skills_root: Path, tool_readonly: dict[str, bool] | None = None)`（None → 读 `readonly_map()`）

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/skills/test_skill_invocation.py`：

```python
"""双轴调用控制默认推导测试（fail-safe：含写类工具默认锁模型端）。"""

import pytest

from src.agents.skills.invocation import derive_invocation_flags


def test_empty_allowed_tools_is_dual_open():
    """allowed-tools 为空 → 双通道开放。"""
    assert derive_invocation_flags([], {"retrieve_kb": True}) == (True, False)


def test_all_readonly_is_dual_open():
    """全部只读工具 → 双通道开放。"""
    flags = derive_invocation_flags(["retrieve_kb", "search_web"], {"retrieve_kb": True, "search_web": True})
    assert flags == (True, False)


def test_any_write_tool_locks_model_side():
    """含任一非只读工具 → 默认关闭模型自动调用（user 仍可显式调用）。"""
    flags = derive_invocation_flags(
        ["retrieve_kb", "publish_report"],
        {"retrieve_kb": True, "publish_report": False},
    )
    assert flags == (True, True)


def test_unknown_tool_is_treated_as_write():
    """allowed-tools 引用了未注册工具（表非空）→ 按写类处理（fail-safe，防拼错放开）。"""
    flags = derive_invocation_flags(["ghost_tool"], {"retrieve_kb": True})
    assert flags == (True, True)


def test_empty_readonly_table_falls_open():
    """工具只读表为空（尚未注册）→ 无法判断，fail-open 不锁模型端。

    否则"加载早于工具注册"这一时序会让所有带 allowed-tools 的 skill 被静默锁死。
    """
    flags = derive_invocation_flags(["retrieve_kb"], {})
    assert flags == (True, False)


def test_loader_applies_derived_flags_and_warns(tmp_path):
    """loader 会覆写未显式声明的双轴，并对含写类未声明记 warning。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "report-publish"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: report-publish\n"
        "description: 发布报告\n"
        "allowed-tools: retrieve_kb, publish_report\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="disable-model-invocation"):
        records = SkillLoader(
            tmp_path, tool_readonly={"retrieve_kb": True, "publish_report": False}
        ).load_all()

    assert records[0].user_invocable is True
    assert records[0].disable_model_invocation is True


def test_loader_keeps_explicit_flags(tmp_path):
    """显式声明的双轴不被推导覆写。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "readonly-report"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: readonly-report\n"
        "description: 只读报告\n"
        "allowed-tools: retrieve_kb, publish_report\n"
        "disable-model-invocation: false\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    records = SkillLoader(
        tmp_path, tool_readonly={"retrieve_kb": True, "publish_report": False}
    ).load_all()

    assert records[0].disable_model_invocation is False


def test_dead_skill_warns(tmp_path):
    """user-invocable false 且 disable-model-invocation true → 死 skill 记 warning。"""
    from src.agents.skills.loader import SkillLoader

    skill_dir = tmp_path / "dead-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: dead-skill\n"
        "description: 谁都用不了\n"
        "user-invocable: false\n"
        "disable-model-invocation: true\n"
        "---\n"
        "正文\n",
        encoding="utf-8",
    )

    with pytest.warns(UserWarning, match="既不可用户调用也不可模型调用"):
        records = SkillLoader(tmp_path, tool_readonly={}).load_all()

    assert len(records) == 1
```

> **单测约定**：契约层单测一律**显式注入** `tool_readonly`（含用 `{}` 表示"表未就绪"），不依赖进程级声明表——否则用例结果会随同会话其它测试的注册顺序而变（Task 3 的进程级表是本设计的一个已知耦合点，仅集成测试走缺省）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_skill_invocation.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.agents.skills.invocation'`

- [ ] **Step 3: 实现推导函数**

新建 `src/agents/skills/invocation.py`：

```python
"""skill 双轴调用控制的默认推导。

依据 `allowed-tools` ⊕ 工具 `readonly` 推导 user_invocable / disable_model_invocation：
- allowed-tools 为空 → 双通道开放
- 全为只读工具 → 双通道开放
- 含任一非只读工具 → 默认 `disable_model_invocation=True`（fail-safe）
- **工具只读表为空 → 无法判断，fail-open 不锁**（见下）

极性取 fail-safe：写类内容的"模型自动调用"等于替用户做授权决定，故默认锁模型端；
作者要放权须显式写 `disable-model-invocation: false`。

**空表为何必须 fail-open**：工具只读表由注册点写入，而 skill 可能在工具注册之前加载
（`AgentService` 构造期工具尚未构造）。若空表也按"未注册工具 = 写类"处理，则**所有**带
`allowed-tools` 的 skill 会被静默锁死模型端；反之表非空时，未命中的工具名才按写类处理
（用于挡住拼错的工具名）。
"""


def derive_invocation_flags(
    allowed_tools: list[str], tool_readonly: dict[str, bool]
) -> tuple[bool, bool]:
    """按工具只读性推导双轴默认值。

    Args:
        allowed_tools: skill 声明的工具白名单（已归一化为 list）
        tool_readonly: 工具名 -> 是否只读 的映射（readonly_map()）

    Returns:
        (user_invocable, disable_model_invocation)：
        未声明双轴时应写入 SkillRecord 的默认值。

    Note:
        表为空表示"工具尚未注册"，此时无法判断只读性 → fail-open（不锁模型端）；
        表非空但工具名未命中，按写类处理（fail-safe，防拼错工具名放开模型端）。
    """
    if not allowed_tools:
        return True, False
    if not tool_readonly:
        return True, False
    has_write_tool = False
    for tool_name in allowed_tools:
        readonly = tool_readonly.get(tool_name, False)
        if not readonly:
            has_write_tool = True
            break
    if has_write_tool:
        return True, True
    return True, False
```

- [ ] **Step 4: loader 接入推导 + 死 skill 护栏**

`src/agents/skills/loader.py`：

```python
    def __init__(
        self, skills_root: Path, tool_readonly: dict[str, bool] | None = None
    ) -> None:
        """初始化加载器。

        Args:
            skills_root: skills 内容库根目录（含 <name>/SKILL.md 子目录）
            tool_readonly: 工具名 -> 是否只读 映射（供双轴默认推导；None 视为全只读）
        """
        self.skills_root = skills_root
        self._tool_readonly = tool_readonly if tool_readonly is not None else {}
```

`_parse` 里把两个双轴字段改为推导后再覆写：

```python
        allowed_tools = self._resolve_allowed_tools(meta)
        user_invocable, disable_model_invocation = self._resolve_invocation_flags(
            meta, allowed_tools, name
        )
        return SkillRecord(
            ...
            allowed_tools=allowed_tools,
            user_invocable=user_invocable,
            disable_model_invocation=disable_model_invocation,
            source_path=path.resolve(),
        )
```

新增两个方法：

```python
    def _resolve_invocation_flags(
        self, meta: dict, allowed_tools: list[str], name: str
    ) -> tuple[bool, bool]:
        """解析双轴：显式声明优先，未声明则按工具只读性推导（fail-safe）。"""
        declared_user = meta.get("user-invocable")
        declared_model = meta.get("disable-model-invocation")
        derived_user, derived_model = derive_invocation_flags(
            allowed_tools, self._tool_readonly
        )
        if isinstance(declared_user, bool):
            user_invocable = declared_user
        else:
            user_invocable = derived_user
        if isinstance(declared_model, bool):
            disable_model_invocation = declared_model
        else:
            disable_model_invocation = derived_model
            if derived_model:
                warnings.warn(
                    f"skill {name} 含非只读工具但未显式声明 disable-model-invocation，已默认关闭模型自动调用"
                    "（如需开放请显式写 disable-model-invocation: false）"
                )
        if not user_invocable and disable_model_invocation:
            warnings.warn(
                f"skill {name} 既不可用户调用也不可模型调用（死 skill），请检查双轴声明"
            )
        return user_invocable, disable_model_invocation
```

并在文件头 import 加：`from src.agents.skills.invocation import derive_invocation_flags`。

同时**删除 Task 2 留下的 `_resolve_bool` 方法**：双轴字段改由 `_resolve_invocation_flags` 处理后，它已无任何调用方（`grep -n "_resolve_bool" src/agents/skills/loader.py` 应只剩定义外的零处引用 → 删掉定义）。理由：留一个只服务已被替换逻辑的私有方法会误导后续读者以为它仍在校验双轴。

- [ ] **Step 5: 装配点无需改动（loader 缺省读进程级声明表）**

`SkillLoader.__init__` 中，`tool_readonly=None` 时读 Task 3 的单一来源：

```python
        if tool_readonly is None:
            tool_readonly = readonly_map()
        self._tool_readonly = tool_readonly
```

因此 `src/services/agent_service.py:623` 的 `SkillLoader(Path(skills_dir))` **保持原样**即可生效。加载早于工具注册也**安全**：只读表为空 → 推导 fail-open（Task 4 的 `derive_invocation_flags` 显式保证），不会把带 `allowed-tools` 的 skill 静默锁死；下一次 `reload_if_changed()`（`delegate_task.py:82` 已有调用点）会在表就绪后重新推导。

**验收**：`grep -n "reload_if_changed" src/services/agent_service.py src/agents/skills/delegate_task.py` 能看到"重载发生在请求期（表已就绪）"的调用点；并补一条测试断言"空表不锁模型端"（Task 4 Step 1 已含）。

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/agents/skills/ tests/agents/tools/ -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/agents/skills/invocation.py src/agents/skills/loader.py tests/agents/skills/test_skill_invocation.py
git commit -m "feat(skills): 双轴按工具只读性 fail-safe 推导，含写类默认锁模型端并记 warning"
```

---

### Task 5: `SkillRegistry` 双候选列表 + `delegate_task` 消费

**Files:**
- Modify: `src/agents/skills/registry.py`
- Test: `tests/agents/skills/test_skill_registry.py`

> 无需改 `src/agents/skills/delegate_task.py`：它在 `make_delegate_task` 时调用 `skill_registry.to_tool_description()`（`delegate_task.py:62`），过滤逻辑收在 `to_tool_description` 内部即可。

**Interfaces:**
- Consumes: Task 1/4 的 `SkillRecord.user_invocable` / `disable_model_invocation`
- Produces: `SkillRegistry.user_visible() -> list[SkillRecord]`；`SkillRegistry.model_visible() -> list[SkillRecord]`

- [ ] **Step 1: 写失败测试**

在 `tests/agents/skills/test_skill_registry.py` 末尾追加：

```python
def _make_registry(tmp_path, frontmatter: str, name: str = "finance-qa"):
    from src.agents.skills.loader import SkillLoader
    from src.agents.skills.registry import SkillRegistry

    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    registry = SkillRegistry(SkillLoader(tmp_path))
    registry.reload_if_changed()
    return registry


def test_user_visible_excludes_non_invocable(tmp_path):
    """user-invocable:false 的 skill 不出现在用户候选列表。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: hidden-skill\ndescription: 仅模型可用\nuser-invocable: false\n---\n正文\n",
        name="hidden-skill",
    )

    assert registry.user_visible() == []
    assert [r.name for r in registry.model_visible()] == ["hidden-skill"]


def test_model_visible_excludes_disable_model_invocation(tmp_path):
    """disable-model-invocation:true 的 skill 不出现在模型候选列表。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: manual-only\ndescription: 仅用户可调\n"
        "disable-model-invocation: true\n---\n正文\n",
        name="manual-only",
    )

    assert [r.name for r in registry.user_visible()] == ["manual-only"]
    assert registry.model_visible() == []


def test_to_tool_description_only_lists_model_visible(tmp_path):
    """delegate_task 的可用列表只含模型可见 skill。"""
    registry = _make_registry(
        tmp_path,
        "---\nname: manual-only\ndescription: 仅用户可调\n"
        "disable-model-invocation: true\n---\n正文\n",
        name="manual-only",
    )

    assert registry.to_tool_description() == "当前无可用 skill"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/skills/test_skill_registry.py -v -k "visible"`
Expected: FAIL —— `AttributeError: 'SkillRegistry' object has no attribute 'user_visible'`

- [ ] **Step 3: 实现候选列表**

`src/agents/skills/registry.py`：新增两个方法，并把 `to_tool_description` 改为只遍历模型可见项：

```python
    def user_visible(self) -> list[SkillRecord]:
        """返回允许用户 `/xxx` 调用的 skill（按名排序）。"""
        return [
            record
            for _, record in sorted(self._records.items())
            if record.user_invocable
        ]

    def model_visible(self) -> list[SkillRecord]:
        """返回允许模型自动 delegate_task 调用的 skill（按名排序）。"""
        return [
            record
            for _, record in sorted(self._records.items())
            if not record.disable_model_invocation
        ]
```

```python
    def to_tool_description(self, max_chars: int = 500) -> str:
        """生成 delegate_task 的可用 skill 列表文本（仅列模型可见项）。

        Args:
            max_chars: description 长度上限（字符）

        Returns:
            description 文本；无可见 skill 时返回"当前无可用 skill"
        """
        visible = self.model_visible()
        if not visible:
            return "当前无可用 skill"
        lines = [f"{rec.name}: {rec.description}" for rec in visible]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/skills/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/skills/registry.py tests/agents/skills/test_skill_registry.py
git commit -m "feat(skills): 注册表提供 user_visible/model_visible 双候选，delegate 描述只列模型可见"
```

---

### Task 6: `AgentPreset` 模型与加载器

**Files:**
- Create: `src/agents/presets/__init__.py`
- Create: `src/agents/presets/models.py`
- Create: `src/agents/presets/loader.py`
- Test: `tests/agents/presets/__init__.py`
- Test: `tests/agents/presets/test_preset_loader.py`

**Interfaces:**
- Consumes: Task 2 的 `src/config/const.py: CAPABILITY_NAME_PATTERN`
- Produces:
  - `AgentPreset(name, display_name, description, system_prompt, tools, skills, max_turns, source_path)`
  - `AgentPresetLoader(agents_root: Path).load_all() -> list[AgentPreset]`（fail-open；名称非法/同名由注册表 fail-fast）

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/presets/__init__.py`（空文件）与 `tests/agents/presets/test_preset_loader.py`：

```python
"""AgentPresetLoader 解析测试（agents/<name>.md，驼峰 frontmatter）。"""

import pytest

from src.agents.presets.loader import AgentPresetLoader


def test_parse_preset_with_all_fields(tmp_path):
    """解析完整 frontmatter：display_name/description/tools/skills/maxTurns + 正文人设。"""
    (tmp_path / "finance-expert.md").write_text(
        "---\n"
        "name: finance-expert\n"
        "display_name: 财务专家\n"
        "description: 财报分析、估值与财务风险研判\n"
        "tools: retrieve_kb\n"
        "skills: finance-qa\n"
        "maxTurns: 8\n"
        "---\n"
        "你是一名资深财务分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert len(presets) == 1
    preset = presets[0]
    assert preset.name == "finance-expert"
    assert preset.display_name == "财务专家"
    assert preset.tools == ["retrieve_kb"]
    assert preset.skills == ["finance-qa"]
    assert preset.max_turns == 8
    assert preset.system_prompt == "你是一名资深财务分析师。"


def test_display_name_defaults_to_name(tmp_path):
    """缺省 display_name 用 name。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 数据分析\n---\n你是数据分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].display_name == "analyst"


def test_description_falls_back_to_first_paragraph(tmp_path):
    """缺省 description 用正文首段。"""
    (tmp_path / "legal.md").write_text(
        "---\nname: legal\n---\n你是法务顾问，负责合同审阅。\n\n更多说明。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].description == "你是法务顾问，负责合同审阅。"


def test_non_ascii_name_is_skipped(tmp_path):
    """非 ASCII slug 名称记 warning 并跳过。"""
    (tmp_path / "财务.md").write_text(
        "---\nname: 财务专家\n---\n正文\n", encoding="utf-8"
    )

    with pytest.warns(UserWarning, match="非法"):
        presets = AgentPresetLoader(tmp_path).load_all()

    assert presets == []


def test_missing_dir_returns_empty(tmp_path):
    """目录不存在时返回空列表，不抛异常。"""
    assert AgentPresetLoader(tmp_path / "nope").load_all() == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/presets/ -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.agents.presets'`

- [ ] **Step 3: 新建模型**

`src/agents/presets/__init__.py`（空文件），`src/agents/presets/models.py`：

```python
"""智能体预设模型 — AgentPreset（agents/<name>.md 解析后的运行时对象）。

preset = 磁盘声明式智能体定义：frontmatter 声明元数据、正文是 system prompt（人设）。
与 skill 的区别：智能体是"谁在干活"（会话级身份），skill 是"按哪本手册干"（消息级内容）。
v1 不含 model（本项目模型不可选）。
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentPreset:
    """单个智能体预设的运行时对象。

    Attributes:
        name: 预设名（ASCII slug，注册表 key 与请求 `agent` 字段的值）
        display_name: 选择器展示名（frontmatter 缺省时等于 name）
        description: 何时使用（选择器副标题 / 委派参考）
        system_prompt: 正文，作为主 agent 的 system prompt 人设层
        tools: 该预设作为 fork 执行者时允许的工具名（空 list = 不收窄）
        skills: 预加载 skill 名（会话首轮注入一次）
        max_turns: 作为 fork 执行者时的最大轮次（空 = 用系统默认）
        source_path: md 文件绝对路径（懒重载 signature 用）
    """

    name: str  # 预设名（ASCII slug）
    display_name: str  # 展示名（缺省 = name）
    description: str  # 何时使用
    system_prompt: str  # 正文人设（注入主 agent system prompt）
    tools: list[str] = field(default_factory=list)  # fork 执行者工具白名单
    skills: list[str] = field(default_factory=list)  # 预加载 skill 名
    max_turns: int | None = None  # fork 执行者最大轮次，None=系统默认
    source_path: Path = field(default_factory=Path)  # md 文件绝对路径
```

- [ ] **Step 4: 新建加载器**

`src/agents/presets/loader.py`：

```python
"""AgentPresetLoader — 扫描 agents/*.md 并解析 frontmatter + 正文人设。

frontmatter 用**驼峰**键（`maxTurns`），与 skill 的连字符风格不同（照抄主流：
claude-code / codebuddy 的 agent 定义均为驼峰）。fail-open：单文件解析失败或
名称非法记 warning 并跳过；同名冲突由 AgentPresetRegistry fail-fast。
"""

import warnings
from pathlib import Path

import yaml

from src.agents.presets.models import AgentPreset
from src.config.const import CAPABILITY_NAME_PATTERN


class AgentPresetLoader:
    """从 agents_root 扫描并解析全部 .md 为 AgentPreset。"""

    def __init__(self, agents_root: Path) -> None:
        """初始化加载器。

        Args:
            agents_root: 智能体预设内容库根目录（含 <name>.md 平坦文件）
        """
        self.agents_root = agents_root

    def load_all(self) -> list[AgentPreset]:
        """扫描 agents_root 下全部 .md，解析为 AgentPreset 列表。

        Returns:
            解析成功的 AgentPreset 列表；目录不存在或为空时返回空列表。
            单文件解析失败（YAML 非法 / 名称非法 / IO 错误）记 warning 并跳过。
        """
        presets: list[AgentPreset] = []
        if not self.agents_root.exists():
            return presets
        for path in sorted(self.agents_root.glob("*.md")):
            try:
                presets.append(self._parse(path))
            except (yaml.YAMLError, ValueError, OSError) as exc:
                warnings.warn(f"智能体预设 {path.name} 解析失败，已跳过: {exc}")
        return presets

    def _parse(self, path: Path) -> AgentPreset:
        """解析单个 agents/<name>.md。

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
            ValueError: frontmatter 非 mapping / 名称为空或非 ASCII slug
            OSError: 读取文件失败
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        name = self._resolve_name(meta, path.stem)
        display_name = meta.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            display_name = name
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            description = self._first_paragraph(body)
        return AgentPreset(
            name=name,
            display_name=display_name,
            description=description,
            system_prompt=body.strip(),
            tools=self._resolve_list(meta, "tools"),
            skills=self._resolve_list(meta, "skills"),
            max_turns=self._resolve_max_turns(meta),
            source_path=path.resolve(),
        )

    def _resolve_name(self, meta: dict, fallback_name: str) -> str:
        """解析并校验预设名（ASCII slug 约束；覆盖 frontmatter name 与文件名）。"""
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            name = fallback_name
        if not CAPABILITY_NAME_PATTERN.match(name):
            raise ValueError(f"名称非法（仅允许 ASCII slug）: {name!r}")
        if not CAPABILITY_NAME_PATTERN.match(fallback_name):
            raise ValueError(f"文件名非法（仅允许 ASCII slug）: {fallback_name!r}")
        return name

    def _resolve_list(self, meta: dict, key: str) -> list[str]:
        """解析可选的字符串列表字段（逗号分隔字符串或 YAML 列表）。"""
        value = meta.get(key)
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            items = [str(part).strip() for part in value]
        else:
            items = []
        return [item for item in items if item]

    def _resolve_max_turns(self, meta: dict) -> int | None:
        """解析 maxTurns（bool 视为非法，回落 None）。"""
        value = meta.get("maxTurns")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 md 拆成 (frontmatter dict, 正文)；无 frontmatter 时返回 (空 dict, 全文)。"""
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                meta = yaml.safe_load("\n".join(lines[1:i]))
                body = "\n".join(lines[i + 1 :]).strip()
                if meta is None:
                    return {}, body
                if not isinstance(meta, dict):
                    raise ValueError("frontmatter 必须是 mapping")
                return meta, body
        return {}, raw

    def _first_paragraph(self, body: str) -> str:
        """取正文首段作 description 兜底（按空行切分取第一段，截断到 200 字）。"""
        if not body:
            return ""
        paragraph = body.split("\n\n")[0].replace("\n", " ").strip()
        return paragraph[:200]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/agents/presets/ -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agents/presets/ tests/agents/presets/
git commit -m "feat(presets): 新增 AgentPreset 模型与 agents/*.md 加载器（驼峰 frontmatter + ASCII 名称护栏）"
```

---

### Task 7: `AgentPresetRegistry`（按名索引 + 懒重载 + 同名 fail-fast）

**Files:**
- Create: `src/agents/presets/registry.py`
- Test: `tests/agents/presets/test_preset_registry.py`

**Interfaces:**
- Consumes: Task 6 的 `AgentPresetLoader` / `AgentPreset`
- Produces:
  - `AgentPresetRegistry(loader: AgentPresetLoader)`
  - `.reload_if_changed() -> None`（signature = 文件列表 + mtime_ns + size；变化才重扫）
  - `.get(name: str) -> AgentPreset | None`
  - `.all() -> list[AgentPreset]`（按名排序，供清单接口）

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/presets/test_preset_registry.py`：

```python
"""AgentPresetRegistry 测试：按名索引、懒重载、同名 fail-fast。"""

import pytest

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.registry import AgentPresetRegistry


def _write(tmp_path, filename: str, name: str, desc: str = "描述"):
    (tmp_path / filename).write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n你是{name}。\n",
        encoding="utf-8",
    )


def test_index_by_name_and_get(tmp_path):
    """按名索引；不存在返回 None（调用方降级默认）。"""
    _write(tmp_path, "finance-expert.md", "finance-expert")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()

    assert registry.get("finance-expert").display_name == "finance-expert"
    assert registry.get("ghost") is None


def test_reload_picks_up_new_file(tmp_path):
    """新文件写入后 reload 可见（mtime 驱动）。"""
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    assert registry.all() == []

    _write(tmp_path, "legal-expert.md", "legal-expert")
    registry.reload_if_changed()

    assert [p.name for p in registry.all()] == ["legal-expert"]


def test_same_name_declaration_fails_fast(tmp_path):
    """两个文件声明同名 → fail-fast（禁止静默覆盖）。"""
    _write(tmp_path, "a.md", "duplicated")
    _write(tmp_path, "b.md", "duplicated")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))

    with pytest.raises(ValueError, match="冲突"):
        registry.reload_if_changed()


def test_all_is_sorted_by_name(tmp_path):
    """all() 按名排序，供清单接口稳定输出。"""
    _write(tmp_path, "zeta.md", "zeta")
    _write(tmp_path, "alpha.md", "alpha")
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()

    assert [p.name for p in registry.all()] == ["alpha", "zeta"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/presets/test_preset_registry.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.agents.presets.registry'`

- [ ] **Step 3: 实现注册表**

`src/agents/presets/registry.py`：

```python
"""AgentPresetRegistry — 聚合 AgentPreset 并按名索引（含懒重载）。

懒重载语义与 SkillRegistry 一致：signature = 文件列表 + 每个 .md 的
(mtime_ns, size)，变化才重扫；同名声明 fail-fast（配置错误，非运行时降级场景）。
"""

from __future__ import annotations

import hashlib

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.models import AgentPreset


class AgentPresetRegistry:
    """按名聚合 AgentPreset，提供懒重载与列表输出。"""

    def __init__(self, loader: AgentPresetLoader) -> None:
        """初始化注册表。

        Args:
            loader: AgentPresetLoader 实例（agents_root 已注入）
        """
        self._loader = loader
        self._presets: dict[str, AgentPreset] = {}
        self._last_signature: str | None = None

    def reload_if_changed(self) -> None:
        """扫描 signature 变化时重载（无变化跳过）。

        Raises:
            ValueError: 两个文件声明同名（fail-fast，禁止静默覆盖）
        """
        signature = self._compute_signature()
        if signature == self._last_signature:
            return
        presets: dict[str, AgentPreset] = {}
        for preset in self._loader.load_all():
            if preset.name in presets:
                existing = presets[preset.name]
                raise ValueError(
                    f"智能体预设名称冲突: {preset.name}（来自 {existing.source_path} "
                    f"与 {preset.source_path}，fail-fast 禁止静默覆盖）"
                )
            presets[preset.name] = preset
        self._presets = presets
        self._last_signature = signature

    def get(self, name: str) -> AgentPreset | None:
        """按名取预设；不存在返回 None（调用方降级系统默认 prompt）。"""
        return self._presets.get(name)

    def all(self) -> list[AgentPreset]:
        """返回全部预设（按名排序，供清单接口与启动日志）。"""
        return [self._presets[name] for name in sorted(self._presets)]

    def _compute_signature(self) -> str:
        """计算 agents_root 的扫描 signature（文件级 mtime + size）。"""
        root = self._loader.agents_root
        if not root.exists():
            return "empty"
        parts: list[str] = []
        for path in sorted(root.glob("*.md")):
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/agents/presets/ -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/presets/registry.py tests/agents/presets/test_preset_registry.py
git commit -m "feat(presets): AgentPresetRegistry 按名索引 + 懒重载 + 同名 fail-fast"
```

---

### Task 8: 内容迁移与存量 frontmatter 清理

**Files:**
- Create: `agents/finance-expert.md`
- Modify: `skills/finance-analyst/SKILL.md`（人设段迁出，改写为方法论）
- Modify: `skills/finance-qa/SKILL.md`（清理冗余 `context: inline`，正文占位符改 `$ARGUMENTS`）
- Test: `tests/agents/presets/test_preset_content.py`

**Interfaces:**
- Consumes: Task 6/7 的加载器与注册表
- Produces: 磁盘内容 —— 一个可直接被 `AgentPresetLoader` 加载的预设 + 两个符合新契约的 skill

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/presets/test_preset_content.py`：

```python
"""仓库内真实内容文件的契约测试（防内容与契约脱节）。"""

from pathlib import Path

from src.agents.presets.loader import AgentPresetLoader
from src.agents.skills.loader import SkillLoader

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_finance_expert_preset_loads():
    """agents/finance-expert.md 能被加载且带中文展示名。"""
    presets = AgentPresetLoader(REPO_ROOT / "agents").load_all()

    names = [p.name for p in presets]
    assert "finance-expert" in names
    preset = next(p for p in presets if p.name == "finance-expert")
    assert preset.display_name == "财务专家"
    assert preset.system_prompt


def test_repo_skills_all_load_and_are_named_ascii():
    """仓库内全部 skill 均通过名称校验且被解析（无跳过）。"""
    records = SkillLoader(REPO_ROOT / "skills").load_all()

    assert len(records) == len(list((REPO_ROOT / "skills").glob("*/SKILL.md")))
    for record in records:
        assert record.name.isascii()


def test_finance_analyst_is_methodology_not_persona():
    """finance-analyst 已改写为方法论：正文不含"你是一名"式人设。"""
    records = SkillLoader(REPO_ROOT / "skills").load_all()

    record = next(r for r in records if r.name == "finance-analyst")
    body = record.fork_body if record.fork_body is not None else record.inline_prompt
    assert "你是一名" not in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/agents/presets/test_preset_content.py -v`
Expected: FAIL —— `assert "finance-expert" in names` 失败（文件尚不存在）

- [ ] **Step 3: 新建智能体预设**

新建 `agents/finance-expert.md`：

```markdown
---
name: finance-expert
display_name: 财务专家
description: 财报分析、估值与财务风险研判
skills: finance-qa
---

你是一名资深财务分析师，服务企业内部的财务与投资研判场景。

工作原则：
1. 只基于检索到的材料作答，材料不足时如实说明，不臆测数据。
2. 关注指标口径（报告期、合并/母公司、同比/环比）并在结论中注明。
3. 输出结构：关键指标与趋势 → 驱动因素 → 风险点 → 结论与建议。
4. 涉及多方法论分析时，优先按已加载的方法论 skill 执行。
```

- [ ] **Step 4: 改写 `finance-analyst` 为方法论**

`skills/finance-analyst/SKILL.md` 全文替换（去掉"你是一名…"人设，保留方法论）：

```markdown
---
name: finance-analyst
description: 财务深度分析方法论（何时使用：需要多步建模、财务比率推算、趋势解读等深度分析；材料须由主 agent 预检索一并传入 task）
context: fork
---

按以下方法论完成财务深度分析：

1. 只基于任务中给定的材料作答，不得假设材料之外的数据。
2. 指标口径先行：先对齐报告期与合并口径，再计算比率。
3. 输出结构化分析：关键指标趋势 → 驱动因素 → 风险点 → 结论建议。
4. 每个事实标注数据来源编号。

任务：$ARGUMENTS
```

> **为什么本任务不加 `allowed-tools` / `agent:`**：fork 子代理目前恒为零工具（`executor` 传 `tools=[]`），工具放开与 `create_agent` 替换属 **Plan 2**。此刻声明 `allowed-tools: retrieve_kb` 会形成"声明无效"的中间态，使本任务的验收标准无法断言其生效。等 Plan 2 落地时一并补上。

- [ ] **Step 5: 清理 `finance-qa`**

`skills/finance-qa/SKILL.md` 全文替换（删冗余 `context: inline`，占位符改 `$ARGUMENTS`）：

```markdown
---
name: finance-qa
description: 财务知识库问答规则（何时使用：知识库已覆盖的一般财务事实问题；先检索后答）
---

回答财务问题时：
1. 先调用 retrieve_kb 检索知识库，不要凭记忆作答。
2. 回答中的每个事实标注对应的年份/报告期。
3. 引用检索到的来源：在句末标注与检索返回一致的编号 [n]。
4. 检索不到或数据明显不完整时，如实说明证据不足，不要编造。

任务：$ARGUMENTS
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/agents/presets/test_preset_content.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add agents/ skills/finance-analyst/SKILL.md skills/finance-qa/SKILL.md tests/agents/presets/test_preset_content.py
git commit -m "content: 新增 finance-expert 智能体预设，finance-analyst 改写为方法论，finance-qa 契约对齐"
```

---

### Task 9: 收口 —— 全量门禁 + 文档登记

**Files:**
- Modify: `tests/agents/skills/test_skill_executor.py`（若引用 `agent_prompt` / `thinking` / `max_iterations`，改为新字段名）
- Modify: `docs/agents/code-map.md`（登记 `agents/` 与 `src/agents/presets/`）
- Modify: `docs/agents/glossary.md`（登记「智能体预设」「会话级 vs 消息级」「双轴调用控制」）
- Modify: `docs/agents/logging-rules.md`（记录"加载期问题走 warnings、不登记 Event"的例外）

**Interfaces:**
- Consumes: 前 8 个任务的成果
- Produces: 可合并状态（门禁全绿 + 文档一致）

- [ ] **Step 1: 修掉字段改名的遗留引用**

Run: `grep -rn "agent_prompt\|max_iterations\|\.thinking" src/ tests/`
对每一处：`agent_prompt` → `fork_body`；`max_iterations` / `thinking` 相关断言删除或改为新行为断言。

- [ ] **Step 2: 记录"本计划不新增日志事件"的决定**

本计划的加载期问题（名称非法 / 废弃字段 / 双轴推导 / 死 skill）**沿用 `SkillLoader` 既有的 `warnings.warn` 模式**，不新增 `Event` 枚举——理由：① 现有 loader 全部走 `warnings.warn`（改动最小、与既有风格一致）；② 这些是**加载期内容问题**，不是运行时请求路径，`[层名] 事件 k=v` 的行模板更适合请求期日志；③ 避免为只出现一次的加载期问题引入长期维护的 `EventSpec`。

在 `docs/agents/logging-rules.md` 的「已知例外」段追加一行：

```markdown
- skill / 智能体预设的**加载期**问题（名称非法、废弃字段、双轴推导、死 skill）走 `warnings.warn`，
  不登记为 `Event`（一次性内容问题，非请求路径；见 Plan 1 §Task 9）
```

（运行时事件——`agent` 绑定/忽略告警、`/xxx` 前缀命中与未命中——属 Plan 3 范围，届时按"开放登记制"登记。）

- [ ] **Step 3: 登记文档**

- `docs/agents/code-map.md`：补 `agents/`（内容目录，`<name>.md` 平坦文件）与 `src/agents/presets/`（模型/加载器/注册表），并说明三个 `agents` 的区别（根 `agents/` 内容、`src/agents/` 代码、`docs/agents/` 文档）。
- `docs/agents/glossary.md`：补「智能体预设（agent preset）」「会话级 vs 消息级」「双轴调用控制（user-invocable / disable-model-invocation）」。

- [ ] **Step 4: 跑全量质量门禁**

Run: `pytest tests/ -v && ruff format . && ruff check . --fix && pyright src/ && python -m src.cli.check_docs`
Expected: pytest 全绿；ruff 无错；pyright 不新增 error；check_docs `0 error`

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: 契约层收口——字段改名遗留清理、日志与文档登记、门禁全绿"
```

---

## Self-Review

**1. Spec coverage（对照 `specs/` 逐条）**

| Requirement | 覆盖任务 |
|---|---|
| `skill-registry`「Skill 文件结构」（字段集 / 逗号分隔 / 废弃字段 / ASCII 名） | Task 2 |
| `skill-registry`「SkillRecord 运行时对象」（字段集 / context 正文落点 / 双轴推导写入） | Task 1、Task 4 |
| `tool-registry`「工具注册表」（readonly 属性 + 供双轴推导） | Task 3 |
| `skill-invocation`「双轴调用控制」+「双轴默认推导」（含死 skill 告警） | Task 4 |
| `skill-invocation`「两个通道各自做候选过滤」 | Task 5 |
| `agent-preset`「智能体预设文件结构」（字段 / ASCII 名 / display_name / 缺省） | Task 6 |
| `agent-preset`「AgentPreset 运行时对象」（字段集 / 按名索引 / 同名冲突） | Task 6、Task 7 |
| `agent-preset` 内容迁移（finance-expert / finance-analyst 改写） | Task 8 |
| tasks.md 1.1–1.4 / 2.1–2.3 / 3.1–3.3 / 3.5–3.6 / 5.1–5.2 / 3.7 / 1.3 | Task 1–9 |

**未覆盖（有意留给后续计划）**：3.4（AgentService 装配 presets → Plan 3）、5.3（`delegate_task` 已经消费 `model_visible()`，但工具集放开属于 Plan 2）、5.10/5.11（清单接口与常量 → Plan 3）、前端全部（Plan 4）。

**2. Placeholder scan**：已逐条检查，无 TBD / "类似 Task N" / "加适当的错误处理" 类占位；每个代码步骤都给了可直接粘贴的实现或测试。

**3. Type consistency**：`fork_body`（Task 1 定义 → Task 2 写入 → Task 8 断言）、`derive_invocation_flags(allowed_tools, tool_readonly) -> tuple[bool, bool]`（Task 4 定义 → Task 4 loader 调用）、`readonly_map() -> dict[str, bool]`（Task 3 定义 → Task 4 消费）、`AgentPreset` 字段名（Task 6 定义 → Task 7/8 消费）、`CAPABILITY_NAME_PATTERN`（Task 2 定义 → Task 6 复用）均已核对一致。

**4. 已知待决（阻塞 Plan 2/3，不在本计划范围）**：P1 图入口分派、P2 直出路径 verify 三处依赖 → **已在 change 落为 design D26 + tasks 4.10/4.11 + agent-service spec 4 个 Scenario**；P4 已改为复用 `DELEGATE_DEFAULT_MAX_TURNS`（tasks 4.4/5.11 已修）。Plan 2 可据此开工。

### 5. 本轮复审修正（R1–R5，已回填到上文步骤）

| # | 问题 | 修正 |
|---|---|---|
| **R1** | 我原先写的 `derive_invocation_flags` 在**只读表为空**时（工具尚未注册）对任何 `allowed-tools` 都返回 `disable_model_invocation=True` → **把所有带工具白名单的 skill 静默锁死模型端**；计划里那句"空表 → 双通道开放，不会误锁"与代码行为**相反** | 推导函数加"表为空 → fail-open 不锁"分支 + 单测 `test_empty_readonly_table_falls_open`；Step 5 注释改正 |
| **R2** | Task 1 删字段后 `SkillExecutor` 仍引用 `agent_prompt`/`thinking`/`max_iterations` → 仓库在 Task 1~9 之间**处于 import 报错状态**，违反"每个任务结束时可独立验证" | Task 1 增加 Step 5「executor 最小适配」+ Step 6「跑全量测试确认树仍绿」，Files/提交同步；Plan 2 的 4.4 收窄为剩余清理 |
| **R3** | Task 9 的日志事件步骤是"新增事件或记录理由"的**二选一**（未决） | 明确**不新增 Event**（加载期问题沿用 `warnings.warn`），只在 `logging-rules.md` 已知例外段记一行 |
| **R4** | 进程级只读表使 loader 行为依赖**测试执行顺序**（同会话其它用例注册了工具会改结果） | 计划内单测一律**显式注入** `tool_readonly`（含 `{}` 表示"表未就绪"），并在 Task 4 加单测约定说明 |
| **R5** | `_warn_deprecated_fields` 被塞在 `_resolve_name` 里（解析名称的方法顺带发字段告警，职责混淆） | 移到 `_parse` 顶部单独调用 |
| **R6** | Task 5 的 Files 里写了"改 `delegate_task.py`"，但过滤逻辑收在 `to_tool_description` 内即可，该文件**无需改动**（过度改动的假任务） | Files 收窄为 `registry.py`，并注明理由 |
| **R7** | `delegate_task` 的工具 description 在 `make_delegate_task`（图构建期）一次性生成，`reload_if_changed()` 只刷新注册表、**不刷新已生成的 description** → 新增 skill 在重启前不会出现在模型可用列表里，而新的 `GET /api/skills` 会立即可见（两者不一致） | 本计划不做（属既有行为）；在 `capability-catalog` spec 的"无需重启生效"上加限定语。**已知会接受该不一致** |
| **R8** | Task 4 把双轴字段改由 `_resolve_invocation_flags` 处理后，Task 2 定义的 `_resolve_bool` **再无调用方**（死方法残留进 master） | Task 4 Step 4 增加"删除 `_resolve_bool`"步骤与自查命令 |
| **R9** | tasks 第 8 节文件顺序为 8.1…8.8 → **8.12** → 8.9 → 8.10 → 8.11（上轮插入位置不当），执行者按号定位会错位 | 已重排为 8.1…8.12 顺序 |
| **R10** | Plan 1 Task 8 给 `finance-analyst` 加了 `allowed-tools: retrieve_kb`，但**工具放开属 Plan 2** → Plan 1 单独落地后该声明无效，形成"声明与行为不一致"的中间态，且本任务无法断言其生效 | Task 8 移除 `allowed-tools`（只保留 `context: fork` + 正文改写），留待 Plan 2 补 |
| **R11** | `SKILL_NAME_PATTERN` 同时被 agent preset 复用（Task 6），名字与用途不符 | 重命名为 `CAPABILITY_NAME_PATTERN`（Task 2/6 与 const 一致改名） |

> R1 是**必须修**的一条：不修则 Plan 1 落地后 `finance-analyst`（Task 8 给它加了 `allowed-tools: retrieve_kb`）会在工具表尚未就绪的加载窗口里被锁掉模型自动调用，且只留一条 warning，问题极难定位。
