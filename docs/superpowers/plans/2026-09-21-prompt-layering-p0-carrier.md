# prompt 分层与领域绑定（prompt-layering-and-domain-binding / P0 载体）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `src/config/prompts.py` 的 **12 条** prompt 常量逐字搬到 `src/config/prompts/templates/*.yaml`，用一个加载入口读回**逐字节相同**的文本，并让搬迁前后**最终 prompt 逐字不变**由 golden 测试证明。

**Architecture:** P0 是**纯搬运**，不含任何段拆分或措辞改动。做法分四步：① 先在**搬迁前**从现有代码路径生成 golden（模板层 12 条正文 + 组装层若干组合的最终 system 文本），把它作为不可修改的验收物入库；② 把 `prompts.py` 升级为包 `prompts/`（避免同名遮蔽），`__init__.py` 保留 re-export 使导入路径不变；③ 建 12 条模板并把消费点改为经加载入口读取；④ 删除已迁出的 12 条常量 —— 此时 golden 必须仍然全绿。段模型（六段拆分、条件注入、领域绑定）属 P1，**本计划不做**。

**Tech Stack:** Python 3.11+ / PyYAML / pytest（不发起真实网络调用）/ ruff / pyright

**Spec:** `docs/openspec/changes/prompt-layering-and-domain-binding/`（`proposal.md` / `design.md` / `specs/prompt-carrier/spec.md` / `specs/prompt-composition/spec.md`）。设计决策以 `design.md` 的 **D1（载体选 YAML）/ D5（P0 闸门）/ D12.6 Q1（12 迁 7 留）/ D12.6 Q2（kind/section/domain 字段）/ D12.7 Q10（PromptManager 收口）/ D12.8 Q12（体积只做事故兜底）** 为准。

## 阶段定位（重要，先读）

| 阶段 | 范围 | 状态 |
|---|---|---|
| **P0（本文件）** | 包化、12 条搬运模板 + 1 条新增 base-general 落地、加载入口、消费点接线、删常量、golden 闸门、启动期校验、分段字符数日志 | 本次 |
| P1 | 六段拆分与重组、`base` 三选一（替换）解析、检索阶梯搬到 `sources`、`knowledge_base.domain` + 领域 base、条件注入（工具集 AND 适用域）、verify 注入点接工具集、测试改写与基线重采 | 待 P0 落地后写 |
| P2 | 内容对齐 WeKnora（base 瘦身、完成条件、证据足够即停、数据·指令边界、`output` 四条的落地）、RAGAS + 三个症状指标 | 待 P1 落地后写 |

**P0 的边界（明确不做）**：不做段拆分/重组（P1）；不改任何 prompt 的**措辞**（逐字搬运，golden 为证）；不引入领域绑定与 `domain` 列（P1）；不改条件注入逻辑（P1）；不动 `VERIFY_*` / `FORK_*` 7 条常量（它们是行为键，留在 Python）；不动远端读取名单（P1 的 2.23）。

## 全局约束（每个任务都适用）

- **逐字搬运**：12 条搬运模板的 `content` 与搬迁前常量**逐字节相同**（含首尾换行、缩进、全角标点）。golden 一旦生成，**不得修改**。
- **第 13 条 `base-general` 是新增、不是搬运**：它不参与 P0 的 golden 比对（正文在 P1 定稿），但必须存在 —— 否则 Task 7 的"存在 `domain: general` 的 base 模板"校验无法通过。
- **不发起真实网络调用**（CLAUDE.md）：所有测试不得构造会走 Langfuse 的 `PromptManager()`；用替身或强制本地路径。
- **不改措辞**：本阶段任何"顺手改一句"都是违规 —— 那会让"搬运无损"的证明失效。
- **单位**：长度一律用**字符数**（`len(str)`），不写字节；字段名带 `chars`。
- **日志**：走 `docs/agents/logging-rules.md`；容器值用**紧凑 JSON（无空格）**；不新增逐段日志行。
- **不要用三元表达式**（CLAUDE.md），写完整 `if/else`。
- **不要在仓库根跑 `ruff format .`**（会重排大量既有 md 内的代码块）；只格式化改动的 `.py`。
- **单文件 ≤ 400 行、单函数 ≤ 80 行**；超过则拆。

## 文件结构（本阶段改动面）

```
src/config/prompts/                      # 由 prompts.py 升级而来（Task 2）
├── __init__.py                          # re-export（导入路径不变）+ 7 条行为键常量
├── loader.py                            # 加载入口（Task 4）
├── validation.py                        # 启动期校验（Task 7）
└── templates/                           # 13 条模板（Task 3：12 条搬运 + 1 条新增）
    ├── base-financial.yaml              # FINANCIAL_SYSTEM_PROMPT 的拼接前基础段
    ├── base-general.yaml                # 新增：domain=general 的占位 base（P1 定稿）
    ├── sources-kb-bound-discipline.yaml # KB_BOUND_RETRIEVAL_DISCIPLINE
    ├── sources-kb-unbound.yaml          # KB_UNBOUND_SYSTEM_PROMPT
    ├── output-inline-citation.yaml      # INLINE_CITATION_INSTRUCTION
    ├── tools-delegate-guidance.yaml     # DELEGATE_GUIDANCE_SECTION
    ├── task-user-prompt.yaml            # USER_PROMPT_TEMPLATE
    ├── task-classifier-system.yaml      # CLASSIFIER_SYSTEM_PROMPT
    ├── task-classifier-user.yaml        # CLASSIFIER_USER_TEMPLATE
    ├── task-rewrite-system.yaml         # REWRITE_SYSTEM_PROMPT
    ├── task-rewrite-user.yaml           # REWRITE_USER_TEMPLATE
    ├── task-entity-system.yaml          # ENTITY_EXTRACTION_SYSTEM_PROMPT
    └── task-entity-user.yaml            # ENTITY_EXTRACTION_USER_TEMPLATE

tests/fixtures/prompt_golden/            # golden（Task 1，搬迁前生成）
├── templates.json                       # 12 条正文 + 搬迁前常量名
└── assembly.json                        # 组装层若干组合的最终文本
tests/config/prompts/                    # 新增测试
├── test_golden_templates.py
├── test_golden_assembly.py
├── test_loader.py
└── test_templates_validation.py
```

**常量 → 模板 id 映射（搬迁前所在行号，逐字复制来源）**

| 常量 | `prompts.py` 行 | 模板 id | `kind` | `section` |
|---|---|---|---|---|
| `DELEGATE_GUIDANCE_SECTION` | 33–38 | `tools-delegate-guidance` | section | tools |
| `FINANCIAL_SYSTEM_PROMPT`（**仅拼接前的基础段**） | 42–62 | `base-financial` | section | base（`domain: finance`） |
| `KB_UNBOUND_SYSTEM_PROMPT` | 68–71 | `sources-kb-unbound` | section | sources |
| `KB_BOUND_RETRIEVAL_DISCIPLINE` | 74–77 | `sources-kb-bound-discipline` | section | sources |
| `USER_PROMPT_TEMPLATE` | 90–99 | `task-user-prompt` | task | — |
| `CLASSIFIER_SYSTEM_PROMPT` | 105–128 | `task-classifier-system` | task | — |
| `CLASSIFIER_USER_TEMPLATE` | 139–160 | `task-classifier-user` | task | — |
| `REWRITE_SYSTEM_PROMPT` | 166–184 | `task-rewrite-system` | task | — |
| `REWRITE_USER_TEMPLATE` | 186–198 | `task-rewrite-user` | task | — |
| `ENTITY_EXTRACTION_SYSTEM_PROMPT` | 206–214 | `task-entity-system` | task | — |
| `ENTITY_EXTRACTION_USER_TEMPLATE` | 218–239 | `task-entity-user` | task | — |
| `INLINE_CITATION_INSTRUCTION` | 248–251 | `output-inline-citation` | section | output |

**不迁的 7 条（留在 `__init__.py`）**：`VERIFY_GUIDANCE_PROMPT`(265)、`VERIFY_HINT_PROMPT`(276)、`VERIFY_CITATION_GUIDANCE_PROMPT`(285)、`VERIFY_KB_CITATION_GUIDANCE_PROMPT`(293)、`FORK_TASK_APPEND_TMPL`(305)、`FORK_DEFAULT_EXECUTOR_PROMPT`(310)、`FORK_EXECUTION_CONTRACT`(318)。

## ⚠ 已知事实与陷阱（先读这张表）

