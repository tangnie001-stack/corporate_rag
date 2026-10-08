# 跨轮历史摘要（Phase B1）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让超出历史预算的更旧对话不再被丢弃，而是压成结构化摘要、持久化并在后续轮次复用（模型侧上下文从「最近若干轮」变成「摘要 + 最近若干轮」）。

**Architecture:** 两段式。**生成**在回合**正常完成**后异步进行（`turn_runner` 的 else 分支，`svc.chat_manager` 已持有历史写句柄），以「与裁剪同口径算出的**将被丢弃段**」为判据、best-effort 抢独立摘要锁、冻结快照、写回 Redis。**注入**在 seed 点只读：`stream_chat` 预取摘要 → `launch_context` → `AgentState` 新字段 → `agent_node` 构造独立 `HumanMessage` 插到最后一个 `SystemMessage` 之后。

**Tech Stack:** Python 3.11+ / LangChain 1.3.11 + LangGraph 1.2.9 / Redis（含进程内降级）/ pytest / tiktoken（已装）。

**Spec:** `docs/openspec/changes/cross-turn-history-summary/`（proposal.md / design.md / `specs/history-summarization/spec.md` / `specs/clarification-interaction/spec.md` / `specs/context-budget/spec.md`）

## Global Constraints

- **常量集中**：新增阈值一律放 `src/config/`（`const.py` 固定阈值 / `settings.py` 环境变量）；业务代码不得内联数字。本 change 需新增 `HISTORY_SUMMARY_TRIGGER_TOKENS = 12288`、`SUMMARY_MAX_TOKENS = 2048`、`SUMMARY_LOCK_TTL`、`SUMMARY_TIMEOUT_S`。
- **日志**：事件必须先在 `src/core/log_events.py` 的 `Event` 枚举 + `src/core/log_event_specs.py` 的 `EVENT_SPECS` **两处登记**（import 期 `_validate_registry` 断言逐一对应；前缀须在 `LOG_PREFIXES` 内；级别只能 `info/warning/error`）；运行时只用 `core_logging.log_event(Event.X, **fields)`。本 change 新增 `summary done`（`session`/info）与 `summary fallback`（`session`/warning）。
- **不用三元表达式**（`a if cond else b`），写完整 if/else。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底，用 `isinstance`。
- **注释/docstring 用中文**；写"现在的机制"，不写变更历史。
- **文件 < 400 行、函数 < 80 行**（⚠ `src/agents/graph/middleware.py` 已 393 行，**本 change 不得往它加代码**）。
- **token 计数一律经** `src/infra/llm/token_count.py::count_tokens`。
- **测试不发起真实网络调用**（LLM 一律 monkeypatch）、不依赖 DB。
- **worktree 内禁用 `git add -A` / `git add .`**（`.venv`/`data` 是 symlink）——一律 `git add <显式路径...>`。
- **摘要段 SHALL NOT 落 MySQL**；`GET /sessions/messages` 回放保持完整原文。
- **摘要段 SHALL NOT 携带 `[数字]` 编号**（防 `format` 阶段 `contexts[n-1]` 串号）；只剥离，**不因编号丢弃整篇摘要**。
- **实现落点约定（勘察后确定）**：`AgentLoopBundle` 在 `src/agents/graph/agent_node.py:168-174`（**不在** `agent_factory.py`）；回合收尾在 `src/services/turn_runner.py` 的 `_run_with_finalize`，无 `chat_manager` 参数，历史写句柄一律走 **`svc.chat_manager`**。

---

### Task 1: 抽出历史窗口切分（纯函数，行为不变）

**Files:**
- Create: `src/agents/graph/history_window.py`
- Modify: `src/agents/graph/agent_node.py:29-61`（`_truncate_history` 改为薄包装）+ `:16-20`（import）
- Test: `tests/agents/graph/test_history_window.py`（已有，**不改断言**）

**Interfaces:**
- Consumes: `src.infra.llm.token_count.count_tokens(text: str) -> int`（已存在）、`src.infra.llm.chat_message.ChatMessage`
- Produces:
  - `split_history_window(history: list[ChatMessage], max_turns: int, token_budget: int) -> tuple[list[ChatMessage], list[ChatMessage]]` —— 返回 `(kept, discarded)`，`kept` 与既有 `_truncate_history` 的返回值**逐一相等**
  - `exceeds_budget(kept: list[ChatMessage], token_budget: int) -> bool`

> 为什么先做这步：生成侧的触发判据必须与裁剪**同口径**（评审 F1），否则会把"保留尾部覆盖全量"的情形误判为触阈、每轮白调一次 LLM 且摘要永不落地。抽出纯函数让两侧共用一份切分逻辑。

- [ ] **Step 1: 写失败测试**

在 `tests/agents/graph/test_history_window.py` **追加**（原有用例保持不动）：

```python
from src.agents.graph.history_window import exceeds_budget, split_history_window

# `_MSG_X` / `_MSG_Y` / `_TURN_TOKENS` 已在本文件上方（Phase A 引入）定义，
# 直接复用，**不要重复定义**（会构成逐字重复）。


def _three_turns():
    history = []
    for _ in range(3):
        history.append(ChatMessage(role="user", content=_MSG_X))
        history.append(ChatMessage(role="assistant", content=_MSG_Y))
    return history


def test_split_returns_kept_and_discarded():
    """切分返回 (kept, discarded)，两段拼起来等于原历史。"""
    history = _three_turns()
    kept, discarded = split_history_window(history, max_turns=1, token_budget=10**6)
    assert kept[-1] is history[-1]
    assert len(discarded) == len(history) - len(kept)
    assert discarded + kept == history


def test_split_kept_matches_truncate_history():
    """切分产出的 kept 与 _truncate_history 的返回值逐一相等（同口径）。"""
    history = _three_turns()
    budget = _TURN_TOKENS + 1
    kept, _ = split_history_window(history, max_turns=10, token_budget=budget)
    assert kept == _truncate_history(history, max_turns=10, token_budget=budget)


def test_split_discarded_empty_when_tail_covers_all():
    """保留尾部覆盖全量时 discarded 为空——生成侧据此跳过（防每轮空转）。"""
    history = _three_turns()
    kept, discarded = split_history_window(history, max_turns=10, token_budget=10**6)
    assert discarded == []
    assert kept == history


def test_exceeds_budget():
    """预算判定：等于预算不算超。"""
    history = _three_turns()
    kept, _ = split_history_window(history, max_turns=10, token_budget=10**6)
    total = sum(count_tokens(m.content) for m in kept)
    assert exceeds_budget(kept, total) is False
    assert exceeds_budget(kept, total - 1) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agents/graph/test_history_window.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.agents.graph.history_window'`

- [ ] **Step 3: 实现纯函数**

创建 `src/agents/graph/history_window.py`：

