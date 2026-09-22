# langfuse-trace-wiring 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Langfuse v2 的 `@observe` 接进对话链路与 CLI 评测链路 —— 一轮生成产出一条 id 与请求 `trace_id` 逐字一致的 trace、主 agent 每次推理一条 generation，并自建 30 天保留期清理。

**Architecture:** 用 SDK 原语（`langfuse.decorators.observe` + `langfuse_context`）替代自研 `LangfuseTracer` 封装。trace 根落在 `agent_service._run_generation`（生产侧唯一调用点，且本身在后台 asyncio task 内），CLI 侧在 `eval_ragas` 的每问循环内另开一条根。新增一个薄基础设施模块 `src/infra/llm/tracing.py` 承载「开关 / flush / trace id 校验」三件事，作为唯一入口。

**Tech Stack:** Python 3.11+ · `langfuse==2.60.10`（v2 SDK，配 v2.95.11 服务端）· LangGraph 1.2.9 · `langchain==1.3.11` · pytest · ruff · pyright

**Spec:** `docs/openspec/changes/langfuse-trace-wiring/` —— `proposal.md`、`design.md`（决策 D1–D20）、`specs/llm-tracing/spec.md`（6 requirement / 26 scenario）、`specs/token-usage-model/spec.md`（delta）、`tasks.md`（DoD 与 §6/§7 处置记录）。**执行时 design 与 specs 一并读** —— 本计划的每一步都从它们推出，不在计划里复述理由。

## Global Constraints

以下为 spec 的项目级要求，**每个任务隐含包含**：

- **保留期 30 天**；命令行可覆盖；**下界 1 天**（低于即拒绝）；**单次删除上限 1000 条**（超过即中止且不删任何数据）；非 dry-run **必须** `--yes`（不用交互输入）
- **trace id 白名单**：`^[A-Za-z0-9_-]{1,120}$`（最终字符集须在 Task 12 与服务端实际接受范围对齐后再钉死）；不合法 → 静默丢弃并服务端重生成，**不返回 400**
- **`LANGFUSE_ENABLE` 默认 `true`**；关闭时**不产出任何 trace** 且**对话不受影响**；后端不可达时同理（故障只进日志）
- **`@observe` 必须 `capture_input=False`**，输入只经 `update_current_observation(input=...)` / `update_current_trace(input=...)` 显式写入 —— 禁止把 `ctx` / `manager` / `graph` / 事件与队列等内部对象带进 trace
- **trace 根必须在承载生成的那个 asyncio task 内**；SSE 订阅与推送侧不单独产生 trace
- **新增 trace 根的前提**：处于有 per-request id 的上下文（HTTP 中间件或 CLI 循环）。`src/core/logging.py:102` 的进程级默认 id **不是**合法的根 id
- **测试 SHALL NOT 发起真实网络**；`pytest` 全绿**必须在 tracing 被全局关停的前提下**取得
- **日志**：事件消息英文 `k=v` + `[层名]` 前缀；中文仅限用户可见文案
- **常量集中**：新增阈值/开关入 `src/config/`；事件入 `src/core/log_events.py` + `src/core/log_event_specs.py`（**两处同名登记**）
- **代码风格**：不用三元表达式（写完整 `if/else`）；所有函数写 docstring；dataclass 每个字段行内注释；不确定类型的值用显式判断而非 `getattr(x, "a", d)`
- **文件 ≤ 400 行、函数 ≤ 80 行**
- **部署形态**：单 worker，流式状态在进程内

---

## File Structure

**新建**

| 文件 | 职责 |
|---|---|
| `src/infra/llm/tracing.py` | 接线基础设施唯一入口：`configure_tracing()` / `flush_tracing()` / `is_valid_trace_id()` / `new_trace_id()` / `TRACE_ID_PATTERN` |
| `src/cli/purge_langfuse_traces.py` | trace 保留期清理 CLI（dry-run + 五项护栏 + 审计） |
| `tests/infra/llm/test_tracing.py` | 上述模块的单测（不发网络） |
| `tests/cli/test_purge_langfuse_traces.py` | 清理 CLI 的护栏单测（全部用替身 client） |

**修改**

| 文件 | 改什么 |
|---|---|
| `tests/conftest.py` | import 期关停 tracing + session 级 autouse fixture 兜底 |
| `src/middleware/trace_id.py` | 入站 id 白名单（**必须在 `set()` 之前**） |
| `src/services/agent_service.py` | `_run_generation` 加 `@observe` 根 + `update_current_trace` |
| `src/api/chat.py` | `answer_builder` 传 `langfuse_observation_id` |
| `src/agents/graph/agent_node.py` | `agent_model` 加 `@observe(as_type="generation")` + 字段回填 |
| `src/cli/eval_ragas.py` | 抽「处理单问」为被装饰的函数 + `configure_tracing` + `flush_tracing` |
| `src/main.py` | lifespan 接 `configure_tracing` / `flush_tracing` |
| `src/config/settings.py` | `LANGFUSE_*` 内置默认值清理（key 改空串、HOST 对齐） |
| `.env.template` / `.env.example` | `LANGFUSE_ENABLE=true` 等与 settings 对齐 |
| `src/rag/stream.py` | **删除**（`estimate_usage` 迁走、`stream_answer` 死代码） |
| `src/infra/llm/token_usage.py` | 接收迁移过来的 `estimate_usage` |
| `src/infra/llm/trace_context.py` | 删 `current_tracer` |
| `src/infra/llm/langfuse_tracing.py` | **删除**（`LangfuseTracer` / `@traced`） |
| `src/core/log_events.py` + `log_event_specs.py` | 删三个 `TRACE_*` 事件 |
| 文档 6 处 + `docs/adr/` | 见 Task 10 / 11 |

**任务顺序的两个判断（已按讨论调整）**

1. **Task 1 必须在最前**：Task 4 一接线，`tests/services/` 那批用例就会构造真实客户端发网络 —— 基线不可信之前，后面所有"绿"都没有意义。
2. **"取消后 trace 仍在"与"嵌套自动成立"折进 Task 4 / Task 5 的验收步骤**，不拖到收尾 —— 它们正是 `@observe` 行为契约的一部分，晚发现会让 Task 4/5 返工。只把「id 字符集需服务端」与「后端不可达需停容器」留在 Task 12。

---

## Task 1: 测试基线 —— 全局关停 tracing

**Files:**
- Modify: `tests/conftest.py`（文件头 + 新增 autouse fixture）
- Delete: `tests/infra/llm/test_langfuse.py`
- Create: `tests/config/test_langfuse_disabled.py`

**Interfaces:**
- Produces: 全局测试约束「tracing 在测试进程中恒为关停」—— 后续所有任务据此才有资格声称 `pytest` 全绿

- [ ] **Step 1: 先复现问题（不依赖 `.env`）**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag-langfuse-trace
mv .env .env.bak && .venv/bin/python -m pytest tests/infra/llm/test_langfuse.py -q 2>&1 | tail -5; mv .env.bak .env
```

Expected: **FAIL 或尝试连 `http://langfuse:3000`** —— 证明该文件的 skip 条件（`not LANGFUSE_ENABLE`）在无 `.env` 时失效。记下实际输出，Task 1 完成后要复现"绿"。

- [ ] **Step 2: 删除已失效的测试文件**

```bash
git rm tests/infra/llm/test_langfuse.py
```

理由（写进提交信息）：它的被测对象 `LangfuseTracer` 将在 Task 9 删除，且它断言了当前类**不存在**的 `tracer._initialized` —— 已经坏了，只是被 `.env` 的 `LANGFUSE_ENABLE=false` 掩盖。替代覆盖在 Task 2 的 `tests/infra/llm/test_tracing.py`。

- [ ] **Step 3: 写失败测试（断言关停生效）**

`tests/config/test_langfuse_disabled.py`：

```python
"""断言测试进程内 tracing 被全局关停（D14）。

为什么需要它：`settings.py` 的 `LANGFUSE_*` 内置默认值指向真实 host 与 key，
一旦接线，任何走 `_run_generation` / `agent_model` 的用例都会构造真实客户端并
上报 —— 违反「测试 mock 外部依赖，不发起真实网络调用」。本文件把"关停已生效"
钉成可回归的契约。
"""

from src.config import settings


def test_langfuse_disabled_in_tests():
    """测试进程内 LANGFUSE_ENABLE 必须为 False。"""
    assert settings.LANGFUSE_ENABLE is False


def test_langfuse_context_disabled_in_tests():
    """langfuse SDK 的装饰器上下文也必须处于 disabled。"""
    from langfuse.decorators import langfuse_context

    client = langfuse_context.client_instance
    assert client is not None
    assert client.enabled is False
```