| # | 事实 / 陷阱 | 依据 | 怎么避 |
|---|---|---|---|
| **F1** | **`prompts.py` 与 `prompts/` 不能并存**：同名包会**遮蔽**同名模块，`from src.config.prompts import X` 会解析到包，模块符号全不可见 | `src/config/` 下现有 `prompts.py`（单文件）；Python FileFinder 对同一路径项**先查目录（包）再查文件** | Task 2 先 `git mv src/config/prompts.py src/config/prompts/__init__.py`，**不留旧文件**；Task 2 的验收就是"既有 import 全绿" |
| **F2** | **`FINANCIAL_SYSTEM_PROMPT` 是拼接值**：定义处 `"""…""" + DELEGATE_GUIDANCE_SECTION`（`prompts.py:42-64`） | 同左 | 模板 `base-financial` 只存**拼接前的基础段**（42–62 行的字符串本体），`tools-delegate-guidance` 单独一条；消费点照旧追加 → 既逐字不变，又不在 YAML 里存两份 |
| **F3** | **`tests/rag/test_prompt_layers.py` 在模块级 `import KB_BOUND_RETRIEVAL_DISCIPLINE`**（`:7-11`）并断言其在 content 中（`:72-78`） | 该文件实测 | 删常量会让**整个文件** import 失败（9 条测试全红）。Task 6 必须同时删该 import 与 `:72-78` |
| **F4** | **`tests/rag/test_prompt_layers.py:24-33` 与 `:100-107` 构造真实 `PromptManager()`** → 会走远端 → 真实网络请求 | 该文件实测；`settings.py` 默认 `LANGFUSE_ENABLE=true` | Task 1 的 golden 与 Task 5 的闸门**一律用替身 PM**（不构造真实 `PromptManager()`）；`:24`/`:100` 两条现有测试在 Task 6 一并去掉网络依赖 |
| **F5** | **`tests/rag/test_prompt_layers.py:59` 不需要改**（`assert "基础段正文" not in content`）—— 替换语义被保留，P1 后 `base` 即预设正文，该断言继续成立 | `design.md` 的自我更正（2026-09-21） | Task 6 **不要动** `:59`；它不是"保护缺陷" |
| **F6** | `KB_BOUND_RETRIEVAL_DISCIPLINE` 以 `"\n\n"` 开头、`INLINE_CITATION_INSTRUCTION` 以 `"\n"` 开头并以 `"\n"` 结尾 | `prompts.py:74-77`、`:248-251` | YAML 的 `content: |` 块标量**默认会吃掉尾部换行**；必须用 `content: |+`（keep）或在 golden 比对失败时按实际字节调整 —— Task 3 的验收就是"与 golden 逐字节相同" |
| **F7** | `_FALLBACK_SYSTEM_PROMPT = FINANCIAL_SYSTEM_PROMPT + INLINE_CITATION_INSTRUCTION`（`prompt_manager.py:31`）；`_FALLBACK_USER_TEMPLATE` 同理（`:32`） | 同左 | 迁移后这两个"兜底拼接"改为 `loader` 取两条模板再拼接，**拼接顺序与运算符必须一致** |
| **F8** | `prompt_manager.py:211` 用 `template.format(context=…, query=…)` 渲染 user 模板 —— `str.format` 遇未提供字段**抛 KeyError** | 同左 | 本阶段**不给任何模板加新占位符**（P1 才处理占位符语义统一）；`{context}` / `{query}` 等既有占位符原样保留 |
| **F9** | `src/cli/compare_rewrite.py:27` import `CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_TEMPLATE`，并在 `:544-545` 使用（其自有 `BUNDLED_CLASSIFY_*` 副本在 `:42`/`:73`，是"对比的另一边"） | 同左 | Task 6 把 `:27` 的 import 改为经 loader 取；**保留** `BUNDLED_CLASSIFY_*` 不动 |
| **F10** | `src/config/__init__.py:5` 的 docstring 用 `FINANCIAL_SYSTEM_PROMPT` 举例（**只是注释**，无代码依赖） | 该文件仅 `from src.config.settings import *` | Task 8 顺手把举例换成 YAML 模板 id，避免过时注释 |

---

### Task 1: 生成 golden（**必须在任何改动之前做**）

**Files:**
- Create: `tests/fixtures/prompt_golden/templates.json`
- Create: `tests/fixtures/prompt_golden/assembly.json`
- Create: `scripts/gen_prompt_golden.py`（一次性生成脚本，**保留在仓库**以便复现生成过程）
- Create: `tests/config/prompts/__init__.py`（空文件）
- Test: `tests/config/prompts/test_golden_templates.py`

**Interfaces:**
- Produces: `tests/fixtures/prompt_golden/templates.json` 的结构 —— `{"<模板id>": {"const": "<搬迁前常量名>", "content": "<逐字正文>"}, ...}`；`assembly.json` —— `{"<case名>": "<最终 system 拼接结果>", ...}`。

**为什么生成 golden 必须早于一切**：`design.md` D5/Q4 已定 —— P0 的"逐字不变"由 golden 证明；golden 一旦在搬迁后生成，就变成"用结果证明结果"，闸门归零。所以这是 Task 1。

- [ ] **Step 1: 写一次性生成脚本**

`scripts/gen_prompt_golden.py`：