```python
"""历史窗口切分 —— 轮数粗筛 + 绝对 token 预算的唯一切分口径。

抽成纯函数的原因：跨轮历史摘要的**触发判据**必须与裁剪**同口径**
（判据落在"将被丢弃的那一段"），否则会把"保留尾部覆盖全量"误判为触阈。
本模块不读写任何存储、不做日志，可独立测试。
"""

from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens


def _estimate_total(messages: list[ChatMessage]) -> int:
    """返回消息列表的总 token 数（分词器近似）。"""
    total = 0
    for message in messages:
        total += count_tokens(message.content)
    return total


def split_history_window(
    history: list[ChatMessage], max_turns: int, token_budget: int
) -> tuple[list[ChatMessage], list[ChatMessage]]:
    """按「轮数粗筛 → 绝对预算细裁」切出 (保留, 被丢弃)。

    保留段的语义与既有历史裁剪完全一致：先取最近 max_turns 轮，再按
    token 预算从最旧逐条弹出；最近 1 轮（最后两条）始终不裁。

    Args:
        history: 完整对话历史（user/assistant 交替）
        max_turns: 保留的最近轮数
        token_budget: 保留段的总 token 上限（绝对值）

    Returns:
        (kept, discarded)：kept 与旧实现的历史裁剪返回值逐一相等；
        discarded 为被丢弃的更旧部分（可能为空）
    """
    if len(history) > max_turns * 2:
        kept = history[-(max_turns * 2) :]
    else:
        kept = list(history)
    total = _estimate_total(kept)
    while total > token_budget and len(kept) > 2:
        dropped = kept.pop(0)
        total -= count_tokens(dropped.content)
    discarded = history[: len(history) - len(kept)]
    return kept, discarded


def exceeds_budget(messages: list[ChatMessage], token_budget: int) -> bool:
    """返回消息列表总 token 是否**超过**预算（等于不算超）。"""
    return _estimate_total(messages) > token_budget
```

- [ ] **Step 4: 让 `_truncate_history` 复用它（行为不变）**

`src/agents/graph/agent_node.py`：import 区（`:16-20` 附近）新增

```python
from src.agents.graph.history_window import exceeds_budget, split_history_window
```

把 `:29-61` 的 `_truncate_history` **整体替换**为：

```python
def _truncate_history(
    history: list[ChatMessage],
    max_turns: int = HISTORY_MAX_TURNS,
    token_budget: int = HISTORY_TOKEN_BUDGET,
) -> list[ChatMessage]:
    """历史窗口截断：保留最近 N 轮 + 绝对 token 预算，最近 1 轮完整保留。

    切分口径见 `history_window.split_history_window`（与摘要触发判据同源）。

    Args:
        history: 完整对话历史（user/assistant 交替排列）
        max_turns: 保留的最近轮数（每轮 user+assistant 两条消息）
        token_budget: 历史消息总 token 上限（绝对值，集中 `const.py`）

    Returns:
        截断后的历史列表；最近 1 轮（最后 2 条）始终不截。
        计数走 `count_tokens`（分词器近似）。返回新列表，不修改入参。
    """
    kept, _ = split_history_window(history, max_turns, token_budget)
    if exceeds_budget(kept, token_budget):
        core_logging.log_event(
            Event.HISTORY_BUDGET_EXCEEDED,
            budget=token_budget,
            used=sum(count_tokens(m.content) for m in kept),
            kept=len(kept),
        )
    return kept
```

并在该文件的 import 区确保有 `from src.infra.llm.token_count import count_tokens`（若无则补）。

- [ ] **Step 5: 运行测试确认通过（含既有断言不变）**

Run: `python -m pytest tests/agents/graph/test_history_window.py -v`
Expected: PASS —— 原有 4 个用例（含 `token_budget` 那三条）**断言一字未改**，新增 4 个用例通过

- [ ] **Step 6: 提交**

Run: `ruff check src/agents/graph/history_window.py src/agents/graph/agent_node.py && pyright src/agents/graph/history_window.py`

```bash
git add src/agents/graph/history_window.py src/agents/graph/agent_node.py tests/agents/graph/test_history_window.py
git commit -m "refactor(context): 抽出历史窗口切分纯函数（供摘要触发判据同口径复用）"
```

---

### Task 2: 摘要核心模块（不接入链路）

**Files:**
- Create: `src/chat/history_summary.py`
- Create: `src/config/prompts/templates/task-summary-system.yaml`
- Modify: `src/config/settings.py:50` 下方（新增 `SUMMARY_MODEL`）、`src/config/const.py:51-92` 段内（新增四个常量）
- Modify: `src/models.py:199` 后（新增 `get_summary_llm`）
- Test: `tests/chat/test_history_summary.py`

**Interfaces:**
- Consumes: `split_history_window`（Task 1）、`count_tokens`、`get_summary_llm()`（本任务产出）、prompt 模板（本任务产出）
- Produces:
  - `SummaryResult` dataclass：`text: str`、`covered: int`（已摘要到的消息条数）、`degraded: bool`、`reason: str`
  - `strip_citation_numbers(text: str) -> str`
  - `validate_summary(text: str, discarded_tokens: int, previous_tokens: int | None) -> tuple[bool, str]`
  - `async summarize_history(previous_text: str, previous_covered: int, discarded: list[ChatMessage]) -> SummaryResult`
  - `SUMMARY_SYSTEM_TEMPLATE_ID: str = "task-summary-system"`

- [ ] **Step 1: 加常量与配置**

`src/config/settings.py` 在 `CLASSIFY_MODEL`（`:50`）下方加：

```python
# 跨轮历史摘要专用模型：默认沿用主模型，可用 env 覆盖为更便宜的档位。
# 摘要调用固定关闭思考（实测深思考让输出 token 涨约 2.7 倍，其中 64% 是不可见思考）。
SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", LLM_MODEL)
```

`src/config/const.py` 的「agent 循环护栏常量」段（`:51-92`，`SESSION_LOCK_TTL` 之后的段末）加：

```python
# 跨轮历史摘要：触发判据落在「将被丢弃的那一段」的 token 量上（不是全量历史——
# 保留尾部覆盖全量时被丢弃段为空，以全量判据会导致每轮调用摘要模型却永不落地）。
# 经验值：0.75 × HISTORY_TOKEN_BUDGET，给摘要产出留 1/4 余量，待线上观测标定。
HISTORY_SUMMARY_TRIGGER_TOKENS = 12288
# 摘要产出的输出上限（token）。经验值来源：本地参考项目 deepseek-harness 摘要上限 8192、
# WeKnora 提示 "under 500 words"、ragflow 摘要预算 1200 字符；再受本项目
# 「摘要段 + 尾部 ≤ HISTORY_TOKEN_BUDGET」聚合约束收敛。该上限同时是
# 「拒绝截断摘要」不变量的判据前提（无上限则截断无从判定）。
SUMMARY_MAX_TOKENS = 2048
# 摘要并发锁 TTL（秒）：独立于 SESSION_LOCK_TTL——回合收尾时轮次锁即将释放，
# 两者语义与生命周期不同，不可复用同一键。取摘要调用超时 + 30s 余量。
SUMMARY_LOCK_TTL = 90
# 摘要模型调用的超时保险丝（秒）：超时按失败降级，防长尾占用锁。
SUMMARY_TIMEOUT_S = 60
```

- [ ] **Step 2: 加摘要模型工厂**

`src/models.py` 在 `get_classify_llm`（`:178-199`）之后加：

```python
def get_summary_llm() -> ChatOpenAI:
    """创建跨轮历史摘要专用 LLM 实例（关闭思考 + 显式输出上限）。

    与 `get_classify_llm` 的区别：那个是分类器语义、模型走 `CLASSIFY_MODEL`；
    摘要走独立的 `SUMMARY_MODEL`。必须设 `max_tokens`——否则「拒绝截断摘要」
    这一不变量失去判据（未设上限时几乎不会出现 length 截断）。

    Returns:
        关闭思考、带输出上限的 ChatOpenAI 实例
    """
    from src.config import SUMMARY_MAX_TOKENS, SUMMARY_MODEL

    extra_kwargs: dict = json.loads(LLM_KWARGS)
    extra_kwargs.update(
        {"extra_body": {"enable_thinking": False}, "max_tokens": SUMMARY_MAX_TOKENS}
    )
    return ChatOpenAI(
        model=SUMMARY_MODEL,
        temperature=0,
        api_key=SecretStr(LLM_API_KEY),
        base_url=LLM_BASE_URL,
        callbacks=_content_logging_callbacks(),
        **extra_kwargs,
    )
```

