# 上下文预算地基（Phase A）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把项目的上下文预算从"写死窗口 × 比例 + 字符数粗估"改成"集中的绝对预算 + 分词器计数 + 轮内度量"，为零 LLM、可断言的地基（Phase A）。

**Architecture:** 新增一个薄计数模块（tiktoken 近似 + 失败降级 + 启动预热），把 `estimate_usage` 与 `_truncate_history` 两处 `len//2` 切到它；`_truncate_history` 的预算口径改为集中常量 `HISTORY_TOKEN_BUDGET`（与模型窗口解耦，删除写死的 `context_window=8000` 与 `token_ratio`）；在既有最内层 middleware `AgentSpanMiddleware` 的**模型调用前**度量本轮输入 token，达上限记一条新事件——**只度量，不截断**。

**Tech Stack:** Python 3.11+ / LangChain 1.3.11 + LangGraph / tiktoken 0.13.0（已装）/ pytest。

**Spec:** `docs/openspec/changes/context-window-and-budget-foundation/`（proposal.md / design.md / specs/context-budget/spec.md / specs/token-usage-model/spec.md）。设计依据另见 `docs/adr/0017-context-ladder-and-memory-scope.md` 与调研报告 `docs/context-memory-research.md`。

## Global Constraints

- **常量集中**：新增阈值一律放 `src/config/`（`const.py` 放固定阈值，`settings.py` 放环境变量）。业务代码不得内联数字。
- **日志**：事件必须先在 `src/core/log_events.py` 的 `Event` 枚举与 `src/core/log_event_specs.py` 的 `EVENT_SPECS` 登记（import 期 `_validate_registry` 会断言两者**逐一对应**，前缀须在 `LOG_PREFIXES` 内，级别只能是 `info/warning/error`）；运行时只用 `core_logging.log_event(Event.X, **fields)`，不得手拼字符串。
- **不用三元表达式**（`a if cond else b`），写完整 if/else。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底，用 `isinstance` 判断。
- **注释与 docstring 用中文**；写"现在的机制"，不写变更历史。
- **文件 < 400 行、函数 < 80 行**。
- **测试不发起真实网络调用**；测试内不得依赖 DB。
- **worktree 内禁用 `git add -A` / `git add .`**（`.venv` 是 symlink，会被当未跟踪文件带进提交）——一律 `git add <显式路径...>`。
- **验证三件套**：`pytest`（定向）/ `ruff check .` / `pyright src/`（不新增 error）。
- **本 change 明确不做**：窗口注册表/解析器、离线探针 CLI、工具结果截断/去重/占位、摘要压缩。轮内度量**只记不改内容**。

---

### Task 1: 统一 token 计数入口

**Files:**
- Create: `src/infra/llm/token_count.py`
- Create: `tests/infra/llm/test_token_count.py`
- Modify: `src/main.py`（lifespan 启动预热）

**Interfaces:**
- Consumes: 无（本任务是最底层）
- Produces:
  - `count_tokens(text: str) -> int`
  - `count_messages_tokens(messages: Sequence[Any]) -> int`
  - `warm_token_encoder() -> bool`

> 背景（实测，决定实现形态）：`tiktoken.get_encoding("cl100k_base")` **首次调用要联网下载词表**——容器内实测 3.88s / 1.68MB，落在 `~/.cache`。故必须①进程内缓存 encoder、②启动时预热、③拿不到时降级为旧口径 `len//2` 并记一次 warning（绝不中断请求）。

- [ ] **Step 1: 写失败测试**

创建 `tests/infra/llm/test_token_count.py`：