```python
"""一次性生成 P0 的 prompt golden（迁移前运行，之后不得重跑覆盖）。

用法：python scripts/gen_prompt_golden.py
产物：tests/fixtures/prompt_golden/{templates,assembly}.json

设计要点（见 change 的 design.md D5 / D12.6 Q4）：
- golden 必须从**迁移前的常量路径**产出，故本脚本在 P0 落地后即失效（常量已删）。
- 一律用替身 PromptManager，不构造真实实例，避免走 Langfuse 远端（既有测试
  test_prompt_layers.py:24-33 就是因为构造真实实例才会发网络请求）。
"""

import json
from pathlib import Path

from src.agents.graph.verify import guardrails  # noqa: F401  (仅为断言其可导入)
from src.config import prompts as P
from src.rag.prompt import build_system_prompt

# 常量名 → 模板 id（与 plan 的映射表一致）
TEMPLATE_MAP: dict[str, str] = {
    "DELEGATE_GUIDANCE_SECTION": "tools-delegate-guidance",
    "FINANCIAL_SYSTEM_PROMPT": "base-financial",
    "KB_UNBOUND_SYSTEM_PROMPT": "sources-kb-unbound",
    "KB_BOUND_RETRIEVAL_DISCIPLINE": "sources-kb-bound-discipline",
    "USER_PROMPT_TEMPLATE": "task-user-prompt",
    "CLASSIFIER_SYSTEM_PROMPT": "task-classifier-system",
    "CLASSIFIER_USER_TEMPLATE": "task-classifier-user",
    "REWRITE_SYSTEM_PROMPT": "task-rewrite-system",
    "REWRITE_USER_TEMPLATE": "task-rewrite-user",
    "ENTITY_EXTRACTION_SYSTEM_PROMPT": "task-entity-system",
    "ENTITY_EXTRACTION_USER_TEMPLATE": "task-entity-user",
    "INLINE_CITATION_INSTRUCTION": "output-inline-citation",
}

# 拼接前的基础段：FINANCIAL_SYSTEM_PROMPT 末尾拼了 DELEGATE_GUIDANCE_SECTION（F2），
# golden 存"拼接前"的部分，模板也只存这一部分。
def _financial_base() -> str:
    """返回 FINANCIAL_SYSTEM_PROMPT 去掉尾部 DELEGATE_GUIDANCE_SECTION 的正文。"""
    full = P.FINANCIAL_SYSTEM_PROMPT
    tail = P.DELEGATE_GUIDANCE_SECTION
    if not full.endswith(tail):
        raise AssertionError("FINANCIAL_SYSTEM_PROMPT 尾部不是 DELEGATE_GUIDANCE_SECTION，"
                             "拼接假设已变，需重检 plan 的 F2")
    return full[: -len(tail)]


class _StubPromptManager:
    """替身：只实现 build_system_prompt 用到的方法，绝不触网（F4）。"""

    BASE_SENTINEL = "<BASE-SENTINEL>"

    def get_base_system_prompt(self) -> str:
        return self.BASE_SENTINEL

    def get_user_template(self, context: str = "", query: str = "") -> str:
        return P.USER_PROMPT_TEMPLATE.format(context=context, query=query)


def _templates() -> dict:
    result = {}
    for const_name, tid in TEMPLATE_MAP.items():
        if const_name == "FINANCIAL_SYSTEM_PROMPT":
            content = _financial_base()
        else:
            content = getattr(P, const_name)
        result[tid] = {"const": const_name, "content": content}
    return result


def _assembly() -> dict:
    """组装层 golden：固定输入下的最终 system 文本（分离 base 正文，只钉组装行为）。

    用 sentinel 作 base，使本层只证明"哪几段、什么顺序、什么分隔"，与正文内容解耦 ——
    正文由 templates.json 那一层钉死。
    """
    pm = _StubPromptManager()
    cases = {
        "A_unbound_no_persona": dict(persona="", kb_bound=False, has_skills=False),
        "B_unbound_with_persona": dict(persona="你是财务专家。", kb_bound=False, has_skills=False),
        "C_bound_no_persona": dict(persona="", kb_bound=True, has_skills=False),
        "D_bound_with_persona_no_skills": dict(
            persona="你是财务专家。", kb_bound=True, has_skills=False
        ),
        "E_bound_with_persona_and_skills": dict(
            persona="你是财务专家。", kb_bound=True, has_skills=True
        ),
    }
    out = {}
    for name, kwargs in cases.items():
        messages = build_system_prompt(prompt_manager=pm, **kwargs)
        # 多条 system 消息按序拼接后用 \n---\n 分隔，保留"几条消息"这一结构信息
        out[name] = "\n---\n".join(m.content for m in messages)
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "tests" / "fixtures" / "prompt_golden"
    target.mkdir(parents=True, exist_ok=True)
    (target / "templates.json").write_text(
        json.dumps(_templates(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (target / "assembly.json").write_text(
        json.dumps(_assembly(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"golden written to {target}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行脚本，生成 golden**

Run: `python scripts/gen_prompt_golden.py`
Expected: 打印 `golden written to …/tests/fixtures/prompt_golden`，生成两个 json。

- [ ] **Step 3: 目视核对 golden 的 12 条与源码一致**

Run: `python -c "import json;d=json.load(open('tests/fixtures/prompt_golden/templates.json'));print(len(d));print(sorted(d))"`
Expected: `12`，且 key 与映射表的模板 id 完全一致。

再抽查 3 条与源码逐字相同（`KB_BOUND_RETRIEVAL_DISCIPLINE` 开头应为 `\n\n检索纪律：`，`INLINE_CITATION_INSTRUCTION` 结尾应为 `\n`）：
Run: `python -c "import json;d=json.load(open('tests/fixtures/prompt_golden/templates.json'));print(repr(d['sources-kb-bound-discipline']['content'][:6]));print(repr(d['output-inline-citation']['content'][-3:]))"`
Expected: 第一行 `'\n\n检索'`，第二行以 `\n` 结尾（`F6` 的换行必须如实保留）。

- [ ] **Step 4: 写模板层闸门测试（此刻应通过）**

`tests/config/prompts/test_golden_templates.py`：

```python
"""P0 闸门（模板层）：12 条模板正文与搬迁前 golden 逐字相同。

设计依据：change prompt-layering-and-domain-binding 的 design.md D5 / D12.6 Q4。
本层是"搬运无损"的强证明：纯文本比对、不触网、不依赖组装逻辑。
迁移后 golden 不得修改（改动 golden 等于把闸门归零）。
"""

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "prompt_golden" / "templates.json"


@pytest.fixture(scope="module")
def golden() -> dict:
    """读取 golden（迁移前生成，之后不得重生成）。"""
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_has_twelve_templates(golden: dict) -> None:
    """黄金样本覆盖 12 条迁移常量。"""
    assert len(golden) == 12


def test_migrated_templates_match_golden_byte_for_byte(golden: dict) -> None:
    """每条模板的正文与 golden 逐字节相同（搬运无损的正证明）。"""
    from src.config.prompts import loader

    for template_id, entry in golden.items():
        actual = loader.get_content(template_id)
        assert actual == entry["content"], (
            f"模板 {template_id}（常量 {entry['const']}）搬运后与 golden 不一致；"
            f"长度 golden={len(entry['content'])} actual={len(actual)}"
        )
```

- [ ] **Step 5: 跑测试，确认**失败**（此时 loader 还不存在）**

Run: `pytest tests/config/prompts/test_golden_templates.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.config.prompts.loader'`（或 import 报错）。这正是 TDD 的"先红"。

- [ ] **Step 6: 提交（golden 与红测试一起入库）**

```bash
git add scripts/gen_prompt_golden.py tests/fixtures/prompt_golden tests/config/prompts
git commit -m "test(prompt): 生成 P0 golden 并加入模板层闸门（迁移前基线）"
```

---

### Task 2: `prompts.py` 升级为包（消除同名遮蔽）

**Files:**
- Move: `src/config/prompts.py` → `src/config/prompts/__init__.py`
- Modify: `src/config/__init__.py:5`（docstring 举例）

**Interfaces:**
- Produces: 包 `src.config.prompts`，其 `__init__` 仍导出全部原公开符号（本任务**不动符号**，只换容器形态）。

- [ ] **Step 1: 记录迁移前的 import 基线**

Run: `python -c "from src.config.prompts import FINANCIAL_SYSTEM_PROMPT, KB_BOUND_RETRIEVAL_DISCIPLINE, FORK_EXECUTION_CONTRACT; print('ok')"`
Expected: `ok`（后续 Task 结束时要能重复这一句）

- [ ] **Step 2: git mv 成包**

```bash
mkdir -p src/config/prompts_tmp
git mv src/config/prompts.py src/config/prompts_tmp/__init__.py
git mv src/config/prompts_tmp src/config/prompts
rmdir src/config/prompts_tmp 2>/dev/null || true
ls -la src/config/prompts/
```

⚠ **不得留下 `src/config/prompts.py`**（F1：并存会遮蔽包）。`ls` 应只看到 `__init__.py`。

- [ ] **Step 3: 验证导入路径不变**

Run: `python -c "from src.config.prompts import FINANCIAL_SYSTEM_PROMPT, KB_BOUND_RETRIEVAL_DISCIPLINE, FORK_EXECUTION_CONTRACT; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 跑既有相关测试，确认零回归**

Run: `pytest tests/rag tests/config tests/infra/llm tests/agents/skills -q`
Expected: 与 Task 1 之前**同样的通过数**（本步不改行为；`test_golden_templates.py` 仍红，属预期）。

- [ ] **Step 5: 更新 `src/config/__init__.py` 的 docstring 举例**

把 `from src.config.prompts import FINANCIAL_SYSTEM_PROMPT  # 提示词模板（推荐显式路径）` 改为指向包内模板（`F10`）：

```python
"""配置包 — 集中管理环境参数和 LLM 提示词。

使用方式：
  from src.config import MEMORY_WINDOW              # settings 中的常量
  from src.config.prompts import loader             # prompt 加载入口（模板见 prompts/templates/）
"""
```

- [ ] **Step 6: 提交**

```bash
git add src/config/prompts src/config/__init__.py
git commit -m "refactor(config): prompts.py 升级为 prompts 包，消除同名遮蔽风险"
```

---

### Task 3: 落地 12 条模板

**Files:**
- Create: `src/config/prompts/templates/*.yaml`（12 个）
- Test: `tests/config/prompts/test_templates_parse.py`

**Interfaces:**
- Produces: 12 个 YAML 文件，每个以 `templates:` 为根、含 `- id` / `kind` / `content`，`kind: section` 的另含 `section`（`base` 的另含 `domain`）。多个模板**可以放在同一个文件**里（本任务按"一个文件一个模板"建，便于 diff）。

- [ ] **Step 1: 建第一个模板并确认换行策略**

`src/config/prompts/templates/sources-kb-bound-discipline.yaml`（`F6`：该常量以 `\n\n` 开头、**结尾无换行**）：

```yaml
templates:
  - id: sources-kb-bound-discipline
    kind: section
    section: sources
    content: |-
      检索纪律：本会话已绑定知识库。实质性问题必须先调用 retrieve_kb 检索，
      根据检索到的文档内容作答，不要预先猜测问题是否在知识库范围内。
```

⚠ 正文的**首部两个换行**（`\n\n`）由"`content` 键后的换行 + 正文首行为空行"表达：即写成 `content: |+` 且正文第一行为空行，或写成 `content: "\n\n检索纪律：…"`。**以 golden 比对为准**（Step 3 会立刻暴露偏差）：先按下面任一写法，跑测试，不一致就按 golden 的实际字节调整 `|-` / `|+` / 引号写法。