- [ ] **Step 4: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/config/test_langfuse_disabled.py -q
```

Expected: **FAIL**（`assert True is False`，因为当前无 `.env` 时默认 `true`）

- [ ] **Step 5: 实现 —— 改 `tests/conftest.py`**

文件**最顶部**（必须早于任何 `src.*` 导入，因为 `settings.py` 在导入时读环境变量）：

```python
"""测试共享 fixture 和配置。

提供：
  - AppService / VectorStore 实例
  - 测试知识库生命周期（创建 / 销毁）
  - 测试文档路径和数据库验证辅助函数

并且在导入任何业务模块之前**关停 Langfuse tracing**（见下）。
"""

from __future__ import annotations

import os

# ⚠️ 必须位于所有 src.* 导入之前：src/config/settings.py 在**导入时**读取环境变量，
# 此后再设等于无效。没有这一行，测试会构造真实 Langfuse 客户端并向外上报。
os.environ["LANGFUSE_ENABLE"] = "false"

import asyncio  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Generator  # noqa: E402
```

并在文件内合适位置（现有 fixture 之后）追加兜底 fixture：

```python
@pytest.fixture(scope="session", autouse=True)
def _disable_langfuse_tracing() -> Generator[None, None, None]:
    """兜底关停 tracing，并保证退出前把 SDK 缓冲清掉。

    上面那行环境变量是主手段（须早于导入）；本 fixture 是第二道保险 ——
    防止将来有人重构 conftest 的导入顺序时静默失效。
    """
    from langfuse.decorators import langfuse_context

    from src.config import settings

    settings.LANGFUSE_ENABLE = False
    langfuse_context.configure(enabled=False)
    yield
    langfuse_context.flush()
```

- [ ] **Step 6: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/config/test_langfuse_disabled.py -q
```

Expected: **2 passed**

- [ ] **Step 7: 全量回归（先确认基线绿）**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 全绿（DB 相关用例若因环境不可用而报错，单独记下并在 Task 13 处理，**不要**为此放宽断言）

- [ ] **Step 8: 提交**

```bash
git add tests/conftest.py tests/config/test_langfuse_disabled.py
git commit -m "test(langfuse): 全局关停 tracing 并删除失效的 test_langfuse"
```

---

## Task 2: 接线基础设施模块 `src/infra/llm/tracing.py`

**Files:**
- Create: `src/infra/llm/tracing.py`
- Test: `tests/infra/llm/test_tracing.py`

**Interfaces:**
- Produces:
  - `TRACE_ID_PATTERN: re.Pattern[str]`
  - `is_valid_trace_id(value: str) -> bool`
  - `new_trace_id() -> str`（返回 `trace_<uuid4>`）
  - `configure_tracing() -> None`（读 `settings.LANGFUSE_ENABLE`，调 `langfuse_context.configure(enabled=...)`）
  - `flush_tracing() -> None`
- Consumes: 无

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/test_tracing.py`：

```python
"""接线基础设施单测 —— 不发网络。"""

import uuid

from src.infra.llm import tracing


def test_new_trace_id_shape():
    """生成的 id 形如 trace_<uuid4>。"""
    tid = tracing.new_trace_id()
    assert tid.startswith("trace_")
    uuid.UUID(tid.removeprefix("trace_"))


def test_valid_trace_id_accepts_normal_values():
    """常规值（含 CLI 的 eval_<hex>）判为合法。"""
    assert tracing.is_valid_trace_id("trace_1234-abcd_EF")
    assert tracing.is_valid_trace_id("eval_a1b2c3d4e5f6")
    assert tracing.is_valid_trace_id("a")


def test_valid_trace_id_rejects_illegal_values():
    """空串、空白、超长、带非法字符一律拒绝。"""
    assert not tracing.is_valid_trace_id("")
    assert not tracing.is_valid_trace_id("has space")
    assert not tracing.is_valid_trace_id("has/slash")
    assert not tracing.is_valid_trace_id("x" * 121)
    assert tracing.is_valid_trace_id("x" * 120)


def test_configure_tracing_follows_settings(monkeypatch):
    """开关取值来自 settings（调用时读取，而非导入时冻结）。"""
    from langfuse.decorators import langfuse_context

    from src.config import settings

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", False)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is False

    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", True)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is True

    # 收尾：恢复测试期的关停状态
    monkeypatch.setattr(settings, "LANGFUSE_ENABLE", False)
    tracing.configure_tracing()
    assert langfuse_context.client_instance.enabled is False
```

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/infra/llm/test_tracing.py -q
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'src.infra.llm.tracing'`

- [ ] **Step 3: 实现**

`src/infra/llm/tracing.py`：

```python
"""Langfuse 接线基础设施 —— 开关、flush 与 trace id 校验的唯一入口。

本模块只做三件事，不含任何业务语义：

1. **开关**：把 `settings.LANGFUSE_ENABLE` 翻译成 SDK 的启用状态。服务侧由
   `src/main.py` 的 lifespan 调用，CLI 侧由 `src/cli/eval_ragas.py` 自行调用
   —— CLI 不经 lifespan，漏掉它会让 CLI 完全脱离开关控制。
2. **flush**：SDK 默认批量上报，进程退出前不 flush 会丢最后一批缓冲事件。
3. **trace id 校验**：入站 `X-Trace-ID` / `?trace_id` 是不可信输入，接线后它会
   成为 Langfuse 的 trace 主键（`client.trace(id=...)` 是 upsert）。不合法必须
   拦在写入之前 —— 客户端对 id 零校验，服务端拒绝时异常会被 SDK 吞掉只记日志，
   表现为 trace 静默消失。
"""

import re
import uuid
from typing import Final

from langfuse.decorators import langfuse_context

from src.config import settings

#: 入站 trace id 的白名单。最终字符集须与服务端实际接受范围对齐后钉死。
TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


def is_valid_trace_id(value: str) -> bool:
    """判断入站 trace id 是否合法。

    Args:
        value: 来自请求头 / 查询参数的原始值

    Returns:
        True 表示可原样使用；False 表示须丢弃并服务端重生成
    """
    return TRACE_ID_PATTERN.fullmatch(value) is not None


def new_trace_id() -> str:
    """生成服务端 trace id。

    Returns:
        形如 `trace_<uuid4>` 的标识，与响应头 / 日志 / SSE 三处共用
    """
    return f"trace_{uuid.uuid4()}"


def configure_tracing() -> None:
    """按当前开关状态配置 SDK（幂等）。

    开关在**调用时**从 `settings` 读取，不在导入时冻结 —— 否则测试无法
    monkeypatch、CLI 与服务也无法各自决定。
    """
    langfuse_context.configure(enabled=settings.LANGFUSE_ENABLE)


def flush_tracing() -> None:
    """把 SDK 缓冲的事件强制上报（幂等；关闭状态下是廉价 no-op）。"""
    langfuse_context.flush()
```

