# Langfuse 工具观测与 trace 富化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每条工具调用在 Langfuse 里成为一条带工具名 / 入参 / 返回 / 耗时的 span（同一轮归入 `tools` 父 span），并给 trace 补 `user_id` / 低基数 `tags` / 业务 `metadata` 与实际模型成本。

**Architecture:** 不侵入图与工具定义 —— 在 `_run_generation` 既有的 `graph.astream_events(...)` 循环里新增一个请求内私有的消费者 `ToolTraceCollector`，按 `run_id` 配对 `on_tool_start` / `on_tool_end`，父 span 以节点级 `on_chain_start/end(name=="tools")` 开合。span 一律用命令式 `Langfuse.span(trace_id=…)` 创建（装饰器无法表达 start/end 分离）。

**Tech Stack:** Python 3.11 / LangGraph 1.2.9 / langchain-core 1.4.8 / Langfuse 自托管 v2.95.11（SDK `langfuse==2.60.10`）/ pytest + pytest-asyncio

**Spec:** `docs/openspec/changes/langfuse-trace-enrichment/`（`proposal.md` / `design.md` / `specs/llm-tracing/spec.md` / `tasks.md`）—— 本计划实现它；执行时两者一起读。

## Global Constraints

- **不新增第三方依赖**；只用既有 `langfuse` / `langchain_core` / `sqlalchemy`。
- **禁止三元表达式**：写完整 `if/else` 结构。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 兜底；用 `isinstance` 或 `x.attr if x is not None else default`。
- **单文件 ≤ 400 行、单函数 ≤ 80 行**；超了拆模块。
- **硬编码集中管理**：常量进 `src/config/`（`settings.py` = 环境变量/阈值，`const.py` = 固定常量）。
- **观测失败不得影响对话**：所有 Langfuse 调用包在 try/except 内，异常只记 warning。
- **绝对不做**：调用 `client.trace(...)`（会无条件覆盖 trace 的 `timestamp`）；`new Langfuse()`（绕过 `LANGFUSE_ENABLE` 与 `flush`）；用 `checkpoint_ns` 判空过滤事件（会丢掉全部工具事件）；按工具名分支。
- **测试期 tracing 已全局关停**（`tests/conftest.py`），新增用例不得发起真实网络调用。
- **提交命令形状**：commit 单独一条命令、输出重定向到文件、后台跑、**不加 `| tail`**。
- **本仓在 `/mnt/d`（Windows 盘经 9p）**：查受跟踪文件用 `git grep`，别用 `grep -r` 扫全仓。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/infra/llm/tool_trace.py` | 新建 | `ToolTraceCollector`：事件 → Langfuse span 的唯一落点 |
| `src/cli/seed_langfuse_models.py` | 新建 | 幂等把模型定价写入 Langfuse |
| `src/config/settings.py` | 修改 | 新增模型单价字段（env 驱动，默认 0 = 未配置） |
| `src/services/agent_service.py` | 修改 | 循环内挂采集器 + `finally` 关闭；trace 扩参 |
| `src/agents/graph/message_payload.py` | 修改 | role 规范化 + 补 `tool_calls` / tool `name` |
| `src/agents/graph/agent_node.py` | 修改 | 工具轮 generation 的 `output` 补 `tool_calls` |
| `src/api/chat.py` | 修改 | 请求内捕获 `user_id` 并显式传入；订正误注释 |
| `docs/adr/0014-*.md` + `docs/adr/README.md` | 新建/修改 | 记录命令式接入的取舍与 trace 记录范围 |
| `docs/agents/code-map.md` / `cookbook.md` | 修改 | 落点登记 + seed 操作步骤 |
| `.env.template` / `.env.example` | 修改 | 单价字段与 USD/单 token 口径说明 |

---

### Task 1: 模型单价配置字段

**Files:**
- Modify: `src/config/settings.py`（在 `LANGFUSE_ENABLE` 之后、`====== 分块质量评估 ======` 之前）
- Modify: `.env.template`、`.env.example`
- Test: `tests/config/test_settings.py`

**Interfaces:**
- Produces: `settings.MODEL_INPUT_PRICE_PER_TOKEN: float`、`settings.MODEL_OUTPUT_PRICE_PER_TOKEN: float`（默认 `0.0`，`0.0` 表示「未配置」）；两者经 `src/config/__init__.py` 的 `from src.config.settings import *` 自动可从 `src.config` 导入。

- [ ] **Step 1: 写失败测试**

追加到 `tests/config/test_settings.py`：

```python
def test_model_price_defaults_to_zero_meaning_unconfigured():
    """单价默认必须是 0.0 —— 0 是「未配置」的哨兵值（seed CLI 据此跳过）。"""
    from src.config import MODEL_INPUT_PRICE_PER_TOKEN, MODEL_OUTPUT_PRICE_PER_TOKEN

    assert MODEL_INPUT_PRICE_PER_TOKEN == 0.0
    assert MODEL_OUTPUT_PRICE_PER_TOKEN == 0.0


def test_model_price_is_env_driven(monkeypatch):
    """单价可由环境变量覆盖，且为浮点。"""
    import importlib

    monkeypatch.setenv("MODEL_INPUT_PRICE_PER_TOKEN", "0.000003")
    monkeypatch.setenv("MODEL_OUTPUT_PRICE_PER_TOKEN", "0.000006")

    from src.config import settings as settings_module

    reloaded = importlib.reload(settings_module)
    assert reloaded.MODEL_INPUT_PRICE_PER_TOKEN == 0.000003
    assert reloaded.MODEL_OUTPUT_PRICE_PER_TOKEN == 0.000006

    monkeypatch.delenv("MODEL_INPUT_PRICE_PER_TOKEN")
    monkeypatch.delenv("MODEL_OUTPUT_PRICE_PER_TOKEN")
    importlib.reload(settings_module)
```

> 注意：`importlib.reload` 会把本模块所有 `os.getenv` 重新求值；`tests/config/test_settings.py` 里既有的 `test_langfuse_enable_default_true` 就是这么做的，末尾的 reload 是为了把环境还原干净，避免污染同会话其他用例。

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_settings.py -q`
Expected: FAIL —— `ImportError: cannot import name 'MODEL_INPUT_PRICE_PER_TOKEN'`

- [ ] **Step 3: 实现配置字段**

在 `src/config/settings.py` 的 `LANGFUSE_ENABLE` 行之后插入：

```python
# ====== 模型定价（Langfuse 成本计算）======
# 供 seed CLI 写入 Langfuse 的模型定义；**单位是 USD / 单个 token**（不是每 1M）。
# 例：$3 per 1M tokens 要填 0.000003。
# 0.0 是哨兵值，表示「未配置」：seed CLI 会跳过，不在 Langfuse 里留下会被误读成
# 「免费」的零价模型定义。
MODEL_INPUT_PRICE_PER_TOKEN: float = float(
    os.getenv("MODEL_INPUT_PRICE_PER_TOKEN", "0")
)
MODEL_OUTPUT_PRICE_PER_TOKEN: float = float(
    os.getenv("MODEL_OUTPUT_PRICE_PER_TOKEN", "0")
)
```

在 `.env.template` / `.env.example` 的 Langfuse 段之后各加：

```bash
# ====== 模型定价（Langfuse 成本计算）======
# 单位：USD / 单个 token（$3 per 1M tokens → 填 0.000003）
# 0 = 未配置 → seed CLI 跳过，不在 Langfuse 写入零价模型定义
MODEL_INPUT_PRICE_PER_TOKEN=0
MODEL_OUTPUT_PRICE_PER_TOKEN=0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_settings.py -q`
Expected: PASS（新用例 2 passed）

- [ ] **Step 5: 提交**