- [ ] **Step 2: 建其余 11 条搬运模板 + 1 条新增 `base-general`（共 13 个文件）**

按计划的映射表逐条创建 **11** 条（`sources-kb-bound-discipline` 已在 Step 1 建），`content` 正文**逐字节复制**自 `src/config/prompts/__init__.py` 的对应行段（含全角标点、首尾换行与缩进；生成后会由 Task 1 的 golden 逐字校验）。四个需要特别小心的：

1. `base-financial.yaml` —— **只放拼接前的基础段**（`F2`；即 `FINANCIAL_SYSTEM_PROMPT` 去掉尾部 `DELEGATE_GUIDANCE_SECTION` 之后的部分，对应 `prompts/__init__.py:43-62`），并带 `domain: finance`：
   ```yaml
   templates:
     - id: base-financial
       kind: section
       section: base
       domain: finance
       content: |-
         你是一个智能问答助手，优先通过工具检索企业知识库回答用户问题，知识库无法覆盖时可联网搜索补充。

         处理流程：
         1. 闲聊、问候、感谢等无需资料的问题：直接回答，不调用任何工具
         …（此下逐字节复制 prompts/__init__.py:43-62，不得改动任何字符与标点）…
   ```
2. `output-inline-citation.yaml` —— 该常量**首尾都有 `\n`**，最容易在 YAML 里丢尾部换行（`F6`）。
3. `task-*.yaml` —— 6 条离线任务模板，`kind: task`、**不写 `section`**。
4. `base-general.yaml` —— **新增**（非搬运）：`kind: section` / `section: base` / `domain: general`，正文为占位文案，例如：
   ```yaml
   templates:
     - id: base-general
       kind: section
       section: base
       domain: general
       content: |-
         你是一个企业知识库问答助手，通过本轮可用的能力帮助用户理解信息、完成被请求的工作。
   ```
   ⚠ 该条**不在 golden 的 12 条里**，因此不参与逐字比对 —— 它的正文在 P1 才定稿。

- [ ] **Step 3: 写解析测试并跑（此刻应通过）**

`tests/config/prompts/test_templates_parse.py`：

```python
"""13 条模板文件可解析、字段合法、id 唯一（12 条搬运 + 1 条新增）。"""

from pathlib import Path

import pytest
import yaml

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "src" / "config" / "prompts" / "templates"
VALID_SECTIONS = {"base", "runtime_contract", "sources", "tools", "output"}


def _all_templates() -> list[dict]:
    """汇总所有模板文件里的模板条目。"""
    items: list[dict] = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name} 根节点不是映射"
        assert "templates" in data, f"{path.name} 缺少 templates 根键"
        for entry in data["templates"]:
            items.append({"_file": path.name, **entry})
    return items


def test_template_count_and_migrated_ids() -> None:
    """13 条 = 12 条搬运 + 1 条新增 base-general（见 design.md D12.6 Q1 与 spec 的 general 保留值）。"""
    templates = _all_templates()
    assert len(templates) == 13
    migrated = {
        "tools-delegate-guidance",
        "base-financial",
        "sources-kb-unbound",
        "sources-kb-bound-discipline",
        "task-user-prompt",
        "task-classifier-system",
        "task-classifier-user",
        "task-rewrite-system",
        "task-rewrite-user",
        "task-entity-system",
        "task-entity-user",
        "output-inline-citation",
    }
    assert migrated <= {t["id"] for t in templates}, "12 条搬运模板必须齐全"


def test_ids_are_unique() -> None:
    """id 全局唯一。"""
    ids = [t["id"] for t in _all_templates()]
    assert len(ids) == len(set(ids))


def test_section_templates_have_valid_section() -> None:
    """kind=section 的模板，section 取值在允许集合内。"""
    for t in _all_templates():
        if t["kind"] == "section":
            assert t["section"] in VALID_SECTIONS, t
        else:
            assert t["kind"] == "task", t
            assert "section" not in t, f"task 模板不应有 section：{t['id']}"


def test_base_templates_have_domain() -> None:
    """section=base 的模板必须声明 domain；通用 base 用保留值 general。"""
    for t in _all_templates():
        if t.get("section") == "base":
            assert t.get("domain"), f"{t['id']} 缺少 domain"


@pytest.mark.parametrize("tid", ["base-financial"])
def test_content_is_non_empty(tid: str) -> None:
    """段模板正文非空。"""
    entry = next(t for t in _all_templates() if t["id"] == tid)
    assert entry["content"].strip()
```

Run: `pytest tests/config/prompts/test_templates_parse.py -v`
Expected: PASS（5 项参数化/普通测试全绿）

- [ ] **Step 4: 提交**

```bash
git add src/config/prompts/templates tests/config/prompts/test_templates_parse.py
git commit -m "feat(prompt): 12 条 prompt 常量逐字落为 YAML 模板 + 新增 base-general 占位

模板含 kind/section/domain 字段声明；base-general 为 P1 通用 base 的占位，
不参与 P0 的 golden 比对（golden 只有 12 条搬运常量）。"
```

---

### Task 4: 实现加载入口

**Files:**
- Create: `src/config/prompts/loader.py`
- Test: `tests/config/prompts/test_loader.py`

**Interfaces:**
- Produces（后续任务依赖这些**精确签名**）：
  - `loader.get_content(template_id: str) -> str` —— 取模板正文（未找到抛 `KeyError`）
  - `loader.get_by_section(section: str) -> list[Template]` —— 按段取模板（P1 用；P0 仅需可调用）
  - `loader.get_domain_base(domain: str) -> str` —— 取 `section: base` + `domain` 匹配的正文（P1 用）
  - `loader.load_all() -> dict[str, Template]` —— 全部加载并校验（启动期与校验模块共用）
  - `loader.render(text: str, variables: dict[str, str]) -> str` —— **仅标识符式**替换，未提供的占位符**原样保留**
  - 数据类 `Template(id, kind, section, domain, content)`

- [ ] **Step 1: 写加载器的失败测试**

`tests/config/prompts/test_loader.py`：

```python
"""加载入口：读取、按段查询、占位符规则、失败语义。"""

import pytest

from src.config.prompts import loader


def test_get_content_returns_verbatim_template() -> None:
    """取到的正文与模板文件里的一致（首尾空白不丢）。"""
    content = loader.get_content("sources-kb-bound-discipline")
    assert content.startswith("\n\n检索纪律：")
    assert content.endswith("范围内。")


def test_get_content_unknown_id_raises() -> None:
    """未知 id 抛 KeyError，不返回空串。"""
    with pytest.raises(KeyError):
        loader.get_content("no-such-template")


def test_render_substitutes_known_placeholder() -> None:
    """已知占位符被替换。"""
    text = loader.render("你好 {name}", {"name": "世界"})
    assert text == "你好 世界"


def test_render_keeps_unknown_placeholder_verbatim() -> None:
    """未提供的占位符原样保留，不被替换为空串（spec <prompt-carrier>）。"""
    text = loader.render("你好 {name}，时间 {current_time}", {"name": "世界"})
    assert text == "你好 世界，时间 {current_time}"


def test_render_does_not_evaluate_expressions() -> None:
    """表达式写法当普通文本原样输出，不求值。"""
    text = loader.render("{% if x %}a{% endif %}", {})
    assert text == "{% if x %}a{% endif %}"


def test_get_by_section_returns_section_templates() -> None:
    """按段取模板，且不含 kind=task 的条目。"""
    items = loader.get_by_section("sources")
    ids = {t.id for t in items}
    assert "sources-kb-unbound" in ids
    assert all(t.kind == "section" for t in items)


def test_get_domain_base_returns_domain_text() -> None:
    """按领域取 base 正文。"""
    text = loader.get_domain_base("finance")
    assert text.startswith("你是一个智能问答助手")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/prompts/test_loader.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.config.prompts.loader'`

- [ ] **Step 3: 实现加载器**

`src/config/prompts/loader.py`：

