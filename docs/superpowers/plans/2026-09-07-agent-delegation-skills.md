# 主从委派 + skill 加载器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主 agent 通过 `delegate_task(task, skill)` 工具按需调用磁盘上的声明式 skill（inline 主 agent 自己答 / fork 独立零工具子代理深度分析），并配套 SSE 状态、迭代预算放宽、引导 prompt、首批 skill 内容与防腐扩展。

**Architecture:** 三层——① 内容层 `skills/<name>/SKILL.md`（frontmatter + 正文，业务侧增删改，懒重载免重启）；② 加载层 `src/agents/skills/`（SkillRecord/SkillLoader/SkillRegistry/SkillExecutor）；③ 接线层 `delegate_task` 工具注册进 `make_rag_tools`，fork 分支内推 STAGE_DELEGATE 状态。fork 子代理**工具集恒空**（零工具，材料由主 agent 预检索塞 task），避免写共享 RequestContext.tool_contexts 污染主 agent 引用编号。

**Tech Stack:** Python 3.11 / LangGraph 1.2.9 create_react_agent / PyYAML 6.0.3（已可用）/ langchain-core tools / asyncio / pytest-asyncio。

**Spec:** `docs/openspec/changes/agent-delegation-skills/`（proposal.md / design.md / specs/{skill-registry,delegate-task,delegate-observability}/spec.md / tasks.md）

## Global Constraints

- 语言与注释：全部中文（docstring、注释、提交信息、SSE 文案）。事件日志英文 k=v + `[层名]` 前缀（见 docs/agents/logging-rules.md）。
- fork 子代理工具集**恒空**：`create_react_agent(llm, tools=[], prompt=agent_prompt)`（design D7 / delegate-task spec「零工具硬保证」）。`allowed-tools` frontmatter 解析保留但**本版不启用**。
- 子代理分析文本**不写入** RequestContext.tool_contexts、不作为可引用来源；主 agent 引用仅指向自身 tool_contexts（design D9）。
- SSE STAGE_DELEGATE **仅 fork 路径推**，inline 命中不推（design D14）；推送走 ctx 事件通道（复用 ask_user 的 clarify_channel 先例），**不走外层 astream_events on_tool_start/end 映射**。
- fork skill 正文**不出现任何工具名**（零工具，正文声明工具集属误导）；inline_prompt 控制在 ≤500 字。
- 不加第三方依赖（PyYAML / create_react_agent 已可用）；新增常量统一进 `src/config/const.py`（文案进 SSEInteractionTexts，阈值/超时为顶层常量）。
- 测试 mock 外部依赖（create_react_agent、get_llm、LLM 调用、目录扫描），不发真实网络调用。
- 不用三元表达式；类型不确定不用 `getattr(x, attr, default)` 兜底，用显式 if/else + isinstance。
- 所有 dataclass 字段必须有行内注释；函数必须有 docstring。
- 与活跃 change 边界：不碰 sse-tool-detail 的 detail 结构；不重构 llm-callback-handler；不改 retrieval-quality-signals 的 verify 判定（本 change 对 verify 的改动仅限：delegate 预算放宽接线 Task 7、regen 复位 `_delegate_used`、`kb_citation_guardrail` 的 EXPERT_ANALYSIS_MARKER 豁免——均属 cite/预算语义，不触碰检索行为信号判定）。
- **专家分析豁免（M7 回归）**：fork 子代理的"无源分析观点"由 4.1 引导主 agent 措辞为 `EXPERT_ANALYSIS_MARKER`（const.py，"基于领域经验的分析"），态 B `kb_citation_guardrail` 对含该短语的分析答案豁免补标 regen（4.1 文案与 const 标记必须同文，Task 8 测试锁定）。

---

### Task 1: const.py 常量与 SSEInteractionTexts（DELEGATE 文案/阈值）

**Files:**
- Modify: `src/config/const.py`（`MAX_AGENT_ITERATIONS` 附近加阈值常量；`SSEInteractionTexts` class 内加 stage/文案）
- Create: `tests/config/test_delegate_const.py`（新文件——delegate 常量/文案不属 web 搜索域，不挤进 test_web_search_const.py）

**Interfaces:**
- Consumes: 无（本项目第一个 delegate 相关产出）
- Produces: 顶层常量 `MAX_DELEGATE_BONUS: int = 2`、`DELEGATE_TIMEOUT: int = 120`、`DELEGATE_RESULT_LIMIT: int = 1000`、`INLINE_PROMPT_MAX_CHARS: int = 500`、`EXPERT_ANALYSIS_MARKER: str = "基于领域经验的分析"`；`SSEInteractionTexts` 内：`STAGE_DELEGATE: str = "delegate"`、`DELEGATE_STATUS_START: str = "正在调用领域专家分析..."`、`DELEGATE_STATUS_END: str = "领域专家分析完成"`、`DELEGATE_UNKNOWN_SKILL: str`（模板）、`DELEGATE_TIMEOUT_TEXT: str`、`DELEGATE_TRUNCATED_PREFIX: str`（模板）。后续任务引用以上常量名。

- [ ] **Step 1: 写失败测试**

`tests/config/test_delegate_const.py`：

```python
"""测试 delegate 相关常量与 SSE 文案（agent-delegation-skills change）。"""

from src.config.const import (
    DELEGATE_RESULT_LIMIT,
    DELEGATE_TIMEOUT,
    INLINE_PROMPT_MAX_CHARS,
    MAX_DELEGATE_BONUS,
    SSEInteractionTexts,
)


def test_delegate_constants():
    assert DELEGATE_TIMEOUT > 0
    assert DELEGATE_RESULT_LIMIT == 1000
    assert MAX_DELEGATE_BONUS == 2
    assert INLINE_PROMPT_MAX_CHARS == 500
    assert SSEInteractionTexts.STAGE_DELEGATE == "delegate"
    assert SSEInteractionTexts.DELEGATE_STATUS_START.startswith("正在调用")
    assert SSEInteractionTexts.DELEGATE_STATUS_END.startswith("领域专家分析完成")


def test_delegate_unknown_skill_template():
    msg = SSEInteractionTexts.DELEGATE_UNKNOWN_SKILL.format(
        skill="finance-analyst", available="finance-qa"
    )
    assert "finance-analyst" in msg and "finance-qa" in msg


def test_delegate_truncated_prefix_template():
    msg = SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX.format(
        total=3000, truncated="..."
    )
    assert "3000" in msg and "..." in msg


def test_expert_analysis_marker():
    """专家分析标记短语存在（kb_citation_guardrail 豁免依据，M7）。"""
    from src.config.const import EXPERT_ANALYSIS_MARKER

    assert EXPERT_ANALYSIS_MARKER == "基于领域经验的分析"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/config/test_delegate_const.py -v`
Expected: FAIL（ImportError / AttributeError，常量不存在）

- [ ] **Step 3: 实现**

在 `src/config/const.py` 的 `# ── agent 循环护栏常量 ──` 区段（`MAX_VERIFY_REGENERATIONS` 下方）追加：

```python
# ── delegate（主从委派）护栏常量 ──
# 来源：agent-delegation-skills change；用途：fork 子代理超时/结果截断/inline 规模约束
MAX_DELEGATE_BONUS = 2  # delegate 轮后主 agent 迭代上限放宽轮数（整合余量，单请求总上限仍封顶）
DELEGATE_TIMEOUT = 120  # fork 子代理执行总超时秒数（asyncio.wait_for，防外部 API 挂起烧钱）
DELEGATE_RESULT_LIMIT = 1000  # fork 结果回流主 agent 的截断阈值（字符）
INLINE_PROMPT_MAX_CHARS = 500  # inline skill 正文规模上限（字符，防上下文累积膨胀，超出仅记 warning）
# 专家分析标记短语：fork 子代理"无源分析观点"由 4.1 引导主 agent 措辞（design D9），
# kb_citation_guardrail 据此豁免（防纯分析型 fork 答案被误触发补标 regen，M7）
EXPERT_ANALYSIS_MARKER = "基于领域经验的分析"
```

在 `SSEInteractionTexts` class 内 `STAGE_RETRIEVE` 常量附近追加：

```python
    # delegate_task 工具（fork 路径）对应 stage：仅 fork 命中时推送（inline 命中不推，见 design D14）
    STAGE_DELEGATE: str = "delegate"

    # fork 执行开始/完成文案（delegate_task 工具体内经 ctx 通道投递 status dict，_convert_event 转 SSEStatusEvent）
    DELEGATE_STATUS_START: str = "正在调用领域专家分析..."
    DELEGATE_STATUS_END: str = "领域专家分析完成"
```

在 `SSEInteractionTexts` class 内末尾（`CLARIFY_ANSWER_NOT_FOUND_TEXT` 下方）追加工具返回给主 agent 的文本常量：

```python
    # ── delegate_task 工具返回主 agent 的文本 ──
    # 未知 skill 返回模板：{skill}=请求的 skill 名；{available}=可用 skill 列表（空列表显示"无"）
    DELEGATE_UNKNOWN_SKILL: str = "skill 不存在: {skill}，可用 skill: {available}"
    # fork 超时文案（delegate_task 返回给 LLM，促其基于现有上下文作答）
    DELEGATE_TIMEOUT_TEXT: str = "Error: 领域专家分析超时，请基于已有检索上下文作答"
    # fork 结果截断前缀模板：{total}=完整字数；{truncated}=截断后的摘要文本
    DELEGATE_TRUNCATED_PREFIX: str = "子代理已产出完整分析 {total} 字，摘要如下：\n{truncated}"
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/config/test_delegate_const.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/const.py tests/config/test_delegate_const.py
git commit -m "feat: add delegate stage texts and timeout/truncation constants"
```

---

### Task 2: skills/ 目录语义与 SkillRecord 内容模型

**Files:**
- Create: `src/agents/skills/__init__.py`
- Create: `src/agents/skills/models.py`
- Create: `tests/agents/skills/__init__.py`
- Create: `tests/agents/skills/test_skill_models.py`

**Interfaces:**
- Consumes: Task 1 的 `SSEInteractionTexts`（本任务仅模型，暂不需要；后续 loader 使用）
- Produces: `SkillContext` class（`INLINE="inline"` / `FORK="fork"`）、`SkillRecord` dataclass（字段 `name/description/context/inline_prompt/agent_prompt/model/thinking/allowed_tools/max_iterations/source_path`）。Task 3/4/5/6 引用。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_models.py`：

```python
"""测试 SkillRecord 内容模型与 context 常量。"""

from pathlib import Path

from src.agents.skills.models import SkillContext, SkillRecord


def test_context_constants():
    assert SkillContext.INLINE == "inline"
    assert SkillContext.FORK == "fork"


def test_skill_record_defaults():
    rec = SkillRecord(
        name="finance-qa",
        description="财务问答规则",
        source_path=Path("skills/finance-qa/SKILL.md"),
    )
    assert rec.context == SkillContext.INLINE
    assert rec.inline_prompt is None
    assert rec.agent_prompt is None
    assert rec.model is None
    assert rec.thinking is None
    assert rec.allowed_tools == []
    assert rec.max_iterations is None


def test_skill_record_fork_fields():
    rec = SkillRecord(
        name="finance-analyst",
        description="财务建模专家",
        context=SkillContext.FORK,
        agent_prompt="你是财务建模专家",
        model="qwen3.8-max",
        thinking=True,
        allowed_tools=[],
        max_iterations=0,
        source_path=Path("skills/finance-analyst/SKILL.md"),
    )
    assert rec.context == SkillContext.FORK
    assert rec.agent_prompt == "你是财务建模专家"
    assert rec.inline_prompt is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_skill_models.py -v`
Expected: FAIL（ModuleNotFoundError: src.agents.skills）

- [ ] **Step 3: 实现**

`src/agents/skills/models.py`：

```python
"""Skill 内容模型 — SkillRecord（skill 文件解析后的运行时对象）。

skill = 磁盘声明式能力文件（skills/<name>/SKILL.md）：frontmatter 声明元数据、
正文按 context 语义存为 inline_prompt（主 agent 注入执行）或 agent_prompt
（fork 子代理 system_prompt）。skill 是内容/配置而非代码，业务解耦核心。
"""

from dataclasses import dataclass, field
from pathlib import Path


class SkillContext:
    """skill 执行上下文常量（frontmatter context 字段取值）。

    分工：inline = 指令注入主 agent 上下文、主 agent 自己执行；
    fork = 生成独立零工具子代理（create_react_agent）隔离执行。
    """

    INLINE: str = "inline"  # inline 执行形态
    FORK: str = "fork"  # fork 执行形态


@dataclass
class SkillRecord:
    """单个 skill 的运行时对象（由 SkillLoader 解析 SKILL.md 产出）。

    Attributes:
        name: skill 名（目录名；LLM 委派匹配依据）
        description: whenToUse 一句话描述（委派匹配依据）
        context: 执行形态（inline|fork，非法值回落 inline）
        inline_prompt: context=inline 时正文（短方法论，≤500 字，可含 {task} 占位）
        agent_prompt: context=fork 时正文（子代理 system_prompt）
        model: fork 覆盖模型（空=None 继承主 agent llm）
        thinking: fork 是否开思考（None=跟随主 agent 不显式设置）
        allowed_tools: fork 子代理工具白名单（**预留字段本版恒不启用**，见 design D7）
        max_iterations: fork 迭代上限（0=继承；零工具下无工具循环本版不生效，预留）
        source_path: SKILL.md 文件绝对路径（懒重载 signature 用）
    """

    name: str  # skill 名（目录名）
    description: str  # whenToUse 一句话描述
    context: str = SkillContext.INLINE  # inline|fork
    inline_prompt: str | None = None  # context=inline 时正文（短方法论，≤500 字）
    agent_prompt: str | None = None  # context=fork 时正文（子代理 system_prompt）
    model: str | None = None  # fork 覆盖模型，None=继承主 agent llm
    thinking: bool | None = None  # fork 是否开思考，None=跟随主 agent
    allowed_tools: list[str] = field(
        default_factory=list
    )  # 预留字段（本版恒不启用），见 design D7
    max_iterations: int | None = None  # 预留字段（零工具下不生效），见 design D2
    source_path: Path = field(default_factory=Path)  # SKILL.md 绝对路径