- [ ] **Step 4: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/infra/llm/test_tracing.py -q
```

Expected: **4 passed**

- [ ] **Step 5: 静态检查 + 提交**

```bash
.venv/bin/ruff format src/infra/llm/tracing.py tests/infra/llm/test_tracing.py
.venv/bin/ruff check src/infra/llm/tracing.py tests/infra/llm/test_tracing.py
.venv/bin/pyright src/infra/llm/tracing.py
git add src/infra/llm/tracing.py tests/infra/llm/test_tracing.py
git commit -m "feat(tracing): 新增接线基础设施模块（开关/flush/trace id 校验）"
```

---

## Task 3: 入站 trace id 白名单（middleware）

**Files:**
- Modify: `src/middleware/trace_id.py:17-27`
- Test: `tests/middleware/test_trace_id.py`

**Interfaces:**
- Consumes: `tracing.is_valid_trace_id` / `tracing.new_trace_id`（Task 2）
- Produces: 行为契约 —— 非法入站 id 被静默替换，且**替换发生在 `current_trace_id.set()` 之前**

- [ ] **Step 1: 写失败测试**

`tests/middleware/test_trace_id.py`：

```python
"""入站 trace id 白名单的行为契约（D3）。

关键点：校验必须发生在 `current_trace_id.set()` **之前** —— 响应头用的是中间件
内部的局部变量（`trace_id.py:33`），而 SSE done 事件重读 contextvar。若在下游
重生成，两者会取到不同的值，四方对齐当场分叉。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.trace_id import trace_id_middleware


def _app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(trace_id_middleware)

    @app.get("/ping")
    async def ping() -> dict:
        from src.infra.llm.trace_context import current_trace_id

        return {"trace_id": current_trace_id.get()}

    return app


def test_valid_inbound_id_is_kept():
    """合法入站 id 原样使用，响应头与上下文一致。"""
    client = TestClient(_app())
    resp = client.get("/ping", headers={"X-Trace-ID": "trace_my-custom_1"})
    assert resp.headers["X-Trace-ID"] == "trace_my-custom_1"
    assert resp.json()["trace_id"] == "trace_my-custom_1"


def test_illegal_inbound_id_is_replaced_and_aligned():
    """非法入站 id 被替换，且响应头与上下文取到**同一个**新值。"""
    client = TestClient(_app())
    resp = client.get("/ping", headers={"X-Trace-ID": "has space/and slash"})
    replaced = resp.headers["X-Trace-ID"]
    assert replaced != "has space/and slash"
    assert replaced.startswith("trace_")
    assert resp.json()["trace_id"] == replaced


def test_missing_id_is_generated():
    """缺失时生成，响应头与上下文一致。"""
    client = TestClient(_app())
    resp = client.get("/ping")
    assert resp.headers["X-Trace-ID"] == resp.json()["trace_id"]


def test_illegal_id_does_not_fail_request():
    """非法 id 不导致请求失败（静默替换，不返回 400）。"""
    client = TestClient(_app())
    resp = client.get("/ping", headers={"X-Trace-ID": "!!!bad!!!"})
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/middleware/test_trace_id.py -q
```

Expected: FAIL —— `test_illegal_inbound_id_is_replaced_and_aligned` 与 `test_illegal_id_does_not_fail_request` 失败（当前没有任何校验，`has space/and slash` 被原样回写）

- [ ] **Step 3: 实现**

`src/middleware/trace_id.py` 的取值段（当前 `:17-27`）改为：

```python
    # 1. 获取 trace_id：header → query → auto-generate
    #    入站值是不可信输入：接线后它会成为 Langfuse 的 trace 主键（upsert），
    #    非法值必须在此拦下并换成服务端生成值 —— 若留到下游再换，响应头（用的是
    #    下面的局部变量）会与 SSE done / Langfuse（都重读 contextvar）分叉。
    inbound = request.headers.get("X-Trace-ID")
    if not inbound:
        inbound = request.query_params.get("trace_id")
    if inbound and is_valid_trace_id(inbound):
        trace_id = inbound
    else:
        trace_id = new_trace_id()

    # 2. 注入 request.state 和 contextvar
    request.state.trace_id = trace_id
    current_trace_id.set(trace_id)
```

并在文件顶部 import 区加：

```python
from src.infra.llm.tracing import is_valid_trace_id, new_trace_id
```

- [ ] **Step 4: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/middleware/test_trace_id.py -q
```

Expected: **4 passed**

- [ ] **Step 5: 全量回归（middleware 是全局的，必须看有没有别的用例依赖旧行为）**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add src/middleware/trace_id.py tests/middleware/test_trace_id.py
git commit -m "feat(tracing): 入站 trace id 加白名单（在 set 之前校验，保四方对齐）"
```

---

## Task 4: trace 根落在 `_run_generation` + 四方 id 对齐

**Files:**
- Modify: `src/services/agent_service.py`（`_run_generation` 定义处，`:550` 附近）
- Modify: `src/api/chat.py`（`answer_builder` 闭包，`:204-217`）
- Test: `tests/services/test_run_generation_tracing.py`

**Interfaces:**
- Consumes: `tracing`（Task 2）
- Produces: trace 根名 `chat_turn`；调用约定 —— 调用方**必须**传 `langfuse_observation_id=<trace_id>`

- [ ] **Step 1: 写失败测试**

`tests/services/test_run_generation_tracing.py`（沿用 `tests/services/test_agent_service.py:633-667` 既有的直调范式：`RequestContext` + `StreamingRunManager` + `Mock` graph，以及它已导出的 `_chat_model_stream_item` / `_chat_model_end_item`）：

```python
"""trace 根：装饰契约与 trace 级字段（D2 / D3）。不发网络（测试期 tracing 已全局关停）。"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest

from src.chat.streaming import StreamingRunManager
from src.infra.llm.request_context import RequestContext
from src.services import agent_service
from src.services.agent_service import _run_generation


async def _fake_astream(*args, **kwargs):
    """最小图事件源：一个 token + 一次 model end。"""
    from tests.services.test_agent_service import (  # noqa: PLC0415
        _chat_model_end_item,
        _chat_model_stream_item,
    )

    yield _chat_model_stream_item("你好")
    yield _chat_model_end_item("qwen-max")


@pytest.mark.asyncio
async def test_run_generation_records_trace_fields(monkeypatch):
    """根内写入 trace 级 input / session_id，且 input 只含标量。"""
    captured: dict = {}

    class _SpyContext:
        def update_current_trace(self, **kwargs):
            captured.update(kwargs)

        def update_current_observation(self, **kwargs):
            captured.setdefault("_obs", []).append(kwargs)

    monkeypatch.setattr(agent_service, "langfuse_context", _SpyContext())

    mgr = StreamingRunManager()
    fake_graph = Mock()
    fake_graph.astream_events = _fake_astream
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
        langfuse_observation_id="trace_unit_root",
    )

    assert captured["session_id"] == "s1"
    assert captured["input"]["query"] == "q"
    assert captured["input"]["kb_id"] == "kb1"
    assert all(
        isinstance(v, (str, bool, int, float, type(None)))
        for v in captured["input"].values()
    )


def test_run_generation_is_observed_without_input_capture():
    """根必须以 capture_input=False 装饰 —— 否则内部对象会被序列化进 trace。"""
    assert '@observe(name="chat_turn", capture_input=False)' in inspect.getsource(
        agent_service
    )


def test_answer_builder_passes_observation_id():
    """调用方必须把当前 trace_id 作为根 observation id 传入。"""
    from src.api import chat

    assert "langfuse_observation_id=" in inspect.getsource(chat._stream_rag_response)
```

> 说明：`_run_generation` 的签名里**不出现** `langfuse_observation_id` —— 它由 `@observe` 的包装器从 kwargs 取走（本计划写作时实测确认），函数体收不到它。

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q
```

Expected: 三条全 FAIL ——
① `test_run_generation_records_trace_fields`：`TypeError: _run_generation() got an unexpected keyword argument 'langfuse_observation_id'`
② `test_run_generation_is_observed_without_input_capture`：源码断言找不到 `@observe(name="chat_turn", capture_input=False)`
③ `test_answer_builder_passes_observation_id`：`_stream_rag_response` 里还没有 `langfuse_observation_id=`

- [ ] **Step 3: 实现 —— 装饰根**

`src/services/agent_service.py`：顶部加

```python
from langfuse.decorators import langfuse_context, observe
```

`_run_generation` 定义前加装饰器（**注意 `capture_input=False`**）：

```python
@observe(name="chat_turn", capture_input=False)
async def _run_generation(
```

并在函数体开头（`graph` 取值校验之后、进入事件循环之前）写 trace 级字段：

```python
    # trace 级字段：输入只写该写的（根函数入参含 ctx/manager/graph/abort_signal，
    # 自动 capture 会把内部对象序列化进 trace，故装饰器已 capture_input=False）
    langfuse_context.update_current_trace(
        input={"query": query, "kb_id": kb_id, "deep_thinking": deep_thinking},
        session_id=session_id,
        metadata={"direct_skill": direct_skill} if direct_skill else {},
    )
```

- [ ] **Step 4: 实现 —— 调用方传 id**

`src/api/chat.py` 的 `answer_builder`（`return await _run_generation(...)` 那一行前）加：

```python
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
            langfuse_observation_id=current_trace_id.get() or "",
        )
```

- [ ] **Step 5: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/services/test_run_generation_tracing.py -q
```

Expected: **passed**

- [ ] **Step 6: 全量回归**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 全绿（测试期 tracing 关停，不应有网络行为）

- [ ] **Step 7: 实跑验收（开启态四方 id 对齐）**

⚠️ 这一步要动 `.env`（**跨工作区共享的软链**，见 D18）：先记录原值。

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag-langfuse-trace
grep -n "LANGFUSE_ENABLE" .env        # 记下当前值
sed -i 's/^LANGFUSE_ENABLE=.*/LANGFUSE_ENABLE=true/' .env
docker compose up -d app && sleep 5
# 发一轮真实对话（带上自定义 trace id 做端到端串联验证）
curl -N -H "X-Trace-ID: trace_e2e_task4" -H "Content-Type: application/json" \
     -d '{"session_id":"<已有会话 id>","query":"你好"}' \
     http://localhost/api/chat/stream | tail -3
```

验收（四条都要对）：
1. 响应头 `X-Trace-ID: trace_e2e_task4`
2. 该请求期间全部日志行的 `trace_id` = `trace_e2e_task4`
3. SSE `done` 事件的 `trace_id` = `trace_e2e_task4`
4. Langfuse 中该 id 的 trace 存在：

```bash
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select id, name, session_id from traces where id='trace_e2e_task4';"
```

- [ ] **Step 8: 取消路径实测（把纸面断言变成实测）**