`src/config/__init__.py` 需能导出 `SUMMARY_MODEL` / `SUMMARY_MAX_TOKENS`（该文件按 `settings` 的同类写法导出；若为显式列举式，补上这两个名字）。

- [ ] **Step 3: 新增摘要提示词模板（`kind: task`）**

创建 `src/config/prompts/templates/task-summary-system.yaml`：

```yaml
templates:
  - id: task-summary-system
    kind: task
    content: |-
      你是对话历史的压缩器。把"更早的对话"压成一份不丢失关键信息的结构化摘要，供后续轮次作为背景使用。

      严格按以下五段输出（每段用 `## ` 开头；没有内容的段写"无"）：
      ## 用户目标
      ## 已达成的决定
      ## 未解约束
      ## 关键事实与数字
      ## 下一步

      规则：
      1. 全文不超过 600 字，优先用短句与列表，不要复述原文。
      2. 不得输出任何方括号编号（如 [1]、[2]）——编号在后续轮次另有含义。
      3. 若给了"上一版摘要"，请在其基础上**增量更新**：已完成的事项折叠成一行、被废弃的方案整段删除；新版**不得比上一版更长**。
      4. 只输出摘要正文，不要任何前言、解释或代码块标记。
```

- [ ] **Step 4: 写失败测试**

创建 `tests/chat/test_history_summary.py`：

```python
"""跨轮历史摘要核心逻辑的单测（不发起真实 LLM 调用）。"""

from unittest.mock import AsyncMock

import pytest

from src.chat import history_summary
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens


def test_strip_citation_numbers_removes_only_bracket_digits():
    """只剥离 [数字]，不误伤年份与 Markdown 链接。"""
    raw = "结论见 [1]，2024 年数据见 [12]，链接 [文档](http://x)。"
    out = history_summary.strip_citation_numbers(raw)
    assert "[1]" not in out
    assert "[12]" not in out
    assert "2024" in out
    assert "[文档](http://x)" in out


def test_validate_summary_rejects_not_smaller():
    """摘要不比被丢弃段小 → 不采用。"""
    ok, reason = history_summary.validate_summary(
        text="短", discarded_tokens=1, previous_tokens=None
    )
    assert ok is False
    assert reason == "not_smaller"


def test_validate_summary_rejects_growth():
    """就地更新时新摘要不得比上一版更长 → 违反即不采用。"""
    text = "长" * 200
    ok, reason = history_summary.validate_summary(
        text=text,
        discarded_tokens=10**6,
        previous_tokens=max(1, count_tokens(text) - 1),
    )
    assert ok is False
    assert reason == "grew"


def test_validate_summary_accepts_normal():
    """同时满足两条时不拒绝。"""
    text = "摘要正文"
    ok, reason = history_summary.validate_summary(
        text=text, discarded_tokens=10**6, previous_tokens=None
    )
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_summarize_history_degrades_on_llm_failure(monkeypatch):
    """LLM 抛异常 → degraded 结果，不抛出去。"""
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("boom")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    discarded = [ChatMessage(role="user", content="q"), ChatMessage(role="assistant", content="a")]
    result = await history_summary.summarize_history("", 0, discarded)
    assert result.degraded is True
    assert result.text == ""
    assert "boom" in result.reason


@pytest.mark.asyncio
async def test_summarize_history_rejects_truncated(monkeypatch):
    """结束原因为截断 → 不采用（四条不变量之「拒绝截断摘要」）。"""
    llm = AsyncMock()
    llm.ainvoke.return_value = _fake_response("半截摘要", finish_reason="length")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    discarded = [ChatMessage(role="user", content="q" * 500)]
    result = await history_summary.summarize_history("", 0, discarded)
    assert result.degraded is True
    assert result.reason == "truncated"


@pytest.mark.asyncio
async def test_summarize_history_returns_text_and_covered(monkeypatch):
    """成功路径：返回摘要正文与覆盖条数（= 之前覆盖数 + 本次丢弃段条数）。"""
    llm = AsyncMock()
    llm.ainvoke.return_value = _fake_response("## 用户目标\n看年报")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    discarded = [ChatMessage(role="user", content="q1"), ChatMessage(role="assistant", content="a1")]
    result = await history_summary.summarize_history("", 4, discarded)
    assert result.degraded is False
    assert result.text == "## 用户目标\n看年报"
    assert result.covered == 6


def _fake_response(content: str, finish_reason: str = "stop"):
    """构造带 response_metadata 的假模型响应。"""

    class _Resp:
        def __init__(self) -> None:
            self.content = content
            self.response_metadata = {"finish_reason": finish_reason}

    return _Resp()
```

- [ ] **Step 5: 运行测试确认失败**

Run: `python -m pytest tests/chat/test_history_summary.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.chat.history_summary'`

- [ ] **Step 6: 实现摘要核心模块**

创建 `src/chat/history_summary.py`：