```python
"""prompt 模板加载入口 —— YAML → 模板映射 → 段查询 → 文本渲染。

本模块是模板的唯一读取点（spec <prompt-carrier>「加载入口与占位符规则」「读取路径唯一化」）。

职责边界：
- 只负责"读模板 + 渲染占位符"；**不负责**段组装顺序与条件注入（那在 rag/prompt.py，P1 处理）
- 占位符只做**仅标识符式**替换（{identifier}），未提供的原样保留，不求值表达式
  （沿用既有否决：不引入 Jinja2，见 docs/tmp/deep-research-prompt-management.md 第四部分）
- 模板目录固定为 <包目录>/templates；不做远端数据源（终态预留，本期不实现）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"

VALID_SECTIONS: frozenset[str] = frozenset(
    {"base", "runtime_contract", "sources", "tools", "output"}
)

# 仅标识符式占位符：{name}，允许 ASCII 字母/数字/下划线
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Template:
    """一个 prompt 模板。

    Attributes:
        id: 模板唯一标识（kebab-case）；来源：YAML 的 id 字段；用途：按 id 取值
        kind: 段模板或任务模板（section|task）；来源：YAML；用途：决定是否参与段组装
        section: 段名（仅 kind=section）；来源：YAML；用途：段归属
        domain: 领域名（仅 section=base）；来源：YAML；用途：base 三选一（P1）
        content: 正文（逐字）；来源：YAML 的 content 块标量；用途：最终拼进 prompt
    """

    id: str
    kind: str
    content: str
    section: str | None = None
    domain: str | None = None


class TemplateLoadError(RuntimeError):
    """模板加载/校验失败（启动期透传，不留请求期降级分支）。"""


def _read_file(path: Path) -> list[Template]:
    """读取单个模板文件，返回其中的模板列表。

    Args:
        path: 模板文件路径

    Returns:
        该文件内的模板列表

    Raises:
        TemplateLoadError: YAML 无法解析，或根节点/条目结构非法
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateLoadError(f"模板文件不可读：{path}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateLoadError(f"模板 YAML 解析失败：{path}") from exc
    if not isinstance(data, dict) or "templates" not in data:
        raise TemplateLoadError(f"模板文件缺少 templates 根键：{path}")
    items = data["templates"]
    if not isinstance(items, list):
        raise TemplateLoadError(f"templates 不是列表：{path}")
    result: list[Template] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise TemplateLoadError(f"模板条目不是映射：{path}")
        tid = entry.get("id")
        kind = entry.get("kind")
        content = entry.get("content")
        if not isinstance(tid, str) or not tid:
            raise TemplateLoadError(f"模板缺少合法的 id：{path}")
        if kind not in {"section", "task"}:
            raise TemplateLoadError(f"模板 {tid} 的 kind 非法：{kind!r}")
        if not isinstance(content, str):
            raise TemplateLoadError(f"模板 {tid} 缺少正文")
        section = entry.get("section")
        domain = entry.get("domain")
        if kind == "section" and section not in VALID_SECTIONS:
            raise TemplateLoadError(f"模板 {tid} 的 section 非法：{section!r}")
        if section == "base" and not domain:
            raise TemplateLoadError(f"base 模板 {tid} 缺少 domain")
        result.append(
            Template(id=tid, kind=kind, content=content, section=section, domain=domain)
        )
    return result


@lru_cache(maxsize=1)
def load_all() -> dict[str, Template]:
    """加载全部模板（进程内缓存）。

    Returns:
        以模板 id 为键的映射

    Raises:
        TemplateLoadError: 目录不存在、YAML 非法、id 重复
    """
    if not TEMPLATES_DIR.is_dir():
        raise TemplateLoadError(f"模板目录不存在：{TEMPLATES_DIR}")
    mapping: dict[str, Template] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        for template in _read_file(path):
            if template.id in mapping:
                raise TemplateLoadError(f"模板 id 重复：{template.id}")
            mapping[template.id] = template
    if not mapping:
        raise TemplateLoadError(f"模板目录为空：{TEMPLATES_DIR}")
    return mapping


def get_content(template_id: str) -> str:
    """按 id 取模板正文。

    Args:
        template_id: 模板 id

    Returns:
        模板正文（逐字，不做任何处理）

    Raises:
        KeyError: 该 id 不存在
    """
    return load_all()[template_id].content


def get_by_section(section: str) -> list[Template]:
    """按段取全部段模板（不含 kind=task）。

    Args:
        section: 段名（base/runtime_contract/sources/tools/output）

    Returns:
        该段的模板列表（顺序为 id 字典序，稳定）
    """
    return [t for t in load_all().values() if t.kind == "section" and t.section == section]


def get_domain_base(domain: str) -> str:
    """按领域取 base 正文。

    Args:
        domain: 领域名（如 finance；通用用保留值 general）

    Returns:
        该领域的 base 正文

    Raises:
        KeyError: 无匹配模板
    """
    for template in get_by_section("base"):
        if template.domain == domain:
            return template.content
    raise KeyError(domain)


def render(text: str, variables: dict[str, str]) -> str:
    """仅标识符式替换占位符；未提供的占位符原样保留。

    Args:
        text: 含占位符的模板正文
        variables: 变量名 → 值

    Returns:
        替换后的文本（未提供的占位符保持 {name} 原样，不被替换为空串）
    """

    def _substitute(match: re.Match[str]) -> str:
        """单次匹配的替换回调：未提供则原样返回。"""
        name = match.group(1)
        if name in variables:
            return variables[name]
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_substitute, text)
```

- [ ] **Step 4: 跑加载器与闸门测试**

Run: `pytest tests/config/prompts/test_loader.py tests/config/prompts/test_templates_parse.py -v`
Expected: 全 PASS

Run: `pytest tests/config/prompts/test_golden_templates.py -v`
Expected: **应仍 FAIL** —— 加载器已能按 id 取模板，但 Task 3 的 YAML 换行写法若与 golden 有偏差，此处会暴露。**逐条按下例修正 YAML 的块标量写法**（`|-` / `|+` / 引号），直到该测试全绿：

Run: `pytest tests/config/prompts/test_golden_templates.py -q`
Expected: 全 PASS（12 条逐字一致）。⚠ 若某条一直不一致，**是 YAML 写法的问题，不是 golden 的问题** —— 严禁改 golden。

- [ ] **Step 5: 提交**

```bash
git add src/config/prompts/loader.py tests/config/prompts/test_loader.py src/config/prompts/templates
git commit -m "feat(prompt): 实现 prompt 加载入口（仅标识符式替换，未知占位符原样保留）"
```

---

### Task 5: 组装层闸门（证明"最终 prompt 逐字不变"）

**Files:**
- Create: `tests/config/prompts/test_golden_assembly.py`

**Interfaces:**
- Consumes: `tests/fixtures/prompt_golden/assembly.json`（Task 1 产出）、`src/rag/prompt.py:build_system_prompt` 现有签名 `(persona, kb_bound, has_skills, prompt_manager)`。
- Produces: 一个替身 PM 工厂 `_stub_pm()`（后续任务复用；**不得**构造真实 `PromptManager()`，F4）。

- [ ] **Step 1: 写组装层闸门测试**

`tests/config/prompts/test_golden_assembly.py`：

```python
"""P0 闸门（组装层）：固定输入下最终 system 文本与搬迁前 golden 逐字相同。

与模板层闸门的分工（见 plan 的 Task 1）：本层只钉"哪几段、什么顺序、什么分隔"，
base 正文用 sentinel 隔离，故本层不依赖任何具体 prompt 文案。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.rag.prompt import build_system_prompt

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "prompt_golden" / "assembly.json"
BASE_SENTINEL = "<BASE-SENTINEL>"


def _stub_pm() -> MagicMock:
    """替身 PromptManager：只实现 build_system_prompt 用到的方法，绝不触网（F4）。

    Returns:
        带 get_base_system_prompt / get_user_template 的 MagicMock
    """
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = BASE_SENTINEL
    pm.get_user_template.return_value = "用户模板"
    return pm


@pytest.fixture(scope="module")
def golden() -> dict:
    """读取组装层 golden（迁移前生成，不得重生成）。"""
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_assembly_matches_golden(golden: dict, subtests=None) -> None:
    """五种组合的最终 system 文本逐字相同。"""
    cases = {
        "A_unbound_no_persona": dict(persona="", kb_bound=False, has_skills=False),
        "B_unbound_with_persona": dict(persona="你是财务专家。", kb_bound=False, has_skills=False),
        "C_bound_no_persona": dict(persona="", kb_bound=True, has_skills=False),
        "D_bound_with_persona_no_skills": dict(
            persona="你是财务专家。", kb_bound=True, has_skills=False
        ),
        "E_bound_with_persona_and_skills": dict(
            persona="你是财务专家。", kb_bound=True, has_skills=True
        ),
    }
    for name, kwargs in cases.items():
        messages = build_system_prompt(prompt_manager=_stub_pm(), **kwargs)
        actual = "\n---\n".join(m.content for m in messages)
        assert actual == golden[name], f"组装层不一致：case={name}"
```