```bash
# 起一轮长回答，中途取消
curl -N -H "X-Trace-ID: trace_e2e_cancel" -H "Content-Type: application/json" \
     -d '{"session_id":"<已有会话 id>","query":"请详细解释公司法第 20 条"}' \
     http://localhost/api/chat/stream &
sleep 3
curl -X POST -H "X-Trace-ID: trace_e2e_cancel" \
     http://localhost/api/chat/cancel/<session_id>
sleep 5
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select id, name from traces where id='trace_e2e_cancel';"
```

Expected: **能查到该 trace**（observation 可能被标记为中断/错误，但 trace 不丢）。
若查不到 → **停下**：这说明 spec 的「生成被取消时 trace 不被丢失」不成立，须先改契约或改实现，**不允许跳过**。

- [ ] **Step 9: 提交**

```bash
git add src/services/agent_service.py src/api/chat.py tests/services/test_run_generation_tracing.py
git commit -m "feat(tracing): trace 根落在 _run_generation，id 与请求 trace_id 对齐"
```

---

## Task 5: 主 agent generation（含 `capture_input=False`）

**Files:**
- Modify: `src/agents/graph/agent_node.py`（`make_agent_model_node` 内的闭包，`:158` 起；日志点 `:228` 附近）
- Test: `tests/agents/graph/test_agent_node_tracing.py`

**Interfaces:**
- Consumes: `langfuse_context.update_current_observation`（SDK）
- Produces: generation 名 `agent_turn`；字段契约 —— model / input / output / usage / completion_start_time，且 **input 中不含内部对象**

- [ ] **Step 1: 写失败测试**

`tests/agents/graph/test_agent_node_tracing.py`：

```python
"""generation 字段回填契约（D4 / D8）。"""

import langfuse.decorators as deco
from langfuse.model import ModelUsage

from src.agents.graph import agent_node


def _msg_payload_has_no_internal_objects(payload: list[dict]) -> bool:
    """input 只允许出现 role / content 两个键。"""
    allowed = {"role", "content"}
    return all(set(item.keys()) <= allowed for item in payload)


def test_messages_payload_shape():
    """消息载荷是 [{role, content}]，不夹带对象引用。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    payload = agent_node._messages_payload(
        [SystemMessage(content="sys"), HumanMessage(content="hi")]
    )
    assert payload == [
        {"role": "system", "content": "sys"},
        {"role": "human", "content": "hi"},
    ]
    assert _msg_payload_has_no_internal_objects(payload)


def test_observe_decorator_disables_input_capture():
    """agent_model 闭包必须以 capture_input=False 装饰。

    用源码检查而非运行检查：闭包在工厂内定义、无独立引用可拿，
    而这条约束一旦丢失是**静默**的（内部对象被序列化进 trace）。
    """
    import inspect

    src = inspect.getsource(agent_node.make_agent_model_node)
    assert 'capture_input=False' in src
    assert 'as_type="generation"' in src
```

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py -q
```

Expected: FAIL —— `AttributeError: module 'src.agents.graph.agent_node' has no attribute '_messages_payload'`

- [ ] **Step 3: 实现 —— 载荷 helper**

`src/agents/graph/agent_node.py` 顶部 import 区加：

```python
from langfuse.decorators import langfuse_context, observe
from langfuse.model import ModelUsage
```

并在 `_extract_text` 之后加：

```python
def _messages_payload(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """把消息列表转成 Langfuse 输入载荷 [{role, content}]。

    只取 role 与 content —— 消息对象上还挂着 id / response_metadata 等字段，
    整对象交给序列化器会把不该进 trace 的东西带进去。

    Args:
        messages: LangChain 消息列表

    Returns:
        [{"role": <消息类型>, "content": <文本>}, ...]
    """
    return [{"role": m.type, "content": _extract_text(m)} for m in messages]
```

- [ ] **Step 4: 实现 —— 装饰闭包**

在 `make_agent_model_node` 内，闭包定义处改为：

```python
    @observe(name="agent_turn", as_type="generation", capture_input=False)
    async def agent_model(state: AgentState) -> dict:
```

并把「首个 chunk 到达」的记录从毫秒数改成**时刻**（`completion_start_time` 要的是时间点，不是偏移）—— 在两处 `if first_chunk_ms < 0:` 分支里同时记时刻：

```python
        first_chunk_ms = -1
        first_chunk_at: datetime | None = None
```

```python
                if first_chunk_ms < 0:
                    first_chunk_ms = int((time.monotonic() - turn_start) * 1000)
                    first_chunk_at = datetime.now(timezone.utc)
```

（两个分支各改一处；文件顶部补 `from datetime import datetime, timezone`）

- [ ] **Step 5: 实现 —— 回填 generation 字段**

在两分支合流之后、`model turn` 日志之前（`:228` 附近）插入：

```python
        # generation 字段回填（D8：input 必须显式写，不能靠自动捕获）
        langfuse_context.update_current_observation(
            model=model_name,
            input=_messages_payload(messages),
            output=_extract_text(result),
            usage=ModelUsage(
                input=usage_in,
                output=usage_out,
                total=usage_in + usage_out,
            ),
            completion_start_time=first_chunk_at,
            metadata={
                "iteration": iteration,
                "usage_estimated": usage_estimated,
                "temperature": temperature,
                "temp_source": temp_source,
                "kb_bound": bool(state.kb_id),
            },
        )
```

- [ ] **Step 6: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/agents/graph/test_agent_node_tracing.py -q
```

Expected: **2 passed**

- [ ] **Step 7: 全量回归**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 全绿

- [ ] **Step 8: 实跑验收 —— 字段齐全 + 内部对象不入 trace**

```bash
docker compose restart app && sleep 5
curl -N -H "X-Trace-ID: trace_e2e_task5" -H "Content-Type: application/json" \
     -d '{"session_id":"<已有会话 id>","query":"你好"}' \
     http://localhost/api/chat/stream | tail -2
sleep 5
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select name, type, model, prompt_tokens, completion_tokens, completion_start_time
     from observations where trace_id='trace_e2e_task5';"
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select input::text from observations where trace_id='trace_e2e_task5'
     and type='GENERATION';"
```

验收：generation 存在且 `model` / token 计数 / `completion_start_time` 非空；`input` 文本里**不出现** `StreamingRunManager` / `RequestContext` / `CompiledStateGraph` 之类字样。

- [ ] **Step 9: 嵌套自动成立实测（把 D11 的假设验掉）**

发一轮**会触发 fork 子代理**的对话（用已绑定的技能，或 `/xxx` 直出），确认子代理期间产生的日志/事件挂在**同一条** trace 下：

```bash
curl -N -H "X-Trace-ID: trace_e2e_fork" -H "Content-Type: application/json" \
     -d '{"session_id":"<已绑定技能会话>","query":"/financial-statement-analyzer 分析这家公司"}' \
     http://localhost/api/chat/stream | tail -2
sleep 20
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select count(*) from observations where trace_id='trace_e2e_fork';"
```

Expected: 计数 ≥ 1（只要根在，子代理期间**没有产生第二条 root** 即算通过；本任务**不要求** fork 的 LLM 有独立 generation —— 那是 Non-Goal）。若 fork 产生了**独立 trace**，说明嵌套假设不成立 → 停下，回到 design D11 重新决策。

- [ ] **Step 10: 提交**

```bash
git add src/agents/graph/agent_node.py tests/agents/graph/test_agent_node_tracing.py
git commit -m "feat(tracing): 主 agent 每次推理记 generation（capture_input=False）"
```

---

## Task 6: CLI 评测链路接线（每问一条 trace + flush）

**Files:**
- Modify: `src/cli/eval_ragas.py`（`generate_answers_and_contexts` `:102-190`，CLI 入口 `:550` 附近）
- Test: `tests/cli/test_eval_ragas_tracing.py`

**Interfaces:**
- Consumes: `tracing.configure_tracing` / `tracing.flush_tracing`（Task 2）
- Produces: 被装饰的单问函数 `_answer_one_question(...)`；根名 `eval_question`

- [ ] **Step 1: 写失败测试**

`tests/cli/test_eval_ragas_tracing.py`：

```python
"""CLI 侧接线契约（D6 / D2 的 Q6 决定）。"""

import inspect

from src.cli import eval_ragas


def test_single_question_helper_is_observed():
    """每个问题必须由被 @observe 装饰的独立函数承载（每问一条 trace）。"""
    assert hasattr(eval_ragas, "_answer_one_question")
    src = inspect.getsource(eval_ragas._answer_one_question)
    assert "eval_question" in inspect.getsource(eval_ragas)


def test_cli_configures_and_flushes_tracing():
    """CLI 不经 lifespan，必须自己 configure 与 flush。"""
    src = inspect.getsource(eval_ragas)
    assert "configure_tracing()" in src
    assert "flush_tracing()" in src
```

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/cli/test_eval_ragas_tracing.py -q
```

Expected: FAIL —— 无 `_answer_one_question` / 无 `configure_tracing`

- [ ] **Step 3: 实现 —— 抽出单问函数并装饰**

在 `src/cli/eval_ragas.py` 顶部 import 区加：

```python
from langfuse.decorators import langfuse_context, observe

from src.infra.llm.tracing import configure_tracing, flush_tracing
```

把原循环体里"调用图 → 取 answer → 取 contexts → 取 retrieval_details"抽成：

```python
@observe(name="eval_question", capture_input=False)
async def _answer_one_question(
    graph: Any,
    kb_id: str,
    session_id: str,
    query: str,
    trace_id: str,
) -> tuple[str, list[str], list[dict]]:
    """对单个问题跑一次图，返回 (answer, contexts, retrieval_details)。

    trace 级输入显式写入 —— 与 `_run_generation` 同理，根函数入参含 graph 等
    内部对象，不能靠自动捕获。

    Args:
        graph: LangGraph 编译后的图实例
        kb_id: 知识库 UUID
        session_id: 会话 ID
        query: 该问题文本
        trace_id: 该问题的 trace id（仅用于写入 trace 级 metadata）

    Returns:
        (回答文本, 渲染后的上下文列表, 结构化检索明细)
    """
    langfuse_context.update_current_trace(
        input={"query": query}, session_id=session_id
    )
    final_state = await graph.ainvoke(
        {
            "kb_id": kb_id,
            "session_id": session_id,
            "query": query,
            "trace_id": trace_id,
            "_history": [],
        }
    )
    answer = final_state.get("answer", "")
    contexts = [c.to_prompt_text() for c in final_state.get("tool_contexts", [])]
    details = [
        {
            "source": getattr(c, "source", ""),
            "score": getattr(c, "score", 0.0),
            "kind": getattr(c, "kind", "kb"),
        }
        for c in final_state.get("tool_contexts", [])
    ]
    return answer, contexts, details
```

> `retrieval_details` 里原有的 `getattr(c, ...)` 是本仓既有写法（`tool_contexts` 约定为 `RAGContext`、防御性容忍异常对象），**照搬不改**。

循环体改为调用它（保留原有的日志与异常处理结构）：

```python
        try:
            answer, ctx_list, details = await _answer_one_question(
                graph, kb_id, session_id, q, trace_id,
                langfuse_observation_id=trace_id,
            )
            answers.append(answer)
            contexts.append(ctx_list)
            retrieval_details.append(details)
            core_logging.log_event(
                Event.QA_ANSWER_DONE,
                index=i + 1,
                answer_len=len(answer),
                contexts=len(ctx_list),
            )
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.QA_ANSWER_FAILED, index=i + 1, err=str(e))
            answers.append(f"[ERROR] {e}")
            contexts.append([])
            retrieval_details.append([])
        finally:
            current_trace_id.reset(token)