```python
"""统一 token 计数入口的单测（不联网：encoder 已在环境缓存）。"""

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm import token_count
from src.infra.llm.token_count import count_messages_tokens, count_tokens


def test_count_tokens_handles_empty():
    """空串为 0，不抛异常。"""
    assert count_tokens("") == 0


def test_count_tokens_counts_text():
    """非空文本计数为正整数。"""
    assert count_tokens("hello world") > 0


def test_count_tokens_exceeds_char_heuristic_on_chinese():
    """中文语料下分词器计数显著高于旧口径 len//2（钉住"确实换了口径"）。"""
    text = "腾讯控股二零二四年年报营业收入" * 10
    assert count_tokens(text) > len(text) // 2


def test_count_messages_tokens_joins_str_contents():
    """各条 str 型 content 以空格连接后统一计数。"""
    messages = [
        SystemMessage(content="SYS-ONE"),
        SystemMessage(content="SYS-TWO"),
        HumanMessage(content="hi"),
    ]
    assert count_messages_tokens(messages) == count_tokens("SYS-ONE SYS-TWO hi")


def test_count_messages_tokens_skips_non_message():
    """非 BaseMessage 元素被跳过，不抛异常。"""
    assert count_messages_tokens([object(), HumanMessage(content="hi")]) == count_tokens(
        "hi"
    )


def test_count_tokens_degrades_when_encoder_unavailable(monkeypatch):
    """encoder 不可用时降级为 len(text)//2，且不抛异常。"""
    monkeypatch.setattr(token_count, "_encoder", lambda: None)
    assert count_tokens("abcdefghij") == 5


def test_warm_token_encoder_reports_availability(monkeypatch):
    """预热返回可用性布尔值；encoder 缺失时为 False。"""
    monkeypatch.setattr(token_count, "_encoder", lambda: None)
    assert token_count.warm_token_encoder() is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/infra/llm/test_token_count.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.infra.llm.token_count'`

- [ ] **Step 3: 实现计数模块**

创建 `src/infra/llm/token_count.py`：

```python
"""统一 token 计数入口 —— tiktoken 近似计数。

定位：跨调用点的**相对一致与量级**，不是绝对精度（非 OpenAI 模型无本地分词器；
真值仍以 provider 返回的 `usage_metadata` 为准）。

encoder 首次获取需联网下载词表（实测容器内约 4s、1.7MB，落 ~/.cache），故：
  - 进程内缓存 encoder，只付一次成本；
  - 应用启动时预热（`warm_token_encoder`），避免首个用户请求承担该延迟；
  - 拿不到 encoder 时降级为旧口径 `len(text) // 2` 并记一次 warning，绝不中断请求。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from typing import Any

import tiktoken
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

_ENCODING_NAME = "cl100k_base"

# 降级告警只记一次（encoder 缺失是环境级问题，逐次告警只会刷屏）
_WARNED_UNAVAILABLE = False


@lru_cache(maxsize=1)
def _encoder() -> Any | None:
    """返回进程内缓存的 tiktoken encoder；不可用时返回 None（并记一次 warning）。"""
    global _WARNED_UNAVAILABLE
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception as exc:  # noqa: BLE001
        if not _WARNED_UNAVAILABLE:
            _WARNED_UNAVAILABLE = True
            logger.warning(
                "[llm] token encoder unavailable, fallback to len//2 err=%s", exc
            )
        return None


def warm_token_encoder() -> bool:
    """预热 encoder（应用启动时调用），返回其可用性。

    Returns:
        True = encoder 可用；False = 不可用（调用方无需处理，计数会自行降级）
    """
    return _encoder() is not None


def count_tokens(text: str) -> int:
    """返回文本的 token 数；encoder 不可用时降级为 `len(text) // 2`。"""
    if not text:
        return 0
    encoder = _encoder()
    if encoder is None:
        return max(1, len(text) // 2)
    return len(encoder.encode(text))


def count_messages_tokens(messages: Sequence[Any]) -> int:
    """返回消息列表的 token 数：各条 str 型 content 以空格连接后统一计数。

    非 `BaseMessage` 元素与 list 型（多模态）content 一律跳过。

    Args:
        messages: 待计数的消息序列

    Returns:
        连接后文本的 token 数；无可计数内容时为 0
    """
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, BaseMessage):
            continue
        if isinstance(message.content, str):
            parts.append(message.content)
    return count_tokens(" ".join(parts))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/infra/llm/test_token_count.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 在应用启动时预热 encoder**

修改 `src/main.py`：在 import 区加一行

```python
from src.infra.llm.token_count import warm_token_encoder
```

并在 `lifespan` 内、`core_logging.log_event(Event.APP_STARTING)`（`src/main.py:55`）之后插入：

```python
    # 预热 token 分词器：首次获取需联网下载词表（实测约 4s），放启动期可避免
    # 首个用户请求承担该延迟；不可用不影响启动（计数会自行降级）
    warm_token_encoder()
```

- [ ] **Step 6: 全量校验并提交**

Run: `python -m pytest tests/infra/llm/ -v && ruff check src/infra/llm/token_count.py src/main.py tests/infra/llm/test_token_count.py && pyright src/infra/llm/token_count.py`
Expected: 全绿、无新增 error