```python
"""跨轮历史摘要核心 —— 提示词组装、调用、四条不变量校验、编号剥离。

只做"把一段历史压成摘要"的纯逻辑与一次 LLM 调用，不读写存储、不碰并发锁
（存储归 `ChatManager`，编排归回合收尾处）。任何失败都返回 `degraded` 结果，
绝不外抛——调用方据此回退到纯裁剪。

本模块**不记日志**：降级信号由编排层（`summary_scheduler`）按 `degraded` 与
`reason` 统一落 `[session]` 事件，避免同一失败在两处重复记录、也避免本模块
依赖事件注册表（事件在后续任务才登记）。
"""

import asyncio
import re
from dataclasses import dataclass

from src.config.const import SUMMARY_TIMEOUT_S
from src.config.prompts import loader
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.models import get_summary_llm

SUMMARY_SYSTEM_TEMPLATE_ID = "task-summary-system"

# 只匹配方括号内的 1-2 位数字（引用编号）；不碰年份、Markdown 链接等。
_CITATION_RE = re.compile(r"\[\d{1,2}\]")


@dataclass(frozen=True)
class SummaryResult:
    """一次摘要尝试的结果。"""

    text: str  # 摘要正文；degraded 时为空串
    covered: int  # 已摘要到的消息条数（上一次覆盖数 + 本次丢弃段条数）
    degraded: bool  # 是否降级（未采用摘要）
    reason: str  # 降级原因；成功时为空串


def strip_citation_numbers(text: str) -> str:
    """剥离正文中的 `[数字]` 引用编号（不因编号丢弃整篇摘要）。"""
    return _CITATION_RE.sub("", text)


def validate_summary(
    text: str, discarded_tokens: int, previous_tokens: int | None
) -> tuple[bool, str]:
    """校验四条不变量中可离线判定的两条（更小 / 严格不增长）。

    Args:
        text: 候选摘要正文
        discarded_tokens: 被丢弃段的 token 量
        previous_tokens: 上一版摘要的 token 量；None 表示尚无上一版

    Returns:
        (是否采纳, 不采纳原因)；原因为空串表示通过
    """
    if not text.strip():
        return False, "empty"
    tokens = count_tokens(text)
    if tokens >= discarded_tokens:
        return False, "not_smaller"
    if previous_tokens is not None and tokens > previous_tokens:
        return False, "grew"
    return True, ""


def _render_discarded(discarded: list[ChatMessage]) -> str:
    """把被丢弃段渲染为摘要输入文本（按角色标注）。"""
    lines: list[str] = []
    for message in discarded:
        if message.role == "user":
            lines.append(f"[用户] {message.content}")
        else:
            lines.append(f"[助手] {message.content}")
    return "\n".join(lines)


async def summarize_history(
    previous_text: str, previous_covered: int, discarded: list[ChatMessage]
) -> SummaryResult:
    """生成/更新摘要；任何失败都返回 degraded 结果，绝不外抛。

    Args:
        previous_text: 上一版摘要正文（无则空串）
        previous_covered: 上一版摘要已覆盖的消息条数
        discarded: 本次要压入摘要的更旧消息段（非空）

    Returns:
        SummaryResult；degraded=True 时 text 为空、covered 沿用上一次覆盖数
    """
    covered = previous_covered + len(discarded)
    system_prompt = loader.get_content(SUMMARY_SYSTEM_TEMPLATE_ID)
    body = _render_discarded(discarded)
    if previous_text:
        user_prompt = f"上一版摘要：\n{previous_text}\n\n本次要并入的更早对话：\n{body}"
    else:
        user_prompt = f"要压缩的更早对话：\n{body}"
    try:
        llm = get_summary_llm()
        response = await asyncio.wait_for(
            llm.ainvoke(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
            ),
            timeout=SUMMARY_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        return SummaryResult("", previous_covered, True, f"llm_error: {exc}")
    # 不变量③「拒绝截断摘要」：结束原因为长度截断即视为失败，不采用半截摘要。
    # 该判据成立的前提是 `get_summary_llm()` 已设显式 max_tokens。
    metadata = response.response_metadata
    finish_reason = ""
    if isinstance(metadata, dict):
        finish_reason = str(metadata.get("finish_reason", ""))
    if finish_reason == "length":
        return SummaryResult("", previous_covered, True, "truncated")
    text = strip_citation_numbers(str(response.content))
    previous_tokens = count_tokens(previous_text) if previous_text else None
    ok, reason = validate_summary(
        text,
        discarded_tokens=sum(count_tokens(m.content) for m in discarded),
        previous_tokens=previous_tokens,
    )
    if not ok:
        return SummaryResult("", previous_covered, True, reason)
    return SummaryResult(text, covered, False, "")
```

- [ ] **Step 7: 运行测试确认通过**

Run: `python -m pytest tests/chat/test_history_summary.py -v`
Expected: PASS（7 passed）
（`loader.get_content(template_id)` 已存在于 `src/config/prompts/loader.py:129`。新模板由 `loader.load_all()` 在启动期加载，**跑一次** `python -c "from src.config.prompts import validation; validation.validate_all(); print('templates OK')"` 确认模板可被加载并通过段校验。）

- [ ] **Step 8: 提交**

Run: `ruff check . && pyright src/chat/history_summary.py src/models.py && python -c "from src.config.prompts import validation; validation.validate_all(); print('templates OK')"`

```bash
git add src/config/settings.py src/config/const.py src/config/__init__.py src/models.py src/chat/history_summary.py src/config/prompts/templates/task-summary-system.yaml tests/chat/test_history_summary.py
git commit -m "feat(context): 新增跨轮历史摘要核心（提示词/模型工厂/四条不变量/编号剥离）"
```

---

### Task 3: 摘要存储、独立锁、启动清理、事件

**Files:**
- Create: `src/services/summary_lock.py`
- Modify: `src/chat/manager.py`（新增摘要 get/set/clear + 内存降级 + `clear_history_async` 一并清）
- Modify: `src/main.py:72-88`（残留锁清理扩展）
- Modify: `src/core/log_events.py` + `src/core/log_event_specs.py`（两个事件）
- Test: `tests/chat/test_manager_summary.py`、`tests/services/test_summary_lock.py`

**Interfaces:**
- Consumes: Task 2 的 `SummaryResult`
- Produces:
  - `ChatManager.save_summary_async(session_id: str, text: str, covered: int) -> None`
  - `ChatManager.get_summary_async(session_id: str) -> tuple[str, int]`（无则 `("", 0)`）
  - `ChatManager.clear_summary_async(session_id: str) -> None`
  - `src.services.summary_lock.acquire_summary_lock(redis, session_id) -> bool` / `release_summary_lock(redis, session_id) -> None`
  - `Event.SUMMARY_DONE`（`"summary done"`，session/info，fields `("covered","tokens")`）、`Event.SUMMARY_FALLBACK`（`"summary fallback"`，session/warning，fields `("reason","err")`）

- [ ] **Step 1: 登记两个事件**

`src/core/log_events.py` 的 `[session]` 段（`:90-101`，`SKILL_RESOLVED` 之后）加：

```python
    # [session] 跨轮历史摘要成功写入（覆盖条数 / 摘要 token 量）
    SUMMARY_DONE = "summary done"
    # [session] 跨轮历史摘要降级（未采用摘要，回退纯裁剪）
    SUMMARY_FALLBACK = "summary fallback"
```

`src/core/log_event_specs.py` 的 session 段（`:139-166`，`"skill resolved"` 条目之后）加：

```python
    "summary done": EventSpec(
        "summary done", "session", "info", ("covered", "tokens")
    ),
    "summary fallback": EventSpec(
        "summary fallback", "session", "warning", ("reason", "err")
    ),
```

- [ ] **Step 2: 独立摘要锁**

创建 `src/services/summary_lock.py`：

```python
"""跨轮历史摘要的并发守卫（Redis SETNX，独立于会话轮次锁）。

**不复用** `chat_lock:{sid}`：轮次锁承载"同一会话同时只跑一轮"的语义、
且在回合收尾的 finally 里才释放；摘要生成发生在收尾之后，两者语义与
生命周期都不同，复用会互相阻塞。

best-effort：拿不到即跳过本次摘要（不等待、不排队）。锁带 TTL，
并且前缀纳入启动期残留锁清理——否则进程被杀会留下永久锁，使该会话
此后永远无法再生成摘要。
"""

from src.config.const import SUMMARY_LOCK_TTL

SUMMARY_LOCK_PREFIX = "chat_summary_lock:"


def _lock_key(session_id: str) -> str:
    """构造摘要锁 key。"""
    return f"{SUMMARY_LOCK_PREFIX}{session_id}"


async def acquire_summary_lock(redis, session_id: str) -> bool:
    """SETNX 获取摘要锁（带 TTL），返回是否获取成功。"""
    return bool(
        await redis.set(_lock_key(session_id), "1", nx=True, ex=SUMMARY_LOCK_TTL)
    )


async def release_summary_lock(redis, session_id: str) -> None:
    """释放摘要锁（删除对应 Redis key）。"""
    await redis.delete(_lock_key(session_id))
```

`tests/services/test_summary_lock.py`：