```

- [ ] **Step 4: 实现 —— CLI 开关与 flush**

在 CLI 入口（`main()` 内、`asyncio.run(run_and_dispose(...))` 之前）加：

```python
    # CLI 不经 main.py 的 lifespan：开关与 flush 都要自己做（D5 / D6）
    configure_tracing()
```

并在生成段结束之后（`asyncio.run(...)` 返回后、进入 `run_evaluation` 之前）加：

```python
    # 生成段结束即 flush：--gate 路径会提前 sys.exit，晚 flush 等于丢数据
    flush_tracing()
```

同时在 `main()` 的 `try/finally`（若无则补一层）里再调一次 `flush_tracing()`，覆盖异常退出路径。

- [ ] **Step 5: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/cli/test_eval_ragas_tracing.py -q
```

Expected: **2 passed**

- [ ] **Step 6: 全量回归 + 提交**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
git add src/cli/eval_ragas.py tests/cli/test_eval_ragas_tracing.py
git commit -m "feat(tracing): CLI 评测链路每问一条 trace，并自行 configure/flush"
```

---

## Task 7: 开关真正生效（服务侧 + 配置四面一致）

**Files:**
- Modify: `src/main.py`（lifespan `:35-52`）
- Modify: `src/config/settings.py`（`:298-307`）
- Modify: `.env.template`（`:87` 附近）、`.env.example`（`:27` 附近）
- Test: `tests/config/test_settings.py`（追加用例）

**Interfaces:**
- Consumes: `tracing.configure_tracing` / `tracing.flush_tracing`（Task 2）
- Produces: 服务侧开关生效；`settings.py` 内置 `LANGFUSE_*` 默认值不再"假装有值"

- [ ] **Step 1: 写失败测试**

追加到 `tests/config/test_settings.py`：

```python
def test_langfuse_defaults_do_not_point_to_nonexistent_host():
    """内置 HOST 默认值不得指向仓库内不存在的服务名（D19）。"""
    import importlib

    import src.config.settings as settings_mod

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LANGFUSE_HOST", None)
        reloaded = importlib.reload(settings_mod)
        assert "langfuse:3000" not in reloaded.LANGFUSE_HOST
        assert reloaded.LANGFUSE_HOST == "http://langfuse-web:3000"


def test_langfuse_key_defaults_are_empty():
    """内置 key 默认值为空串 —— 没配就明确不可用，不假装有值（D19）。"""
    import importlib

    import src.config.settings as settings_mod

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("LANGFUSE_SECRET_KEY", None)
        os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        reloaded = importlib.reload(settings_mod)
        assert reloaded.LANGFUSE_SECRET_KEY == ""
        assert reloaded.LANGFUSE_PUBLIC_KEY == ""
```

> 注意：`conftest.py` 在 import 期设了 `LANGFUSE_ENABLE=false`；上面两个用例显式 `pop` 掉 key/host 再 reload，故不受影响。

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/config/test_settings.py -q
```

Expected: FAIL —— 当前 `LANGFUSE_HOST` 默认 `http://langfuse:3000`、key 默认非空

- [ ] **Step 3: 实现 —— settings 默认值**

`src/config/settings.py` `:295-307` 改为：

```python
# ====== Langfuse ======
# 自托管后端见 docs/adr/0011-langfuse-v2-downgrade.md。
# **内置默认值刻意留空**：K8s/CI/新 clone 若没有 .env，应表现为"明确不可用"，
# 而不是拿一个不存在的服务名与另一对 key 去连（那会静默失败、只留日志）。
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
# 容器内地址为 langfuse-web:3000（compose 服务名）；宿主机访问用 127.0.0.1:3000
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "http://langfuse-web:3000")
# 全局开关：false 时完全不产出 trace（且不影响对话）
LANGFUSE_ENABLE: bool = os.getenv("LANGFUSE_ENABLE", "true").lower() == "true"
```

- [ ] **Step 4: 实现 —— lifespan 接开关与 flush**