```bash
git add src/config/settings.py tests/config/test_settings.py .env.template .env.example
git commit -m "feat(config): 新增模型单价字段（USD/单 token，0=未配置）"
```

---

### Task 2: seed 模型定价的幂等 CLI

**Files:**
- Create: `src/cli/seed_langfuse_models.py`
- Test: `tests/cli/test_seed_langfuse_models.py`

**Interfaces:**
- Consumes: `settings.LLM_MODEL`、`settings.MODEL_INPUT_PRICE_PER_TOKEN`、`settings.MODEL_OUTPUT_PRICE_PER_TOKEN`
- Produces:
  - `build_model_request(model_name: str, input_price: float, output_price: float) -> dict` —— 返回 `CreateModelRequest` 的构造参数（含 `match_pattern` / `unit`）
  - `async def seed(client, model_name: str, input_price: float, output_price: float) -> str` —— 返回 `"skipped"` / `"created"` / `"exists"`
  - `def run() -> int` —— 退出码：0 正常/跳过，1 失败

- [ ] **Step 1: 写失败测试**

新建 `tests/cli/test_seed_langfuse_models.py`：

```python
"""seed 模型定价的幂等与 pattern 行为（不发网络：client 为替身）。"""

from src.cli.seed_langfuse_models import build_model_request, seed


def test_match_pattern_is_anchored_and_matches_target():
    """锚定正则匹配目标模型（含大小写）。"""
    import re

    req = build_model_request("qwen3.8-flash", 0.000003, 0.000006)
    pattern = req["match_pattern"]
    assert re.search(pattern, "qwen3.8-flash")
    assert re.search(pattern, "QWEN3.8-FLASH")


def test_match_pattern_does_not_misfire_on_lookalikes():
    """Postgres `~` 是子串匹配：pattern 未锚定会误配形近名，故必须锚定。"""
    import re

    pattern = build_model_request("qwen3.8-flash", 0.000003, 0.000006)["match_pattern"]
    for lookalike in (
        "prefix-qwen3.8-flash",
        "qwen3.8-flash-old",
        "qwen3.8-flash-2026-07-15",
        "qwen3x8-flash",
        "qwen3-8-flash",
    ):
        assert not re.search(pattern, lookalike), lookalike


def test_match_pattern_escapes_regex_metachars():
    """模型名里的 `.` 必须被转义，不能当通配符。"""
    import re

    pattern = build_model_request("a.b", 0.0, 0.0)["match_pattern"]
    assert re.search(pattern, "a.b")
    assert not re.search(pattern, "axb")


def test_request_carries_unit_tokens():
    """unit 必须是 TOKENS，否则服务端不会按 token 算价。"""
    req = build_model_request("m", 0.000003, 0.000006)
    assert req["unit"] == "TOKENS"
    assert req["model_name"] == "m"
    assert req["input_price"] == 0.000003
    assert req["output_price"] == 0.000006


class _Models:
    def __init__(self, existing: list):
        self._existing = existing
        self.created: list = []

    def list(self, *, page=None, limit=None):
        class _Resp:
            def __init__(self, data):
                self.data = data

        return _Resp(self._existing)

    def create(self, *, request):
        self.created.append(request)
        return request


class _Client:
    def __init__(self, existing):
        self.api = type("_Api", (), {"models": _Models(existing)})()


async def test_skips_when_price_is_zero():
    """单价为 0（未配置）时不得写入零价模型定义。"""
    client = _Client([])
    outcome = await seed(client, "m", 0.0, 0.0)
    assert outcome == "skipped"
    assert client.api.models.created == []


async def test_creates_when_absent_and_idempotent_when_present():
    """不存在则创建；同名已存在则跳过（幂等判据只看 model_name）。"""
    client = _Client([])
    assert await seed(client, "m", 0.000003, 0.000006) == "created"
    assert len(client.api.models.created) == 1

    existing = [type("_M", (), {"model_name": "m"})()]
    client2 = _Client(existing)
    assert await seed(client2, "m", 0.000003, 0.000006) == "exists"
    assert client2.api.models.created == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_seed_langfuse_models.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.cli.seed_langfuse_models'`

- [ ] **Step 3: 实现 CLI**

新建 `src/cli/seed_langfuse_models.py`：

```python
"""把模型定价写入 Langfuse（幂等）。

**为什么是幂等 CLI 而不是 UI 手配**：dev / prod 要各自落地同一份定价，手配不可复现；
而 Langfuse v2 的 `LANGFUSE_INIT_*` 只能播种 project / user / key，**不能播种模型定价**。

**为什么 0 要跳过**：0 是「未配置」的哨兵（见 `settings.py`）。写入一个单价为 0 的
模型定义会让人误读成「免费」，比不写更糟。

**为什么 pattern 必须锚定**：服务端用 Postgres 的 `~` 做匹配，那是**子串**正则 ——
未锚定的 `(?i)qwen3.8-flash` 会命中 `prefix-qwen3.8-flash` / `qwen3.8-flash-old`
这类形近名，把单价套到别的模型上（是**误配**，不是匹配不到）。

用法：
    python -m src.cli.seed_langfuse_models            # 按 settings 写入
    python -m src.cli.seed_langfuse_models --dry-run  # 只打印将写入的内容

退出码：0 = 正常（含 skipped / exists）；1 = 失败（配置缺失或后端不可达）。
"""

import argparse
import asyncio
import re
import sys
from typing import Any

from langfuse.decorators import langfuse_context

from src.config import (
    LANGFUSE_ENABLE,
    LLM_MODEL,
    MODEL_INPUT_PRICE_PER_TOKEN,
    MODEL_OUTPUT_PRICE_PER_TOKEN,
)

#: Langfuse 模型定义的计价单位（配合 input_price/output_price 即 USD / 单 token）
UNIT_TOKENS = "TOKENS"

#: `models.list` 的翻页大小
LIST_PAGE_SIZE = 100


def build_model_request(
    model_name: str, input_price: float, output_price: float
) -> dict[str, Any]:
    """构造 Langfuse 模型定义的请求参数。

    Args:
        model_name: 模型名（取自 `settings.LLM_MODEL`）
        input_price: 输入单价（USD / 单 token）
        output_price: 输出单价（USD / 单 token）

    Returns:
        含 `model_name` / `match_pattern` / `unit` / `input_price` / `output_price`
        的字典；`match_pattern` 为锚定正则，由 `re.escape` 构造以防误配。
    """
    return {
        "model_name": model_name,
        "match_pattern": "(?i)^" + re.escape(model_name) + "$",
        "unit": UNIT_TOKENS,
        "input_price": input_price,
        "output_price": output_price,
    }


def _list_model_names(models: Any) -> set[str]:
    """翻页列出 Langfuse 侧已有的模型名。

    Args:
        models: `client_instance.api.models` 客户端

    Returns:
        已存在的模型名集合
    """
    names: set[str] = set()
    page = 1
    while True:
        response = models.list(page=page, limit=LIST_PAGE_SIZE)
        rows = response.data or []
        for row in rows:
            names.add(row.model_name)
        if len(rows) < LIST_PAGE_SIZE:
            return names
        page += 1


async def seed(
    client: Any, model_name: str, input_price: float, output_price: float
) -> str:
    """幂等写入一条模型定价。

    Args:
        client: Langfuse 客户端（生产传 `langfuse_context.client_instance`）
        model_name: 模型名
        input_price: 输入单价（USD / 单 token）
        output_price: 输出单价（USD / 单 token）

    Returns:
        "skipped"（未配置单价）/ "exists"（同名已存在）/ "created"（本次创建）
    """
    if input_price == 0.0 and output_price == 0.0:
        print("[skip] 单价未配置（0）——不写入零价模型定义")
        return "skipped"

    models = client.api.models
    if model_name in _list_model_names(models):
        print(f"[exists] 模型定义已存在，跳过：{model_name}")
        return "exists"

    request = build_model_request(model_name, input_price, output_price)
    models.create(request=request)
    print(
        f"[created] {model_name} "
        f"pattern={request['match_pattern']} "
        f"unit={request['unit']} "
        f"in={input_price} out={output_price} (USD/token)"
    )
    return "created"


def run(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数（None 时取 sys.argv[1:]）

    Returns:
        退出码：0 正常；1 失败
    """
    parser = argparse.ArgumentParser(description="把模型定价写入 Langfuse（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    args = parser.parse_args(argv)

    request = build_model_request(
        LLM_MODEL, MODEL_INPUT_PRICE_PER_TOKEN, MODEL_OUTPUT_PRICE_PER_TOKEN
    )
    if args.dry_run:
        print(f"[dry-run] 将写入：{request}")
        return 0

    if not LANGFUSE_ENABLE:
        print("[error] LANGFUSE_ENABLE=false，跳过")
        return 1

    client = langfuse_context.client_instance
    try:
        asyncio.run(
            seed(
                client,
                LLM_MODEL,
                MODEL_INPUT_PRICE_PER_TOKEN,
                MODEL_OUTPUT_PRICE_PER_TOKEN,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 后端不可达等统一收敛为一行
        print(f"[error] 写入失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/cli/test_seed_langfuse_models.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/cli/seed_langfuse_models.py tests/cli/test_seed_langfuse_models.py
git commit -m "feat(cli): 新增 seed_langfuse_models（幂等写入模型定价）"
```