```

`src/agents/skills/__init__.py`：

```python
"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.models import SkillContext, SkillRecord

__all__ = ["SkillContext", "SkillRecord"]
```

`tests/agents/skills/__init__.py`：空文件。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_skill_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/skills/ tests/agents/skills/
git commit -m "feat: add SkillRecord model and skills package skeleton"
```

---

### Task 3: SkillLoader — frontmatter 解析与目录扫描

**Files:**
- Create: `src/agents/skills/loader.py`
- Modify: `src/agents/skills/__init__.py`（re-export SkillLoader）
- Test: `tests/agents/skills/test_skill_loader.py`

**Interfaces:**
- Consumes: Task 2 `SkillRecord`/`SkillContext`
- Produces: `class SkillLoader`：`__init__(self, skills_root: Path)`；`load_all(self) -> list[SkillRecord]`；`skills_root` 属性。解析规则：目录名作 skill 名；name 缺省用目录名；description 缺省用正文首段；context 非法回落 inline + warning；正文按 context 存 inline_prompt / agent_prompt。Task 4/5/6 引用。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_loader.py`（用 `tmp_path` 构造 skills 目录，不碰真实文件系统）：

```python
"""测试 SkillLoader — SKILL.md frontmatter 解析与目录扫描。"""

from pathlib import Path

import pytest

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext


def _write_skill(root: Path, name: str, frontmatter: str, body: str) -> Path:
    """在 root/<name>/SKILL.md 写入 frontmatter+正文，返回文件路径。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def test_load_inline_skill(tmp_path):
    _write_skill(
        tmp_path,
        "finance-qa",
        "name: finance-qa\ndescription: 财务问答规则\ncontext: inline\n",
        "回答财务问题时先检索知识库，标注报告期，引用 [n]。",
    )
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.name == "finance-qa"
    assert rec.description == "财务问答规则"
    assert rec.context == SkillContext.INLINE
    assert "先检索" in rec.inline_prompt
    assert rec.agent_prompt is None


def test_load_fork_skill(tmp_path):
    _write_skill(
        tmp_path,
        "finance-analyst",
        "description: 财务建模专家\ncontext: fork\nmodel: qwen3.8-max\nthinking: true\n",
        "你是一名财务建模专家，基于给定材料做多步分析。",
    )
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    rec = records[0]
    assert rec.context == SkillContext.FORK
    assert rec.model == "qwen3.8-max"
    assert rec.thinking is True
    assert "财务建模专家" in rec.agent_prompt
    assert rec.inline_prompt is None


def test_skill_name_defaults_to_dirname(tmp_path):
    """frontmatter 缺 name → 用目录名。"""
    _write_skill(
        tmp_path,
        "my-analyst",
        "description: x\ncontext: fork\n",
        "正文",
    )
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.name == "my-analyst"


def test_description_defaults_to_body_first_paragraph(tmp_path):
    """frontmatter 缺 description → 用正文首段。"""
    _write_skill(tmp_path, "qa", "context: inline\n", "这是正文第一段。\n\n第二段。")
    rec = SkillLoader(tmp_path).load_all()[0]
    assert "这是正文第一段" in rec.description


def test_invalid_context_falls_back_inline(tmp_path):
    """context 非法值 → 回落 inline + 记 warning（不抛异常）。"""
    _write_skill(
        tmp_path,
        "bad",
        "description: x\ncontext: hybrid\n",
        "方法论正文",
    )
    rec = SkillLoader(tmp_path).load_all()[0]
    assert rec.context == SkillContext.INLINE
    assert "方法论正文" in rec.inline_prompt


def test_subdirectory_without_skill_md_ignored(tmp_path):
    """含子目录但无 SKILL.md → 忽略（不算 skill）。"""
    (tmp_path / "no-skill").mkdir()
    (tmp_path / "no-skill" / "readme.txt").write_text("x", encoding="utf-8")
    records = SkillLoader(tmp_path).load_all()
    assert records == []


def test_nested_skill_dirs_ignored(tmp_path):
    """只扫一层 skills/<name>/SKILL.md，不递归更深。"""
    _write_skill(tmp_path, "a", "description: x\n", "正文")
    _write_skill(tmp_path / "a" / "nested", "b", "description: y\n", "正文2")
    records = SkillLoader(tmp_path).load_all()
    assert len(records) == 1
    assert records[0].name == "a"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_skill_loader.py -v`
Expected: FAIL（ModuleNotFoundError: src.agents.skills.loader）

- [ ] **Step 3: 实现**

`src/agents/skills/loader.py`：

```python
"""SkillLoader — 扫描 skills/<name>/SKILL.md 并解析 frontmatter + 正文。

SKILL.md 结构：YAML frontmatter（--- 包裹）+ 正文。frontmatter 字段：
name/description/context/model/thinking/allowed-tools/max-iterations（横线键转
下划线）。解析规则：
- name 缺省用目录名；description 缺省用正文首段
- context 非法值回落 inline 并记 warning（fail-open，不阻塞加载）
- 正文按 context 存 inline_prompt（inline）或 agent_prompt（fork）
- 只扫一层 skills/<name>/SKILL.md，不递归（目录即 skill 边界）
"""

import warnings
from pathlib import Path

import yaml

from src.agents.skills.models import SkillContext, SkillRecord

_FRONTMATTER_KEYS = {
    "name": str,
    "description": str,
    "context": str,
    "model": str | None,
    "thinking": bool | None,
    "allowed-tools": list,
    "max-iterations": int | None,
}


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
            单文件解析失败记 warning 并跳过（fail-open，坏 skill 不拖垮整体）。
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
            except (yaml.YAMLError, ValueError) as exc:
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
            ValueError: frontmatter 非 dict / 字段类型不符
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        name = meta.get("name") if isinstance(meta.get("name"), str) else fallback_name
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            description = self._first_paragraph(body)
        context = meta.get("context", SkillContext.INLINE)
        if context not in (SkillContext.INLINE, SkillContext.FORK):
            warnings.warn(
                f"skill {name} context 非法值 {context!r}，回落 inline"
            )
            context = SkillContext.INLINE
        allowed_tools = meta.get("allowed-tools")
        if not isinstance(allowed_tools, list):
            allowed_tools = []
        model = meta.get("model")
        if not isinstance(model, str):
            model = None
        thinking = meta.get("thinking")
        if not isinstance(thinking, bool):
            thinking = None
        max_iterations = meta.get("max-iterations")
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
            max_iterations = None
        if context == SkillContext.INLINE:
            inline_prompt = body
            agent_prompt = None
        else:
            inline_prompt = None
            agent_prompt = body
        return SkillRecord(
            name=name,
            description=description,
            context=context,
            inline_prompt=inline_prompt,
            agent_prompt=agent_prompt,
            model=model,
            thinking=thinking,
            allowed_tools=[str(t) for t in allowed_tools],
            max_iterations=max_iterations,
            source_path=path.resolve(),
        )

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 SKILL.md 拆成 (frontmatter dict, 正文)。

        Args:
            raw: 文件全文

        Returns:
            (frontmatter dict, 正文 str)；无 frontmatter 时返回 (空 dict, 全文)

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
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

修改 `src/agents/skills/__init__.py` re-export：

```python
"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext, SkillRecord

__all__ = ["SkillContext", "SkillLoader", "SkillRecord"]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_skill_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/skills/
git commit -m "feat: add SkillLoader with SKILL.md frontmatter parsing"
```

---

### Task 4: SkillRegistry — 聚合、冲突 fail-fast、to_tool_description、懒重载

**Files:**
- Create: `src/agents/skills/registry.py`
- Modify: `src/agents/skills/__init__.py`（re-export）
- Test: `tests/agents/skills/test_skill_registry.py`

**Interfaces:**
- Consumes: Task 3 `SkillLoader.load_all()`
- Produces: `class SkillRegistry`：`__init__(self, loader: SkillLoader)`；`reload_if_changed(self) -> None`（记录目录 signature，变化才重扫）；`get(self, name: str) -> SkillRecord | None`；`names(self) -> list[str]`；`to_tool_description(self, max_chars: int = 500) -> str`；`_last_signature` 属性。**不暴露测试辅助方法**（Task 5/6 引用；同名冲突走真实路径 `loader.load_all()` 返回同名记录 → reload 抛 ValueError）。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_registry.py`：

```python
"""测试 SkillRegistry — 聚合/冲突/to_tool_description/懒重载。"""

from pathlib import Path

import pytest

from src.agents.skills.loader import SkillLoader
from src.agents.skills.registry import SkillRegistry


def _write_skill(
    root: Path, name: str, context: str = "inline", body: str = "正文",
    frontmatter_name: str | None = None,
) -> None:
    """在 root/<name>/SKILL.md 写入合法 skill；frontmatter_name 覆盖 frontmatter 的 name。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fm_name = frontmatter_name if frontmatter_name is not None else name
    (d / "SKILL.md").write_text(
        f"---\nname: {fm_name}\ndescription: {name} 规则\ncontext: {context}\n---\n\n{body}",
        encoding="utf-8",
    )


def test_registry_loads_all(tmp_path):
    _write_skill(tmp_path, "a")
    _write_skill(tmp_path, "b", context="fork")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    assert set(reg.names()) == {"a", "b"}
    assert reg.get("a") is not None
    assert reg.get("missing") is None


def test_duplicate_frontmatter_name_fail_fast(tmp_path):
    """两个目录 frontmatter 声明同名 → reload 抛 ValueError（fail-fast 不静默覆盖）。

    这是真实冲突来源：目录名不同但 frontmatter name 相同，SkillRecord.name 取
    frontmatter name，注册表 key 冲突必须报错而非后者覆盖前者。
    """
    _write_skill(tmp_path, "dir-a", frontmatter_name="dup")
    _write_skill(tmp_path, "dir-b", frontmatter_name="dup")
    reg = SkillRegistry(SkillLoader(tmp_path))
    with pytest.raises(ValueError):
        reg.reload_if_changed()


def test_tool_description_lists_name_and_when_to_use(tmp_path):
    _write_skill(tmp_path, "finance-qa")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    desc = reg.to_tool_description()
    assert "finance-qa" in desc
    assert "finance-qa 规则" in desc


def test_tool_description_truncated_by_budget(tmp_path):
    """超出预算时截断（渐进披露，完整内容命中才加载）。"""
    for i in range(20):
        _write_skill(tmp_path, f"skill-{i}")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    desc = reg.to_tool_description(max_chars=120)
    assert len(desc) <= 120


def test_lazy_reload_detects_new_skill(tmp_path):
    """懒重载：目录变化（新增 skill）后 reload_if_changed 更新注册表。"""
    _write_skill(tmp_path, "a")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    assert reg.names() == ["a"]
    _write_skill(tmp_path, "b", context="fork")
    reg.reload_if_changed()
    assert set(reg.names()) == {"a", "b"}


def test_lazy_reload_detects_modified_content(tmp_path):
    """懒重载：已存在 skill 内容变化也重扫（文件级 signature，非仅目录 mtime）。"""
    _write_skill(tmp_path, "a", body="第一版")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    assert "第一版" in reg.get("a").inline_prompt
    (tmp_path / "a" / "SKILL.md").write_text(
        "---\nname: a\ndescription: a 规则\n---\n\n第二版内容", encoding="utf-8"
    )
    reg.reload_if_changed()
    assert "第二版内容" in reg.get("a").inline_prompt