- [ ] **Step 2: 跑测试，确认此刻通过（尚未接线，走的还是常量路径）**

Run: `pytest tests/config/prompts/test_golden_assembly.py -v`
Expected: PASS（此刻 `rag/prompt.py` 仍读常量，与 golden 同源）

- [ ] **Step 3: 提交**

```bash
git add tests/config/prompts/test_golden_assembly.py
git commit -m "test(prompt): 加入 P0 组装层闸门（五组合逐字比对）"
```

---

### Task 6: 消费点接线 + 删除已迁出的 12 条常量

**Files:**
- Modify: `src/config/prompts/__init__.py`（删 12 条常量，保留 7 条）
- Modify: `src/rag/prompt.py:5-10,58-65`（改为经 loader 取）
- Modify: `src/infra/llm/prompt_manager.py:22-32,177,211,237-238`（`_FALLBACK_*` 与取值改经 loader）
- Modify: `src/agents/graph/verify/guardrails.py`（只改 import 来源，常量名不变）
- Modify: `src/infra/search/query_router.py`、`src/infra/search/document_entity_extractor.py`（改为经 loader 取）
- Modify: `src/cli/compare_rewrite.py:27,544-545`（`F9`）
- Modify: `tests/rag/test_prompt_layers.py`（`F3`：删 import 与 `:72-78`；`F4`：`:24-33`、`:100-107` 去网络依赖；**`F5`：不动 `:59`**）

**Interfaces:**
- Consumes: `loader.get_content(template_id)`、`loader.render(text, variables)`（Task 4）
- Produces: 模块级常量名不再存在于 `src.config.prompts`（除 7 条行为键）；`rag/prompt.py` 的公开签名**不变**。

- [ ] **Step 1: 改 `src/rag/prompt.py` 经 loader 取文本**

该文件现在从 `src.config.prompts` 导入 4 个已迁常量（`DELEGATE_GUIDANCE_SECTION`、`INLINE_CITATION_INSTRUCTION`、`KB_BOUND_RETRIEVAL_DISCIPLINE`、`KB_UNBOUND_SYSTEM_PROMPT`）。把导入整块替换为：

```python
from src.config.prompts import loader

# 模板正文经唯一加载入口读取（P1 会改为按段组装；P0 只是换读取来源，文本逐字不变）
_KB_BOUND_DISCIPLINE = loader.get_content("sources-kb-bound-discipline")
_KB_UNBOUND = loader.get_content("sources-kb-unbound")
_INLINE_CITATION = loader.get_content("output-inline-citation")
_DELEGATE_GUIDANCE = loader.get_content("tools-delegate-guidance")
```

然后把这 4 个名字在函数体内的引用逐个改名（`KB_BOUND_RETRIEVAL_DISCIPLINE` → `_KB_BOUND_DISCIPLINE`，以此类推）。**只改名与来源，不改任何拼接顺序、分隔符或判断条件** —— Task 5 的组装层闸门会逐字断言。

⚠ 模块导入期读文件是否可接受，由实现者定；若不接受，改为在 `build_system_prompt` 内按需取（`loader.load_all()` 有 `lru_cache`，每请求零 I/O）。**二选一，但必须与 Task 5 闸门一致**。

- [ ] **Step 2: 改 `prompt_manager.py` 的两个兜底拼接**

```python
_FALLBACK_SYSTEM_PROMPT: str = (
    loader.get_content("base-financial") + loader.get_content("output-inline-citation")
)
_FALLBACK_USER_TEMPLATE: str = loader.get_content("task-user-prompt")
```

⚠ 顺序与运算符必须与原实现一致（原来就是 `FINANCIAL_SYSTEM_PROMPT + INLINE_CITATION_INSTRUCTION`，`F7`）。

- [ ] **Step 3: 改离线任务的三处消费点**

- `src/infra/search/query_router.py`：`CLASSIFIER_SYSTEM_PROMPT` → `loader.get_content("task-classifier-system")`；`CLASSIFIER_USER_TEMPLATE` → `loader.get_content("task-classifier-user")`（原 `REWRITE_*` 亦同）
- `src/infra/search/document_entity_extractor.py`：改 `task-entity-system` / `task-entity-user`
- `src/cli/compare_rewrite.py:27`：改为 `from src.config.prompts import loader`，`:544-545` 处取 `loader.get_content("task-classifier-system")` / `"task-classifier-user"`；**保留** `BUNDLED_CLASSIFY_*`（`:42`/`:73`）不动（`F9`）

- [ ] **Step 4: 删 12 条常量**

在 `src/config/prompts/__init__.py` 中删除这 12 个名字：`DELEGATE_GUIDANCE_SECTION`、`FINANCIAL_SYSTEM_PROMPT`、`KB_UNBOUND_SYSTEM_PROMPT`、`KB_BOUND_RETRIEVAL_DISCIPLINE`、`USER_PROMPT_TEMPLATE`、`CLASSIFIER_SYSTEM_PROMPT`、`CLASSIFIER_USER_TEMPLATE`、`REWRITE_SYSTEM_PROMPT`、`REWRITE_USER_TEMPLATE`、`ENTITY_EXTRACTION_SYSTEM_PROMPT`、`ENTITY_EXTRACTION_USER_TEMPLATE`、`INLINE_CITATION_INSTRUCTION`。

**保留**：`VERIFY_GUIDANCE_PROMPT`、`VERIFY_HINT_PROMPT`、`VERIFY_CITATION_GUIDANCE_PROMPT`、`VERIFY_KB_CITATION_GUIDANCE_PROMPT`、`FORK_TASK_APPEND_TMPL`、`FORK_DEFAULT_EXECUTOR_PROMPT`、`FORK_EXECUTION_CONTRACT`。

- [ ] **Step 5: 改测试（按 F3/F4/F5 的边界）**

`tests/rag/test_prompt_layers.py`：
- 删 `:7-11` 对 `KB_BOUND_RETRIEVAL_DISCIPLINE` 的模块级 import
- 删/改 `:72-78` 的断言（该常量已不存在）；改为断言"绑库时最终 prompt 含检索纪律的关键词"，用 loader 取值比对：
  ```python
  def test_persona_bound_keeps_retrieval_discipline():
      """选定 agent 且绑定 KB → 环境约束层注入检索纪律（人设被替换后仍强制叠加）。"""
      pm = _pm(base="基础段正文")
      messages = build_system_prompt(
          persona="你是财务专家。", kb_bound=True, has_skills=False, prompt_manager=pm
      )
      discipline = loader.get_content("sources-kb-bound-discipline")
      assert discipline in messages[0].content
  ```
- `:24-33`、`:100-107` 去掉真实 `PromptManager()`：改为 `_pm()` 替身（`F4`）
- **不要动 `:59`**（`F5`）

其他受影响测试（`prompt-mapping`/`tasks` 已列的清单）：`tests/config/test_prompt_web_search.py`（9 短语断言改为对 loader 取出的对应模板断言）、`tests/config/test_prompt_delegate.py:4,15`、`tests/infra/llm/test_prompt_manager_fallback.py:4-5,16,21`。

- [ ] **Step 6: 跑全套闸门 + 相关测试**

Run: `pytest tests/config/prompts tests/rag tests/config tests/infra/llm -q`
Expected: 全 PASS，**包括 Task 1 的模板层与 Task 5 的组装层闸门**（这是"搬运无损"的最终证明）

- [ ] **Step 7: 确认常量已真删、导入路径仍可用**

Run: `python -c "from src.config.prompts import VERIFY_GUIDANCE_PROMPT, FORK_EXECUTION_CONTRACT; print('kept ok')"`
Run: `python -c "from src.config.prompts import FINANCIAL_SYSTEM_PROMPT" ; echo "exit=$?"`
Expected: 第一行 `kept ok`；第二行报 `ImportError` 且 `exit=1`（证明已删除，无并存副本）

- [ ] **Step 8: 质量门禁**