`src/main.py` 的 `lifespan`：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期处理器 — 启动/关闭。"""
    core_logging.log_event(Event.APP_STARTING)
    section_chars = validation.validate_all()
    core_logging.log_event(Event.PROMPT_VALIDATED, section_chars=section_chars)
    await _clear_stale_chat_locks()
    # 开关生效点：服务侧（CLI 侧在 eval_ragas 自行调用，它不经 lifespan）
    configure_tracing()
    yield
    core_logging.log_event(Event.APP_STOPPING)
    # 关停前上报缓冲事件，否则最后一批 trace 随进程消失
    flush_tracing()
```

顶部 import 加 `from src.infra.llm.tracing import configure_tracing, flush_tracing`。

- [ ] **Step 5: 实现 —— 配置文件对齐**

`.env.template`：`LANGFUSE_ENABLE=false` → `LANGFUSE_ENABLE=true`
`.env.example`：`LANGFUSE_ENABLE=` → `LANGFUSE_ENABLE=true`

并在 `.env.template` 的 `LANGFUSE_ENABLE` 上方注释里补一句：

```
# 应用侧 trace 产出的总开关（同时管 prompt 远端读取，见 glossary）。关闭时不产出
# trace 且对话不受影响；默认 true。
```

- [ ] **Step 6: 运行，确认通过 + 全量回归**

```bash
.venv/bin/python -m pytest tests/config/ -q
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 全绿

- [ ] **Step 7: 实跑验收 —— 关闭态零行为变化**

```bash
sed -i 's/^LANGFUSE_ENABLE=.*/LANGFUSE_ENABLE=false/' .env
docker compose up -d --force-recreate app && sleep 5
curl -N -H "X-Trace-ID: trace_e2e_off" -H "Content-Type: application/json" \
     -d '{"session_id":"<已有会话 id>","query":"你好"}' \
     http://localhost/api/chat/stream | head -3
sleep 3
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select count(*) from traces where id='trace_e2e_off';"
```

Expected: `0`（关闭态不产 trace），且 SSE 事件序列与开启时一致（`status → token… → done`），落库正常。

- [ ] **Step 8: 提交**

```bash
git add src/main.py src/config/settings.py .env.template .env.example tests/config/test_settings.py
git commit -m "feat(tracing): 开关经 lifespan 生效；清理 LANGFUSE_* 内置默认值"
```

---

## Task 8: trace 保留期清理 CLI

**Files:**
- Create: `src/cli/purge_langfuse_traces.py`
- Test: `tests/cli/test_purge_langfuse_traces.py`

**Interfaces:**
- Produces: `python -m src.cli.purge_langfuse_traces [--retention-days N] [--dry-run] [--yes]`；常量 `DEFAULT_RETENTION_DAYS=30` / `MIN_RETENTION_DAYS=1` / `MAX_DELETE_PER_RUN=1000`
- Consumes: `langfuse.Langfuse` 的 `api.trace.list(to_timestamp=...)`（返回 `Traces(data=[...])`）与 `api.trace.delete_multiple(trace_ids=[...])`

**已确认的 SDK 形态**（本计划写作时实读签名，勿臆测）：

```
trace.list(*, page, limit, user_id, name, session_id, from_timestamp, to_timestamp, order_by, ...) -> Traces
Traces: pydantic_v1.BaseModel, 字段 data: List[TraceWithDetails], meta: MetaResponse, Config.frozen=True
trace.delete_multiple(*, trace_ids: Sequence[str]) -> DeleteTraceResponse
```

- [ ] **Step 1: 写失败测试**

`tests/cli/test_purge_langfuse_traces.py`：

```python
"""清理 CLI 的护栏契约（D7）。全部用替身 client，不发网络。"""

from datetime import datetime, timedelta, timezone

import pytest

from src.cli import purge_langfuse_traces as purge


class _FakeTrace:
    def __init__(self, trace_id: str) -> None:
        self.id = trace_id


class _FakeTraces:
    def __init__(self, ids: list[str]) -> None:
        self.data = [_FakeTrace(i) for i in ids]
        self.meta = type("M", (), {"page": 1, "total_pages": 1})()


class _FakeTraceApi:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.deleted: list[list[str]] = []
        self.list_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _FakeTraces(self._ids)

    def delete_multiple(self, *, trace_ids):
        self.deleted.append(list(trace_ids))
        return type("R", (), {"status": "ok"})()


class _FakeClient:
    def __init__(self, ids: list[str]) -> None:
        self.api = type("A", (), {"trace": _FakeTraceApi(ids)})()


def test_retention_below_lower_bound_is_rejected():
    """保留期低于下界必须拒绝，且不查不删。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=0, dry_run=False, confirmed=True, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []
    assert fake.api.trace.list_calls == []