```python
"""摘要锁的单测（不连真实 Redis，用假客户端）。"""

import pytest

from src.services.summary_lock import (
    SUMMARY_LOCK_PREFIX,
    acquire_summary_lock,
    release_summary_lock,
)


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.set_calls = []

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.asyncio
async def test_acquire_uses_independent_key_with_ttl():
    """键独立于 chat_lock，且 SETNX 必须带 TTL。"""
    redis = _FakeRedis()
    assert await acquire_summary_lock(redis, "s1") is True
    key, _, nx, ex = redis.set_calls[0]
    assert key == f"{SUMMARY_LOCK_PREFIX}s1"
    assert key != "chat_lock:s1"
    assert nx is True
    assert ex is not None and ex > 0


@pytest.mark.asyncio
async def test_acquire_fails_when_held():
    """已持锁时再次获取失败（best-effort 跳过的前提）。"""
    redis = _FakeRedis()
    await acquire_summary_lock(redis, "s1")
    assert await acquire_summary_lock(redis, "s1") is False


@pytest.mark.asyncio
async def test_release_deletes_key():
    """释放后键消失。"""
    redis = _FakeRedis()
    await acquire_summary_lock(redis, "s1")
    await release_summary_lock(redis, "s1")
    assert redis.store == {}
```

- [ ] **Step 3: ChatManager 的摘要读写（含内存降级）**

`src/chat/manager.py`：在 `self._memory_store`（`:57`）之后加内存字段：

```python
        # 内存降级时的摘要存储：session_id -> (摘要正文, 覆盖条数)
        self._memory_summaries: dict[str, tuple[str, int]] = {}
```

在 `clear_history_async`（`:304`）的**方法体开头**（`delegate_budget.reset(...)` 之后）加一行清摘要；并在内存分支 `self._memory_store.pop(session_id, None)` 之后加：

```python
            self._memory_summaries.pop(session_id, None)
```

在 Redis 分支的 `await self._redis.delete(key)` 之后加：

```python
            await self._redis.delete(self._summary_key(session_id))
```

新增三个方法（放在 `clear_history_async` 之后）：

```python
    @property
    def redis(self):
        """返回底层 Redis 客户端（内存降级时为 None）。

        供摘要锁等外部守卫取用；避免其它层直接摸 `_redis` 私有属性。
        """
        return self._redis

    def _summary_key(self, session_id: str) -> str:
        """生成摘要 Redis key，格式为 "chat_summary:{session_id}"。"""
        return f"chat_summary:{session_id}"

    async def save_summary_async(
        self, session_id: str, text: str, covered: int
    ) -> None:
        """保存跨轮历史摘要（正文 + 已覆盖的消息条数）。

        写入成功时续期 TTL，使摘要与对话历史同生命周期。

        Args:
            session_id: 会话 ID
            text: 摘要正文
            covered: 已覆盖到的消息条数
        """
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_summaries[session_id] = (text, covered)
            return
        assert self._redis is not None
        try:
            key = self._summary_key(session_id)
            payload = json.dumps({"text": text, "covered": covered}, ensure_ascii=False)
            await self._redis.set(key, payload)
            await self._redis.expire(key, self.ttl)
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.SUMMARY_FALLBACK, reason="store_failed", err=str(e))

    async def get_summary_async(self, session_id: str) -> tuple[str, int]:
        """读取跨轮历史摘要。

        Returns:
            (摘要正文, 覆盖条数)；不存在时返回 ("", 0)
        """
        await self._ensure_redis_async()
        if self._in_memory:
            return self._memory_summaries.get(session_id, ("", 0))
        assert self._redis is not None
        try:
            raw = await self._redis.get(self._summary_key(session_id))
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.SUMMARY_FALLBACK, reason="read_failed", err=str(e))
            return "", 0
        if not raw:
            return "", 0
        data = json.loads(raw)
        return str(data.get("text", "")), int(data.get("covered", 0))

    async def clear_summary_async(self, session_id: str) -> None:
        """清除跨轮历史摘要（会话删除/清空时调用）。"""
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_summaries.pop(session_id, None)
            return
        assert self._redis is not None
        try:
            await self._redis.delete(self._summary_key(session_id))
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.SUMMARY_FALLBACK, reason="clear_failed", err=str(e))
```

`tests/chat/test_manager_summary.py`：

```python
"""ChatManager 摘要读写的单测（内存降级路径，不连真实 Redis）。"""

import pytest

from src.chat.manager import ChatManager


@pytest.fixture
def manager(monkeypatch):
    """构造一个强制走内存降级路径的 ChatManager。"""
    mgr = ChatManager.__new__(ChatManager)
    mgr.ttl = 60
    mgr._redis_url = "redis://localhost:6379/0"
    mgr._redis = None
    mgr._in_memory = True
    mgr._memory_store = {}
    mgr._memory_summaries = {}
    mgr._persistence = None
    return mgr


@pytest.mark.asyncio
async def test_summary_roundtrip_in_memory(manager):
    """无 Redis 时摘要仍可读写（内存降级分支）。"""
    await manager.save_summary_async("s1", "摘要正文", 6)
    assert await manager.get_summary_async("s1") == ("摘要正文", 6)


@pytest.mark.asyncio
async def test_get_summary_missing_returns_empty(manager):
    """不存在时返回空串与 0。"""
    assert await manager.get_summary_async("nope") == ("", 0)


@pytest.mark.asyncio
async def test_clear_history_also_clears_summary(manager):
    """清空历史时摘要一并清除（同一会话数据同生命周期）。"""
    await manager.save_summary_async("s1", "摘要正文", 6)
    await manager.clear_history_async("s1")
    assert await manager.get_summary_async("s1") == ("", 0)


def test_redis_property_exposes_client(manager):
    """公开的 redis 访问口：内存降级时为 None（供摘要锁判定是否可用）。"""
    assert manager.redis is None
```

- [ ] **Step 4: 启动期残留锁清理扩展**

`src/main.py` 的 `_clear_stale_chat_locks`（`:72-88`）里，把

```python
        keys = await redis.keys("chat_lock:*")
```

替换为：

```python
        from src.services.summary_lock import SUMMARY_LOCK_PREFIX

        # 摘要锁与轮次锁同属"进程内持有、进程被杀即泄漏"的键，必须一并清理：
        # 摘要锁若不清理，该会话此后 best-effort 永远拿不到锁 ⇒ 永不再生成摘要。
        keys = await redis.keys("chat_lock:*")
        keys += await redis.keys(f"{SUMMARY_LOCK_PREFIX}*")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/chat/test_manager_summary.py tests/services/test_summary_lock.py tests/core/ -v`
Expected: PASS（含 import 期事件注册表一致性校验）

- [ ] **Step 6: 提交**

Run: `ruff check . && pyright src/chat/manager.py src/services/summary_lock.py src/main.py`

```bash
git add src/services/summary_lock.py src/chat/manager.py src/main.py src/core/log_events.py src/core/log_event_specs.py tests/chat/test_manager_summary.py tests/services/test_summary_lock.py
git commit -m "feat(context): 摘要存储/独立锁/启动清理与两个 session 事件"
```

---

### Task 4: 回合正常完成后异步生成（触发接线）

**Files:**
- Create: `src/services/summary_scheduler.py`
- Modify: `src/services/turn_runner.py:146-163`（else 分支末尾挂调用）
- Test: `tests/services/test_summary_scheduler.py`

**Interfaces:**
- Consumes: Task 1 `split_history_window`、Task 2 `summarize_history` / `SummaryResult`、Task 3 `ChatManager.save_summary_async` / `summary_lock`
- Produces:
  - `maybe_schedule_summary(svc, session_id: str, history: list[ChatMessage]) -> None`（**同步函数**，不 await；内部判定触阈后 spawn 后台任务）
  - 模块级 `_RUNNING: set[asyncio.Task]`（持强引用，防 GC）

- [ ] **Step 1: 写失败测试**

创建 `tests/services/test_summary_scheduler.py`：