def test_lazy_reload_no_change_keeps_records(tmp_path):
    """无变化时 reload 幂等，不重复解析。"""
    _write_skill(tmp_path, "a")
    reg = SkillRegistry(SkillLoader(tmp_path))
    reg.reload_if_changed()
    first = reg.get("a")
    reg.reload_if_changed()
    assert reg.get("a") is first
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_skill_registry.py -v`
Expected: FAIL（src.agents.skills.registry 不存在）

- [ ] **Step 3: 实现**

说明：注册表以 frontmatter name（缺省目录名）为 key；真实冲突来源是两个目录 frontmatter 声明同名，`reload_if_changed` 在聚合时检测并抛 ValueError。**不提供 `register_conflict_for_test` 之类测试辅助方法**（污染生产 API）。

`src/agents/skills/registry.py`：

```python
"""SkillRegistry — 聚合 SkillRecord 并按名索引（含懒重载）。

进程内注册表：SkillLoader 加载的 SkillRecord 统一收口。懒重载语义：
delegate_task 调用前（Task 6）先 reload_if_changed() —— 记录当前扫描 signature
（含目录列表与每个 SKILL.md 的 mtime），变化才重扫，避免每次 delegate 全量解析。

删除安全（design D17）：skill 被删 → reload 后 get() 返回 None → delegate_task
返回"skill 不存在"，主 agent 降级自己答。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillRecord


class SkillRegistry:
    """按名聚合 SkillRecord，提供懒重载与工具描述生成。"""

    def __init__(self, loader: SkillLoader) -> None:
        """初始化注册表。

        Args:
            loader: SkillLoader 实例（skills_root 已注入）
        """
        self._loader = loader
        self._records: dict[str, SkillRecord] = {}
        self._last_signature: str | None = None

    def reload_if_changed(self) -> None:
        """扫描 signature 变化时重载注册表（无变化则跳过）。

        signature = 目录列表 + 各 SKILL.md (相对路径, mtime_ns, 文件大小)。
        用内容相关 signature 而非目录 mtime：编辑子目录文件不改父目录 mtime，
        只查目录 mtime 会漏掉内容修改。

        Raises:
            ValueError: 两个目录 frontmatter 声明同名（skill 名冲突，fail-fast）
        """
        signature = self._compute_signature()
        if signature == self._last_signature:
            return
        loaded = self._loader.load_all()
        records: dict[str, SkillRecord] = {}
        for rec in loaded:
            if rec.name in records:
                raise ValueError(
                    f"skill 名称冲突: {rec.name}（来自 {records[rec.name].source_path} "
                    f"与 {rec.source_path}，fail-fast 禁止静默覆盖）"
                )
            records[rec.name] = rec
        self._records = records
        self._last_signature = signature

    def get(self, name: str) -> SkillRecord | None:
        """按名取 SkillRecord；不存在返回 None（调用方降级，design D17）。

        Args:
            name: skill 名

        Returns:
            SkillRecord 或 None
        """
        return self._records.get(name)

    def names(self) -> list[str]:
        """当前注册的全部 skill 名（有序，供 description/错误提示）。"""
        return sorted(self._records)

    def to_tool_description(self, max_chars: int = 500) -> str:
        """生成 delegate_task 的可用 skill 列表文本（渐进披露，预算截断）。

        每行一个 skill："<名>: <whenToUse>"。总长度超 max_chars 时截断并追加
        "..."，保证 delegate_task.description 不撑爆工具 schema 预算。

        Args:
            max_chars: description 长度上限（字符）

        Returns:
            description 文本；无 skill 时返回"当前无可用 skill"
        """
        if not self._records:
            return "当前无可用 skill"
        lines = [f"{rec.name}: {rec.description}" for rec in self._records.values()]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text

    def _compute_signature(self) -> str:
        """计算当前 skills_root 的扫描 signature（目录+文件级）。

        Returns:
            sha256 hex；目录不存在时返回空字符串签名
        """
        root = self._loader.skills_root
        if not root.exists():
            return "empty"
        parts: list[str] = []
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            path = skill_dir / "SKILL.md"
            if not path.exists():
                continue
            stat = path.stat()
            rel = path.relative_to(root)
            parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
```

修改 `src/agents/skills/__init__.py`：

```python
"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.registry import SkillRegistry

__all__ = ["SkillContext", "SkillLoader", "SkillRecord", "SkillRegistry"]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_skill_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/skills/
git commit -m "feat: add SkillRegistry with lazy reload and tool description"
```

---

### Task 5: SkillExecutor — inline 指令注入 / fork 零工具子代理执行（截断 + 超时）

**Files:**
- Create: `src/agents/skills/executor.py`
- Modify: `src/agents/skills/__init__.py`（re-export）
- Test: `tests/agents/skills/test_skill_executor.py`

**Interfaces:**
- Consumes: Task 2 `SkillRecord`/`SkillContext`；Task 1 常量（`DELEGATE_TIMEOUT`/`DELEGATE_RESULT_LIMIT`/`SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX`）。依赖 `src.models.get_llm`（fork model 覆盖）——**函数内 lazy import** 避免与 models 循环依赖。
- Produces: `class SkillExecutor`：`__init__(self, main_llm)`（主 agent llm 实例，fork model 空时复用）；`async def execute(self, record: SkillRecord, task: str) -> str`——inline 返回 `record.inline_prompt` 渲染（{task} 替换，无占位则原样返回）；fork 返回子代理纯文本（截断 ≤ DELEGATE_RESULT_LIMIT，超时返回 DELEGATE_TIMEOUT_TEXT）。`execute` **不感知 ctx、不推 SSE**（SSE 由 delegate_task 工具 fork 分支负责，Task 6）。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_skill_executor.py`（mock `create_react_agent`，不发真实 LLM 调用）：

```python
"""测试 SkillExecutor — inline 注入 / fork 子代理（零工具）/ 截断 / 超时。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import SSEInteractionTexts


def _record(**overrides) -> SkillRecord:
    """构造 SkillRecord，缺省 inline 形态。"""
    defaults = {
        "name": "test-skill",
        "description": "测试 skill",
        "context": SkillContext.INLINE,
        "inline_prompt": "方法论 {task}",
        "agent_prompt": None,
        "model": None,
        "thinking": None,
        "allowed_tools": [],
        "max_iterations": None,
        "source_path": Path("/tmp/test-skill/SKILL.md"),
    }
    defaults.update(overrides)
    return SkillRecord(**defaults)


@pytest.mark.asyncio
async def test_inline_returns_rendered_prompt():
    """inline：返回 inline_prompt，{task} 替换为任务文本。"""
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(_record(), task="计算毛利率")
    assert out == "方法论 计算毛利率"


@pytest.mark.asyncio
async def test_inline_without_placeholder_returns_as_is():
    """inline 无 {task} 占位：原样返回正文。"""
    rec = _record(inline_prompt="固定方法论，无需任务占位")
    exe = SkillExecutor(main_llm=MagicMock())
    out = await exe.execute(rec, task="任意任务")
    assert out == "固定方法论，无需任务占位"


@pytest.mark.asyncio
async def test_fork_reuses_main_llm_when_model_empty():
    """fork 且未声明 model：复用主 agent llm 实例（不新建）。"""
    main_llm = MagicMock()
    exe = SkillExecutor(main_llm=main_llm)
    rec = _record(
        context=SkillContext.FORK,
        agent_prompt="你是财务建模专家",
        inline_prompt=None,
        model=None,
    )

    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {
        "messages": [MagicMock(content="分析结果：营收下降 20%")]
    }

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create:
        out = await exe.execute(rec, task="分析年报")

    # 零工具硬保证：tools=[] 传入 create_react_agent
    args, kwargs = mock_create.call_args
    assert kwargs["tools"] == []
    assert kwargs["prompt"] == "你是财务建模专家"
    # 复用主 agent llm，未调 get_llm
    assert args[0] is main_llm
    assert "分析结果" in out


@pytest.mark.asyncio
async def test_fork_model_override_builds_new_llm():
    """fork 且声明 model：get_llm(model=record.model) 新建实例。"""
    fake_llm = MagicMock()
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="专家分析")]}

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create, patch(
        "src.agents.skills.executor.get_llm", return_value=fake_llm
    ) as mock_get_llm:
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="专家人格",
            inline_prompt=None,
            model="qwen3.8-max",
        )
        out = await exe.execute(rec, task="分析")
        mock_get_llm.assert_called_once_with(model="qwen3.8-max")
        args, kwargs = mock_create.call_args
        assert args[0] is fake_llm
        assert "专家分析" in out


@pytest.mark.asyncio
async def test_fork_result_truncated():
    """fork 结果超 DELEGATE_RESULT_LIMIT → 截断并带总字数提示。"""
    long_text = "字" * 3000
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content=long_text)]}

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
        )
        out = await exe.execute(rec, task="分析")
        assert "3000" in out
        assert len(out) < 2000  # 截断后带前缀，远小于 3000
        assert out.startswith(SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX[:10])


@pytest.mark.asyncio
async def test_fork_timeout_returns_timeout_text():
    """fork 超时 → 返回 DELEGATE_TIMEOUT_TEXT（asyncio.wait_for 兜底）。"""
    from src.agents.skills import executor as exec_mod
    from src.config.const import DELEGATE_TIMEOUT

    async def _never(*args, **kwargs):
        await asyncio.sleep(DELEGATE_TIMEOUT + 1)
        return {"messages": []}

    fake_sub = AsyncMock()
    fake_sub.ainvoke.side_effect = _never

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ), patch.object(exec_mod, "DELEGATE_TIMEOUT", 0.01):
        exe = SkillExecutor(main_llm=MagicMock())
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
        )
        out = await exe.execute(rec, task="分析")
        assert out == SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT


@pytest.mark.asyncio
async def test_fork_thinking_true_builds_llm_with_enable_thinking():
    """fork 且 thinking=True：新建 llm 时 extra_body.enable_thinking=True（D12 消费）。"""
    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create, patch(
        "src.agents.skills.executor.get_llm", return_value=fake_llm
    ) as mock_get_llm:
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=True,
        )
        await exe.execute(rec, task="分析")
        # model 空 → 继承主 agent model_name；thinking=True → extra_body enable_thinking
        kwargs = mock_get_llm.call_args.kwargs
        assert kwargs["model"] == "main-model"
        assert kwargs["extra_body"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_fork_thinking_false_builds_llm_with_thinking_off():
    """fork 且 thinking=False：新建 llm 且 enable_thinking=False（显式关思考）。"""
    fake_llm = MagicMock()
    fake_llm.model_name = "main-model"
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create, patch(
        "src.agents.skills.executor.get_llm", return_value=fake_llm
    ) as mock_get_llm:
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=False,
        )
        await exe.execute(rec, task="分析")
        kwargs = mock_get_llm.call_args.kwargs
        assert kwargs["extra_body"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_fork_thinking_none_reuses_main_llm():
    """thinking 未声明（None）：不新建 llm，复用主 agent 实例（D12 跟随主 agent）。"""
    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="分析")]}

    with patch(
        "src.agents.skills.executor.create_react_agent", return_value=fake_sub
    ) as mock_create, patch(
        "src.agents.skills.executor.get_llm"
    ) as mock_get_llm:
        main_llm = MagicMock()
        main_llm.model_name = "main-model"
        exe = SkillExecutor(main_llm=main_llm)
        rec = _record(
            context=SkillContext.FORK,
            agent_prompt="人格",
            inline_prompt=None,
            model=None,
            thinking=None,
        )
        out = await exe.execute(rec, task="分析")
        mock_get_llm.assert_not_called()
        args, _kwargs = mock_create.call_args
        assert args[0] is main_llm
        assert "分析" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_skill_executor.py -v`
Expected: FAIL（ModuleNotFoundError: src.agents.skills.executor）

- [ ] **Step 3: 实现**

`src/agents/skills/executor.py`：

```python
"""SkillExecutor — inline 指令注入 / fork 零工具子代理执行。

- inline：返回 skill 正文（render 后的方法论），主 agent 自己执行（不产生子代理）。
- fork：create_react_agent(llm, tools=[], prompt=agent_prompt) 生成独立零工具
  子代理，初始消息 = task，返回纯文本（不带 [n]）。零工具 = 防递归硬保证 + 不
  写共享 RequestContext.tool_contexts（design D7/D8/D9）。

可观测性（design D11）：fork 子代理复用主 agent 的 llm 实例（或 get_llm 新建实例）——
与主 agent **同级观测**（同一实例自带 callbacks；Langfuse 是否捕获取决于网关层，应用层
不新增 Langfuse 工作，见 design Risks「create_react_agent 观测缺口」）。本层不推 SSE
（由 delegate_task 工具 fork 分支负责）。

模型/思考（design D12）：skill 声明 model → get_llm(model=...) 新建；model 空 →
  继承主 agent 的 model_name。skill 声明 thinking → 新建实例 extra_body
  enable_thinking（显式开/关）；thinking 未声明 → 复用主 agent 实例（跟随主 agent，
  不覆盖其既有的 per-call thinking 行为）。

超时（design D13）：asyncio.wait_for(DELEGATE_TIMEOUT)，超时返回
DELEGATE_TIMEOUT_TEXT（ToolMessage 文本引导主 agent 基于现有上下文作答）。

截断（design D10）：fork 结果超 DELEGATE_RESULT_LIMIT → 截断为结构化摘要
（含总字数提示），防主 agent 上下文膨胀。
"""

import asyncio

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import (
    DELEGATE_RESULT_LIMIT,
    DELEGATE_TIMEOUT,
    SSEInteractionTexts,
)


class SkillExecutor:
    """按 skill context 分发执行：inline 注入 / fork 子代理。"""

    def __init__(self, main_llm) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 且 skill 未声明 model 时按
                model_name 继承复用；声明 model/thinking 时经 get_llm 新建）
        """
        self._main_llm = main_llm
        # 主 agent llm 为 ChatOpenAI 族（get_llm 产物），model_name 为标准属性；
        # 测试替身需显式设 main_llm.model_name（缺省 None 走 get_llm 默认模型）
        name = main_llm.model_name
        if not isinstance(name, str) or not name:
            name = None
        self._main_model_name = name

    async def execute(self, record: SkillRecord, task: str) -> str:
        """执行一个 skill，返回给主 agent 的文本。

        Args:
            record: 命中的 SkillRecord
            task: 主 agent 委托的任务描述（fork 时作子代理初始消息；inline 时填入
                inline_prompt 的 {task} 占位）

        Returns:
            inline：渲染后的方法论文本；fork：子代理纯文本（截断或超时文案）
        """
        if record.context == SkillContext.INLINE:
            return self._render_inline(record, task)
        return await self._run_fork(record, task)

    def _render_inline(self, record: SkillRecord, task: str) -> str:
        """渲染 inline_prompt：{task} 替换为任务文本（无占位则原样返回）。

        Args:
            record: inline SkillRecord
            task: 任务文本

        Returns:
            渲染后的方法论文本
        """
        prompt = record.inline_prompt or ""
        if "{task}" in prompt:
            return prompt.replace("{task}", task)
        return prompt

    async def _run_fork(self, record: SkillRecord, task: str) -> str:
        """fork 执行：零工具子代理深度分析。

        Args:
            record: fork SkillRecord
            task: 任务描述（子代理初始 HumanMessage）

        Returns:
            子代理纯文本；截断（>DELEGATE_RESULT_LIMIT）带总字数提示；超时返回
            DELEGATE_TIMEOUT_TEXT
        """
        llm = self._resolve_fork_llm(record)
        sub_agent = create_react_agent(
            llm,
            tools=[],  # 零工具硬保证（design D7）：防递归 + 不污染主 ctx
            prompt=record.agent_prompt,
        )
        try:
            result = await asyncio.wait_for(
                sub_agent.ainvoke({"messages": [HumanMessage(content=task)]}),
                timeout=DELEGATE_TIMEOUT,
            )
        except TimeoutError:
            return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
        messages = result.get("messages", []) if isinstance(result, dict) else []
        text = self._last_message_text(messages)
        return self._truncate(text)

    def _resolve_fork_llm(self, record: SkillRecord):
        """解析 fork 子代理的 llm 实例（model/thinking 消费，design D12）。

        Args:
            record: fork SkillRecord

        Returns:
            llm 实例：
            - model 或 thinking 任一声明 → get_llm(model=record.model 或主 agent
              model_name, extra_body.enable_thinking=record.thinking) 新建
              （新建实例才能携带 enable_thinking；复用实例无法改构造期 extra_body）
            - 两者均未声明 → 复用主 agent llm 实例（跟随主 agent）
        """
        if record.model is None and record.thinking is None:
            return self._main_llm
        from src.models import get_llm  # 函数内 import 避免循环依赖

        kwargs: dict = {}
        if record.model is not None:
            kwargs["model"] = record.model
        elif self._main_model_name is not None:
            kwargs["model"] = self._main_model_name
        if record.thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": record.thinking}
        return get_llm(**kwargs)

    def _last_message_text(self, messages: list) -> str:
        """取子代理结果最后一条消息的文本（AIMessage content str）。

        Args:
            messages: ainvoke 返回的消息列表

        Returns:
            content 的纯文本；无法提取时返回空串
        """
        if not messages:
            return ""
        last = messages[-1]
        content = last.content
        if not isinstance(content, str):
            content = str(content)
        return content

    def _truncate(self, text: str) -> str:
        """fork 结果截断（超阈值时带总字数提示）。

        Args:
            text: 子代理完整输出

        Returns:
            未超阈值原样返回；超阈值截断为 DELEGATE_TRUNCATED_PREFIX + 前 N 字
        """
        if len(text) <= DELEGATE_RESULT_LIMIT:
            return text
        truncated = text[:DELEGATE_RESULT_LIMIT]
        return SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX.format(
            total=len(text), truncated=truncated
        )
```

> 注：`create_react_agent` / `HumanMessage` 用**模块级 import**（Task 5/6 测试 patch `"src.agents.skills.executor.create_react_agent"` 依赖模块级名字存在；若留在函数内 import，patch 目标会 AttributeError）。

修改 `src/agents/skills/__init__.py`：

```python
"""skill 加载器/注册表/执行器实现包（主从委派能力）。