def test_unconfirmed_deletion_is_rejected():
    """非 dry-run 且未确认 → 拒绝执行。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=False, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_environment_guard_blocks_real_deletion():
    """未武装环境变量 → 拒绝真删（dry-run 不受限）。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=False
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_dry_run_lists_but_does_not_delete():
    """dry-run 输出待删标识且不删。"""
    fake = _FakeClient(["t1", "t2"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=True, confirmed=False, allowed=False
    )
    assert code == 0
    assert fake.api.trace.deleted == []


def test_over_limit_aborts_without_deleting():
    """超过单次上限 → 中止且不删任何数据。"""
    fake = _FakeClient([f"t{i}" for i in range(purge.MAX_DELETE_PER_RUN + 1)])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_real_deletion_passes_cutoff_and_audits(capsys):
    """真删：cutoff 为 now-retention，删除被调用，且审计输出含数量。"""
    fake = _FakeClient(["t1", "t2"])
    before = datetime.now(timezone.utc) - timedelta(days=30)
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code == 0
    assert fake.api.trace.deleted == [["t1", "t2"]]
    cutoff = fake.api.trace.list_calls[0]["to_timestamp"]
    assert abs((cutoff - before).total_seconds()) < 120
    out = capsys.readouterr().out
    assert "2" in out and "t1" in out


def test_empty_result_is_safe():
    """无超期数据 → 正常结束、不报错、不调用删除。"""
    fake = _FakeClient([])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code == 0
    assert fake.api.trace.deleted == []
```

- [ ] **Step 2: 运行，确认失败**

```bash
.venv/bin/python -m pytest tests/cli/test_purge_langfuse_traces.py -q
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'src.cli.purge_langfuse_traces'`

- [ ] **Step 3: 实现**

`src/cli/purge_langfuse_traces.py`：

```python
"""Langfuse trace 保留期清理 —— 删掉早于保留期的 trace。

**为什么必须自建**：Langfuse 的 Data Retention 在自托管下属企业版功能，OSS v2
没有。接线后 trace 会无界增长（ADR-0011 复查条件③的硬要求）。

**为什么带这么多护栏**：这是对运行中观测库的**不可逆删除**。一条误配命令就能
删光整库，因此下界、确认、上限、审计、环境约束缺一不可。

用法：
    python -m src.cli.purge_langfuse_traces --dry-run
    LANGFUSE_PURGE_ALLOW=1 python -m src.cli.purge_langfuse_traces --yes

退出码：0 = 正常（含 dry-run 与空结果）；非 0 = 被护栏拒绝或执行失败。
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from langfuse import Langfuse

from src.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

#: 默认保留期（天）
DEFAULT_RETENTION_DAYS: Final[int] = 30
#: 保留期下界：低于它一律拒绝，防 `--retention-days 0` 删光运行库
MIN_RETENTION_DAYS: Final[int] = 1
#: 单次删除上限：超过即中止且不删任何数据
MAX_DELETE_PER_RUN: Final[int] = 1000
#: 真删所需的环境变量（防 dev 的配置误连 prod 库）
ALLOW_ENV_VAR: Final[str] = "LANGFUSE_PURGE_ALLOW"


def _fetch_expired(client: Any, cutoff: datetime, limit: int) -> list[str]:
    """取出早于 cutoff 的 trace id，最多取 limit+1 条（多取一条用于判超限）。

    Args:
        client: Langfuse 客户端（测试传替身）
        cutoff: 时间界限，早于此值的 trace 视为超期
        limit: 单次上限

    Returns:
        trace id 列表（可能比 limit 多 1）
    """
    ids: list[str] = []
    page = 1
    while len(ids) <= limit:
        batch = client.api.trace.list(
            to_timestamp=cutoff, page=page, limit=100, order_by="timestamp.asc"
        )
        if not batch.data:
            break
        ids.extend(trace.id for trace in batch.data)
        page += 1
    return ids[: limit + 1]


def run(
    *,
    client: Any,
    retention_days: int,
    dry_run: bool,
    confirmed: bool,
    allowed: bool,
) -> int:
    """执行一次清理，返回退出码。

    Args:
        client: Langfuse 客户端（测试传替身）
        retention_days: 保留期天数
        dry_run: True 时只列出不删除
        confirmed: 是否已显式确认（`--yes`）
        allowed: 环境是否已武装（`LANGFUSE_PURGE_ALLOW=1`）

    Returns:
        0 表示正常结束；非 0 表示被护栏拒绝或执行失败
    """
    if retention_days < MIN_RETENTION_DAYS:
        print(
            f"[error] 保留期 {retention_days} 天低于下界 {MIN_RETENTION_DAYS} 天，拒绝执行",
            file=sys.stderr,
        )
        return 2

    if not dry_run and not confirmed:
        print("[error] 非 dry-run 必须显式传 --yes", file=sys.stderr)
        return 2

    if not dry_run and not allowed:
        print(
            f"[error] 未武装环境（{ALLOW_ENV_VAR}=1），拒绝真删。当前目标后端：{LANGFUSE_HOST}",
            file=sys.stderr,
        )
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    ids = _fetch_expired(client, cutoff, MAX_DELETE_PER_RUN)
    print(f"[info] 目标后端={LANGFUSE_HOST} 保留期={retention_days}天 命中={len(ids)}条")

    if not ids:
        print("[info] 无超期 trace，退出")
        return 0

    if len(ids) > MAX_DELETE_PER_RUN:
        print(
            f"[error] 命中 {len(ids)} 条超过单次上限 {MAX_DELETE_PER_RUN}，"
            f"未删除任何数据；请分批执行",
            file=sys.stderr,
        )
        return 2

    if dry_run:
        print("[dry-run] 以下 trace 将被删除：")
        for trace_id in ids:
            print(f"  - {trace_id}")
        return 0

    client.api.trace.delete_multiple(trace_ids=ids)
    print(f"[info] 已删除 {len(ids)} 条超期 trace：")
    for trace_id in ids:
        print(f"  - {trace_id}")
    return 0


def main() -> None:
    """CLI 入口：解析参数、构造客户端、交 run() 执行。"""
    import os

    parser = argparse.ArgumentParser(description="Purge expired Langfuse traces")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    client = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST.rstrip("/"),
    )
    code = run(
        client=client,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
        confirmed=args.yes,
        allowed=os.getenv(ALLOW_ENV_VAR) == "1",
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行，确认通过**

```bash
.venv/bin/python -m pytest tests/cli/test_purge_langfuse_traces.py -q
```

Expected: **7 passed**

- [ ] **Step 5: 实跑 —— dry-run（安全，不需要 `--yes`）**

```bash
.venv/bin/python -m src.cli.purge_langfuse_traces --dry-run
```

Expected: 打印目标后端 / 保留期 / 命中数；当前库只有 1 条 trace 且未超期 → `命中=0`

- [ ] **Step 6: 提交**

```bash
.venv/bin/ruff format src/cli/purge_langfuse_traces.py tests/cli/test_purge_langfuse_traces.py
.venv/bin/ruff check src/cli/purge_langfuse_traces.py tests/cli/test_purge_langfuse_traces.py
git add src/cli/purge_langfuse_traces.py tests/cli/test_purge_langfuse_traces.py
git commit -m "feat(tracing): 新增 trace 保留期清理 CLI（dry-run + 五项护栏 + 审计）"
```

**注意**：真正的级联删除验证与定时接入**不在本计划内** —— 它们是「计划外部署动作」的第①、②步（见 `tasks.md`）。本任务只交付**代码**。

---

## Task 9: 死代码清理 + `estimate_usage` 迁移 + 主规格 delta

**Files:**
- Create: `src/infra/llm/estimate.py`（或并入 `token_usage.py` —— 见 Step 3 的选择）
- Delete: `src/infra/llm/langfuse_tracing.py`、`src/rag/stream.py`
- Modify: `src/infra/llm/trace_context.py`、`src/core/log_events.py`、`src/core/log_event_specs.py`、`src/agents/graph/agent_node.py`（import）、`src/agents/skills/fork_stream.py`（import）、`src/services/agent_service.py`（`self._tracer`）
- Modify: `docs/openspec/changes/langfuse-trace-wiring/specs/token-usage-model/spec.md`（若上面选了新宿主，同步措辞）

**Interfaces:**
- Produces: `estimate_usage(messages: list, output: str) -> TokenUsage` 的新宿主；全仓对 `LangfuseTracer` / `@traced` / `current_tracer` / `stream_answer` 零引用

- [ ] **Step 1: 迁移 `estimate_usage` 到 `src/infra/llm/token_usage.py`**

把 `src/rag/stream.py:14-21` 的 `estimate_usage` 连同 docstring 原样搬进 `src/infra/llm/token_usage.py`（该文件已是 `TokenUsage` 的定义处，两者同一职责）。

同时把 `TokenUsage` 的 docstring 从「用于 `end_generation` 的参数传递」改为不再引用将被删除的 `end_generation`：

```python
class TokenUsage:
    """Token 用量统一结构 —— 跨调用点共享的估算与映射结果。"""
```

- [ ] **Step 1b: 写并跑 `TokenUsage` 不变量测试（补 `token-usage-model` delta 的覆盖）**

`tests/infra/llm/test_token_usage.py`（已存在则**追加**这两个用例）：

```python
def test_estimate_usage_total_is_sum_of_parts():
    """经由构造入口产出的实例，total 恒等于两项之和（spec delta 的场景）。"""
    from src.infra.llm.token_usage import estimate_usage

    est = estimate_usage([], "hello world")
    assert est.total_tokens == est.prompt_tokens + est.completion_tokens
    assert est.total_tokens > 0


def test_estimate_usage_lives_with_token_usage():
    """estimate_usage 与 TokenUsage 同模块（delta 的场景：不再是 rag/stream.py）。"""
    import src.infra.llm.token_usage as mod

    assert hasattr(mod, "TokenUsage")
    assert hasattr(mod, "estimate_usage")
```

Run: `.venv/bin/python -m pytest tests/infra/llm/test_token_usage.py -q`
Expected: PASS（`estimate_usage` 本就显式传 `total_tokens=input+output`，迁移后立即成立）

> **不要**写成 `TokenUsage(prompt_tokens=3, completion_tokens=4).total_tokens == 7` —— `TokenUsage` 是普通 dataclass、不做字段推导，手工构造的实例 `total_tokens` 就是默认 `0`。不变量由**构造点**保证，断言对象必须是"经由构造入口产出的实例"。

- [ ] **Step 2: 改两个 import 点**

```bash
grep -rn "from src.rag.stream import estimate_usage" src/ tests/
```

Expected: **恰好命中两处** —— `src/agents/graph/agent_node.py:27`、`src/agents/skills/fork_stream.py:21`。`tests/` 下**无引用**（本计划写作时已 grep 确认），故只需改这两个 import 点。
把两处改为 `from src.infra.llm.token_usage import estimate_usage`。

- [ ] **Step 3: 删除死代码**

```bash
git rm src/rag/stream.py src/infra/llm/langfuse_tracing.py
```

然后在 `src/infra/llm/trace_context.py` 删掉 `current_tracer`（ContextVar 定义 + `TYPE_CHECKING` 里的 import），并更新模块 docstring（它现在只提供 three 个 ContextVar）。

在 `src/services/agent_service.py` 删掉 `self._tracer = LangfuseTracer()`（`:765`）与顶部 `from src.infra.llm.langfuse_tracing import LangfuseTracer`（`:47`）。

- [ ] **Step 4: 删三个 `TRACE_*` 事件（两处同名登记）**

`src/core/log_events.py`：删 `TRACE_READY` / `TRACE_INIT_FAILED` / `TRACE_SKIP`
`src/core/log_event_specs.py`：删对应的三条 spec（与上一步同一批）

- [ ] **Step 5: 验证零引用**

```bash
grep -rn "LangfuseTracer\|@traced\|current_tracer\|stream_answer\|TRACE_READY\|TRACE_SKIP\|TRACE_INIT_FAILED" src/ tests/ --include=*.py | grep -v __pycache__
```

Expected: **无输出**（`docs/` 下的历史记录不算，那些是冻结档案）

- [ ] **Step 6: 全量回归**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
.venv/bin/python -m ruff check .
.venv/bin/pyright src/
.venv/bin/python -m src.cli.check_docs
```

Expected: 全绿 / 无 error

- [ ] **Step 7: 提交**

```bash
git add -A src/ tests/
git commit -m "refactor(tracing): 删 LangfuseTracer 一整套死代码，estimate_usage 迁入 token_usage"
```

---

## Task 10: 文档同步

**Files:**
- Modify: `docs/agents/glossary.md`（`:13` 的 `trace_id`、`:207` 的「trace 保留窗口」→「trace 保留期」）
- Modify: `docs/agents/code-map.md`（`:58` 的 cli 一行 + `src/infra/llm/` 结构变化）
- Modify: `docs/agents/cookbook.md`（`:315` 那段"UI 里看不到数据是正常的"）
- Modify: `docs/agents/logging-rules.md`（三个 `TRACE_*` 事件移除）
- Modify: `docs/agents/data-flow.md`（接线后的链路）
- Modify: `src/cli/README.md`（`:135` 的承诺兑现）

**Interfaces:**
- Consumes: Task 1–9 的实际实现（文档写**现状**，不写计划）

- [ ] **Step 1: glossary —— 术语统一与双重身份**

`:13` 的 `trace_id` 行改为：

```
| `trace_id` | 请求追踪 ID，格式 `trace_<uuid>`；**同时是 Langfuse 的 trace id**（接线后）。由 `X-Trace-ID` 头或 `?trace_id` 决定，**属不可信入站输入**，须经白名单校验后才使用 | — |
```

`:207` 的条目名「`trace 保留窗口`」→「`trace 保留期`」，正文改为描述**现状**（已落地清理、保留期 30 天、下界 1 天、上限 1000 条），不再说"另案尚未落地"。

同时在该节补一条**开关双重语义**的说明：`LANGFUSE_ENABLE` 同时管 prompt 远端读取与 trace 产出（依据 `docs/adr/0010`）。

- [ ] **Step 2: code-map —— 结构变化**

`:58` 的 cli 一行补上清理入口；`src/infra/llm/` 的模块列表补 `tracing.py`、去掉 `langfuse_tracing.py`。

- [ ] **Step 3: cookbook —— 改写那段过时说明**

`:315` 一段改为「**UI 里看不到数据怎么排查**」：① 确认 `.env` 的 `LANGFUSE_ENABLE=true` 且 `app` 已重启；② 发一轮真实对话；③ 按响应头 `X-Trace-ID` 去 Langfuse 检索。**删掉**"用 `LangfuseTracer` 裸发一条 trace"（该类已删除）。

- [ ] **Step 4: logging-rules —— 事件增减**

删掉三个 `TRACE_*` 事件的登记；若本变更引入任何新事件，在**两处同名**补齐（本计划不引入新事件，故只删）。

- [ ] **Step 5: data-flow —— 链路**

补一条：一次对话请求的 `trace_id` 如何贯穿 中间件 → 后台任务 → trace 根 → generation。

- [ ] **Step 6: cli/README —— 兑现承诺**

`:135` 那句保持不变（它现在**成立**了），但在同段补一句执行前提说明：

```
（前提：`.env` 的 `LANGFUSE_ENABLE=true` 且后端可达 —— 关闭时该列无对应 trace）
```

- [ ] **Step 7: 闸门 + 提交**

```bash
.venv/bin/python -m src.cli.check_docs
git add docs/agents/glossary.md docs/agents/code-map.md docs/agents/cookbook.md \
        docs/agents/logging-rules.md docs/agents/data-flow.md src/cli/README.md
git commit -m "docs(tracing): 同步 glossary/code-map/cookbook/logging-rules/data-flow 与 cli README"
```

---

## Task 11: ADR（新决策 + ADR-0011 复评记录）

**Files:**
- Create: `docs/adr/0012-langfuse-trace-content-and-retention.md`（编号按当时的实际最大号 +1）
- Modify: `docs/adr/README.md`（索引表登记新 ADR）
- Modify: 视 D10 的决定，可能新增 `docs/adr/0013-*.md` 记复评，或写在 0012 的「关系」段

**Interfaces:**
- Consumes: `docs/adr/README.md` 的模板与「取代关系与头部字段」规则（含反向指针）

- [ ] **Step 1: 确认编号与既有 ADR**

```bash
ls docs/adr/
.venv/bin/python -m src.cli.check_adr
```

Expected: 当前最大号 0011；`check_adr` 0 error

- [ ] **Step 2: 写新 ADR（D16）**

按 `docs/adr/README.md` 的模板写 `docs/adr/0012-langfuse-trace-content-and-retention.md`，**必须包含**：

- **决策**：trace 记录 prompt / 回答原文（`capture_output=True` 保留）+ 保留期 30 天
- **候选方案**表格（三个）：① 沿用 SDK 默认记原文＋30 天（选中）② 复用 `LLM_LOG_CONTENT` 开关（关闭时不记原文）③ 新增独立开关 `LANGFUSE_CAPTURE_CONTENT`
- **理由**：与"性能/成本"无关，核心是**可回放性 vs 留存面**的取舍；并说明**被否决的候选各因什么被否决**
- **后果**：正面 = 链路可完整回放；负面/接受的代价 = **新增一个持久原文副本**，其保留期与**对话记录的保留期不一致**（trace 会活过对话被删之后）；**不解决的问题** = 未做脱敏（`mask` 回调留作将来）
- **复查触发条件**：合规要求出现 / trace 量级超阈值 / 需要按用户删除数据

- [ ] **Step 3: 落 ADR-0011 复评记录（D10）**

在 0012 的头部加 `- **关系**：` 一段，写明「本 ADR 的记录同时承载 ADR-0011 复查条件②的复评结论：接线后**维持 v2 降级**，依据是接线不引 ClickHouse、不新增存储、Langfuse 仍在旁路（开关可关、后端不可达不影响对话）」。
**若决定改用独立 ADR**，则新建 `0013-*` 并在 0012 用 `- **关系**：` 互指；**不要**改动 ADR-0011 的正文与 Status（本情形属"确认"而非"推翻"）。

- [ ] **Step 4: 登记索引表**

在 `docs/adr/README.md` 的「索引」表加一行（编号 / 一句话 / Status / 取代关系）。

- [ ] **Step 5: 闸门**

```bash
.venv/bin/python -m src.cli.check_adr
```

Expected: 0 error（编号连续、必填字段齐、索引已登记、无反向指针缺失）

- [ ] **Step 6: 提交**

```bash
git add docs/adr/
git commit -m "docs(adr): 记录 trace 内容与保留期取舍；登记 ADR-0011 复查条件②的复评结论"
```

---

## Task 12: 剩余两条未验证断言

**Files:** 无代码改动（纯验证 + 记录）
- Modify: `docs/openspec/changes/langfuse-trace-wiring/tasks.md` §3（实施期修正记录）

**Interfaces:**
- Consumes: Task 4/5 已验的两条；本任务补完最后两条

- [ ] **Step 1: id 字符集 —— 服务端到底接受什么**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag-langfuse-trace
for TID in "trace_with-dash_and_underscore" "trace.with.dots" "trace:colon" \
           "$(printf 'x%.0s' {1..200})"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "X-Trace-ID: $TID" -H "Content-Type: application/json" \
    -d '{"session_id":"<已有会话 id>","query":"你好"}' \
    http://localhost/api/chat/stream)
  echo "inbound=$TID -> http=$code"
done
sleep 5
docker exec corporate-rag-postgres psql -U langfuse -d langfuse -tAc \
  "select id from traces where id like 'trace.%' or id like 'trace:%' or length(id) > 100;"
```

Expected: 点号/冒号/超长的入站值**先被 middleware 拒掉**（不该出现在库里）。
若服务端接受的范围**窄于**当前白名单（例如拒收点号），把白名单收紧到实测通过的范围，并同步 `specs/llm-tracing/spec.md` 的 requirement 文本。

- [ ] **Step 2: 后端不可达 —— 对话不受影响**

```bash
docker compose stop langfuse-web
curl -s -N -H "X-Trace-ID: trace_e2e_down" -H "Content-Type: application/json" \
     -d '{"session_id":"<已有会话 id>","query":"你好"}' \
     http://localhost/api/chat/stream | tail -3
docker compose start langfuse-web
```

Expected: SSE 正常走完（`status → token… → done`），**无面向用户的错误**；日志里能看到 tracing 相关的告警。

- [ ] **Step 3: 记录结论**

在 `docs/openspec/changes/langfuse-trace-wiring/tasks.md` 的 §3「实施期修正记录」表里补两行（日期 / 落点 / 事实 / 处置 / 是否回改 design）。若第 1 步收紧了白名单，**回改 design + spec**。

- [ ] **Step 4: 提交**

```bash
git add docs/openspec/changes/langfuse-trace-wiring/tasks.md docs/agents/ src/infra/llm/tracing.py
git commit -m "docs(tracing): 记录 id 字符集与后端不可达两条实测结论"
```

---

## Task 13: 收尾 —— 闸门全绿 + DoD 逐条核对

**Files:** 无代码改动

- [ ] **Step 1: 质量门禁**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . --fix
.venv/bin/pyright src/
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
.venv/bin/python -m src.cli.check_docs
.venv/bin/python -m src.cli.check_adr
openspec validate langfuse-trace-wiring
```

Expected: 全绿 / 0 error / valid

- [ ] **Step 2: 逐条核对 `tasks.md` 的 DoD（11 条）**

打开 `docs/openspec/changes/langfuse-trace-wiring/tasks.md` 的 DoD 表，**逐条**写出证据（哪次实跑 / 哪个命令输出 / 哪条 pytest 用例）。凡拿不出证据的条目：**不许勾**，回到对应任务补验。

- [ ] **Step 3: 恢复 `.env` 到记录的原始值**

```bash
sed -i 's/^LANGFUSE_ENABLE=.*/LANGFUSE_ENABLE=<最初记录的值>/' .env
docker compose up -d --force-recreate app
grep -n "LANGFUSE_ENABLE" .env
```

- [ ] **Step 4: 提交收尾**

```bash
git add -A docs/
git commit -m "docs(tracing): DoD 逐条取证收口"
```

- [ ] **Step 5: 交接给收尾流程**

本计划不包含 worktree 收尾。执行完后按 `superpowers:finishing-a-development-branch` 处理合并/清理，并按 `tasks.md` 的「计划外部署动作」另行处理**定时接入 + 真删验证 + prod 落地**（那三件**不在此计划内**）。

---

## 计划外（提醒，不在本计划的任务里）

以下属 `tasks.md` 的「计划外部署动作」，**由人工 / 部署流程执行**，不要塞进本计划的 checkout：

1. **级联删除小规模验证**（D15 的硬前置，验证通过才能接定时任务）
2. **定时接入**（宿主 cron / systemd timer / compose 定时服务，形态开工前定）
3. **prod 侧落地**（同步 prod 机上的 `.env` + 确认 `langfuse-web` 在跑；按 D17 验收故障隔离）
4. **dev 侧开关的启用与回滚**（D18：`.env` 跨工作区共享、手工动作、先记录原值）

另有三条**登记为遗留、明确不在本变更内**：`check_docs` 补 openspec specs 扫描范围、`LANGFUSE_*` 是否加 fail-fast、trace 量级超阈值后的 v3/v4 迁移评估。