---

### Task 3: `ToolTraceCollector`（核心）

**Files:**
- Create: `src/infra/llm/tool_trace.py`
- Test: `tests/infra/llm/test_tool_trace.py`

**Interfaces:**
- Produces:
  - `ToolTraceCollector(enabled: bool, trace_id: str, client: Any = None)`
  - `.consume(item: Any) -> None` —— 事件循环每项调一次
  - `.close() -> None` —— 收尾兜底，必须被调用
  - 内部扩展点：`._normalize_input(item)` / `._normalize_output(item)`（MCP 接入时在此加归一化 / 摘要，本期不做）

- [ ] **Step 1: 写失败测试**

新建 `tests/infra/llm/test_tool_trace.py`：

```python
"""ToolTraceCollector：事件 → span 的配对、归组、错误与兜底（client 为替身）。"""

from src.infra.llm.tool_trace import ToolTraceCollector


class _FakeSpan:
    """替身 span：记录 end() 收到的字段。"""

    def __init__(self, span_id: str, name: str, parent_id: str | None):
        self.id = span_id
        self.name = name
        self.parent_observation_id = parent_id
        self.ended: dict | None = None

    def end(self, **kwargs):
        self.ended = kwargs


class _FakeClient:
    """替身客户端：只实现 span(trace_id=...) 这一条被用到的路径。"""

    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def span(self, **kwargs):
        span = _FakeSpan(
            f"s{len(self.spans)}",
            kwargs.get("name", ""),
            kwargs.get("parent_observation_id"),
        )
        self.spans.append(span)
        return span


class _ToolMessage:
    """替身 ToolMessage（只带被测代码要读的三个字段）。"""

    def __init__(self, content: str, tool_call_id: str, name: str):
        self.content = content
        self.tool_call_id = tool_call_id
        self.name = name


def _item(event: str, name: str = "", run_id: str = "r1", **data):
    """构造一条 astream_events item（只含被测代码读取的键）。"""
    return {
        "event": event,
        "name": name,
        "run_id": run_id,
        "metadata": {"langgraph_node": "tools"},
        "data": data,
    }


def test_pairs_tool_span_by_run_id_and_nests_under_round():
    """chain 事件开合父 span；工具 span 以它为父，并按 run_id 配对 end。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(
        _item("on_tool_start", name="retrieve_kb", run_id="rA", input={"query": "q"})
    )
    collector.consume(_item("on_chain_end", name="tools"))
    collector.consume(
        _item("on_tool_end", name="retrieve_kb", run_id="rA", output="[1] 来源…")
    )

    round_span, tool_span = client.spans
    assert round_span.name == "tools"
    assert tool_span.name == "retrieve_kb"
    assert tool_span.parent_observation_id == round_span.id
    assert tool_span.ended is not None
    assert tool_span.ended["output"] == "[1] 来源…"
    assert round_span.ended is not None


def test_parallel_tools_share_one_parent():
    """同一轮并行多个工具：各自成 span，父同为一个 round span。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))
    collector.consume(_item("on_tool_start", name="b", run_id="r2", input={}))
    collector.consume(_item("on_tool_end", name="a", run_id="r1", output="A"))
    collector.consume(_item("on_tool_end", name="b", run_id="r2", output="B"))

    assert len(client.spans) == 3
    round_span = client.spans[0]
    assert client.spans[1].parent_observation_id == round_span.id
    assert client.spans[2].parent_observation_id == round_span.id


def test_tool_message_output_is_unpacked():
    """data.output 是 ToolMessage 对象时必须显式取字段，不能整对象塞进 span。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.consume(
        _item(
            "on_tool_end",
            name="retrieve_kb",
            run_id="r1",
            output=_ToolMessage("[1] 来源…", "call_1", "retrieve_kb"),
        )
    )

    tool_span = client.spans[-1]
    assert tool_span.ended["output"] == {
        "tool_call_id": "call_1",
        "name": "retrieve_kb",
        "content": "[1] 来源…",
    }


def test_tool_error_marks_error_level():
    """工具抛错（无 on_tool_end）也要收尾，并标 ERROR。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.consume(
        _item(
            "on_tool_error",
            name="retrieve_kb",
            run_id="r1",
            error=RuntimeError("boom"),
            tool_call_id="call_1",
        )
    )

    tool_span = client.spans[-1]
    assert tool_span.ended["level"] == "ERROR"
    assert "boom" in tool_span.ended["status_message"]


def test_close_ends_leftovers():
    """取消/异常路径：close() 必须关掉未结束的 span，不留悬空节点。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="retrieve_kb", run_id="r1", input={}))
    collector.close()

    assert all(span.ended is not None for span in client.spans)


def test_disabled_collector_produces_nothing():
    """LANGFUSE_ENABLE=false 时整条路径短路，一次 span 都不建。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=False, trace_id="t1", client=client)

    collector.consume(_item("on_chain_start", name="tools"))
    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))
    collector.close()

    assert client.spans == []


def test_events_outside_tools_node_are_ignored():
    """只认 metadata.langgraph_node == 'tools' 的事件（与 _convert_event 同口径）。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="t1", client=client)

    item = _item("on_tool_start", name="retrieve_kb", run_id="r1", input={})
    item["metadata"] = {"langgraph_node": "agent"}
    collector.consume(item)

    assert client.spans == []


def test_blank_trace_id_disables_collector():
    """trace_id 为空（无根）时不建 span —— 命令式路径没有 trace 可挂。"""
    client = _FakeClient()
    collector = ToolTraceCollector(enabled=True, trace_id="", client=client)

    collector.consume(_item("on_tool_start", name="a", run_id="r1", input={}))

    assert client.spans == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/llm/test_tool_trace.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.infra.llm.tool_trace'`

- [ ] **Step 3: 实现采集器**

新建 `src/infra/llm/tool_trace.py`：