入口：make_delegate_task（Task 6）→ SkillRegistry（懒重载）+ SkillExecutor。
"""

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.registry import SkillRegistry

__all__ = [
    "SkillContext",
    "SkillExecutor",
    "SkillLoader",
    "SkillRecord",
    "SkillRegistry",
]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_skill_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/skills/
git commit -m "feat: add SkillExecutor with inline/fork zero-tool execution"
```

---

### Task 6: delegate_task 工具（inline/fork 双路径 + SSE 推送 + 注册进 make_rag_tools）

**Files:**
- Create: `src/agents/skills/delegate_task.py`
- Modify: `src/agents/tools/rag_tools.py`（make_rag_tools 注册 delegate_task；`__all__`）
- Modify: `src/agents/skills/__init__.py`（re-export make_delegate_task）
- Modify: `src/agents/graph/workflow.py`（build_graph 增 delegate_task 可选参并透传；make_rag_tools 为模块级 import，供测试 monkeypatch）
- Modify: `src/config/settings.py`（`SKILLS_DIR` 环境变量，可选）
- Modify: `src/core/log_events.py`（Event 枚举增 `DELEGATE_SKIP`）与 `src/core/log_event_specs.py`（EVENT_SPECS 登记，见 Q4）
- Modify: `src/services/agent_service.py`（AgentService.__init__ 接线 + `_convert_event` 加 status 分支；已核实位置）
- Test: `tests/agents/skills/test_delegate_task.py`
- Test: `tests/services/test_agent_service.py`（SSE status 分支转换，沿用该文件既有风格）
- Test: `tests/agents/graph/test_graph.py`（build_graph 透传接线守卫）

**Interfaces:**
- Consumes: Task 4 `SkillRegistry`；Task 5 `SkillExecutor`；Task 1 常量；ask_user 的 ctx 通道先例（`current_request_ctx.get().clarify_channel`）
- Produces: `make_delegate_task(skill_registry, executor) -> BaseTool`（langchain @tool `delegate_task`，args_schema=DelegateTaskArgs{task, skill}）。fork 分支内推 `{"type":"status","stage":"delegate","phase":"start"|"end"}` 到 ctx.clarify_channel；`_convert_event` 新增对 `{"type":"status"}` dict 的分支 → SSEStatusEvent。Task 7 依赖本工具的 fork 行为做 SSE 集成验证。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_delegate_task.py`：

```python
"""测试 delegate_task 工具 — inline 命中 / fork 命中 / 未知 skill / SSE 状态推送。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.skills.delegate_task import make_delegate_task
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.infra.llm.request_context import RequestContext, current_request_ctx


def _record(name: str, context: str, body: str, model: str | None = None) -> SkillRecord:
    if context == SkillContext.INLINE:
        return SkillRecord(
            name=name,
            description=f"{name} 规则",
            context=context,
            inline_prompt=body,
            agent_prompt=None,
            model=model,
            source_path=Path(f"/tmp/{name}/SKILL.md"),
        )
    return SkillRecord(
        name=name,
        description=f"{name} 专家",
        context=context,
        inline_prompt=None,
        agent_prompt=body,
        model=model,
        source_path=Path(f"/tmp/{name}/SKILL.md"),
    )


class _FakeRegistry:
    """测试用 SkillRegistry 替身（避免真实目录）。"""

    def __init__(self, records: dict):
        self._records = records
        self.reload_calls = 0

    def reload_if_changed(self) -> None:
        self.reload_calls += 1

    def get(self, name):
        return self._records.get(name)

    def names(self):
        return sorted(self._records)

    def to_tool_description(self, max_chars: int = 500) -> str:
        lines = [f"{n}: {r.description}" for n, r in self._records.items()]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3] + "..."
        return text


@pytest.mark.asyncio
async def test_inline_hit_returns_prompt_and_no_status():
    """inline 命中：返回渲染后方法论，不推 STAGE_DELEGATE 状态。"""
    rec = _record("finance-qa", SkillContext.INLINE, "请按规则作答：{task}")
    reg = _FakeRegistry({"finance-qa": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out = await tool.ainvoke({"task": "2024营收多少", "skill": "finance-qa"})
    finally:
        current_request_ctx.reset(token)

    assert "请按规则作答：2024营收多少" in out
    assert ctx.clarify_channel.empty()  # inline 不推状态


@pytest.mark.asyncio
async def test_fork_hit_pushes_start_and_end_status():
    """fork 命中：推 start + end 两条 STAGE_DELEGATE 状态。"""
    rec = _record("finance-analyst", SkillContext.FORK, "你是财务建模专家")
    reg = _FakeRegistry({"finance-analyst": rec})
    executor = SkillExecutor(main_llm=MagicMock())
    tool = make_delegate_task(reg, executor)

    fake_sub = AsyncMock()
    fake_sub.ainvoke.return_value = {"messages": [MagicMock(content="专家分析")]}

    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        with patch(
            "src.agents.skills.executor.create_react_agent", return_value=fake_sub
        ):
            out = await tool.ainvoke({"task": "分析年报", "skill": "finance-analyst"})
    finally:
        current_request_ctx.reset(token)

    assert "专家分析" in out
    phases = []
    while not ctx.clarify_channel.empty():
        item = ctx.clarify_channel.get_nowait()
        if item.get("type") == "status":
            assert item.get("stage") == "delegate"
            phases.append(item.get("phase"))
    assert phases == ["start", "end"]


@pytest.mark.asyncio
async def test_delegate_task_description_lists_skills():
    """delegate_task.description 列出可用 skill（spec 2.1 动态描述）。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    assert "finance-qa" in tool.description


@pytest.mark.asyncio
async def test_unknown_skill_returns_available_list():
    """未知 skill：返回"skill 不存在"+ 可用列表。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))

    out = await tool.ainvoke({"task": "x", "skill": "missing-skill"})
    assert "skill 不存在" in out
    assert "finance-qa" in out


@pytest.mark.asyncio
async def test_delegate_task_registered_with_name_and_schema():
    """工具名为 delegate_task，入参 schema 含 task/skill。"""
    rec = _record("finance-qa", SkillContext.INLINE, "方法论")
    reg = _FakeRegistry({"finance-qa": rec})
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    assert tool.name == "delegate_task"
    schema = tool.args_schema.model_fields
    assert "task" in schema and "skill" in schema
```

`tests/services/test_agent_service.py` 追加 status 分支测试（先读既有文件风格再贴，Step 3 提供最小用例）：

```python
def test_convert_status_dict_to_sse_status_event():
    """_convert_event 把 delegate fork 投递的 status dict 转 SSEStatusEvent。"""
    from src.services.agent_service import _convert_event
    from src.utils.sse import SSEStatusEvent
    from src.config.const import SSEInteractionTexts

    events = _convert_event(
        {"type": "status", "stage": "delegate", "phase": "start"}
    )
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SSEStatusEvent)
    assert ev.stage == SSEInteractionTexts.STAGE_DELEGATE
    assert ev.message == SSEInteractionTexts.DELEGATE_STATUS_START

    events = _convert_event(
        {"type": "status", "stage": "delegate", "phase": "end"}
    )
    assert events[0].message == SSEInteractionTexts.DELEGATE_STATUS_END