```bash
git add src/infra/llm/token_count.py tests/infra/llm/test_token_count.py src/main.py
git commit -m "feat(context): 新增统一 token 计数入口（tiktoken 近似 + 降级 + 启动预热）"
```

---

### Task 2: `estimate_usage` 切到分词器

**Files:**
- Modify: `src/infra/llm/token_usage.py:15-26`
- Modify: `tests/agents/graph/test_loop_middleware.py:527-550`

**Interfaces:**
- Consumes: Task 1 的 `count_messages_tokens(messages) -> int` 与 `count_tokens(text: str) -> int`
- Produces: `estimate_usage(messages: list, output: str) -> TokenUsage`（**签名与返回形状不变**，仅计数依据改变）

- [ ] **Step 1: 修正既有断言（先让它表达新口径）**

`tests/agents/graph/test_loop_middleware.py:528-534` 的 docstring 与 `:550` 的断言依赖旧口径 `len//2`。改 docstring 为：

```python
    """用量估算的输入同样含 system 段（漏掉会让每轮估算系统性偏小）。

    口径：`estimate_usage` 把消息 content 以空格连接后交 tiktoken 计数。
    `"SYS-ONE SYS-TWO hi"` → **7** token；若只算 `request.messages`（丢 system）
    则为 `"hi"` → 1 —— 该断言正是用来钉住这一点。
    """
```

并把 `:550` 改为：

```python
    assert recorded[0]["usage"]["input"] == 7
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agents/graph/test_loop_middleware.py::test_agent_turn_usage_estimate_includes_system -v`
Expected: FAIL —— `assert 9 == 7`（`estimate_usage` 仍是 `len//2`）

- [ ] **Step 3: 切换计数依据**

把 `src/infra/llm/token_usage.py` 全文替换为：

```python
"""Token 用量数据结构 — 统一描述 LLM 调用的 token 消耗。"""

from dataclasses import dataclass

from src.infra.llm.token_count import count_messages_tokens, count_tokens


@dataclass
class TokenUsage:
    """Token 用量统一结构 —— 跨调用点共享的估算与映射结果。"""

    prompt_tokens: int = 0  # 输入 token 数（提示部分，从 LLM 原生或估算）
    completion_tokens: int = 0  # 输出 token 数（补全部分，从 LLM 原生或估算）
    total_tokens: int = 0  # 总 token 数（prompt + completion）


def estimate_usage(messages: list, output: str) -> TokenUsage:
    """估算 token 用量（分词器计数；消息与输出复用同一计数入口）。"""
    prompt_tokens = max(1, count_messages_tokens(messages))
    completion_tokens = max(1, count_tokens(output))
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
```

> 原实现用 `getattr(m, "content", "")` 做提取；本步改为复用 `count_messages_tokens`（`isinstance(BaseMessage)` 显式判断），既去掉字符数粗估、也去掉隐式兜底与重复的提取逻辑。两个调用点（`middleware._record_turn`、`fork_stream.py:337`）传的都是 `BaseMessage`，行为不变。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/agents/graph/test_loop_middleware.py tests/infra/llm/test_token_usage.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

Run: `ruff check src/infra/llm/token_usage.py && pyright src/infra/llm/token_usage.py`

```bash
git add src/infra/llm/token_usage.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(context): estimate_usage 改用分词器计数（修受影响断言）"
```

---

### Task 3: 历史预算口径收口

**Files:**
- Modify: `src/config/const.py:77-78`
- Modify: `src/agents/graph/agent_node.py:16-57,119`
- Modify: `tests/agents/graph/test_history_window.py`

**Interfaces:**
- Consumes: Task 1 的 `count_tokens(text: str) -> int`
- Produces:
  - `HISTORY_TOKEN_BUDGET: int`（`src/config/const.py`）
  - `_truncate_history(history: list[ChatMessage], max_turns: int = HISTORY_MAX_TURNS, token_budget: int = HISTORY_TOKEN_BUDGET) -> list[ChatMessage]`（**`token_ratio` 与 `context_window` 两个形参被删除**）

- [ ] **Step 1: 改写既有测试（表达新口径）**

把 `tests/agents/graph/test_history_window.py` 全文替换为：