```python
"""工具调用 → Langfuse span 采集器（事件流驱动，请求内私有）。

消费 `_run_generation` 里既有的 `graph.astream_events(...)` 事件流：以节点级
`on_chain_start/end(name=="tools")` 开合该轮的父 span，再把 `on_tool_start/end/error`
按 `run_id` 配对成子 span。

三条不变量（改动时不得破坏）：
1. **不按工具名分支、`data.output` 原样透传** —— 对工具实现无感，MCP 工具经统一入口
   进 ToolNode 即自动覆盖；
2. span 一律用 `Langfuse.span(trace_id=...)` 建，**不调用 `client.trace()`** ——
   后者会无条件覆盖 trace 的 `timestamp`；
3. `close()` 必须被调用（挂 `finally`）—— 取消 / 异常路径不关的 span 会永远悬空。
"""

import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import ToolMessage
from langfuse.decorators import langfuse_context

logger = logging.getLogger(__name__)

# 事件名与 `src/agents/graph/state.py::LangGraphEvent` 同值。此处刻意写字面量而不从
# agents 层 import：infra 不应反向依赖 agents（同值字符串是稳定契约）。
_EV_CHAIN_START = "on_chain_start"
_EV_CHAIN_END = "on_chain_end"
_EV_TOOL_START = "on_tool_start"
_EV_TOOL_END = "on_tool_end"
_EV_TOOL_ERROR = "on_tool_error"

# 只认该节点下的事件（与 `_convert_event` 的既有口径一致）。
# **不得**改用 `checkpoint_ns` 判空：实测 on_tool_* 的 checkpoint_ns 是
# `tools:<uuid>`（非空），照此过滤会丢掉全部工具事件。
_TOOLS_NODE = "tools"


def _now() -> datetime:
    """当前 UTC 时间（span 的起止时刻）。"""
    return datetime.now(UTC)


class ToolTraceCollector:
    """把工具事件转成 Langfuse span 的请求内私有采集器。

    每次请求 new 一个、绝不共享（跨事件累积状态 + 并发隔离）。

    Args:
        enabled: 是否产出（取自 `settings.LANGFUSE_ENABLE`；命令式路径不受
            `configure(enabled=False)` 管，必须自己断电）
        trace_id: 本轮 trace id（`current_trace_id.get()`）；空串视为无根、不产出
        client: Langfuse 客户端；None 时惰性取 `langfuse_context.client_instance`
            （**不得** `new Langfuse()`：那会绕过开关与关停 flush）
    """

    def __init__(self, enabled: bool, trace_id: str, client: Any = None) -> None:
        self._enabled = enabled and bool(trace_id)
        self._trace_id = trace_id
        self._client = client
        self._open: dict[str, Any] = {}  # run_id -> 尚未结束的工具 span
        self._round: Any = None  # 当前 tools 父 span

    # ---------- 对外 ----------

    def consume(self, item: Any) -> None:
        """事件循环每项调一次；非 tools 节点的事件直接返回。

        Args:
            item: `astream_events` 的单项（dict）
        """
        if not self._enabled or not isinstance(item, dict):
            return
        metadata = item.get("metadata") or {}
        if metadata.get("langgraph_node") != _TOOLS_NODE:
            return
        kind = item.get("event", "")
        if kind == _EV_CHAIN_START:
            self._open_round()
        elif kind == _EV_CHAIN_END:
            self._close_round()
        elif kind == _EV_TOOL_START:
            self._on_tool_start(item)
        elif kind == _EV_TOOL_END:
            self._on_tool_end(item)
        elif kind == _EV_TOOL_ERROR:
            self._on_tool_error(item)

    def close(self) -> None:
        """收尾兜底：关闭所有未结束的 span（取消 / 异常路径必经）。"""
        for span in list(self._open.values()):
            self._end_span(span)
        self._open.clear()
        self._close_round()

    # ---------- 扩展点（MCP 接入时在此加逻辑，本期不实现） ----------

    def _normalize_input(self, item: dict) -> Any:
        """事件 → span 入参。

        Args:
            item: 工具事件

        Returns:
            事件的 `data.input`（LLM 可见的干净实参，不含 InjectedState）
        """
        return (item.get("data") or {}).get("input")

    def _normalize_output(self, item: dict) -> Any:
        """事件 → span 输出。

        `on_tool_end` 的 `data.output` 在工具路径下是 `ToolMessage` 对象 —— 必须显式
        取字段后再写，整对象交给序列化器会落成不可读的东西。

        Args:
            item: 工具事件

        Returns:
            字符串原样返回；`ToolMessage` 拆成
            `{tool_call_id, name, content}`；其他类型原样返回
        """
        output = (item.get("data") or {}).get("output")
        if output is None:
            return None
        if not isinstance(output, ToolMessage):
            return output
        name = output.name if output.name is not None else ""
        return {
            "tool_call_id": output.tool_call_id,
            "name": name,
            "content": output.content,
        }

    # ---------- 内部 ----------

    def _get_client(self) -> Any:
        """取 Langfuse 客户端单例（与 configure / flush 同源）。"""
        if self._client is None:
            self._client = langfuse_context.client_instance
        return self._client

    def _open_round(self) -> None:
        """开该轮的 `tools` 父 span（同一轮只开一次）。"""
        if self._round is not None:
            return
        try:
            self._round = self._get_client().span(
                trace_id=self._trace_id, name=_TOOLS_NODE, start_time=_now()
            )
        except Exception:  # noqa: BLE001 - 观测失败不得影响对话
            logger.warning("[tool_trace] open round span failed", exc_info=True)

    def _close_round(self) -> None:
        """关该轮父 span。"""
        span, self._round = self._round, None
        if span is not None:
            self._end_span(span)

    def _on_tool_start(self, item: dict) -> None:
        """开一条工具 span，按 run_id 记账。"""
        if self._round is not None:
            parent_id = self._round.id
        else:
            parent_id = None
        try:
            span = self._get_client().span(
                trace_id=self._trace_id,
                parent_observation_id=parent_id,
                name=str(item.get("name", "")),
                input=self._normalize_input(item),
                start_time=_now(),
            )
        except Exception:  # noqa: BLE001
            logger.warning("[tool_trace] open tool span failed", exc_info=True)
            return
        self._open[str(item.get("run_id", ""))] = span

    def _on_tool_end(self, item: dict) -> None:
        """按 run_id 取回 span 并写入返回值。"""
        span = self._open.pop(str(item.get("run_id", "")), None)
        if span is None:
            return
        self._end_span(span, output=self._normalize_output(item))

    def _on_tool_error(self, item: dict) -> None:
        """工具抛错：标记 ERROR 并写入错误信息（此路径没有 on_tool_end）。"""
        span = self._open.pop(str(item.get("run_id", "")), None)
        if span is None:
            return
        data = item.get("data") or {}
        self._end_span(
            span,
            output=data.get("input"),
            level="ERROR",
            status_message=str(data.get("error", "")),
        )

    def _end_span(
        self,
        span: Any,
        *,
        output: Any = None,
        level: str = "DEFAULT",
        status_message: str = "",
    ) -> None:
        """关一条 span；异常吞掉只记 warning。"""
        try:
            span.end(
                end_time=_now(),
                output=output,
                level=level,
                status_message=status_message,
            )
        except Exception:  # noqa: BLE001
            logger.warning("[tool_trace] end span failed", exc_info=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/infra/llm/test_tool_trace.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add src/infra/llm/tool_trace.py tests/infra/llm/test_tool_trace.py
git commit -m "feat(tracing): 新增 ToolTraceCollector（事件流驱动，run_id 配对 + 兜底关闭）"
```

---

### Task 4: 把采集器挂进 `_run_generation`

**Files:**
- Modify: `src/services/agent_service.py`（`_run_generation`：建采集器 → 循环内 `consume` → `finally` 里 `close`）
- Test: `tests/services/test_run_generation_tracing.py`