def test_convert_status_unknown_phase_uses_end_text():
    """未知 phase 回落 end 文案（防御，不抛）。"""
    from src.services.agent_service import _convert_event
    from src.config.const import SSEInteractionTexts

    events = _convert_event({"type": "status", "stage": "delegate", "phase": "???"})
    assert events[0].message == SSEInteractionTexts.DELEGATE_STATUS_END
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_delegate_task.py tests/services/test_agent_service.py -v`
Expected: FAIL（src.agents.skills.delegate_task 不存在；_convert_event status 分支未实现）

- [ ] **Step 3: 实现**

`src/agents/skills/delegate_task.py`：

```python
"""delegate_task 工具 — 主 agent 按需委派 skill（inline/fork 双执行路径）。

工具职责：
1. 调用前 reload registry（懒重载，design D16）
2. 按 skill 命中分发：unknown → 返回"skill 不存在"+ 可用列表；inline → 返回
   方法论（主 agent 自己答）；fork → SkillExecutor 跑零工具子代理
3. fork 执行期间经 ctx.clarify_channel 推 STAGE_DELEGATE 状态（start/end），
   inline 命中不推（design D14）；不走外层 astream_events 映射
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext
from src.agents.skills.registry import SkillRegistry
from src.config.const import SSEInteractionTexts
from src.infra.llm.request_context import current_request_ctx


class DelegateTaskArgs(BaseModel):
    """delegate_task 工具参数（LLM 可见的入参契约）。"""

    task: str = Field(description="要委派的任务描述（fork 深度任务需带主 agent 预检索的材料）")
    skill: str = Field(description="要调用的 skill 名（可用列表见工具描述）")


def make_delegate_task(
    skill_registry: SkillRegistry, executor: SkillExecutor
):
    """构建 delegate_task 工具。

    Args:
        skill_registry: SkillRegistry（懒重载；调用前 reload；to_tool_description
            生成工具 description 列可用 skill）
        executor: SkillExecutor（inline/fork 双执行）

    Returns:
        langchain @tool delegate_task
    """

    @tool(
        "delegate_task",
        args_schema=DelegateTaskArgs,
        description=(
            "调用领域专家 skill 处理任务后返回结果。判断当前任务需要领域专家能力"
            "（深度分析/专用方法论）时调用；轻量领域问题优先自己答，不要为每个问题委派。"
            f"可用 skill：\n{skill_registry.to_tool_description()}"
        ),
    )
    async def delegate_task(task: str, skill: str) -> str:
        """调用领域专家 skill 处理任务后返回结果。

        何时调用：判断当前任务需要领域专家能力（深度分析/专用方法论）时调用；
        轻量领域问题优先自己答，不要为每个问题委派。
        可用 skill 见工具描述（本 docstring 不直接给 LLM 展示，description 参数覆盖）。
        skill 的 context 决定执行方式：inline 返回方法论由你自己执行；fork 生成
        独立子代理深度分析后返回文本，由你整合进最终回答（引用仍指向你的检索来源）。

        Args:
            task: 任务描述（fork 深度分析需把预检索材料一并放入）
            skill: 要调用的 skill 名

        Returns:
            inline：方法论文本；fork：子代理分析文本（纯文本，无 [n]）；未知
            skill 返回错误提示 + 可用列表
        """
        skill_registry.reload_if_changed()
        record = skill_registry.get(skill)
        if record is None:
            available = ", ".join(skill_registry.names()) or "无"
            return SSEInteractionTexts.DELEGATE_UNKNOWN_SKILL.format(
                skill=skill, available=available
            )
        if record.context == SkillContext.INLINE:
            return await executor.execute(record, task)

        ctx = current_request_ctx.get()
        if ctx is not None:
            await ctx.clarify_channel.put(
                {"type": "status", "stage": SSEInteractionTexts.STAGE_DELEGATE, "phase": "start"}
            )
        try:
            return await executor.execute(record, task)
        finally:
            if ctx is not None:
                await ctx.clarify_channel.put(
                    {"type": "status", "stage": SSEInteractionTexts.STAGE_DELEGATE, "phase": "end"}
                )

    return delegate_task
```

> 已知取舍（design D5 实施边界）：工具 description 在 `make_delegate_task` 注册时由 `to_tool_description()` 生成一次。运行期**新增** skill 的文件不会自动进入已注册工具的 description（需重建图/重启）；**已注册** skill 的内容修改经 registry 懒重载即时生效（delegate_task 每次调用前 `reload_if_changed`）。这与 design D5「渐进披露」描述一致（只列当前注册集合并预算截断），把 description 更新到"每请求动态"留作后续迭代（见 Self-Review 已知取舍）。

修改 `src/agents/skills/__init__.py` 加 re-export：

```python
from src.agents.skills.delegate_task import make_delegate_task

__all__ = [
    "SkillContext",
    "SkillExecutor",
    "SkillLoader",
    "SkillRecord",
    "SkillRegistry",
    "make_delegate_task",
]
```

修改 `src/agents/tools/rag_tools.py`：

1. 文件顶部 module docstring 的「构建工具列表」说明不必改；`make_rag_tools` 函数签名追加参数并注册 delegate_task。修改后的签名与注册段：

```python
def make_rag_tools(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    reranker,
    prompt_manager,
    delegate_task: BaseTool | None = None,
) -> list[BaseTool]:
    """构建工具列表：注册表管理；retrieve_kb 始终注册（KB=RAG 开关在工具内实现）。

    Args:
        vector_store: 向量存储实例（闭包注入，search 使用）
        bm25: BM25 检索引擎实例（闭包注入，混合检索时使用）
        reranker: Reranker 模型实例（闭包注入，rerank_results 使用）
        prompt_manager: 提示词管理器（闭包注入，当前工具未直接使用，保留签名）
        delegate_task: 可选 delegate_task 工具（skill 库有内容时由调用方注入并注册；
            无 skill 时传 None 不注册，主 agent 工具集保持固定三件套）

    Returns:
        工具列表：retrieve_kb（知识库检索）、ask_user（澄清追问）；开启 web 兜底时追加
        search_web；delegate_task 非空时追加 delegate_task；经 ToolRegistry.enabled_tools() 过滤启用项
    """
    ...
    registry = ToolRegistry()
    registry.register("retrieve_kb", retrieve_kb)
    registry.register("ask_user", ask_user)
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web

        registry.register("search_web", search_web)
    if delegate_task is not None:
        registry.register("delegate_task", delegate_task)
    return registry.enabled_tools()
```

2. `__all__` 加入 `DelegateTaskArgs`（如需对外暴露；如无外部引用可不加，保持最小改动——仅 make_rag_tools 签名内引用 BaseTool，需在文件顶部确保 `BaseTool` 已 import（文件已有 `from langchain_core.tools import BaseTool, tool`，见现状第 14 行，无需改）。

修改 `src/services/agent_service.py`：

1. `_convert_event`（在 `if isinstance(item, dict) and item.get("type") == "ask_user"` 分支之后、`# 哨兵类已被 _dual_stream 提前消费` 之前）插入 status 分支：

```python
    if isinstance(item, dict) and item.get("type") == "status":
        stage = item.get("stage", "")
        if stage == SSEInteractionTexts.STAGE_DELEGATE:
            # delegate_task fork 分支经 ctx.clarify_channel 投递的 status dict →
            # SSEStatusEvent（inline 命中不投递，见 design D14）
            phase = item.get("phase")
            if phase == "start":
                message = SSEInteractionTexts.DELEGATE_STATUS_START
            else:
                message = SSEInteractionTexts.DELEGATE_STATUS_END
            return [SSEStatusEvent(stage=stage, message=message)]
        return []
```

2. `_QueueItem` TypeAlias 的注释（`# 合并队列元素类型：LangGraph 事件（StreamEvent）或 ask_user 事件 dict，或哨兵`）同步更新为 `或工具经 ctx.clarify_channel 投递的事件 dict（ask_user/status）`。

**接线（真实图注入，Critical）：** 只改 `make_rag_tools` 签名还不够——`workflow.build_graph` 默认路径（tools=None）裸调 `make_rag_tools(vector_store, bm25, reranker, prompt_manager)` 不会传 delegate_task，生产图永不注册该工具。接线方案：build_graph 增可选参 `delegate_task`，默认路径透传；AgentService.__init__ 构造 registry/executor 并传入。

修改 `src/agents/graph/workflow.py`：build_graph 签名加 `delegate_task: BaseTool | None = None`，tools 为 None 的默认分支改为：

```python
    if tools is not None:
        rag_tools = tools
    else:
        rag_tools = make_rag_tools(
            vector_store,
            bm25,
            reranker,
            prompt_manager,
            delegate_task=delegate_task,
        )
```

并在文件顶部 import 区补 `from langchain_core.tools import BaseTool`。测试 `test_graph_topology` 不传 delegate_task → 默认 None，节点集断言不变（delegate_task 是工具不是节点）。

修改 `src/services/agent_service.py` `AgentService.__init__`（现第 544 行 build_graph 调用处；需在文件顶部 import 区补 `from pathlib import Path`、`from src.config import settings` 或直接 `import os`，见下）：

```python
    from src.agents.skills import (
        SkillExecutor,
        SkillLoader,
        SkillRegistry,
        make_delegate_task,
    )

    self._vector_store = vector_store
    self._bm25 = bm25
    self._llm = llm or get_llm()
    self._reranker = reranker or get_rerank()
    self._chat_manager = chat_manager
    self._prompt_manager = prompt_manager or PromptManager()
    self._tracer = LangfuseTracer()

    # skill 委派（agent-delegation-skills）：SKILLS_DIR 环境变量覆盖，缺省项目根 skills/
    skills_dir = os.getenv("SKILLS_DIR")
    if not skills_dir:
        skills_dir = str(Path(__file__).resolve().parents[2] / "skills")
    delegate_task_tool = None
    if Path(skills_dir).exists():
        skill_registry = SkillRegistry(SkillLoader(Path(skills_dir)))
        skill_registry.reload_if_changed()  # description 在 make_delegate_task 时按当前注册表生成
        if skill_registry.names():
            skill_executor = SkillExecutor(self._llm)
            delegate_task_tool = make_delegate_task(skill_registry, skill_executor)
        else:
            # skills 目录存在但无任何 skill：delegate_task 不注册（描述会列空列表，注册无意义）
            core_logging.log_event(Event.DELEGATE_SKIP, reason="registry_empty")
    else:
        # skills 目录缺失（volume 未挂载/路径错）→ delegate 静默不可用需可观测
        core_logging.log_event(
            Event.DELEGATE_SKIP, reason="skills_dir_missing", skills_dir=skills_dir
        )

    self._graph: CompiledStateGraph = build_graph(
        vector_store,
        bm25,
        self._llm,
        self._reranker,
        self._prompt_manager,
        delegate_task=delegate_task_tool,
    )
```

（文件顶部已 import `os` 与 `Path`？现状 agent_service 顶部无此二者，需补 `import os` 与 `from pathlib import Path`。若走环境变量统一管理，可在 `src/config/settings.py` 加 `SKILLS_DIR: str = os.getenv("SKILLS_DIR", "")`，此段改用 `settings.SKILLS_DIR`。）

> **Event.DELEGATE_SKIP 登记（Q4，随本 Task 一并做）**：日志事件名/前缀/级别/字段集中在 `src/core/log_events.py`（Event 枚举）+ `src/core/log_event_specs.py`（EVENT_SPECS 注册表，import 期校验一致性）。新增：
>
> - `log_events.py` Event 枚举加成员：`DELEGATE_SKIP = "delegate skip"`；
> - `log_event_specs.py` EVENT_SPECS 加条目：`"delegate skip": EventSpec("delegate skip", "app", "warning", ("reason", "skills_dir"))`——skills 目录缺失/注册表为空属部署配置告警，用 `app` 层前缀（AgentService 初始化属应用装配层）。fields 的 `skills_dir` 在 registry_empty 分支不传（可选字段不序列化）。登记后跑一遍 `pytest tests/core/ -v`（如存在 log_event 一致性测试）确认枚举与注册表匹配。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_delegate_task.py tests/services/test_agent_service.py tests/core/test_log_events.py -v`
Expected: PASS（新增 Event.DELEGATE_SKIP 后须通过枚举/注册表一致性断言，否则 import 期校验炸）

先运行 `python -m src.cli.check_docs`（防腐）确认没把 status dict / stage 表述判为腐化；若报错则按 pyproject [tool.doc_anchors] exclude_symbols 处理。

再补一条接线级断言（skill 目录存在时 delegate_task 出现在工具列表；加到 tests/agents/skills/test_delegate_task.py）：

```python
def test_make_rag_tools_registers_delegate_when_provided(tmp_path):
    """make_rag_tools(delegate_task=...) 时工具列表含 delegate_task。"""
    from src.agents.skills.delegate_task import make_delegate_task
    from src.agents.skills.executor import SkillExecutor
    from src.agents.tools.rag_tools import make_rag_tools

    d = tmp_path / "skills" / "finance-qa"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: finance-qa\ndescription: 财务问答\ncontext: inline\n---\n\n方法论",
        encoding="utf-8",
    )
    from src.agents.skills.loader import SkillLoader
    from src.agents.skills.registry import SkillRegistry

    reg = SkillRegistry(SkillLoader(tmp_path / "skills"))
    reg.reload_if_changed()
    tool = make_delegate_task(reg, SkillExecutor(main_llm=MagicMock()))
    tools = make_rag_tools(None, None, None, None, delegate_task=tool)
    names = [t.name for t in tools]
    assert "delegate_task" in names


def test_make_rag_tools_without_delegate_keeps_fixed_set():
    """未传 delegate_task → 工具列表保持既有集合（无 delegate_task）。"""
    from src.agents.tools.rag_tools import make_rag_tools

    tools = make_rag_tools(None, None, None, None)
    names = [t.name for t in tools]
    assert "delegate_task" not in names
```

图级接线回归（Q2，补到 `tests/agents/graph/test_graph.py`）——真实生产链路是 AgentService → `build_graph(delegate_task=...)` → 默认分支 `make_rag_tools(..., delegate_task=...)`。这条链必须有测试守卫，否则未来改 build_graph 默认分支漏传参数时单测全绿、生产静默无 delegate_task：

```python
def test_build_graph_passes_delegate_task_to_rag_tools(monkeypatch):
    """build_graph 默认分支须把 delegate_task 透传给 make_rag_tools（接线守卫）。"""
    from unittest.mock import MagicMock, sentinel

    from src.agents.graph import workflow as wf

    captured = {}

    def fake_make_rag_tools(vector_store, bm25, reranker, prompt_manager, **kwargs):
        captured["delegate_task"] = kwargs.get("delegate_task")
        return []  # 空工具列表即可（本测试只验证透传，不跑图）

    monkeypatch.setattr(wf, "make_rag_tools", fake_make_rag_tools)
    wf.build_graph(
        MagicMock(),
        None,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        delegate_task=sentinel.delegate_tool,
    )
    assert captured["delegate_task"] is sentinel.delegate_tool
```

> 说明：现有 `test_graph_topology` 等调用 `build_graph(...)` 不传 delegate_task → 默认 None，节点集断言不变；本测试用 monkeypatch 短路 make_rag_tools，无需真建图。

- [ ] **Step 5: Commit**

```bash
git add src/agents/skills/ src/agents/tools/rag_tools.py src/agents/graph/workflow.py src/services/agent_service.py src/config/settings.py src/core/log_events.py src/core/log_event_specs.py tests/
git commit -m "feat: add delegate_task tool with inline/fork dispatch and SSE status push"
```

---

### Task 7: 迭代预算联动与 KB 溯源护栏 delegate 语义（预算 +2 / regen 复位 / 专家分析豁免）

**Files:**
- Modify: `src/agents/graph/state.py`（AgentState 加 `_delegate_used: bool`）
- Modify: `src/agents/graph/agent_node.py`（agent_model 置位 `_delegate_used`；route_agent 放宽判定）
- Modify: `src/agents/graph/verify/guardrails.py`（两处 regen dict 复位 `_delegate_used: False`；kb_citation_guardrail 加 EXPERT_ANALYSIS_MARKER 豁免）
- Modify: `src/agents/graph/verify/regen_decision.py`（regen dict 复位 `_delegate_used: False`）
- Modify: `src/config/const.py`（`MAX_AGENT_ITERATIONS` 语义注释补 delegate 说明，可选）
- Test: `tests/agents/graph/test_agent_node.py`（沿用既有风格）
- Test: `tests/agents/graph/test_verify_node.py`（断言 regen dict 复位 _delegate_used + EXPERT_ANALYSIS_MARKER 豁免回归）
- Test: `tests/agents/graph/test_graph.py`（沿用既有风格）

**Interfaces:**
- Consumes: Task 1 `MAX_DELEGATE_BONUS` / `EXPERT_ANALYSIS_MARKER`；现状 `route_agent(state) -> str`、`AgentState._max_agent_iterations`、guardrails/regen_decision 的 regen dict（当前只复位 `_agent_iterations: 0`）
- Produces: `AgentState._delegate_used: bool = False`；`route_agent` 判定改为 `_agent_iterations >= (_max_agent_iterations + MAX_DELEGATE_BONUS if _delegate_used else _max_agent_iterations)`；verify 三个 regen 产出 dict 统一带 `_delegate_used: False`（regen = 全新 5 轮预算语义不被 delegate 放宽放大）；`kb_citation_guardrail` 对含 `EXPERT_ANALYSIS_MARKER` 的分析答案豁免（M7，纯分析 fork 不被误触发补标 regen）。Task 9 冒烟依赖。

- [ ] **Step 1: 写失败测试**

先看既有 `tests/agents/graph/test_agent_node.py` / `test_state.py` 风格（已确认存在；该文件已有 MockChatModel/StubPromptManager/AIMessage import，无需新增 MagicMock），在 `tests/agents/graph/test_agent_node.py` 追加：

```python
def test_route_agent_delegate_budget_relaxed():
    """delegate 轮后：迭代上限 +2（整合余量），未 delegate 行为不变。"""
    from src.agents.graph.agent_node import route_agent
    from src.agents.graph.state import AgentState
    from src.config.const import MAX_AGENT_ITERATIONS, MAX_DELEGATE_BONUS

    # 未 delegate：达上限直接收尾
    s = AgentState()
    s._agent_iterations = MAX_AGENT_ITERATIONS
    s._delegate_used = False
    s.messages = [AIMessage(content="final")]
    assert route_agent(s) == "agent_finalize"

    # 已 delegate：达原上限仍给 +2 余量（有 tool_calls 走 tools）
    s2 = AgentState()
    s2._agent_iterations = MAX_AGENT_ITERATIONS
    s2._delegate_used = True
    tool_call_msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "retrieve_kb", "args": {"query": "x"}, "id": "1", "type": "tool_call"}
        ],
    )
    s2.messages = [tool_call_msg]
    assert route_agent(s2) == "tools"

    # 超过放宽后上限：收尾
    s3 = AgentState()
    s3._agent_iterations = MAX_AGENT_ITERATIONS + MAX_DELEGATE_BONUS
    s3._delegate_used = True
    s3.messages = [AIMessage(content="final")]
    assert route_agent(s3) == "agent_finalize"
```

在 `tests/agents/graph/test_agent_node.py` 追加置位行为测试（沿用该文件 MockChatModel + `@pytest.mark.asyncio` 风格，勿用 MagicMock+asyncio.run）：

```python
@pytest.mark.asyncio
async def test_agent_model_sets_delegate_used_flag():
    """agent 输出含 delegate_task tool_call → state._delegate_used 置位。"""
    fake_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "delegate_task",
                "args": {"task": "分析", "skill": "x"},
                "id": "t1",
                "type": "tool_call",
            }
        ],
    )
    llm = MockChatModel(fake_response)
    node = make_agent_model_node(llm, [], StubPromptManager())
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    out = await node(state)
    assert out["_delegate_used"] is True


@pytest.mark.asyncio
async def test_agent_model_non_delegate_keeps_flag_false():
    """普通工具轮不置位 _delegate_used（缺省保持 False）。"""
    fake_response = AIMessage(
        content="需要检索",
        tool_calls=[
            {"name": "retrieve_kb", "args": {"query": "x"}, "id": "t1", "type": "tool_call"}
        ],
    )
    llm = MockChatModel(fake_response)
    node = make_agent_model_node(llm, [], StubPromptManager())
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    out = await node(state)
    assert out.get("_delegate_used", False) is False
```

在 `tests/agents/graph/test_verify_node.py` 追加 regen 复位断言（mock verify_node 走 regen 分支，断言返回 dict 带 `_delegate_used: False`；参考该文件 `test_verify_node_missing_confirmed_regenerates` 的 ctx/state 构造与 monkeypatch 风格）：

```python
@pytest.mark.asyncio
async def test_kb_guardrail_regen_resets_delegate_used():
    """kb_citation_guardrail regen dict 复位 _delegate_used（防 regen 预算被 +2 放大）。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail
    from src.agents.graph.state import AgentState
    from src.infra.llm.request_context import RequestContext
    from src.rag.context import RAGContext

    ctx = RequestContext(
        session_id="s1",
        tool_contexts=[
            RAGContext(content="腾讯2024年营收3943亿元", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="d1:0", kind="kb")
        ],
    )
    state = AgentState(answer="腾讯2024年营收3943亿")
    state._delegate_used = True  # 模拟 delegate 已发生
    decision = await kb_citation_guardrail(state, ctx)
    assert decision is not None
    assert decision["_delegate_used"] is False  # regen=全新 5 轮预算
    assert decision["_agent_iterations"] == 0