```python
"""测试历史窗口截断 — _truncate_history 的轮数粗筛 + 绝对 token 预算。

预算口径为集中的绝对值 `HISTORY_TOKEN_BUDGET`（与模型窗口解耦），
计数走 `src.infra.llm.token_count`（tiktoken）。直接构造 ChatMessage 调用
模块函数，不发真实网络调用。

"最近 1 轮保留例外"路径会记 `history budget exceeded`，故用 autouse fixture
拦截 `log_event`：既收集该事件供断言，也避免日志污染测试输出。
"""

import pytest

from src.agents.graph.agent_node import _truncate_history
from src.config.const import HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens

_MSG_X = "x" * 2000
_MSG_Y = "y" * 2000
# 一轮 = user(x*2000) + assistant(y*2000)；动态算出，避免把分词器数字写死在断言里
_TURN_TOKENS = count_tokens(_MSG_X) + count_tokens(_MSG_Y)


@pytest.fixture(autouse=True)
def _capture_events(monkeypatch) -> list[dict]:
    """拦截 log_event（日志走 loguru，caplog 抓不到）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.core.logging.log_event", fake_log_event)
    return calls


def test_truncate_keeps_recent_turns(_capture_events):
    """15 轮历史 → 输出 ≤ 20 条（最近 10 轮），最后一条是最近的 assistant 消息。"""
    history = []
    for i in range(15):
        history.append(ChatMessage(role="user", content=f"q{i}"))
        history.append(ChatMessage(role="assistant", content=f"a{i}"))

    out = _truncate_history(history, max_turns=HISTORY_MAX_TURNS)

    assert len(out) <= 20  # 10 轮 * 2 条
    assert out[-1].content == "a14"  # 最近一条保留
    assert _capture_events == []  # 未超预算：不记例外


def test_token_budget_truncates_oldest(_capture_events):
    """总 token 超预算时从最旧逐条弹出，条数下降但不少于最近 1 轮。"""
    history = []
    for _ in range(5):
        history.append(ChatMessage(role="user", content=_MSG_X))
        history.append(ChatMessage(role="assistant", content=_MSG_Y))

    # 预算恰好容得下 2 轮（+1 token 余量），故第 3 轮起被裁
    out = _truncate_history(history, token_budget=_TURN_TOKENS * 2 + 1)

    assert len(out) == 4
    assert out[-1].content == _MSG_Y
    assert _capture_events == []  # 裁到预算内：不记例外


def test_recent_round_always_kept_and_logged(_capture_events):
    """极端小预算下最后 1 轮（2 条）仍完整保留，并显式记录该例外。"""
    history = []
    for i in range(3):
        history.append(ChatMessage(role="user", content=f"q{i}" * 2000))
        history.append(ChatMessage(role="assistant", content=f"a{i}" * 2000))

    out = _truncate_history(history, token_budget=100)

    assert len(out) == 2  # 最近 1 轮（2 条）不被截
    assert out[0].content == "q2" * 2000
    assert out[-1].content == "a2" * 2000
    exceptions = [
        e for e in _capture_events if e["event"] == Event.HISTORY_BUDGET_EXCEEDED
    ]
    assert len(exceptions) == 1
    assert exceptions[0]["budget"] == 100
    assert exceptions[0]["used"] > 100
    assert exceptions[0]["kept"] == 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agents/graph/test_history_window.py -v`
Expected: FAIL —— `TypeError: _truncate_history() got an unexpected keyword argument 'token_budget'`（且 `HISTORY_TOKEN_BUDGET` 尚未定义）

- [ ] **Step 3: 改常量**

`src/config/const.py:77-78`，把：

```python
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数
HISTORY_TOKEN_RATIO = 0.3  # 历史 token 占 context 窗口上限比例
```

替换为：

```python
HISTORY_MAX_TURNS = 10  # 历史注入保留最近轮数（轮数粗筛）
# 历史注入的 token 预算（**绝对值**，与模型窗口解耦）。原「窗口 × 比例」口径已废弃：
# 比例会随窗口漂移，且窗口值本身是假设值。初值取保守量级（远小于任一模型窗口），
# 待按线上 Langfuse token 分布标定后再调。
HISTORY_TOKEN_BUDGET = 16384
```

- [ ] **Step 4: 改 `_truncate_history` 与调用点**

`src/agents/graph/agent_node.py`：import 区（`:16-20`）把 `HISTORY_TOKEN_RATIO` 换成 `HISTORY_TOKEN_BUDGET`：

```python
from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_TOKEN_BUDGET,
    SKILL_INJECTION_PREFIX,
)
```

并新增一行 import：