**Interfaces:**
- Consumes: `ToolTraceCollector(enabled, trace_id, client=None)` / `.consume(item)` / `.close()`（Task 3）
- Produces: 无对外新符号（仅内部接线）

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_run_generation_tracing.py`：

```python
async def _fake_astream_with_tool(*args, **kwargs):
    """最小事件源：一条 tools 父 span 事件 + 一次工具开始/结束。"""
    yield {
        "event": "on_chain_start",
        "name": "tools",
        "run_id": "rc",
        "metadata": {"langgraph_node": "tools"},
        "data": {},
    }
    yield {
        "event": "on_tool_start",
        "name": "retrieve_kb",
        "run_id": "r1",
        "metadata": {"langgraph_node": "tools"},
        "data": {"input": {"query": "q"}},
    }
    yield {
        "event": "on_tool_end",
        "name": "retrieve_kb",
        "run_id": "r1",
        "metadata": {"langgraph_node": "tools"},
        "data": {"output": "ok"},
    }


@pytest.mark.asyncio
async def test_run_generation_feeds_collector_and_closes(monkeypatch):
    """循环里每一项都要喂给采集器；收尾必须 close（取消/异常也走这里）。"""
    seen: list[dict] = []
    closed: list[bool] = []

    class _SpyCollector:
        def __init__(self, *, enabled, trace_id, client=None):
            self.enabled = enabled
            self.trace_id = trace_id

        def consume(self, item):
            seen.append(item)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(agent_service, "ToolTraceCollector", _SpyCollector)

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream_with_tool
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        langfuse_observation_id="trace_unit_tools",  # type: ignore[reportCallIssue]
    )

    assert [i["event"] for i in seen] == [
        "on_chain_start",
        "on_tool_start",
        "on_tool_end",
    ]
    assert closed == [True]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q`
Expected: FAIL —— `AttributeError: <module 'src.services.agent_service'> does not have the attribute 'ToolTraceCollector'`

- [ ] **Step 3: 接线**

在 `src/services/agent_service.py` 顶部 import 区加：

```python
from src.infra.llm.tool_trace import ToolTraceCollector
```

在 `_run_generation` 里、`capture = _StreamCapture()` 之后加：

```python
    tool_trace = ToolTraceCollector(
        enabled=settings.LANGFUSE_ENABLE,
        trace_id=current_trace_id.get() or "",
    )
```

在事件循环体内（`for event in _convert_event(item, capture):` 那个 for 之后、`if abort_signal is not None ...` 之前）加：

```python
            tool_trace.consume(item)
```

在 `finally:` 块里（`watch_task.cancel()` 一行之后）加：

```python
        # 取消 / 异常路径同样走到这里 —— 不关的 span 会永远悬空
        tool_trace.close()
```

> 若 `settings` / `current_trace_id` 尚未在本模块导入，按既有 import 补齐：
> `from src.config import settings`、`from src.infra.llm.trace_context import current_trace_id`。

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q`
Expected: PASS（既有 3 条 + 新增 1 条）

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_run_generation_tracing.py
git commit -m "feat(tracing): _run_generation 接入 ToolTraceCollector（循环喂 + finally 关闭）"
```

---

### Task 5: `_messages_payload` 的 role 规范化与工具信息

**Files:**
- Modify: `src/agents/graph/message_payload.py`
- Test: `tests/agents/graph/test_agent_node_tracing.py`

**Interfaces:**
- Produces: `_messages_payload(messages) -> list[dict[str, Any]]` —— 每项固定含 `role`/`content`；assistant 项在有工具调用时含 `tool_calls`（`[{id,name,args}]`）；tool 项在有 `name` 时含 `name`

- [ ] **Step 1: 改既有断言（先让它红）**

把 `tests/agents/graph/test_agent_node_tracing.py` 里的两个用例改成新契约：

```python
def _msg_payload_keys_are_whitelisted(payload: list[dict]) -> bool:
    """input 只允许出现 role / content / tool_calls / name 四个键。"""
    allowed = {"role", "content", "tool_calls", "name"}
    return all(set(item.keys()) <= allowed for item in payload)


def test_messages_payload_shape_normalizes_roles():
    """role 用 OpenAI 形态（human→user、ai→assistant），不是 LangChain 类型名。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    payload = _messages_payload(
        [SystemMessage(content="sys"), HumanMessage(content="hi")]
    )
    assert payload == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert _msg_payload_keys_are_whitelisted(payload)


def test_messages_payload_carries_tool_calls_and_tool_name():
    """assistant 条目补 tool_calls，tool 条目补 name —— 否则「模型要调什么」读不出。"""
    from langchain_core.messages import AIMessage, ToolMessage

    payload = _messages_payload(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "retrieve_kb", "args": {"query": "q"}, "id": "call_1"}
                ],
            ),
            ToolMessage(content="[1] 来源…", tool_call_id="call_1", name="retrieve_kb"),
        ]
    )
    assert payload[0]["role"] == "assistant"
    assert payload[0]["tool_calls"] == [
        {"id": "call_1", "name": "retrieve_kb", "args": {"query": "q"}}
    ]
    assert payload[1]["role"] == "tool"
    assert payload[1]["name"] == "retrieve_kb"
    assert _msg_payload_keys_are_whitelisted(payload)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py -q`
Expected: FAIL —— `assert {'role': 'human', ...} == {'role': 'user', ...}`

- [ ] **Step 3: 实现**

把 `src/agents/graph/message_payload.py` 改为：

```python
"""消息文本提取与 Langfuse 输入载荷构造（纯函数，无业务依赖）。

`_extract_text` / `_messages_payload` 原本内联在 `agent_node`，与其图节点逻辑
无耦合；抽为独立模块以守住单文件行数红线（CLAUDE.md：单文件 ≤ 400 行）。

载荷刻意用 **OpenAI 形态的 role**（而不是 LangChain 的 `m.type`）：`ai` / `human`
这类类型名在 Langfuse 的对话视图里读不出来，规范化后可直接按对话渲染。
"""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

#: LangChain 消息类型 → OpenAI 形态 role（未列出的原样透传）
_ROLE_MAP = {"ai": "assistant", "human": "user"}


def _extract_text(message: BaseMessage | None) -> str:
    """从 AIMessage 提取文本 content（str 或 content blocks）。

    Args:
        message: 消息对象，None 时返回空字符串

    Returns:
        content 的纯文本形式：str 直接返回；list 拼接 dict blocks 中 type=="text" 的 text；
        其他类型 str() 兜底
    """
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _messages_payload(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """把消息列表转成 Langfuse 输入载荷。

    Args:
        messages: LangChain 消息列表

    Returns:
        `[{role, content}(, tool_calls)(, name)]`；assistant 条目在发起工具调用时
        带 `tool_calls`，tool 条目在知道工具名时带 `name`。消息对象上还挂着 id /
        response_metadata 等字段，**不得整对象交给序列化器**。
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "role": _ROLE_MAP.get(message.type, message.type),
            "content": _extract_text(message),
        }
        if isinstance(message, AIMessage) and message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "args": call.get("args", {}),
                }
                for call in message.tool_calls
                if isinstance(call, dict)
            ]
        elif isinstance(message, ToolMessage) and message.name:
            item["name"] = message.name
        payload.append(item)
    return payload
```

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/message_payload.py tests/agents/graph/test_agent_node_tracing.py
git commit -m "feat(tracing): 载荷 role 规范化为 OpenAI 形态并补 tool_calls/工具名"
```

---

### Task 6: 工具轮 generation 的 `output` 不为空

**Files:**
- Modify: `src/agents/graph/agent_node.py`（`make_agent_model_node.agent_model` 的 `update_current_observation(output=…)`）
- Test: `tests/agents/graph/test_agent_node_tracing.py`

**Interfaces:**
- Consumes: 无
- Produces: generation 的 `output` 规则 —— 文本非空写文本；文本为空且有 `tool_calls` 时写 `{"tool_calls": [{id,name,args}]}`；两者皆无写 `""`