@pytest.mark.asyncio
async def test_kb_guardrail_skips_expert_analysis():
    """纯分析型 fork 答案（含 EXPERT_ANALYSIS_MARKER）不被溯源护栏误触发 regen（M7）。"""
    from src.agents.graph.verify.guardrails import kb_citation_guardrail
    from src.agents.graph.state import AgentState
    from src.infra.llm.request_context import RequestContext
    from src.rag.context import RAGContext
    from src.config.const import EXPERT_ANALYSIS_MARKER

    ctx = RequestContext(
        session_id="s1",
        tool_contexts=[
            RAGContext(content="腾讯2024年营收3943亿元", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="d1:0", kind="kb")
        ],
    )
    state = AgentState(answer=f"建议关注流动性风险（{EXPERT_ANALYSIS_MARKER}，材料未覆盖）")
    assert await kb_citation_guardrail(state, ctx) is None
```

> 说明：guardrails 的 web_citation_guard / kb_citation_guardrail 与 regen_decision 的 regen dict 都只复位 `_agent_iterations: 0`，须同步补 `_delegate_used: False`（见 Step 3 第 4 点）；否则 delegate 放宽的 +2 会把每段 regen 的"全新 5 轮预算"放大成 7。M7 回归：kb_citation_guardrail 对含 EXPERT_ANALYSIS_MARKER 的分析答案豁免（Step 3 第 5 点）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/graph/test_agent_node.py -v`
Expected: FAIL（AgentState 无 `_delegate_used`；route_agent 无放宽）

再跑 `tests/agents/graph/test_verify_node.py -v` 确认新增回归用例目前失败（`_delegate_used` 断言不存在 / EXPERT_ANALYSIS_MARKER 豁免未生效）：
Expected: FAIL（regen dict 无 `_delegate_used` key → KeyError；含 EXPERT_ANALYSIS_MARKER 的分析答案仍被触发 regen）

- [ ] **Step 3: 实现**

`src/agents/graph/state.py` AgentState 内部字段区（`_max_agent_iterations` 旁）加：

```python
    _delegate_used: bool = False  # 本轮是否已调用过 delegate_task（来源：agent 节点在 LLM 输出含 delegate tool_call 时置位；范围：单轮执行；用途：route_agent 放宽迭代上限 +2 整合余量）
```

`src/agents/graph/agent_node.py`：

1. 在 `make_agent_model_node` 的 `agent_model` 内，流式聚合得到 `result` 后先算 `delegate_used`，再据此判定 ITERATION_LIMIT 日志与返回 dict（`_delegate_used` 本轮置位即放宽，与 route_agent 读到的 merged state 一致）：

```python
        delegate_used = any(
            call.get("name") == "delegate_task"
            for call in (result.tool_calls or [])
            if isinstance(call, dict)
        )
        # delegate 轮放宽上限（design D15）：本轮或此前已 delegate → 上限 +MAX_DELEGATE_BONUS
        effective_max = (
            state._max_agent_iterations + MAX_DELEGATE_BONUS
            if (delegate_used or state._delegate_used)
            else state._max_agent_iterations
        )
        if iteration >= effective_max:
            core_logging.log_event(
                Event.ITERATION_LIMIT, query=state.query, iteration=iteration
            )
        if state.messages:
            update_messages = [result]
        else:
            update_messages = [*messages, result]
        update = {
            "messages": update_messages,
            "_agent_iterations": iteration,
        }
        if delegate_used:
            # delegate 轮置位：route_agent 据此放宽迭代上限（design D15）
            update["_delegate_used"] = True
        return update
```

2. 顶部 import 加 `MAX_DELEGATE_BONUS`：

```python
from src.config.const import HISTORY_MAX_TURNS, HISTORY_TOKEN_RATIO, MAX_DELEGATE_BONUS
```

3. `route_agent` 放宽：

```python
def route_agent(state: AgentState) -> str:
    """agent 条件边：有 tool_calls 且未超限 → tools；否则 → agent_finalize。

    超限判定含 delegate 放宽：_delegate_used 置位时上限 +MAX_DELEGATE_BONUS
    （delegate 后主 agent 需整合子代理结果，design D15）；单请求总上限仍由
    verify 保险丝 + 图级 recursion_limit 兜底。

    Args:
        state: 当前图状态

    Returns:
        下一节点名："tools" 或 "agent_finalize"
    """
    if state._agent_iterations >= (
        state._max_agent_iterations + MAX_DELEGATE_BONUS
        if state._delegate_used
        else state._max_agent_iterations
    ):
        return "agent_finalize"
    if not state.messages:
        return "agent_finalize"
    last = state.messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "agent_finalize"
```

4. verify regen dict 统一复位 `_delegate_used`（M2）：regen 轮 = 全新 5 轮预算，`_delegate_used` 残留 True 会把每段 regen 的上限放大成 5+2=7，破坏既有"regen 复位主循环预算"语义。需改三处（每处都只复位 `_agent_iterations: 0`，未清 delegate 标志）：

   - `src/agents/graph/verify/guardrails.py` `web_citation_guard` 的 regen 返回 dict（现 `_agent_iterations: 0` 那处）补 `"_delegate_used": False`；
   - `src/agents/graph/verify/guardrails.py` `kb_citation_guardrail` 的 regen 返回 dict 同样补；
   - `src/agents/graph/verify/regen_decision.py` `decide_missing_web` 的 result dict（`_agent_iterations: 0` 那处）同样补。

   例 guardrails.py 中两处改后形如：

```python
    return {
        "answer": answer,
        "messages": [guidance],
        "_needs_regenerate": True,
        "_agent_iterations": 0,  # regen 轮复位主循环预算，route_agent 不吞本轮的 [n] 补标工具调用
        "_delegate_used": False,  # regen=全新 5 轮预算，不复位则 delegate 放宽 +2 会放大每段 regen 上限
    }
```

   regen_decision.py 的 result dict 同步补一行 `"_delegate_used": False`（在 `"_agent_iterations": 0,` 后）。

5. **kb_citation_guardrail 增加专家分析豁免（M7 回归前置）**：纯分析型 fork 答案（零 [n]、有 kb context、含 EXPERT_ANALYSIS_MARKER 措辞）不应被态 B 溯源护栏误触发补标 regen。在 `kb_citation_guardrail` 的直通条件里并入该短语判断（import `EXPERT_ANALYSIS_MARKER` from const）：

```python
    if (
        not _has_kb_context(ctx)
        or _answer_has_citation(answer)
        or _is_abstention_or_kb_uncovered(answer)
        or EXPERT_ANALYSIS_MARKER in answer  # 专家分析观点豁免（design D9 / M7）
    ):
        return None
```

> 该豁免把"含 EXPERT_ANALYSIS_MARKER 的分析答案"视为不需溯源的观点表述（与 4.1 引导的措辞约定配套：主 agent 复述专家分析时用该短语，则不被溯源护栏误伤；检索事实仍须带 [n]）。`guardrails.py` 顶部 import 区补 `EXPERT_ANALYSIS_MARKER`（与 VERIFY_KB_CITATION_MARKER 同处 import）。注意：该豁免是 verify 行为变化，需在 `tests/agents/graph/test_verify_node.py` 加回归用例（见 Step 1），并确认与既有 `test_kb_guardrail_*` 用例不冲突。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/graph/test_agent_node.py tests/agents/graph/test_verify_node.py tests/agents/graph/test_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/ tests/agents/graph/
git commit -m "feat: relax agent iteration budget by +2 after delegate round"
```

---

### Task 8: 主 agent 引导 prompt（何时 delegate vs 自己答 + 陈述区隔规则）

**Files:**
- Modify: `src/config/prompts.py`（FINANCIAL_SYSTEM_PROMPT 追加 delegate 引导段）
- Modify: `src/infra/llm/prompt_manager.py`（get_system_prompt 幂等追加 DELEGATE_GUIDANCE_SECTION——Langfuse 拉取的 system prompt 也须含引导段）
- Create: `tests/config/test_prompt_delegate.py`（断言引导段存在 + 区隔规则表述）

**Interfaces:**
- Consumes: 无（纯 prompt 内容）
- Produces: `DELEGATE_GUIDANCE_SECTION: str`（追加到 FINANCIAL_SYSTEM_PROMPT 的引导段常量）+ `FINANCIAL_SYSTEM_PROMPT` 尾部引用。Task 9 冒烟 + 未来 Langfuse prompt 同步文案来源。

- [ ] **Step 1: 写失败测试**

`tests/config/test_prompt_delegate.py`：

```python
"""测试主 agent delegate 引导 prompt 存在且含陈述区隔规则。"""

from src.config.const import EXPERT_ANALYSIS_MARKER
from src.config.prompts import DELEGATE_GUIDANCE_SECTION, FINANCIAL_SYSTEM_PROMPT


def test_delegate_guidance_section_exists():
    """引导段含：何时 delegate vs 自己答 + delegate_task 可用 skill 提示。"""
    assert "delegate_task" in DELEGATE_GUIDANCE_SECTION
    assert "何时" in DELEGATE_GUIDANCE_SECTION or "不要" in DELEGATE_GUIDANCE_SECTION


def test_financial_system_prompt_includes_delegate_guidance():
    """主系统 prompt 拼接了 delegate 引导段。"""
    assert DELEGATE_GUIDANCE_SECTION in FINANCIAL_SYSTEM_PROMPT


def test_delegate_guidance_contains_statement_distinction():
    """陈述区隔规则：检索事实引 [n]；专家分析不配 [n]（可标注经验分析）。"""
    text = DELEGATE_GUIDANCE_SECTION
    assert "检索" in text and "[n]" in text
    assert "不配 [n]" in text or "不标注" in text


def test_delegate_guidance_uses_expert_analysis_marker():
    """引导措辞必须含 EXPERT_ANALYSIS_MARKER 原文（kb_citation_guardrail 靠它豁免，M7）。"""
    assert EXPERT_ANALYSIS_MARKER in DELEGATE_GUIDANCE_SECTION
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/config/test_prompt_delegate.py -v`
Expected: FAIL（ImportError / AssertionError）

- [ ] **Step 3: 实现**

`src/config/prompts.py`（注意：`FINANCIAL_SYSTEM_PROMPT` 是模块顶层表达式，若改为 `"""...""" + DELEGATE_GUIDANCE_SECTION`，被引用的常量**必须先于其定义**，否则 import 期 NameError——把 `DELEGATE_GUIDANCE_SECTION` 定义移到 `FINANCIAL_SYSTEM_PROMPT` 之前）：

```python
# ====== delegate（主从委派）引导段 ======
# 追加到 FINANCIAL_SYSTEM_PROMPT 尾部（task 4.1，agent-delegation-skills change）。
# 职责：引导主 agent 判断"何时委派 vs 自己答"，并建立陈述区隔规则——
# 检索事实必须引 tool_contexts 的 [n]；专家分析/建议是观点表述，不配 [n]
# （可标注 EXPERT_ANALYSIS_MARKER 原文"基于领域经验的分析"），防 fork 观点被硬凑
# [n] 幻觉引用（design D9）；kb_citation_guardrail 依赖该短语豁免（const.py）。
DELEGATE_GUIDANCE_SECTION: str = """
委派（delegate_task）：
13. 判断当前问题是否需要领域专家能力（多步财务建模、深度分析）时，调用 delegate_task 委派给对应 skill；轻量领域问题先用 retrieve_kb 检索后自己答，不要为每个问题委派。
14. skill 的可用列表见 delegate_task 工具描述；委派深度分析任务时，先把已检索到的材料随 task 一并传入。
15. 委派 fork 返回的是专家分析文本（无引用编号），它不是检索来源：整合进最终回答时，凡引用数据/事实必须指向你自己的检索来源 [n]；专家观点与建议属分析表述，不配 [n]，必要时标注"基于领域经验的分析"。
"""