```python
"""摘要调度的单测：触发判据、锁 best-effort、失败降级（不发起真实 LLM/Redis）。"""

import asyncio

import pytest

from src.config.const import (
    HISTORY_SUMMARY_TRIGGER_TOKENS,
    HISTORY_TOKEN_BUDGET,
    HISTORY_MAX_TURNS,
)
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.services import summary_scheduler


class _FakeManager:
    def __init__(self):
        self.saved = []
        self.summary = ("", 0)

    async def get_summary_async(self, session_id):
        return self.summary

    async def save_summary_async(self, session_id, text, covered):
        self.saved.append((session_id, text, covered))
        self.summary = (text, covered)

    async def add_message_async(self, *a, **k):
        return None


class _FakeLock:
    def __init__(self, ok=True):
        self.ok = ok
        self.released = 0

    async def acquire(self, redis, session_id):
        return self.ok

    async def release(self, redis, session_id):
        self.released += 1


def _big_history(turns: int = 20):
    """构造超过触阈的历史：每轮 user/assistant 各约 500 token。"""
    history = []
    for i in range(turns):
        history.append(ChatMessage(role="user", content="问" * 700))
        history.append(ChatMessage(role="assistant", content="答" * 700))
    return history


def test_discarded_empty_skips_without_scheduling():
    """保留尾部覆盖全量（被丢弃段为空）⇒ 不调度（防每轮空转）。"""
    scheduler = summary_scheduler.SummaryScheduler(
        manager=_FakeManager(), redis=object()
    )
    short = [
        ChatMessage(role="user", content="短"),
        ChatMessage(role="assistant", content="短"),
    ]
    assert scheduler.should_schedule(short) is False


def test_schedules_when_discarded_exceeds_trigger():
    """被丢弃段超触阈 ⇒ 需要调度。"""
    from src.agents.graph.history_window import split_history_window

    scheduler = summary_scheduler.SummaryScheduler(
        manager=_FakeManager(), redis=object()
    )
    history = _big_history()
    kept, discarded = split_history_window(
        history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
    )
    assert discarded, "构造数据应产生非空被丢弃段"
    assert (
        sum(count_tokens(m.content) for m in discarded) > HISTORY_SUMMARY_TRIGGER_TOKENS
    )
    assert scheduler.should_schedule(history) is True


@pytest.mark.asyncio
async def test_generate_writes_summary_and_emits_done(monkeypatch):
    """生成成功：写回摘要并释放锁。"""
    from src.chat.history_summary import SummaryResult

    monkeypatch.setattr(
        summary_scheduler,
        "summarize_history",
        lambda prev_text, prev_covered, discarded: _ok_result(prev_covered, discarded),
    )
    manager = _FakeManager()
    lock = _FakeLock(ok=True)
    scheduler = summary_scheduler.SummaryScheduler(
        manager=manager, redis=object(), lock=lock
    )
    history = _big_history()
    await scheduler.generate("s1", history)
    assert manager.saved and manager.saved[0][0] == "s1"
    assert lock.released == 1


@pytest.mark.asyncio
async def test_generate_skips_when_lock_unavailable(monkeypatch):
    """抢不到锁 ⇒ 直接跳过，不写、不调 LLM。"""
    called = []
    monkeypatch.setattr(
        summary_scheduler, "summarize_history", lambda *a, **k: called.append(1)
    )
    manager = _FakeManager()
    lock = _FakeLock(ok=False)
    scheduler = summary_scheduler.SummaryScheduler(
        manager=manager, redis=object(), lock=lock
    )
    await scheduler.generate("s1", _big_history())
    assert manager.saved == []
    assert called == []
    assert lock.released == 0


async def _ok_result(prev_covered, discarded):
    from src.chat.history_summary import SummaryResult

    return SummaryResult("## 用户目标\n摘要", prev_covered + len(discarded), False, "")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/services/test_summary_scheduler.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.summary_scheduler'`

- [ ] **Step 3: 实现调度器**

创建 `src/services/summary_scheduler.py`：

```python
"""跨轮历史摘要的调度 —— 触发判据、best-effort 抢锁、后台生成。

生成只在**回合正常完成后**发起（调用方负责选对分支）；本模块自身不等待，
失败一律降级为"不采用摘要"，绝不影响用户可见流程。
"""

import asyncio
import logging

from src.chat.history_summary import summarize_history
from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_SUMMARY_TRIGGER_TOKENS,
    HISTORY_TOKEN_BUDGET,
)
from src.agents.graph.history_window import split_history_window
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.services import summary_lock

logger = logging.getLogger(__name__)

# 后台摘要任务的强引用容器：长 LLM 调用期间防被 GC。
_RUNNING: set[asyncio.Task] = set()


class SummaryScheduler:
    """一次摘要生成所需的最小依赖（便于测试替换）。"""

    def __init__(self, manager, redis, lock=None) -> None:
        """初始化。

        Args:
            manager: ChatManager（提供摘要读写）
            redis: redis.asyncio 客户端；None 时跳过（无 Redis 环境不生成摘要）
            lock: 锁模块替身；默认用 `src.services.summary_lock`
        """
        self._manager = manager
        self._redis = redis
        self._lock = lock if lock is not None else summary_lock

    def should_schedule(self, history: list[ChatMessage]) -> bool:
        """判断是否应生成摘要：**将被丢弃段**非空且超触阈值。

        判据落在被丢弃段而非全量历史——否则"保留尾部覆盖全量"会被误判为触阈。
        """
        _, discarded = split_history_window(
            history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
        )
        if not discarded:
            return False
        used = 0
        for message in discarded:
            used += count_tokens(message.content)
        return used > HISTORY_SUMMARY_TRIGGER_TOKENS

    async def generate(self, session_id: str, history: list[ChatMessage]) -> None:
        """抢锁 → 生成 → 写回 → 释放锁；任何失败都降级，不外抛。"""
        try:
            acquired = await self._lock.acquire(self._redis, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] summary lock acquire failed err=%s", exc)
            return
        if not acquired:
            return
        try:
            _, discarded = split_history_window(
                history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
            )
            if not discarded:
                return
            previous_text, previous_covered = await self._manager.get_summary_async(
                session_id
            )
            result = await summarize_history(previous_text, previous_covered, discarded)
            if result.degraded:
                # 核心模块只返回原因、不记日志；由本层统一落 [session] 降级事件。
                core_logging.log_event(
                    Event.SUMMARY_FALLBACK, reason=result.reason, err=""
                )
                return
            await self._manager.save_summary_async(
                session_id, result.text, result.covered
            )
            core_logging.log_event(
                Event.SUMMARY_DONE,
                covered=result.covered,
                tokens=count_tokens(result.text),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[session] summary generate failed err=%s", exc)
        finally:
            try:
                await self._lock.release(self._redis, session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[session] summary lock release failed err=%s", exc)


def maybe_schedule_summary(manager, redis, session_id: str, history: list[ChatMessage]) -> None:
    """判定触阈后 spawn 后台摘要任务（**不等待**）。

    调用方 SHALL 只在回合**正常完成**且 assistant 已写入历史之后调用。
    history 为发起时冻结的快照，后台任务内不再读取会话历史。

    Args:
        manager: ChatManager
        redis: redis.asyncio 客户端；None 时不生成
        session_id: 会话 ID
        history: 冻结的历史快照
    """
    if redis is None:
        return
    scheduler = SummaryScheduler(manager, redis)
    if not scheduler.should_schedule(history):
        return
    frozen = list(history)
    task = asyncio.create_task(scheduler.generate(session_id, frozen))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
```