Run: `ruff check . --fix && ruff format src/ tests/ scripts/gen_prompt_golden.py`
Run: `pyright src/`
Run: `pytest tests/ -q`
Expected: `ruff` 无错误；`pyright` 不引入**新** error（存量第三方误报不算）；`pytest` 全绿

- [ ] **Step 9: 提交**

```bash
git add -A
git commit -m "refactor(prompt): 消费点改经加载入口，删除已迁出的 12 条常量

契约变更：src.config.prompts 不再导出 12 个 prompt 常量；模板改经
src.config.prompts.loader 读取（正文逐字相同，由 golden 测试证明）。
保留 7 条行为键常量（VERIFY_* / FORK_*）—— 它们是查重 marker 与子代理
执行契约，不走模板化。
tests/rag/test_prompt_layers.py:59 未改：其断言在新契约下仍成立。"
```

---

### Task 7: 启动期加载与校验（失败即启动失败）

**Files:**
- Create: `src/config/prompts/validation.py`
- Modify: `src/main.py`（启动时调用校验）
- Modify: `src/config/settings.py`（新增总字符数上限与占比阈值配置）
- Test: `tests/config/prompts/test_validation.py`

**Interfaces:**
- Consumes: `loader.load_all()`、`loader.TemplateLoadError`
- Produces:
  - `validation.validate_all() -> dict[str, int]` —— 校验并返回**各段模板字符数之和**（`{"base": n, ...}`）；失败抛 `loader.TemplateLoadError`
  - `validation.SECTION_CHARS_LIMIT: int = 50000` —— **事故兜底**上限（模块级常量，便于测试 monkeypatch）
  - `settings.PROMPT_TOKENS_PER_CJK_CHAR: float = 0.6` —— **跨模型借用**系数，须注明口径与局限
  - `settings.PROMPT_CONTEXT_SHARE_WARN: float = 0.05` —— **软告警**阈值（推断值，不得写进契约）
  - `settings.MODEL_CONTEXT_WINDOW_TOKENS: int` —— 所配模型的上下文窗口（用于估算占比）

⚠ **P0 的 `section_chars` 是"模板层"口径**（各段模板正文字符数之和），**不是**"实际参与组装的段"—— 因为 P0 尚未做段组装（P1 才有）。P1 应把它改为"实际拼进 system 的各段字符数"，本计划在 Task 8 会标注这一点。

- [ ] **Step 1: 写校验的失败测试**

`tests/config/prompts/test_validation.py`：

```python
"""启动期校验：字段合法、段完整、general 存在、字符数事故兜底。"""

import pytest

from src.config.prompts import loader, validation


def test_validate_all_passes_on_current_templates() -> None:
    """当前模板集合通过校验，并返回各段字符数。"""
    section_chars = validation.validate_all()
    assert set(section_chars) >= {"base", "sources", "output", "tools"}
    assert all(isinstance(v, int) and v > 0 for v in section_chars.values())


def test_validate_all_rejects_missing_general_domain(monkeypatch) -> None:
    """缺少 domain=general 的 base 模板时校验失败（默认值自身非法）。"""
    original = loader.load_all()

    def fake_load_all():
        """返回去掉 general 领域的模板映射。"""
        return {k: v for k, v in original.items() if v.domain != "general"}

    monkeypatch.setattr(loader, "load_all", fake_load_all)
    with pytest.raises(loader.TemplateLoadError):
        validation.validate_all()


def test_validate_all_rejects_oversized_sections(monkeypatch) -> None:
    """总字符数超过事故兜底上限时校验失败。"""
    monkeypatch.setattr(validation, "SECTION_CHARS_LIMIT", 10)
    with pytest.raises(loader.TemplateLoadError):
        validation.validate_all()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/prompts/test_validation.py -v`
Expected: FAIL —— `ModuleNotFoundError: … validation`

- [ ] **Step 3: 实现校验模块**

`src/config/prompts/validation.py`：

```python
"""prompt 模板的启动期校验。

失败语义（spec <prompt-carrier>「模板加载的失败语义」）：
- 启动期校验，任一失败 → **启动失败**（透传 TemplateLoadError）
- **不提供**请求期降级到内嵌正文的分支 —— 否则与"唯一事实源"冲突，
  且故障会在每个请求重复出现

与 Langfuse 官方建议的差异：采纳其"启动期预取"，不采纳 fallback；
该建议的前提是远端源可能不可达，而本项目的模板源是打进镜像的本地文件。
"""

from __future__ import annotations

from src.config.prompts import loader

# 事故兜底上限：只拦"整篇文档被误粘贴进模板"这类明显损坏，**不是预算闸门**。
# 依据（change design.md D12.8）：未找到厂商的占比建议；同类项目 system prompt
# 实测 6K–24K 字符且普遍无硬上限（codex 6,621–24,026；WeKnora 单模板 18,120）。
SECTION_CHARS_LIMIT: int = 50000


def validate_all() -> dict[str, int]:
    """校验全部模板并返回各段总字符数。

    Returns:
        段名 → 该段全部模板正文的字符数之和（顺序稳定）

    Raises:
        loader.TemplateLoadError: 目录/YAML/id 非法，或段不完整、超出事故兜底上限
    """
    templates = loader.load_all()

    section_chars: dict[str, int] = {}
    for template in templates.values():
        if template.kind != "section":
            continue
        assert template.section is not None  # loader 已校验，此处仅供类型收窄
        section_chars[template.section] = section_chars.get(template.section, 0) + len(
            template.content
        )

    missing = loader.VALID_SECTIONS - set(section_chars)
    if missing:
        raise loader.TemplateLoadError(f"段缺失模板：{sorted(missing)}")

    has_general = any(
        t.kind == "section" and t.section == "base" and t.domain == "general"
        for t in templates.values()
    )
    if not has_general:
        raise loader.TemplateLoadError("缺少 domain=general 的 base 模板（默认值会自身非法）")

    total = sum(section_chars.values())
    if total > SECTION_CHARS_LIMIT:
        raise loader.TemplateLoadError(
            f"system 段总字符数 {total} 超过事故兜底上限 {SECTION_CHARS_LIMIT}；"
            f"各段：{section_chars}"
        )
    return section_chars
```

⚠ 本任务**要多建第 13 个模板** `base-general.yaml`（`kind: section` / `section: base` / `domain: general`），因为 Task 7 的校验要求"存在 `domain: general` 的 base 模板"（否则默认值自身非法，spec `<prompt-composition>` 的领域绑定节）。它的正文在 P0 只是**占位文案**（P1 才写正式通用 base）。

**它不属于 12 条搬运**：golden 只有 12 条，`base-general` 是**新增**，故不参与 golden 比对。Task 3 的解析测试据此区分"12 条搬运"与"1 条新增"。

- [ ] **Step 4: 接进启动流程**

在 `src/main.py` 的应用启动钩子里调用一次（`main.py:38` 附近的启动段）：

```python
from src.config.prompts import validation

section_chars = validation.validate_all()
logger.info(f"[config] prompt templates validated section_chars={section_chars}")
```

⚠ 让 `TemplateLoadError` **透传**（不要 `try/except` 吞掉），启动即失败。

- [ ] **Step 5: 跑测试**

Run: `pytest tests/config/prompts -v`
Expected: 全 PASS

Run: `python -c "from src.config.prompts import validation; print(validation.validate_all())"`
Expected: 打印各段字符数字典，无异常

- [ ] **Step 6: 提交**

```bash
git add src/config/prompts/validation.py src/config/prompts/templates/base-general.yaml src/main.py src/config/settings.py tests/config/prompts/test_validation.py
git commit -m "feat(prompt): 启动期校验模板（字段/段完整/general 存在/字符数事故兜底），失败即启动失败"
```

---

### Task 8: 分段字符数日志 + 占比软告警

**Files:**
- Modify: `src/rag/prompt.py:70-79`（`PROMPT_ASSEMBLED` 事件加 `section_chars`）
- Modify: `src/config/settings.py`（三项配置）
- Modify: `docs/agents/logging-rules.md`（登记字段与值类型编码）
- Test: `tests/config/prompts/test_section_chars_log.py`

**Interfaces:**
- Consumes: `validation.validate_all()`（Task 7）
- Produces: 日志事件 `PROMPT_ASSEMBLED` 增加字段 `section_chars`（紧凑 JSON 字符串，如 `{"base":1234,"sources":890}`）；超占比阈值时另一条 warning。

- [ ] **Step 1: 写测试**

`tests/config/prompts/test_section_chars_log.py`：