```python
from src.infra.llm.token_count import count_tokens
```

把 `:28-57` 的函数整体替换为：

```python
def _truncate_history(
    history: list[ChatMessage],
    max_turns: int = HISTORY_MAX_TURNS,
    token_budget: int = HISTORY_TOKEN_BUDGET,
) -> list[ChatMessage]:
    """历史窗口截断：保留最近 N 轮 + 绝对 token 预算，最近 1 轮完整保留。

    Args:
        history: 完整对话历史（user/assistant 交替排列）
        max_turns: 保留的最近轮数（每轮 user+assistant 两条消息）
        token_budget: 历史消息总 token 上限（绝对值，集中 `const.py`）

    Returns:
        截断后的历史列表：先按轮数保留最近 max_turns 轮，总 token 超出预算时
        从最旧逐条弹出直到达标，最近 1 轮（最后 2 条）始终不截。
        计数走 `count_tokens`（分词器近似）。返回新列表，不修改入参。
    """
    if len(history) > max_turns * 2:
        recent = history[-(max_turns * 2) :]
    else:
        recent = list(history)
    total = sum(count_tokens(m.content) for m in recent)
    while total > token_budget and len(recent) > 2:
        dropped = recent.pop(0)
        total -= count_tokens(dropped.content)
    if total > token_budget:
        core_logging.log_event(
            Event.HISTORY_BUDGET_EXCEEDED,
            budget=token_budget,
            used=total,
            kept=len(recent),
        )
    return recent
```

> 该日志事件在 Task 3 Step 5 登记。

调用点 `:119` 原地不动：`history = _truncate_history(state._history or [])`——预算与轮数都由默认值提供，不再有"写死 8000 且不传参"的错配。

- [ ] **Step 5: 澄清 `MODEL_CONTEXT_WINDOW_TOKENS` 的定位**

`src/config/settings.py:89-94` 的注释仍写着旧模型名（`qwen3.7-flash-2026-07-15`），且未说明"不参与历史预算"。把该注释块替换为：

```python
# context window 大小（token）——**假设值**，仅用于 `src/rag/prompt.py` 的
# system 段占比告警（`PROMPT_CONTEXT_SHARE_WARN`）的量级换算，**不参与历史预算**
# （历史预算见 const.py 的 HISTORY_TOKEN_BUDGET，为与窗口解耦的绝对值）。
# 实测当前运行时模型（qwen3.8-2.4t-a95b）输入上限为 983616；此处仍未按实测更新，
# 待确证后单独处理（属 Phase B 的窗口注册表范围）。
MODEL_CONTEXT_WINDOW_TOKENS: int = int(
    os.getenv("MODEL_CONTEXT_WINDOW_TOKENS", "32768")
)
```

- [ ] **Step 6: 登记「最近 1 轮例外」日志事件**

`src/core/log_events.py` 的 `[agent]` 段（`ITERATION_LIMIT` 一行附近）加：

```python
    HISTORY_BUDGET_EXCEEDED = "history budget exceeded"
```

`src/core/log_event_specs.py` 的 `EVENT_SPECS` 里加（紧邻 `"iteration limit"` 条目）：

```python
    "history budget exceeded": EventSpec(
        "history budget exceeded", "agent", "warning", ("budget", "used", "kept")
    ),
```

- [ ] **Step 7: 运行测试确认通过**

Run: `python -m pytest tests/agents/graph/test_history_window.py tests/core/test_log_events.py tests/core/test_logging_helpers.py -v`
Expected: PASS（含 import 期注册表一致性校验）

- [ ] **Step 8: 回归受影响的调用面并提交**

Run: `python -m pytest tests/agents/ tests/rag/ -v && ruff check . && pyright src/`

```bash
git add src/config/const.py src/config/settings.py src/agents/graph/agent_node.py src/core/log_events.py src/core/log_event_specs.py tests/agents/graph/test_history_window.py
git commit -m "feat(context): 历史预算改为集中绝对预算（废弃窗口×比例口径）"
```

---

### Task 4: 轮内累积度量（只度量，不截断）

**Files:**
- Modify: `src/config/const.py`（新增 `TURN_INPUT_TOKEN_LIMIT`）
- Modify: `src/core/log_events.py`、`src/core/log_event_specs.py`（新事件）
- Modify: `src/agents/graph/middleware.py`（`AgentSpanMiddleware` 加模型调用前度量）
- Modify: `tests/agents/graph/test_loop_middleware.py`（新增两个用例）