# 金融问答系统提示词 ...（原文）...
FINANCIAL_SYSTEM_PROMPT: str = """...原文...""" + DELEGATE_GUIDANCE_SECTION
```

即：新增 `DELEGATE_GUIDANCE_SECTION` 常量放在 `FINANCIAL_SYSTEM_PROMPT` **前面**；`FINANCIAL_SYSTEM_PROMPT` 末尾从 `"""` 改为 `""" + DELEGATE_GUIDANCE_SECTION`。

修改 `src/infra/llm/prompt_manager.py`（Langfuse prompt 兜底语义一致）：`get_system_prompt()` 里仿照 `INLINE_CITATION_INSTRUCTION` 的幂等追加逻辑，确保 `DELEGATE_GUIDANCE_SECTION` 也存在（Langfuse 拉取的 system prompt 若未包含引导段，追加一次；已含则不重复）：

```python
        prompt = self._get(self.PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)
        # 确保内联引用指令始终存在（无论 prompt 来自 Langfuse 还是本地兜底）
        if INLINE_CITATION_INSTRUCTION not in prompt:
            prompt += INLINE_CITATION_INSTRUCTION
        # 确保 delegate 引导段始终存在（Langfuse prompt 未更新时也生效，防委派能力不可见）
        if DELEGATE_GUIDANCE_SECTION not in prompt:
            prompt += DELEGATE_GUIDANCE_SECTION
        return _with_current_date(prompt)
```

import 区补 `DELEGATE_GUIDANCE_SECTION`。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/config/test_prompt_delegate.py -v`
Expected: PASS

先跑存量 prompt 相关测试防回归：

Run: `pytest tests/infra/llm/test_prompt_manager_fallback.py tests/config/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/prompts.py tests/config/test_prompt_delegate.py
git commit -m "feat: add delegate guidance section to main agent system prompt"
```

---

### Task 9: 首批 skill 内容（1 inline finance-qa + 1 fork finance-analyst）

**Files:**
- Create: `skills/finance-qa/SKILL.md`
- Create: `skills/finance-analyst/SKILL.md`
- Test: `tests/agents/skills/test_first_batch_skills.py`（用 SkillLoader/Registry 对真实 skills 目录做一次性校验）

**Interfaces:**
- Consumes: Task 3/4 Loader/Registry 语义（frontmatter 字段、正文按 context 归位）
- Produces: 顶层 `skills/` 内容库（业务侧管理），供 Task 6 注册 delegate_task 时扫描；Design D19 首批。fork 正文**不出现任何工具名**（零工具，见 D7/D9）。

- [ ] **Step 1: 写失败测试**

`tests/agents/skills/test_first_batch_skills.py`：

```python
"""测试首批 skill 内容符合 frontmatter/规模/零工具约束（真实 skills 目录）。"""

from pathlib import Path

from src.agents.skills.loader import SkillLoader
from src.agents.skills.models import SkillContext
from src.config.const import DELEGATE_RESULT_LIMIT, INLINE_PROMPT_MAX_CHARS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = PROJECT_ROOT / "skills"


def test_first_batch_skill_files_present():
    """skills/ 下存在 finance-qa 与 finance-analyst 两个 SKILL.md。"""
    assert (SKILLS_DIR / "finance-qa" / "SKILL.md").exists()
    assert (SKILLS_DIR / "finance-analyst" / "SKILL.md").exists()


def test_first_batch_loads_with_expected_context():
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    assert records["finance-qa"].context == SkillContext.INLINE
    assert records["finance-analyst"].context == SkillContext.FORK


def test_inline_prompt_size_bounded():
    """inline skill 正文 ≤ INLINE_PROMPT_MAX_CHARS（防上下文累积膨胀）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    qa = records["finance-qa"]
    assert len(qa.inline_prompt or "") <= INLINE_PROMPT_MAX_CHARS


def test_fork_prompt_must_not_mention_tool_names():
    """fork skill 正文不出现任何工具名（零工具子代理，见 design D7/D9）。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    analyst = records["finance-analyst"]
    body = analyst.agent_prompt or ""
    for tool_name in ("retrieve_kb", "search_web", "ask_user", "delegate_task"):
        assert tool_name not in body, f"fork skill 正文不应出现工具名 {tool_name}"


def test_fork_prompt_under_delegate_result_limit():
    """fork skill 正文（子代理 system_prompt）控制在合理规模内。"""
    records = {r.name: r for r in SkillLoader(SKILLS_DIR).load_all()}
    body = records["finance-analyst"].agent_prompt or ""
    assert len(body) <= DELEGATE_RESULT_LIMIT
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agents/skills/test_first_batch_skills.py -v`
Expected: FAIL（skills/ 目录不存在）

- [ ] **Step 3: 实现**

`skills/finance-qa/SKILL.md`：

```markdown
---
name: finance-qa
description: 财务知识库问答规则（何时使用：知识库已覆盖的一般财务事实问题；先检索后答）
context: inline
---

回答财务问题时：
1. 先调用 retrieve_kb 检索知识库，不要凭记忆作答。
2. 回答中的每个事实标注对应的年份/报告期。
3. 引用检索到的来源：在句末标注与检索返回一致的编号 [n]。
4. 检索不到或数据明显不完整时，如实说明证据不足，不要编造。
任务：{task}
```

`skills/finance-analyst/SKILL.md`：

```markdown
---
name: finance-analyst
description: 财务建模与深度分析专家（何时使用：需要多步建模、财务比率推算、趋势解读等深度分析；材料须由主 agent 预检索一并传入 task）
context: fork
model: qwen3.8-max
---

你是一名资深财务分析师。基于任务中给定的公司财务材料，完成深度分析。

分析要求：
1. 只基于 task 中提供的材料作答，不得假设材料之外的数据。
2. 输出结构化分析：关键指标趋势 → 驱动因素 → 风险点 → 结论建议。
3. 涉及比率/变化幅度时给出计算过程或明确说明依据，不得凭空给数。
4. 材料不足以支撑的推断，明确标注"材料未覆盖"。
5. 输出为纯分析文本，不包含引用编号；你的分析观点由主 agent 整合并决定如何引用检索来源。
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/agents/skills/test_first_batch_skills.py -v`
Expected: PASS

跑 `python -m src.cli.check_docs`：skills/ 不在 docs/agents 扫描范围，但 `src/agents/skills/` 新代码若被文档引用会被 T2 查——无需处理。

- [ ] **Step 5: Commit**

```bash
git add skills/
git commit -m "feat: add first-batch skills (finance-qa inline, finance-analyst fork)"
```

---

### Task 10: 防腐扩展 — skill frontmatter allowed-tools 工具名校验

**Files:**
- Modify: `src/cli/check_docs.py`（新增 skill 工具名校验函数 + main 接线）
- Modify: `pyproject.toml`（`[tool.doc_anchors]` 增加 exclude_skill_tools 排除表，供示例/伪代码）
- Test: `tests/cli/test_doc_consistency.py`（沿用既有风格追加）

**Interfaces:**
- Consumes: Task 3 的 frontmatter 字段语义（allowed-tools 键）；现状 check_docs 架构（_load_config/_iter_doc_lines/main）
- Produces: `_check_skill_tool_anchors(skills_root, known_tools, exclude) -> list[DocFinding]` + main() 接入。排除键 `exclude_skill_tools`。Task 11 文档登记依赖此脚本全量通过。

- [ ] **Step 1: 写失败测试**

在 `tests/cli/test_doc_consistency.py` 追加（tmp_path 由 pytest fixture 注入形参；先写文件再调校验函数）：

```python
def test_skill_tool_anchor_missing_tool(tmp_path):
    """frontmatter allowed-tools 引用代码中不存在工具名 → error 档。"""
    import pytest

    from src.cli import check_docs as cd

    skill_dir = tmp_path / "sample"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample\ndescription: 示例\nauthorized-tools: []\n"
        "allowed-tools: [retrieve_kb, ghost_tool]\n---\n\n正文",
        encoding="utf-8",
    )

    findings = cd._check_skill_tool_anchors(
        skills_root=tmp_path,
        known_tools={"retrieve_kb", "search_web"},
        exclude=set(),
    )
    errors = [f for f in findings if f.severity == "error"]
    assert any("ghost_tool" in f.anchor for f in errors)
    # retrieve_kb 在 known_tools → 不报
    assert not any(f.anchor == "retrieve_kb" for f in errors)


def test_skill_tool_anchor_exclude_suppresses(tmp_path):
    """allowed-tools 引用的工具名在排除表中 → 不报（示例/伪代码场景）。"""
    from src.cli import check_docs as cd

    skill_dir = tmp_path / "sample"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample\ndescription: 示例\n"
        "allowed-tools: [ghost_tool]\n---\n\n正文",
        encoding="utf-8",
    )

    findings = cd._check_skill_tool_anchors(
        skills_root=tmp_path,
        known_tools={"retrieve_kb", "search_web"},
        exclude={"ghost_tool"},
    )
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []


def test_skill_body_tool_names_exist_in_code():
    """skill 正文引用的工具名必须存在（Q3 叙述级防腐）。

    inline skill（finance-qa）正文引用 retrieve_kb 等指令，若工具改名而正文未
    同步，主 agent 收到指向不存在工具的注入指令。正文属叙述层不在 check_docs
    frontmatter 扫描范围，故在此用已知工具名词典比对正文（fork 正文应零工具名）。
    """
    import re

    from src.cli import check_docs as cd
    from src.agents.skills.loader import SkillLoader

    _TOOL_PATTERN = re.compile(r"(retrieve_kb|search_web|ask_user|delegate_task)")
    known = cd._collect_known_tool_names()
    skills_root = cd._PROJECT_ROOT / "skills"
    if not skills_root.exists():
        return  # skills 目录未建（Task 9 前）→ 跳过，Task 9 后必有
    for rec in SkillLoader(skills_root).load_all():
        body = (rec.inline_prompt or "") + (rec.agent_prompt or "")
        for tool_name in set(_TOOL_PATTERN.findall(body)):
            assert tool_name in known, (
                f"skill {rec.name} 正文引用工具 {tool_name} 但代码未注册（已改名/删除？）"
            )
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/cli/test_doc_consistency.py -v`
Expected: FAIL（cd._check_skill_tool_anchors 不存在）

- [ ] **Step 3: 实现**

`src/cli/check_docs.py`：

1. 顶部 docstring「排除规则」补一行 `exclude_skill_tools: skill frontmatter allowed-tools 中允许引用但代码不存在的工具名`。
2. 模块常量区加：

```python
_SKILLS_DIR = _PROJECT_ROOT / "skills"
```

3. 加函数（放 `_symbol_exists_in_code` 后、main 前）：

```python
def _collect_known_tool_names() -> set[str]:
    """收集代码中实际注册的工具名（registry.register / @tool("...") 字面量）。

    极简 AST/文本扫描 src/agents/tools/ 与 src/agents/skills/：匹配
    registry.register("<name>", ...) 与 @tool("<name>", ...) 字面量。

    Returns:
        实际工具名集合，如 {"retrieve_kb", "ask_user", "search_web", "delegate_task"}
    """
    import ast

    names: set[str] = set()
    roots = (_PROJECT_ROOT / "src" / "agents" / "tools", _PROJECT_ROOT / "src" / "agents" / "skills")
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__" in py.name:
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except OSError:
                continue
            # register("name", / @tool("name") 字面量
            for m in re.finditer(r'(?:register|tool)\(\s*"([^"]+)"', text):
                names.add(m.group(1))
    return names
```

4. 加校验函数：

```python
def _parse_skill_frontmatter_tools(path: Path) -> list[tuple[int, str]]:
    """从 SKILL.md 提取 allowed-tools 引用（行号 + 工具名）。

    Args:
        path: SKILL.md 文件路径

    Returns:
        [(行号, 工具名)] 列表
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    inside = False
    current: list[str] = []
    start_line = 0
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "---":
            if not inside:
                inside = True
                current = []
                start_line = idx
                continue
            # 闭合：解析收集到的 frontmatter 行
            body = "\n".join(current)
            tools: list[tuple[int, str]] = []
            for m in re.finditer(r"allowed-tools\s*:\s*\[([^\]]*)\]", body):
                for tool in re.findall(r'"([^"]+)"', m.group(1)):
                    tools.append((start_line, tool))
            return tools
        if inside:
            current.append(line)
    return []


def _check_skill_tool_anchors(
    skills_root: Path, known_tools: set[str], exclude: set[str]
) -> list[DocFinding]:
    """校验 skill frontmatter allowed-tools 引用的工具名是否在代码实际注册。

    design D18 防腐扩展：工具改名后 skill 若仍引用旧名会静默失效，本函数机械
    检出。只做单向存在性校验（skill 声称存在 → 代码必须找得到）。

    Args:
        skills_root: skills 内容库根目录
        known_tools: 代码中实际注册的工具名集合
        exclude: 允许引用但代码不存在的工具名（示例/伪代码，pyproject 排除表）

    Returns:
        error 档列表（引用代码不存在工具名的 skill frontmatter）
    """
    findings: list[DocFinding] = []
    if not skills_root.exists():
        return findings
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        path = skill_dir / "SKILL.md"
        if not path.exists():
            continue
        for lineno, tool_name in _parse_skill_frontmatter_tools(path):
            if tool_name in exclude or tool_name in known_tools:
                continue
            findings.append(
                DocFinding(
                    severity="error",
                    kind="skill_tool",
                    doc_file=f"skills/{skill_dir.name}/SKILL.md",
                    doc_line=lineno,
                    anchor=tool_name,
                    message=f"skill allowed-tools 引用工具 {tool_name} 但代码未注册（已改名/删除？）",
                )
            )
    return findings
```

5. `main()` 接线：加载排除表后收集 known_tools 并对 skills/ 校验，错误并入 errors（退出码语义不变）：

```python
    code_routes = _collect_code_routes()
    findings: list[DocFinding] = []

    for doc in doc_files:
        findings.extend(_check_path_anchors(doc, exclude_paths))
        findings.extend(_check_route_anchors(doc, code_routes, exclude_routes))
        findings.extend(_check_symbol_anchors(doc, exclude_symbols))

    # skill 防腐（design D18）：frontmatter allowed-tools vs 实际工具注册
    findings.extend(
        _check_skill_tool_anchors(
            _SKILLS_DIR,
            _collect_known_tool_names(),
            exclude_skill_tools,
        )
    )
```