```python
"""PROMPT_ASSEMBLED 带 section_chars（紧凑 JSON、字符数、空段不出现）。"""

import json
from unittest.mock import MagicMock

from src.core.log_events import Event
from src.rag.prompt import build_system_prompt


def _stub_pm() -> MagicMock:
    """替身 PM，绝不触网。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = "基础段"
    pm.get_user_template.return_value = "用户模板"
    return pm


def test_prompt_assembled_carries_section_chars(monkeypatch) -> None:
    """组装日志含各非空段字符数，且为紧凑 JSON（无空格）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        """捕获日志调用。"""
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    build_system_prompt(persona="", kb_bound=True, has_skills=False, prompt_manager=_stub_pm())

    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    raw = payload["section_chars"]
    assert " " not in raw, "容器值必须是紧凑 JSON（logging-rules.md:27-28）"
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert all(isinstance(v, int) for v in parsed.values())
    assert all(v > 0 for v in parsed.values())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/prompts/test_section_chars_log.py -v`
Expected: FAIL —— `KeyError: 'section_chars'`

- [ ] **Step 3: 实现字段 + 软告警**

在 `src/rag/prompt.py` 的 `core_logging.log_event(Event.PROMPT_ASSEMBLED, …)` 调用里加 `section_chars`，并在其后调用软告警：

```python
    section_chars = _section_chars_json()
    core_logging.log_event(
        Event.PROMPT_ASSEMBLED,
        persona_source=persona_source,
        kb_bound=kb_bound,
        has_skills=has_skills,
        discipline_injected=discipline_injected,
        delegate_injected=delegate_injected,
        system_msgs=len(messages),
        section_chars=section_chars,   # 紧凑 JSON，单位字符数
    )
    _warn_if_section_share_high(section_chars)
```

⚠ 若采用"模块导入期读文件"的方案（Task 6 Step 1），本节需在 `src/rag/prompt.py` 顶部加 `import json`（`_warn_if_section_share_high` 要 `json.loads`）。

在同模块加两个小函数（各 `≤ 80` 行、不用三元表达式）：

```python
def _section_chars_json() -> str:
    """把各段模板字符数编码为紧凑 JSON（logging-rules.md 的容器值编码）。

    Returns:
        例如 '{"base":1234,"sources":890}'；空段不出现

    ⚠ P0 口径是「各段模板正文字符数之和」；P1 做了段组装后，应改为
    「实际拼进 system 的各段字符数」（含条件注入与领域三选一的结果）。
    """
    from src.config.prompts import validation

    section_chars = validation.validate_all()
    parts = [f'"{name}":{count}' for name, count in section_chars.items() if count > 0]
    return "{" + ",".join(parts) + "}"


def _warn_if_section_share_high(section_chars_json: str) -> None:
    """system 段占 context window 的估算占比超阈值时记 warning（**不阻断**）。

    换算系数是**跨模型借用**的经验值（见 settings 的注释），只作量级估算；
    阈值是推断值，**不得**当作契约数字写进测试或文档结论（design.md D12.8 Q13）。
    """
    import json

    from src.config import settings

    total = sum(json.loads(section_chars_json).values())
    est_tokens = int(total * settings.PROMPT_TOKENS_PER_CJK_CHAR)
    share = est_tokens / settings.MODEL_CONTEXT_WINDOW_TOKENS
    if share > settings.PROMPT_CONTEXT_SHARE_WARN:
        core_logging.log_event(
            Event.PROMPT_SECTION_SHARE_HIGH,
            est_tokens=est_tokens,
            share=round(share, 4),
            threshold=settings.PROMPT_CONTEXT_SHARE_WARN,
        )
```

⚠ **`PROMPT_SECTION_SHARE_HIGH` 是新事件**，必须在 `src/core/log_events.py` 登记（含 `EventSpec` 的级别，用 `warning`），并在 `docs/agents/logging-rules.md` 的**事件表与值类型编码**两处登记（`section_chars` 走容器值 = 紧凑 JSON）。

- [ ] **Step 4: 跑测试 + 目视日志**

Run: `pytest tests/config/prompts/test_section_chars_log.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/rag/prompt.py src/core/log_events.py src/config/settings.py docs/agents/logging-rules.md tests/config/prompts/test_section_chars_log.py
git commit -m "feat(prompt): 组装日志带 section_chars（紧凑 JSON/字符数），超占比阈值只告警不阻断"
```

---

### Task 9: 文档登记与收尾

**Files:**
- Modify: `docs/agents/code-map.md`（新增 `src/config/prompts/` 包与加载器落点）
- Modify: `CLAUDE.md`（文档组织表：若新增归属文档才需要）
- Modify: `docs/openspec/changes/prompt-layering-and-domain-binding/tasks.md`（勾选 P0 已完成项）

- [ ] **Step 1: 登记 code-map**

在 `docs/agents/code-map.md` 的 `src/` 分层表里补一行：`src/config/prompts/` —— prompt 模板包（`loader.py` 加载入口 / `validation.py` 启动校验 / `templates/*.yaml` 13 条模板（12 搬运 + 1 新增） / `__init__.py` 保留 7 条行为键常量），并注明"改 prompt 文案改 YAML，改挂载点改代码"。

- [ ] **Step 2: 跑一次全量质量门禁**

Run: `pytest tests/ -q && ruff check . && pyright src/`
Expected: 全绿（`pyright` 以不新增 error 为准）

- [ ] **Step 3: 人工核对 golden 未被修改（关键）**

Run: `git log --oneline -- tests/fixtures/prompt_golden/ && git diff HEAD~8 --stat -- tests/fixtures/prompt_golden/`
Expected: golden 只在 Task 1 的提交里出现过一次；此后**无任何改动**。若出现过第二次改动 —— 闸门已失效，必须回退到 Task 1 重新生成。

- [ ] **Step 4: 勾选 tasks.md 的 P0 项并提交**

```bash
git add docs/agents/code-map.md docs/openspec/changes/prompt-layering-and-domain-binding/tasks.md
git commit -m "docs(prompt): 登记 prompts 包与加载器落点，勾选 P0 完成项"
```

---

## 自审记录（写完计划后回看 spec 的结论）

| spec 要求（`<prompt-carrier>` / `<prompt-composition>`） | 对应任务 |
|---|---|
| YAML 载体与目录结构（含"包化、不得并存"） | Task 2 / Task 3 |
| 模板字段 `kind`/`section`/`domain` | Task 3 Step 3 的解析测试 |
| 加载入口唯一 + 占位符只做标识符式替换、未知原样保留 | Task 4 |
| 读取路径唯一化（`PromptManager` 收窄/退役，禁两条路径并存） | **P0 只做"不再另存副本"**（Task 6 Step 2）；完整收窄属 P1（tasks 2.22） |
| 载体替换的逐字不变量（golden） | Task 1 / Task 5 |
| 载体数据源可替换（唯一事实源、回滚靠 git revert） | Task 6 Step 4 / Step 7 |
| 迁移范围 12 迁 / 7 留 | Task 1 映射表 / Task 3 的 `test_template_count_and_migrated_ids`（13 = 12 搬运 + 1 新增 `base-general`） |
| i18n 结构预留但不启用 | 本阶段不引入语言键（P0 无 i18n 字段） |
| 模板加载的失败语义（启动失败、不降级） | Task 7 |
| 分段字符数日志（并入既有事件、紧凑 JSON、字符数、空段不出现） | Task 8 |
| system 段占比观测（软告警、口径注明局限） | Task 8 Step 3 |
| 段模板与独立任务模板分流 | Task 3 的字段 + Task 4 的 `get_by_section` |
| 六段组装 / 条件注入 / 领域三选一 / `domain` 列 | **P1**（本计划不做） |

**未覆盖项已显式声明**：`PromptManager` 的完整收窄、`get_system_prompt()` 追加职责移交、六段组装、条件注入、领域绑定 —— 均属 P1，本计划的"阶段定位"与边界已列明。

## 已知的、须在 P1 处理而不在 P0 的"未完成感"

- P0 的 `base-financial` 模板仍是"混装文案"（角色 + 流程 + 检索阶梯 + 回答规则），因为 P0 禁止改措辞。**它看起来不像"薄 base"是预期的** —— 瘦身在 P1/P2。
- `base-general` 在 P0 只是占位文案，P1 才写正式通用 base。
- `section` 字段在 P0 只被校验、不参与组装（组装顺序属 P1）。