**Interfaces:**
- Consumes: Task 1 的 `count_messages_tokens(messages) -> int`；`AgentSpanMiddleware._sent_messages(request)`（既有，返回 `[system?, *request.messages]`）
- Produces:
  - `TURN_INPUT_TOKEN_LIMIT: int`（`src/config/const.py`）
  - `Event.CONTEXT_BUDGET_HIGH`（值 `"context budget high"`，前缀 `agent`，级别 `warning`，字段 `used` / `limit`）
  - `AgentSpanMiddleware._measure_turn_input(request) -> None`

- [ ] **Step 1: 写失败测试**

在 `tests/agents/graph/test_loop_middleware.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_turn_input_budget_logs_when_over_limit(monkeypatch):
    """轮内输入 token 达上限 → 记 context budget high（含 used/limit）。"""
    events = _capture_events(monkeypatch)
    monkeypatch.setattr(
        "src.agents.graph.middleware.TURN_INPUT_TOKEN_LIMIT", 1, raising=True
    )
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "kb_id": "kb-1",
            "_system_messages": _sysmsgs("SYS-ONE", "SYS-TWO"),
        }
    )
    budget_events = [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH]
    assert len(budget_events) == 1
    assert budget_events[0]["limit"] == 1
    assert budget_events[0]["used"] > 1


@pytest.mark.asyncio
async def test_turn_input_budget_silent_when_under_limit(monkeypatch):
    """未达上限时不记事件（避免日志噪音）。"""
    events = _capture_events(monkeypatch)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"}
    )
    assert [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH] == []


@pytest.mark.asyncio
async def test_turn_input_measure_failure_degrades(monkeypatch):
    """度量抛异常时降级：请求照常完成，不产生该事件。"""
    events = _capture_events(monkeypatch)

    def _boom(_messages):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.agents.graph.middleware.count_messages_tokens", _boom)
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware(), AgentSpanMiddleware()],
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"}
    )
    assert result["messages"][-1].content == "ok"
    assert [e for e in events if e["event"] == Event.CONTEXT_BUDGET_HIGH] == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/agents/graph/test_loop_middleware.py -k turn_input -v`
Expected: FAIL —— `AttributeError: type object 'Event' has no attribute 'CONTEXT_BUDGET_HIGH'`

- [ ] **Step 3: 加常量与新事件**

`src/config/const.py` 的「agent 循环护栏常量」段（`MAX_AGENT_ITERATIONS` 之后）加：

```python
# 单轮内累积输入 token 的告警上限。与 MAX_AGENT_ITERATIONS 联动：迭代越多、
# 工具结果累积越大；本阶段只度量告警，不做任何压缩（压缩属摘要层）。
TURN_INPUT_TOKEN_LIMIT = 65536
```

`src/core/log_events.py` 的 `[agent]` 段加：

```python
    CONTEXT_BUDGET_HIGH = "context budget high"
```

`src/core/log_event_specs.py` 的 `EVENT_SPECS` 里加：

```python
    "context budget high": EventSpec(
        "context budget high", "agent", "warning", ("used", "limit")
    ),
```

- [ ] **Step 4: 在 middleware 里实现模型调用前度量**

`src/agents/graph/middleware.py` 的 import 区改动：

```python
from src.config.const import MAX_DELEGATE_BONUS, TURN_INPUT_TOKEN_LIMIT
```
```python
from src.infra.llm.token_count import count_messages_tokens
```

在 `AgentSpanMiddleware` 内、`_record_turn` 之前插入方法：

```python
    def _measure_turn_input(self, request: Any) -> None:
        """模型调用前度量本轮输入 token；达上限记 context budget high。

        输入取 `_sent_messages`（含 system 段）——与 `_record_turn` 的用量口径同源。
        本阶段**只度量、不处置**：不截断、不改写任何消息。度量失败降级（记 warning
        后继续），绝不因观测把一次成功调用变成错误。
        """
        try:
            used = count_messages_tokens(_sent_messages(request))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[agent] context budget measure failed err=%s", exc)
            return
        if used >= TURN_INPUT_TOKEN_LIMIT:
            core_logging.log_event(
                Event.CONTEXT_BUDGET_HIGH, used=used, limit=TURN_INPUT_TOKEN_LIMIT
            )
```