- [ ] **Step 4: 接入回合正常完成分支**

`src/services/turn_runner.py` 的 else 分支（`:146-163`），在写 Redis 那行（`:162`）**之后**、`manager.add_event(session_id, "done", ...)` 之前插入：

```python
        # 跨轮历史摘要：只在**正常完成**分支、且 assistant 已写入历史之后发起。
        # 取消/异常分支不写 Redis assistant（历史末尾会是未作答的 user 问），
        # 以其为输入会把悬空问题当成史实，故那两条路径不触发。
        # `chat_manager.redis` 为 None（无 Redis / 内存降级）时不生成摘要——
        # best-effort 守卫需要 SETNX，此时行为等同于本 change 之前。
        from src.services.summary_scheduler import maybe_schedule_summary

        summary_history = await svc.chat_manager.get_history_async(session_id) or []
        maybe_schedule_summary(
            svc.chat_manager, svc.chat_manager.redis, session_id, summary_history
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/services/test_summary_scheduler.py tests/services/ -v`
Expected: PASS

- [ ] **Step 6: 提交**

Run: `ruff check . && pyright src/services/summary_scheduler.py src/services/turn_runner.py`

```bash
git add src/services/summary_scheduler.py src/services/turn_runner.py tests/services/test_summary_scheduler.py
git commit -m "feat(context): 回合正常完成后异步生成跨轮历史摘要（冻结快照 + best-effort 锁）"
```

---

### Task 5: seed 点只读注入 + 聚合预算上限

**Files:**
- Modify: `src/agents/graph/state.py:12-52`（新增字段）、`:54-75`（`make_initial_state`）
- Modify: `src/services/agent_service.py:997-1005`（launch_context 增键）、`:643-645`（`make_initial_state` 调用）
- Modify: `src/agents/graph/agent_node.py:99-165`（`_split_initial_messages` 注入摘要段）
- Test: `tests/agents/graph/test_summary_injection.py`

**Interfaces:**
- Consumes: Task 3 `ChatManager.get_summary_async`；Task 1 `split_history_window`；`count_tokens`
- Produces:
  - `AgentState._summary: str`（摘要正文；token 量由 seed 点按需计算，不进 state）
  - `AgentState.make_initial_state(..., summary: str = "")`
  - 注入形态：`HumanMessage(content="<会话摘要>…</会话摘要>\n（以下是更早会话的摘要，仅供参考；事实性结论仍须以本轮检索结果为准；不确定时请用户复述。）")`

- [ ] **Step 1: 写失败测试**

创建 `tests/agents/graph/test_summary_injection.py`：

```python
"""摘要段注入的单测：位置、让位声明、聚合预算上限、无摘要时零影响。"""

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.graph import agent_node
from src.agents.graph.agent_node import _SUMMARY_TAG, _compose_summary_message
from src.config.const import HISTORY_TOKEN_BUDGET
from src.infra.llm.token_count import count_tokens


def test_compose_summary_message_has_tag_and_yield_clause():
    """摘要段带标签与让位声明（事实以本轮检索为准 + 不确定时请用户复述）。"""
    message = _compose_summary_message("## 用户目标\n看年报")
    assert isinstance(message, HumanMessage)
    assert _SUMMARY_TAG in message.content
    assert "以本轮检索结果为准" in message.content
    assert "请用户复述" in message.content


def test_compose_summary_message_strips_citation_numbers():
    """注入前剥离 [数字] 编号（防 format 阶段映射串号）。"""
    message = _compose_summary_message("见 [1] 与 [2]")
    assert "[1]" not in message.content
    assert "[2]" not in message.content


def test_summary_message_inserted_after_last_system_message():
    """摘要段插到最后一个 SystemMessage 之后、普通历史之前。"""
    messages = [
        SystemMessage(content="SYS-1"),
        SystemMessage(content="SYS-2"),
        HumanMessage(content="旧问题"),
        HumanMessage(content="当前问题"),
    ]
    out = agent_node._insert_after_last_system(messages, [_compose_summary_message("摘要")])
    assert isinstance(out[2], HumanMessage)
    assert _SUMMARY_TAG in out[2].content
    assert out[3].content == "旧问题"


def test_summary_and_tail_fit_budget():
    """聚合预算：摘要段与尾部合计超 `HISTORY_TOKEN_BUDGET` 时先缩摘要。"""
    from src.agents.graph.agent_node import _fit_summary_to_budget

    tail = [HumanMessage(content="尾" * 3000)]
    # 摘要远大于「预算 − 尾部」的余量，强制触发截断（否则该用例恒真、测不到东西）
    over = "摘" * 20000
    fitted = _fit_summary_to_budget(over, tail)
    total = count_tokens(fitted) + sum(count_tokens(m.content) for m in tail)
    assert total <= HISTORY_TOKEN_BUDGET
    assert len(fitted) < len(over)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agents/graph/test_summary_injection.py -v`
Expected: FAIL —— `ImportError: cannot import name '_SUMMARY_TAG' from 'src.agents.graph.agent_node'`

- [ ] **Step 3: AgentState 增字段与参数**

`src/agents/graph/state.py` 的 `── 输入 ──` 段内（`direct_skill` 之后）加：

```python
    _summary: str = ""  # 跨轮历史摘要正文（来源：stream_chat 预取后经 make_initial_state 传入；范围：整轮执行；用途：seed 点作为独立历史消息注入；空=无摘要）
```

`make_initial_state`（`:54-75`）签名加参数并在 `return cls(...)` 里带上：

```python
    def make_initial_state(
        cls,
        session_id,
        kb_id,
        query,
        history,
        deep_thinking=False,
        direct_skill="",
        summary="",
    ):
```

```python
            _summary=summary,
```

（摘要的 token 量由 seed 点组装时按需计算、**不进 state**——避免每轮多一次无谓计数。）

- [ ] **Step 4: `agent_node` 注入摘要段**

`src/agents/graph/agent_node.py`：模块级新增常量与两个纯函数（放在 `_truncate_history` 之后）：

```python
# 摘要段的包裹标签：注入时用它标识"这是历史摘要"，并附让位声明（防被当指令/证据）
_SUMMARY_TAG = "<会话摘要>"
_SUMMARY_YIELD_CLAUSE = (
    "（以下是更早会话的摘要，作为对话背景参考；"
    "事实性结论仍须以本轮检索结果为准；不确定时请用户复述。）"
)


def _compose_summary_message(summary: str) -> HumanMessage:
    """把摘要正文组装为独立历史消息（剥编号 + 加标签 + 让位声明）。

    Args:
        summary: 摘要正文

    Returns:
        可直接插入消息列表的 HumanMessage
    """
    from src.chat.history_summary import strip_citation_numbers

    clean = strip_citation_numbers(summary)
    return HumanMessage(content=f"{_SUMMARY_TAG}{clean}</会话摘要>\n{_SUMMARY_YIELD_CLAUSE}")


def _insert_after_last_system(
    messages: list, extra: list
) -> list:
    """把额外消息插到**最后一个 SystemMessage 之后**（复用既有注入位置语义）。"""
    if not extra:
        return messages
    insert_at = 0
    for i, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            insert_at = i + 1
    return messages[:insert_at] + list(extra) + messages[insert_at:]


def _fit_summary_to_budget(summary_text: str, tail: list) -> str:
    """把摘要缩到「摘要 + 尾部 ≤ HISTORY_TOKEN_BUDGET」之内。

    先缩摘要；若尾部本身已超预算（由既有裁剪路径负责），摘要不再让位。

    Args:
        summary_text: 摘要正文
        tail: 保留的最近若干轮消息

    Returns:
        适配后的摘要正文（尽可能保留头部）
    """
    tail_tokens = 0
    for message in tail:
        tail_tokens += count_tokens(message.content)
    allowance = HISTORY_TOKEN_BUDGET - tail_tokens
    if allowance <= 0:
        return ""
    if count_tokens(summary_text) <= allowance:
        return summary_text
    keep: list[str] = []
    used = 0
    for char in summary_text:
        cost = count_tokens(char)
        if used + cost > allowance:
            break
        keep.append(char)
        used += cost
    return "".join(keep)
```