- [ ] **Step 1: 改既有断言（先让它红）**

把 `tests/agents/graph/test_agent_node_tracing.py` 里 `test_empty_text_output_does_not_fall_back_to_state_dict` 的两处断言改为：

```python
    assert explicit_updates, "应发生一次 generation 字段回填"
    assert explicit_updates[0]["output"] == {
        "tool_calls": [{"id": "call_1", "name": "search", "args": {"q": "x"}}]
    }
```

> 该用例的替身 `_EmptyTextToolCallChunk` 正是「只发 tool_calls、无文本」的一轮，
> 因此新契约下 output 应为该结构而不是空串。下面那段「回落不是 state dict」的断言保持不变。

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py::test_empty_text_output_does_not_fall_back_to_state_dict -q`
Expected: FAIL —— `assert '' == {'tool_calls': [...]}`

- [ ] **Step 3: 实现**

在 `src/agents/graph/agent_node.py` 的 `agent_model` 内，把当前的

```python
        # generation 字段回填（D8：input 必须显式写，不能靠自动捕获）
        langfuse_context.update_current_observation(
            model=model_name,
            input=_messages_payload(messages),
            output=_extract_text(result),
```

改为先算 output 再回填：

```python
        # 输出规则（文本优先）：文本非空时写文本；只有 tool_calls 的一轮写该结构，
        # 否则工具轮的 generation 会是空白（既有 trace 里正是如此）。
        text = _extract_text(result)
        tool_calls = [
            {
                "id": call.get("id", ""),
                "name": call.get("name", ""),
                "args": call.get("args", {}),
            }
            for call in (result.tool_calls or [])
            if isinstance(call, dict)
        ]
        if text:
            observation_output: Any = text
        elif tool_calls:
            observation_output = {"tool_calls": tool_calls}
        else:
            observation_output = ""

        # generation 字段回填（D8：input 必须显式写，不能靠自动捕获）
        langfuse_context.update_current_observation(
            model=model_name,
            input=_messages_payload(messages),
            output=observation_output,
```

并在 `agent_node.py` 顶部 import 区确认有 `from typing import Any`（若无则加）。

> 同文件下方的 `estimate_usage(messages, _extract_text(result))` 调用保持不变 —— 它用的是返回值，不是新的 `text` 局部量；为了最小改动不要顺手替换。

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_agent_node_tracing.py
git commit -m "feat(tracing): 工具轮 generation 的 output 写入 tool_calls（不再空白）"
```

---

### Task 7: trace 富化（`user_id` 显式传递 + `tags` / `metadata`）

**Files:**
- Modify: `src/services/agent_service.py`（`_run_generation` 签名与 `update_current_trace`）
- Modify: `src/api/chat.py`（请求内捕获 `user_id`、显式传参、订正误注释）
- Test: `tests/services/test_run_generation_tracing.py`

**Interfaces:**
- Consumes: `RequestContext`（`agent` / `agent_display_name` / `loaded_skills` / `skill_action` / `kb_id` / `kb_domain` / `deep_thinking` / `has_skills`）
- Produces: `_run_generation(..., user_id: str = "")` —— 新增可选参数；trace 上写入 `user_id`（空串转 `None`）、`tags`、`metadata`

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_run_generation_tracing.py`：

```python
@pytest.mark.asyncio
async def test_run_generation_writes_user_tags_metadata(monkeypatch):
    """trace 级写入 user_id / 低基数 tags / 业务 metadata；空 user_id 不得写成空串。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1", kb_id="kb1", kb_domain="finance")
    ctx.agent = "financial-analyst"
    ctx.agent_display_name = "财务专家"
    ctx.loaded_skills = ["finance-qa"]
    ctx.skill_action = "inline"
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "kb1",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        user_id="u-42",
        langfuse_observation_id="trace_unit_enrich",  # type: ignore[reportCallIssue]
    )

    assert captured["user_id"] == "u-42"
    assert captured["tags"] == ["chat", "kb"]
    metadata = captured["metadata"]
    assert metadata["agent"] == "financial-analyst"
    assert metadata["agent_display_name"] == "财务专家"
    assert metadata["kb_id"] == "kb1"
    assert metadata["kb_domain"] == "finance"
    assert metadata["skill_action"] == "inline"
    assert metadata["loaded_skills"] == ["finance-qa"]
    # 高基数取值不得进 tags
    assert "kb1" not in captured["tags"]
    assert "financial-analyst" not in captured["tags"]


@pytest.mark.asyncio
async def test_blank_user_id_is_not_written_as_empty_string(monkeypatch):
    """未登录时 current_user_id 是空串；必须转 None，否则空串会被写进 trace。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            pass

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
    ctx = RequestContext(session_id="s1")
    ctx.clarify_channel = asyncio.Queue()

    await _run_generation(
        "s1",
        "",
        "q",
        [],
        False,
        ctx,
        mgr,
        graph=fake_graph,
        user_id="",
        langfuse_observation_id="trace_unit_nouser",  # type: ignore[reportCallIssue]
    )

    assert captured["user_id"] is None
    assert captured["tags"] == ["chat", "no_kb"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q`
Expected: FAIL —— `TypeError: _run_generation() got an unexpected keyword argument 'user_id'`

- [ ] **Step 3: 实现**

`src/services/agent_service.py` —— `_run_generation` 签名在 `direct_skill: str = ""` 之后加一行：

```python
    user_id: str = "",
```

并在 docstring 的 Args 段补：

```
        user_id: 触发本轮的用户标识（请求内捕获后显式传入）。空串表示未取到，
            写入 trace 前会转成 None —— `update_current_trace` 只过滤 None、不过滤空串
```

把现有的

```python
    trace_metadata: dict = {}
    if direct_skill:
        trace_metadata["direct_skill"] = direct_skill
    langfuse_context.update_current_trace(
        input={"query": query, "kb_id": kb_id, "deep_thinking": deep_thinking},
        session_id=session_id,
        metadata=trace_metadata,
    )
```

改为：

```python
    # trace 级字段：只读 RequestContext，不新增取数
    if kb_id:
        kb_tag = "kb"
    else:
        kb_tag = "no_kb"
    trace_metadata: dict = {
        "agent": ctx.agent,
        "agent_display_name": ctx.agent_display_name,
        "kb_id": kb_id,
        "kb_domain": ctx.kb_domain,
        "deep_thinking": deep_thinking,
        "skill_action": ctx.skill_action,
        "loaded_skills": list(ctx.loaded_skills),
    }
    if direct_skill:
        trace_metadata["direct_skill"] = direct_skill
    langfuse_context.update_current_trace(
        input={"query": query, "kb_id": kb_id, "deep_thinking": deep_thinking},
        session_id=session_id,
        # tags 只能加不能删（实测），故只放低基数稳定值；高基数一律进 metadata
        tags=["chat", kb_tag],
        # 空串必须转 None：SDK 的字段过滤是 `v is not None`
        user_id=user_id or None,
        metadata=trace_metadata,
    )
```

`src/api/chat.py` —— 把 `answer_builder` 之上那条误注释与调用改掉：

```python
    # user_id 与 trace_id 同做法：在请求内捕获后显式传入。**不要**依赖 contextvar
    # 继承 —— create_task 虽会复制上下文，但原注释「任务与请求不共享 context」是错的，
    # 一旦有人按它去「修正」或调整中间件顺序，user_id 会静默变空。
    user_id = current_user_id.get()

    async def answer_builder() -> str:
        # 根 observation 的 id 即 trace id；任务入口已 set 过 current_trace_id，
        # 此处按调用时读取（任务与请求不共享 context，必须显式传）
        return await _run_generation(
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            launch_ctx["query"],
            launch_ctx["history"],
            launch_ctx["deep_thinking"],
            ctx,
            streaming_manager,
            graph=launch_ctx["graph"],
            partial_holder=partial_holder,
            abort_signal=abort_signal,
            direct_skill=launch_ctx["direct_skill"],
            user_id=user_id,
            langfuse_observation_id=current_trace_id.get() or "",  # type: ignore[reportCallIssue]
        )
```

并在 `src/api/chat.py` 的 import 补 `current_user_id`：

```python
from src.infra.llm.trace_context import (
    current_session_id,
    current_trace_id,
    current_user_id,
)
```

> 同文件第 42-49 行那段说明「任务与请求不共享 context」的模块级 docstring 也要一并订正为：
> 「任务的 contextvar 由 `asyncio.create_task` 复制而来；本模块仍**显式**传递 trace_id / user_id，
> 是为了不依赖该复制行为（避免中间件顺序变化导致的静默丢失）。」

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q`
Expected: PASS（既有 + 新增 2 条）

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/api/chat.py tests/services/test_run_generation_tracing.py
git commit -m "feat(tracing): trace 写入 user_id/低基数 tags/业务 metadata（user_id 显式传递）"
```

---

### Task 8: ADR 与文档落点

**Files:**
- Create: `docs/adr/0015-langfuse-tool-spans-imperative-api.md`
- Modify: `docs/adr/README.md`（索引表加一行）
- Modify: `docs/agents/code-map.md`（落点速查加两行）
- Modify: `docs/agents/cookbook.md`（新增「seed 模型定价」一节）

**Interfaces:**
- Consumes: 无（纯文档）
- Produces: ADR-0015（后续变更引用它的「命令式接入 + client 实例口径」）

- [ ] **Step 1: 写 ADR-0015**

新建 `docs/adr/0015-langfuse-tool-spans-imperative-api.md`，头部字段与既有 ADR 同格式：

```markdown
# ADR-0015：工具 span 用命令式 Langfuse client 创建，client 实例统一取 decorators 单例

- **Status**：Accepted
- **Date**：2026-09-24
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **关系**：为 ADR-0012 / ADR-0013 所依附的 `llm-tracing` 能力**扩展观测面**；与既有「纯装饰器」接入方式并存，不取代任何在先 ADR。

## 背景与问题

`llm-tracing` 原定「只用 `@observe` 装饰器接入」。要给**工具调用**建 span 时该路线不够用，实测结论：

| 事实 | 影响 |
|---|---|
| `@observe` 的 `as_type` 只接受 `"generation"`；span 是「有 parent 时的默认类型」 | 无法显式声明 span |
| 没有 `start_as_current_span` 之类的上下文管理器 | **拿不到 start/end 分离的 span** |
| 顶层（无 parent）的 `@observe()`（非 generation）会走 `client.trace(**params)` | 在图节点外这样用会 upsert / 改写 trace |

而工具事件是**成对到达**的（`on_tool_start` / `on_tool_end`），必须在两个事件之间持有同一条 span。

## 决策

1. **工具 span 用命令式 API 创建**：`langfuse_context.client_instance.span(trace_id=…, parent_observation_id=…, …)` → `StatefulSpanClient.end(…)`。
2. **client 实例统一取 `langfuse_context.client_instance`**，**禁止** `new Langfuse()` —— 后者不共享 `LangfuseSingleton`，会绕过 `LANGFUSE_ENABLE` 开关、也不被关停时的 `flush_tracing()` 覆盖（后果：禁用态仍可能出网、关停丢 span）。
3. **不调用 `client.trace(...)`**：SDK 会随请求带上 `timestamp = now`，服务端对该列**无条件覆盖**；改用 `Langfuse.span(trace_id=…)` 后，工具 span 路径**不触碰 trace 行**。
   - ⚠️ 这不等于「trace 的 timestamp 漂移被根除」：`update_current_trace(...)`（既有 generation 路径，每轮一次）**内部就走 `client_instance.trace(id=…)`**，仍在刷新该列 —— 那是**既有行为**，本 ADR 不改变。

## 备选与否决理由

| 备选 | 否决理由 |
|---|---|
| 逐工具加 `@observe` 装饰器 | 要为每个工具单独埋点；MCP 适配器产出的工具**加不上**；且工具在 trace 外被调用会产生游离 trace |
| 在 `tools` 节点函数上包一层 `@observe` | 粒度退化为「一轮一条」，丢掉逐工具耗时与并行区分 |
| `langfuse.callback.CallbackHandler` | 已实测不可用（SDK 2.60.10 依赖 langchain 1.x 已删除的模块，import 即失败） |

## 后果

**正面**：工具调用可点、可读、有耗时；观测代码对工具实现无感 —— 新增工具（含 MCP）经统一入口进 ToolNode 即自动覆盖，不需要改观测代码。

**负面 / 接受的代价**：同一份 trace 上出现两种接入方式（装饰器 + 命令式），需要一份口径说明（本条）与统一的 flush / 禁用语义。

**不解决的问题**：
- fork 子代理的工具调用（其回调传播被 `src/agents/skills/executor.py` 的 `var_child_runnable_config.set(None)` 显式切断，事件不进主图流）
- 工具**内部**子步骤（embed / 检索 / rerank）—— 它们不是 LangChain Runnable，事件流里没有它们的事件
- trace 的 `timestamp` 仍会被 `update_current_trace` 每轮刷新（既有行为）

## 复查触发条件

- Langfuse 升级到 v3+（届时命令式 API 与装饰器 API 都可能变）
- 需要覆盖 fork 子代理的工具观测（届时要在子代理事件流上再挂一个消费者，并单独裁定其 trace 归属）
- `langfuse` SDK 版本变更导致 `Langfuse.span(trace_id=…)` 签名或语义变化
```

- [ ] **Step 2: 在 ADR 索引表登记**

在 `docs/adr/README.md` 的索引表 `| [0013](...) | ... |` 行之后追加：

```markdown
| [0014](0015-langfuse-tool-spans-imperative-api.md) | 工具 span 用命令式 Langfuse client 创建（client 实例取 decorators 单例） | Accepted | 为 `llm-tracing` 扩展观测面；与「纯装饰器」并存，不取代任何在先 ADR |
```

- [ ] **Step 3: `code-map.md` 加落点**

在 `docs/agents/code-map.md` 的「常见改动落点速查」表里，紧随「改一次生成的编排 / 事件转换」那一行之后插入：

```markdown
| 改工具观测（工具 span 的字段 / 归组 / 过滤） | `src/infra/llm/tool_trace.py`（`ToolTraceCollector`）；消费点在 `src/services/agent_service.py::_run_generation` 的事件循环。口径见 ADR-0015 |
| 改模型定价 / 让 Langfuse 出成本 | `src/config/settings.py`（`MODEL_*_PRICE_PER_TOKEN`，USD/单 token）+ `src/cli/seed_langfuse_models.py`；操作步骤见 `cookbook.md` |
```

- [ ] **Step 4: `cookbook.md` 加一节**

在 `docs/agents/cookbook.md` 的末尾（`## 登记` 之前）加：

```markdown
### Langfuse 模型定价：让成本算出来

**场景**：Langfuse 里 trace 有 token 数但没有成本（`calculated_total_cost` 为空）—— 因为实际使用的模型没有模型定义。

**步骤**：
1. 在 `.env` 填单价，**单位是 USD / 单个 token**（例：$3 per 1M tokens 填 `0.000003`）：
   `MODEL_INPUT_PRICE_PER_TOKEN=0.000003`、`MODEL_OUTPUT_PRICE_PER_TOKEN=0.000006`。保持 `0` 表示未配置。
2. **先比对运行时模型名与配置名**（pattern 锚定的是运行时 provider 名，不是配置值）：
   `docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc "SELECT DISTINCT model FROM observations WHERE type='GENERATION'"`
   与 `src/config/settings.py` 的 `LLM_MODEL`（或 `.env` 覆盖值）比对，**不等则先改配置**，不要放宽 pattern（放宽会误配到形近模型）。
3. 预演：`docker exec corporate-rag-app python -m src.cli.seed_langfuse_models --dry-run`
4. 写入：`docker exec corporate-rag-app python -m src.cli.seed_langfuse_models`

**验证**：`docker exec corporate-rag-postgres psql -U langfuse -d langfuse -c "SELECT model_name, input_price, output_price, unit FROM models WHERE model_name LIKE 'qwen%'"` 有一行；随后发一轮对话，该 generation 的 `calculated_total_cost > 0`。

**注意事项**：
- **重复执行安全**：同名模型已存在则跳过（幂等判据只看 `model_name`）。
- **单价填成「每 1M」会让成本差 1e6 倍**，而"成本 > 0"的检查照样通过 —— 务必按 Step 1 的口径填。
- Langfuse v2 的 `LANGFUSE_INIT_*` **不能播种模型定价**，只能在库里建（故有这条 CLI）。
```

- [ ] **Step 5: 提交**

```bash
git add docs/adr/0015-langfuse-tool-spans-imperative-api.md docs/adr/README.md docs/agents/code-map.md docs/agents/cookbook.md
git commit -m "docs(adr): ADR-0015 命令式接入取舍；code-map/cookbook 补落点与 seed 步骤"
```

---

### Task 9: 验证闸门与 dev E2E

**Files:**
- 无新增；只跑闸门与实测

**Interfaces:**
- Consumes: Task 1–8 的全部产出
- Produces: 验收证据（写进 change 的 `tasks.md` 与提交信息）

- [ ] **Step 1: 质量门禁**

```bash
POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q > /tmp/lte-pytest.log 2>&1; echo RC=$?
.venv/bin/ruff check . > /tmp/lte-ruff.log 2>&1; echo RC=$?
.venv/bin/ruff format --check src/ tests/ > /tmp/lte-ruff-fmt.log 2>&1; echo RC=$?
.venv/bin/pyright src/ > /tmp/lte-pyright.log 2>&1; echo RC=$?
```
Expected: 全 `RC=0`；`pyright` 不引入新 error（存量第三方误报不算）。

- [ ] **Step 2: 关闭态回归**

```bash
docker compose up -d --force-recreate app
```
把 `.env` 的 `LANGFUSE_ENABLE` 置 `false` 后重创 app，发一轮对话，然后核对 Langfuse 零新增：

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc "SELECT count(*) FROM traces"
```
Expected: 计数与接线前一致；SSE 事件序列（status → token → model_info → agent_used → done）与开启态一致。

- [ ] **Step 3: 开启态 dev E2E**

`LANGFUSE_ENABLE=true`、重创 app，发一轮**会触发检索**的对话（≥3 次迭代、≥2 次工具调用），记下响应头 `X-Trace-ID`，然后：

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -c "SELECT name, type, parent_observation_id, left(input::text,80), left(output::text,80) FROM observations WHERE trace_id='<X-Trace-ID>' ORDER BY start_time"
```
Expected：
- `tools` 类型 SPAN 的数量 **== 实际工具调用次数**（不是 0 —— 判据写错时这里会是 0）
- 每条工具 SPAN 的 `parent_observation_id` 等于同轮 `tools` 父 span 的 id
- 工具轮 `agent_turn` 的 `output` **非空**（含 `tool_calls`）
- 所有 observation 的 `trace_id` 均等于 `<X-Trace-ID>`

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -c "SELECT user_id, tags, metadata FROM traces WHERE id='<X-Trace-ID>'"
```
Expected：`user_id` 非空（或 NULL 而非空串）；`tags` 含 `chat` 与 `kb`/`no_kb`；`metadata` 含 agent / kb_id 等。

- [ ] **Step 4: 取消路径 E2E**

发一轮长回答，在工具执行期间点「停止生成」，然后查该 trace 的 observation：

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -c "SELECT name, end_time FROM observations WHERE trace_id='<X-Trace-ID>'"
```
Expected：**没有 `end_time IS NULL` 的行**（无悬空 span）。

- [ ] **Step 5: 成本闸门**

按 `cookbook.md` 的步骤填单价 + 比对模型名 + seed，然后发一轮对话并查：

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -c "SELECT model, prompt_tokens, completion_tokens, calculated_total_cost FROM observations WHERE trace_id='<新 trace id>'"
```
Expected：`calculated_total_cost > 0`，且与「已知用量 × 已知单价」手算一致（防 1e6 量纲错误）。

- [ ] **Step 6: openspec 校验并提交验收记录**

```bash
openspec validate --changes langfuse-trace-enrichment
```
Expected: `6 passed / 0 failed`。随后把本任务 5 条实测结论（命令 + 输出摘要）登记进 change 的 `tasks.md` §验证，并提交：

```bash
git add docs/openspec/changes/langfuse-trace-enrichment/tasks.md
git commit -m "test(tracing): 登记 dev E2E 验收证据（工具 span / 取消无悬空 / 成本非零）"
```

---

## Self-Review

**Spec coverage（逐条对齐 `specs/llm-tracing/spec.md`）**

| 需求 / Scenario | 落点 |
|---|---|
| 工具调用记为 span · 单次字段 | Task 3 Step 3（`_on_tool_start`/`_on_tool_end` 写 name/input/output/起止） |
| · 同轮多次归入父 span | Task 3（`_open_round` + `parent_observation_id`）+ 测试 `test_parallel_tools_share_one_parent` |
| · 工具执行失败 | Task 3 `_on_tool_error`（level=ERROR + status_message） |
| · 取消时 span 不悬空 | Task 3 `close()` + Task 4 `finally` |
| · 新工具零改动获得观测 | Task 3 不变量 1（不按工具名分支、output 原样透传） |
| trace 用户/标签/元数据 · 三 Scenario | Task 7（`user_id or None` / `tags=["chat", kb_tag]` / metadata 只读 ctx） |
| 模型成本可见 · 三 Scenario | Task 1（默认 0）+ Task 2（0 跳过 / 幂等）+ Task 9 Step 5（非零闸门） |
| 主 agent 每轮记为 generation · 工具轮输出不为空 | Task 6 |
| · 输入消息角色与工具调用可读 | Task 5 |
| trace 内容记录范围 · 工具入参返回可回放 | Task 3（output 拆 ToolMessage）|
| · 内部运行时对象不进 trace | Task 5 的键白名单断言 + 既有 `capture_input=False`（Task 5 Step 1 测试锁住） |
| · 记录范围有文档说明 | Task 8（ADR-0015 的「不解决的问题」+ cookbook） |

无缺口。

**Placeholder scan**：全篇无 TODO/TBD；每个代码步骤都给了可直接粘贴的实现或测试；文档步骤给了完整新增内容。

**Type consistency**：
- `ToolTraceCollector(enabled, trace_id, client=None)` 在 Task 3 定义、Task 4 以关键字调用（`enabled=` / `trace_id=`）—— 一致。
- `_messages_payload` 返回类型从 `list[dict[str, str]]` 放宽为 `list[dict[str, Any]]`（Task 5）—— 只在本模块与 `agent_node` 使用，无其他调用方需改。
- `_run_generation(..., user_id: str = "")`（Task 7）为**带默认值的新增参数**，既有唯一调用点 `chat.py` 与全部测试调用均不需要改签名。
- `build_model_request(model_name, input_price, output_price) -> dict` 在 Task 2 内部与测试中一致使用。