6. `_load_config()` 返回元组增加 `exclude_skill_tools`（默认空集；pyproject 可覆盖），并把 main 调用处解包更新为 5 元组。**同步更新既有测试对 `_load_config()` 的 4 元组解包**（M8，否则既有用例先炸）：

   - `tests/cli/test_doc_consistency.py` 的 `_scan_all_docs`（现 `exclude_docs, exclude_paths, exclude_routes, _ = _load_config()`）改为 5 元组解包：`exclude_docs, exclude_paths, exclude_routes, _, _ = _load_config()`。
   - 同文件 `test_load_config_defaults`（现 4 元组）改为 `exclude_docs, _ep, _er, _es, _est = _load_config()`，并追加断言 `_est == set()`（pyproject 未配置时默认空集）。

同步 pyproject：

```toml
[tool.doc_anchors]
# exclude_skill_tools: skill frontmatter allowed-tools 中允许引用但代码不存在的工具名
#   （如文档/示例中的历史工具名）
exclude_skill_tools = []
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/cli/test_doc_consistency.py -v`
Expected: PASS

再全量跑防腐脚本确认不影响存量：

Run: `python -m src.cli.check_docs`
Expected: 0 error（首批 skill 未写 allowed-tools，扫描空转属预期）

- [ ] **Step 5: Commit**

```bash
git add src/cli/check_docs.py pyproject.toml tests/cli/test_doc_consistency.py
git commit -m "feat: extend anti-rot check to skill frontmatter allowed-tools"
```

---

### Task 11: 文档登记（glossary / CLAUDE.md / data-flow / api_contract）+ compose volume

**Files:**
- Modify: `docs/agents/glossary.md`（术语登记：skill / SkillRecord / inline 执行 / fork 执行 / delegate_task）
- Modify: `CLAUDE.md`（「代码目录结构」补 `skills/` 与 `src/agents/skills/` 说明；文档组织表登记 skill-delegation 归属文档若有新建）
- Modify: `docs/agents/data-flow.md`（链路 2 补 delegate 分支：主 agent → delegate_task → inline/fork → 回主 agent）
- Modify: `docs/agents/api_contract.md`（SSE stage=STAGE_DELEGATE 登记：推送时机/载荷/inline 不推/与 sse-tool-detail 边界）
- Modify: `docker-compose.prod.yml`（app 服务 volumes 加 `./skills:/app/skills`）
- Modify: `docker-compose.override.yml`（本地 dev 挂载 src，顺带 `skills` 目录可加 `- <project>/skills:/app/skills`，供本地联调热更新）
- Modify: `docs/agents/logging-rules.md`（如需登记 DELEGATE 相关日志前缀/事件，按现有协议追加；若本次无新日志事件可不改——检查实现是否引入新 Event，无则不改）
- Test: `tests/cli/test_doc_consistency.py` 与 `python -m src.cli.check_docs`（防腐门禁）

**Interfaces:**
- Consumes: Task 1-10 的全部新标识符（glossary 反引号符号会被 check_docs T2 校验，必须与代码一致）
- Produces: 文档契约同步完成 + 部署 volume 配置

- [ ] **Step 1: 写失败测试（防腐预跑）**

Run: `python -m src.cli.check_docs`
Expected: 记录当前 error（此时应 0）；若为 0 则此步只作基线。

- [ ] **Step 2: glossary.md 登记**

在 `docs/agents/glossary.md` 术语表追加（词条格式遵循该文件既有结构）：

```markdown
| skill | 磁盘声明式能力文件（skills/<name>/SKILL.md，frontmatter+正文） | agent-delegation-skills |
| SkillRecord | skill 文件解析后的运行时对象（name/description/context/prompt 等） | agent-delegation-skills |
| inline 执行 | skill context=inline：方法论注入主 agent，主 agent 自己执行 | agent-delegation-skills |
| fork 执行 | skill context=fork：create_react_agent 独立零工具子代理深度分析 | agent-delegation-skills |
| delegate_task | 主 agent 委派工具 delegate_task(task, skill)，按 skill context 分发 inline/fork | agent-delegation-skills |
```

- [ ] **Step 3: CLAUDE.md 更新**

「代码目录结构」树在 `agents/` 段落后补：

```
├── agents/       # LangGraph agent 循环：graph（workflow/state/nodes/agent_node）+ tools（retrieve_kb / ask_user / delegate_task）
├── agents/skills/  # skill 加载器/注册表/执行器（SkillRecord/Loader/Registry/Executor）
```

并在树下方或目录说明加一句：顶层 `skills/` 为运行时 skill 内容库（业务侧管理，volume 挂载），`.claude/skills/` 为开发期工具链 skill，两者语义不同。

- [ ] **Step 4: data-flow.md 更新**

链路 2（主问答 agent 循环）加 delegate 分支描述：

```
2a 绑定 KB RAG：用户 query → agent（route_agent）→ retrieve_kb → 回 agent …
2b 未绑定 KB 纯对话：query → agent → （可 search_web）→ …
   — delegate 分支：agent 判定需领域专家 → delegate_task(task, skill)
        ├─ inline：skill 方法论注入 agent 上下文，agent 继续走 2a/2b
        └─ fork：零工具子代理独立分析（材料由 agent 预检索塞 task）→ 纯文本回 agent
          → agent 整合（引用仍只指向自身检索来源）→ verify → format
```

- [ ] **Step 5: api_contract.md 登记**

登记 SSE status 事件 stage 枚举：`delegate`（仅 fork 命中推送）。字段：`{stage: "delegate", message: "正在调用领域专家分析..."/"领域专家分析完成"}`。推送时机：fork 子代理开始/结束时；inline 命中不推。载荷沿用 SSEStatusEvent（stage/message/可选 detail）。与 sse-tool-detail 的边界：本 stage 不带 detail，detail 结构由 sse-tool-detail change 定义。

- [ ] **Step 6: docker-compose volume 挂载**

`docker-compose.prod.yml` app service volumes 段（找 `src:/app/src` 或类似行）追加：

```yaml
      - ./skills:/app/skills
```

`docker-compose.override.yml` 的 app volumes 追加（路径按该文件既有绝对路径风格）：

```yaml
      - /mnt/d/code/demo/AIAgent/corporate_rag/skills:/app/skills
```

- [ ] **Step 7: 防腐验证 + 全量测试**

Run: `python -m src.cli.check_docs`
Expected: 0 error（新文档引用 src/agents/skills/* 与常量均已存在）

Run: `pytest tests/ -v`
Expected: 全绿（新文档引用符号已注册，无回归）

- [ ] **Step 8: Commit**

```bash
git add docs/agents/glossary.md docs/agents/data-flow.md docs/agents/api_contract.md CLAUDE.md docker-compose.prod.yml docker-compose.override.yml
git commit -m "docs: register skill delegation glossary/data-flow/api-contract and mount skills volume"
```

---

### Task 12: 端到端冒烟（inline/fork/verify 集成验证）

**Files:**
- 无新文件（运行级验证；如发现需小修则改对应实现/测试）
- Test: `tests/agents/graph/test_graph.py` 或 `tests/agents/skills/` 下追加一条集成断言（若可构造 mock 图）——优先手工冒烟脚本 + 存量 graph 测试兜底

**Interfaces:**
- Consumes: Task 1-11 全部
- Produces: 验收证据（inline 命中自己答 / fork 命中子代理分析 + 结果回流 + verify/format 引用正常；纯分析型 fork 答案不被 citation guard 误触发补标 regen）

- [ ] **Step 1: 手动冒烟（fork 路径）**

启动后对绑 KB 会话发送"对比腾讯与灿坤 2024 财务风险"类 query，观察：
1. 主 agent 先 retrieve_kb 预检索
2. 命中 delegate_task → 前端收到 `status(stage=delegate, message=正在调用领域专家分析...)`
3. fork 完成后收到 `status(stage=delegate, message=领域专家分析完成)`
4. 最终回答引用 [n] 均指向主 agent 自身检索来源；专家分析观点不配 [n]
5. 日志无污染（tool_contexts 编号未被子代理改写）

- [ ] **Step 2: 手动冒烟（inline 路径）**

发送一般财务事实问题触发 finance-qa inline skill：主 agent 自己答、无 STAGE_DELEGATE 推送；回答带检索 [n]。

- [ ] **Step 3: verify 无源观点豁免断言（M7，自动化回归已含）**

自动化回归：Task 7 Step 1 的 `test_kb_guardrail_skips_expert_analysis`（断言含 EXPERT_ANALYSIS_MARKER 的纯分析答案不被态 B 溯源护栏误触发 regen）已落 `tests/agents/graph/test_verify_node.py`。此处仅补手工确认：构造纯分析型 query（委托 fork 后主 agent 只给分析观点、无检索依据），确认主 agent 按 4.1 引导措辞"基于领域经验的分析"后答案不被补标 regen 卡住。

- [ ] **Step 4: 若发现问题修实现/测试并复跑**

Run: `pytest tests/agents/skills/ tests/agents/graph/ tests/services/ -v`
Expected: PASS

- [ ] **Step 5: Commit（如产生修复）**

```bash
git add -A
git commit -m "fix: smoke-test findings for agent delegation end-to-end"
```

---

## Self-Review

**1. Spec coverage（对照 spec/design/tasks）**

- skill-registry spec：文件结构（Task 9 内容 + Task 3 loader）、SkillRecord 字段（Task 2）、context 值约束回落 inline（Task 3）、正文按 context 解析 inline_prompt/agent_prompt（Task 3）、注册表冲突 fail-fast（Task 4，真实冲突 = 两目录 frontmatter 同名 → reload 抛 ValueError）、to_tool_description 预算截断（Task 4）、懒重载（Task 4，文件级 signature，内容修改也触发）、删除安全 get→None（Task 4 + delegate_task 返回"skill 不存在"Task 6）、防腐校验（Task 10）。
- delegate-task spec：delegate_task(task, skill)（Task 6）、**description 列可用 skill**（Task 6 `description=skill_registry.to_tool_description()` + 测试断言）、inline 返回指引 {task}（Task 5/6）、fork 零工具 create_react_agent(llm, tools=[], prompt=agent_prompt)（Task 5，模块级 import 使 patch 可达）、模型覆盖 get_llm(model=)（Task 5）、thinking 消费 enable_thinking（Task 5，M6 已补）、结果回流纯文本不带 [n]（Task 5 + 主 prompt 引导 Task 8）、截断（Task 5）、超时（Task 5）、防递归零工具硬保证（Task 5 tools=[]）、**真实图接线**（Task 6 workflow.build_graph + AgentService.__init__）。
- delegate-observability spec：SSE 委派状态仅 fork（Task 6，_convert_event status 分支）、常量归属 const（Task 1）、LLM 观测继承（Task 5 llm 实例复用 + 注释）、不产 MODEL_TURN（Task 5 设计注记，无代码——Langfuse 兜底）、预算放宽 +2（Task 7）+ **verify regen 复位 _delegate_used**（Task 7，guardrails/regen_decision 三处）。
- tasks.md 21 项全覆盖：1.1-1.5 → Task 2/3/4；2.1-2.9 → Task 6/5（2.9 工具集隔离验证由 test_fork_reuses_main_llm 断言 tools==[] 覆盖）；3.1-3.4 → Task 1/6/7；4.1-4.4 → Task 8/9/12（4.4 加自动化回归断言，M7）；5.1-5.5 → Task 10/11。

**2. Placeholder scan**：无 TBD/TODO；每步含测试与实现代码。Task 4 已删 `register_conflict_for_test` 假测试辅助，冲突走真实路径（两个目录 frontmatter 同名）。

**3. Type consistency**：
- `make_rag_tools(..., delegate_task=None)` 新增可选参，注册处用关键字传 `delegate_task=delegate_task_tool`；`workflow.build_graph(..., delegate_task=None)` 同形。测试与生产接线一致。
- `SSEInteractionTexts.STAGE_DELEGATE` 在 Task 1/6/7 全部引用一致。
- `route_agent` 放宽：AgentState._delegate_used 与 MAX_DELEGATE_BONUS 命名在 Task 1/7 一致；verify regen 三处复位字段同为 `_delegate_used: False`。
- SkillExecutor 构造签名 `SkillExecutor(main_llm)` 在 Task 5/6 一致；`make_delegate_task(skill_registry, executor)` 在 Task 6 定义并 re-export。
- 测试修复：Task 5 fork 测试 patch 目标与 executor 模块级 import 一致（避免 AttributeError）；Task 7 置位测试用文件既有 MockChatModel（非 MagicMock）；Task 10 测试声明 tmp_path fixture 并先构造文件（不再必挂）。
- 已知取舍（记录不阻塞）：delegate_task 工具 description 在注册时由 registry.to_tool_description() 生成一次（Task 6 已把 description 接上）；**运行期新增** skill 文件需图重建/重启才进工具 description，**已注册** skill 内容修改经懒重载即时生效。design Risks 未显式列此条，实施时可后续以每请求刷新 tool description 迭代。
- 可追溯性说明（已完成同步）：Task 7 的 `EXPERT_ANALYSIS_MARKER` 豁免与 regen 复位 `_delegate_used`、D20 真实图接线/DELEGATE_SKIP 装配告警，均已同步登记回 openspec change 文档——design.md（D9 豁免段 / D11 收敛 / D15 复位 / 新增 D20 / Risks 三条）、proposal.md（Impact 与 What Changes）、delegate-task spec（专家分析观点豁免场景）、delegate-observability spec（观测继承收敛 / verify regen 复位 / 装配告警两个场景）、tasks.md（2.10/3.4/3.5/4.1/4.4/5.1）。`openspec validate agent-delegation-skills` 通过。

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-07-agent-delegation-skills.md`. Two execution options:

1. **Subagent-Driven (recommended)** — 每个 Task 派发独立 subagent，任务间评审，快速迭代
2. **Inline Execution** — 本会话内用 executing-plans 批量执行 + checkpoint 评审

Which approach?