修改 `_split_initial_messages`：在既有 `if injected:` 插入块（`:147-153`）之后、`core_logging.log_event(Event.PROMPT_MESSAGES, ...)`（`:155`）之前插入：

```python
    # 跨轮历史摘要段：作为独立历史消息，插在最后一个 system 之后、普通历史之前。
    # 来源是外部预取并经 state 传入的**值**（图节点不访问存储）；无摘要时零影响。
    if state._summary:
        tail_for_budget = [m for m in messages if not isinstance(m, SystemMessage)]
        fitted = _fit_summary_to_budget(state._summary, tail_for_budget)
        if fitted:
            messages = _insert_after_last_system(
                messages, [_compose_summary_message(fitted)]
            )
```

（`PROMPT_MESSAGES` 的计数保持原样；摘要段计入 `history_msgs` 之外的独立项不必要——它已在 `messages` 里。）

- [ ] **Step 5: `agent_service` 预取摘要并传值**

`src/services/agent_service.py`：在 `stream_chat` 读历史处（`:970-973`）之后加：

```python
        # 跨轮历史摘要：在此预取（本层持有 ChatManager），经 launch_context 传到
        # 图状态——图节点不访问存储。
        summary_text, _ = await self._chat_manager.get_summary_async(session_id)
```

在 `launch_context`（`:997-1005`）里加一个键：

```python
            "summary": summary_text,
```

并在 `_run_generation` 调 `make_initial_state`（`:643-645`）处改为：

```python
    initial_state = AgentState.make_initial_state(
        session_id,
        kb_id,
        query,
        history,
        deep_thinking,
        direct_skill,
        summary,
    )
```

（`summary` 的完整传递链，**四处都要改**：）

1. `_run_generation` 的签名（`agent_service.py:556-569`）在末尾追加参数（既有调用全走关键字，不破坏）：

```python
    direct_skill: str = "",
    user_id: str = "",
    summary: str = "",
) -> str:
```

2. `_run_generation` 内调 `make_initial_state`（`agent_service.py:643-645`）处补第 7 个位置参：

```python
    initial_state = AgentState.make_initial_state(
        session_id, kb_id, query, history, deep_thinking, direct_skill, summary
    )
```

3. `src/services/turn_runner.py` 的 `_make_answer_builder`（`:286-321`）内、调 `_run_generation`（`:303`）的关键字参数里加一行：

```python
            summary=launch_ctx.get("summary", ""),
```

4. `stream_chat` 的 `launch_context`（`agent_service.py:997-1005`）加键（见上一步）。

- [ ] **Step 6: 运行测试确认通过**

Run: `python -m pytest tests/agents/graph/test_summary_injection.py tests/agents/graph/ -v`
Expected: PASS

- [ ] **Step 7: 提交**

Run: `ruff check . && pyright src/agents/graph/agent_node.py src/agents/graph/state.py src/services/agent_service.py`

```bash
git add src/agents/graph/state.py src/agents/graph/agent_node.py src/services/agent_service.py tests/agents/graph/test_summary_injection.py
git commit -m "feat(context): seed 点只读注入摘要段（值通道 + 让位声明 + 聚合预算上限）"
```

---

### Task 6: 文档登记与端到端验收

**Files:**
- Modify: `docs/agents/glossary.md`、`docs/agents/logging-rules.md`、`docs/agents/code-map.md`

**Interfaces:**
- Consumes: Task 1–5 全部产出
- Produces: 无代码产出

- [ ] **Step 1: 登记术语**

`docs/agents/glossary.md` 增加两条（与既有条目格式一致）：

- **跨轮摘要**：把超出历史预算的更旧对话压成结构化摘要（用户目标/已达成的决定/未解约束/关键事实与数字/下一步），持久化在 Redis `chat_summary:{session_id}`，供后续轮次注入。
- **摘要覆盖边界**：摘要已覆盖到的消息条数（按条数计，与 Redis List 索引对应），用于增量更新与避免重复摘要。

- [ ] **Step 2: 登记事件**

`docs/agents/logging-rules.md` 的会话事件登记处补两行：`summary done`（session / info，`covered` / `tokens`）、`summary fallback`（session / warning，`reason` / `err`）。

- [ ] **Step 3: 登记落点**

`docs/agents/code-map.md` 补记：`src/chat/history_summary.py`（摘要核心）、`src/services/summary_scheduler.py`（调度）、`src/services/summary_lock.py`（独立摘要锁）、`src/agents/graph/history_window.py`（历史窗口切分）、`src/models.py::get_summary_llm`。

- [ ] **Step 4: 跑闸门**

Run: `python -m src.cli.check_docs && python -m src.cli.check_adr && openspec validate cross-turn-history-summary`
Expected: 均通过

- [ ] **Step 5: 端到端抽查（真实多轮中文会话）**

按 `docs/agents/cookbook.md` 的 worktree 跑法起服务（**注意 Docker 不隔离**，从 worktree `up -d` 会把用户的 `corporate-rag-app` 重建成指向本 worktree 的 `src`；如不希望如此，先与用户确认）。

1. 连续提问至越过触阈（≥ 20 条消息且被丢弃段 > 12288 token）。
2. 检查 `/data/logs/` 是否出现 `summary done`；Redis 中 `chat_summary:{sid}` 存在且 `covered` 与预期一致。
3. 触阈当轮：注入历史与改动前一致（无摘要段）。
4. 下一轮：日志/观测确认注入含摘要段，顺序为「system → 摘要 → 尾部」；答案 `[n]` 仍指向**本轮**来源。
5. `GET /sessions/messages` 回放仍为**完整原文**、不含摘要。
6. 专项：把 `HISTORY_SUMMARY_TRIGGER_TOKENS` 临时调小以加速触阈，验证：被丢弃段为空时**无** `summary done`（防空转）；人为残留 `chat_summary_lock:{sid}` 后重启，确认启动清理把它删掉（不会永久禁用该会话）。

- [ ] **Step 6: 提交文档**

```bash
git add docs/agents/glossary.md docs/agents/logging-rules.md docs/agents/code-map.md
git commit -m "docs(context): 登记跨轮摘要术语、事件与模块落点"
```

---

## 验收清单（全部任务完成后）

- [ ] `POSTGRES_HOST=localhost python -m pytest tests/ -v` 全绿
- [ ] `ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] `python -m src.cli.check_docs` / `check_adr` / `openspec validate cross-turn-history-summary` 通过
- [ ] `src/agents/graph/middleware.py` **未被本 change 修改**（仍 393 行）
- [ ] 全仓无「摘要写 MySQL」路径；`GET /sessions/messages` 回放不含摘要
- [ ] `chat_summary_lock:{sid}` 与 `chat_lock:{sid}` 是两个不同前缀（grep 断言）