在 `awrap_model_call`（`:217-241`）里，`core_logging.log_event(Event.ITERATION_DONE, ...)`（`:223`）之后、`turn_start = time.monotonic()` 之前插入：

```python
        self._measure_turn_input(request)
```

在 `wrap_model_call`（`:243-248`）里，`core_logging.log_event(Event.ITERATION_DONE, ...)`（`:247`）之后插入同一行：

```python
        self._measure_turn_input(request)
```

同时把类 docstring 的「主循环观测（模型轮次日志 + Langfuse generation span）」一句补充为：

```python
    """主循环观测（模型轮次日志 + Langfuse generation span + 轮内输入预算告警）。
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/agents/graph/test_loop_middleware.py -v`
Expected: PASS（既有用例 + 新增 3 个）

- [ ] **Step 6: 全量校验并提交**

Run: `python -m pytest tests/ -v`（宿主侧前置 `POSTGRES_HOST=localhost`）`&& ruff check . && pyright src/`

```bash
git add src/config/const.py src/core/log_events.py src/core/log_event_specs.py src/agents/graph/middleware.py tests/agents/graph/test_loop_middleware.py
git commit -m "feat(context): 轮内累积输入度量与告警（只度量不截断）"
```

---

### Task 5: 文档登记与端到端验收

**Files:**
- Modify: `docs/agents/logging-rules.md`（新事件登记）
- Modify: `docs/agents/glossary.md`（新术语）
- Modify: `docs/agents/code-map.md`（新模块落点）

**Interfaces:**
- Consumes: Task 1–4 的全部产出
- Produces: 无代码产出（文档与验收证据）

- [ ] **Step 1: 登记新事件到日志规范**

`docs/agents/logging-rules.md` 的 `[agent]` 前缀行补记两个事件；在「扩展登记」小节列出字段：

- `history budget exceeded`（agent / warning）—— 历史裁剪后仍超预算（最近 1 轮保留例外）；字段 `budget` / `used` / `kept`
- `context budget high`（agent / warning）—— 单轮累积输入达上限；字段 `used` / `limit`

- [ ] **Step 2: 登记新术语**

`docs/agents/glossary.md` 增加两条（保持与现有条目格式一致）：

- **历史预算（`HISTORY_TOKEN_BUDGET`）**：跨轮历史注入的 token 上限，**绝对值**，集中 `src/config/const.py`，与模型窗口解耦。
- **轮内累积上限（`TURN_INPUT_TOKEN_LIMIT`）**：单轮内模型调用输入 token 的告警阈值；本阶段只告警。

- [ ] **Step 3: 更新代码地图落点**

`docs/agents/code-map.md` 的 `src/infra/llm/` 条目补记 `token_count.py`（统一 token 计数入口 / tiktoken 近似 / 失败降级）。

- [ ] **Step 4: 运行文档闸门**

Run: `python -m src.cli.check_docs && python -m src.cli.check_adr`
Expected: 均通过

- [ ] **Step 5: 端到端抽查（真实多轮对话）**

在 worktree 起服务或用现有容器（注意：**worktree 不隔离 Docker**，从 worktree `up -d` 会重建同一套容器，属预期）：

1. 发一次中文多轮对话（≥3 轮），确认回答正常。
2. 在容器日志 `/data/logs/` 中确认：
   - 历史实际 token 用量 ≤ `HISTORY_TOKEN_BUDGET`（未出现 `history budget exceeded`）；
   - 若单轮输入较大，出现 `context budget high used=… limit=65536`。
3. 记录实测数字（历史保留条数、单轮 used 值），写入 change 的 notes 或提交信息。

- [ ] **Step 6: 提交文档**

```bash
git add docs/agents/logging-rules.md docs/agents/glossary.md docs/agents/code-map.md
git commit -m "docs(context): 登记预算事件、术语与计数模块落点"
```

---

## 验收清单（全部任务完成后）

- [ ] `POSTGRES_HOST=localhost python -m pytest tests/ -v` 全绿
- [ ] `ruff check .` 无错误；`pyright src/` 不新增 error
- [ ] `python -m src.cli.check_docs` 与 `python -m src.cli.check_adr` 通过
- [ ] `openspec validate context-window-and-budget-foundation` 通过
- [ ] 全仓 `HISTORY_TOKEN_RATIO` 零引用；`context_window=8000` 不再存在
- [ ] 新事件在 `Event` 枚举与 `EVENT_SPECS` 中**逐一对应**（import 期校验通过即可证明）
