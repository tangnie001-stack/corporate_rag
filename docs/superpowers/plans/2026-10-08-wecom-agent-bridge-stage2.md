# 企微接入 Agent 管线 — 阶段 2（通道协议与配置 + 输出投影层）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给企微通道提供一层可单测的「事件流 → 企微流式帧」投影器（`WeComPresenter`），并把回复出口协议扩出可选 `feedback` 参数，使 Agent 的结构化事件此后能保真地落到企微气泡。

**Architecture:** 投影层是纯逻辑：吃 `AsyncIterator[SSEEvent]`（阶段 1 的 `TurnHandle.events` 形态），吐 `ReplySink.reply_stream` 调用。它内部完成「累积快照 + 节流 + 帧数/长度上限 + 引用降级 + 终态兜底 + trace_id footer + 保活」；消费形态必须是**队列 + 超时等待**（不得对上游取事件的在途任务施加超时取消）。本阶段不接驱动、不碰 DB，全部用假 sink 单测。

**Tech Stack:** Python 3.11 / asyncio / loguru / pytest（`@pytest.mark.asyncio`，无 `asyncio_mode=auto`）/ 官方 `aibot` SDK（仅用到 `reply_stream(..., feedback=...)` 签名）。

**Spec:** `openspec/changes/wecom-agent-bridge/design.md`（D4 投影层三段式 / D6 节流·上限·保活 / D7 无渲染丢弃 / D8 引用降级 / D9 trace_id 三路 / D16 空回答兜底 / D17 占位首帧 / D18 脱敏）与 `openspec/changes/wecom-agent-bridge/specs/wecom-stream-projection/spec.md`（8 条 REQUIREMENT）。执行者两份都要读。

**Worktree:** `/root/code/corporate_rag-wecom-bridge`（分支 `feat/wecom-agent-bridge`，基线 `cf7c83a`）。

## Global Constraints

以下为项目级硬约束，**每个任务的要求都隐含包含本节**；值一律照抄，不得改写。

- **层间规则**：`channels/` **不得** import `api/`；`api/` 只做参数校验与路由转发。本阶段新增代码只允许依赖 `src/channels/base.py`、`src/config/`、`src/utils/sse.py`、`loguru`、标准库。
- **文件与函数红线**：单文件 ≤ 400 行；单函数 ≤ 80 行。
- **代码风格**：不用三元表达式（写完整 if/else）；类型不确定处用显式 `isinstance` / `is not None`，不用 `getattr(x, "attr", default)` 兜底。
- **硬编码集中**：本阶段新增的企微投影层**可调参数与用户可见文案**统一放新模块 `src/config/wecom_presenter.py`。
  - 归属裁定（供评审判断）：不放 `settings.py`（现 385 行，7 项参数加注释必越 400 红线）、不放 `const.py`（现 440 行，已越线）；与 `src/config/wecom_bots.py` 同例（该模块正是为避开 `settings.py` 红线而建）。这些文案不经 `to_sse`、不属 `SSEInteractionTexts` 的 SSE 交互文案，故与企微投影参数同居一档。
- **日志**：事件消息英文 `k=v` + `[wecom]` 前缀；用户可见中文文案只出现在配置模块与 `reply_stream` 内容里。统一用 `logger.error(...)` / `logger.warning(...)`，不 `print`。脱敏：**原始异常文本只进日志，不得作为企微回复内容**。
- **测试运行**：宿主侧一律 `POSTGRES_HOST=localhost pytest ...`（本阶段测试不碰 DB，但保持统一形状）。异步用例用 `@pytest.mark.asyncio`（本仓未开 `asyncio_mode=auto`）。
- **测试 mock 外部依赖**：不得发起真实网络调用（本阶段无网络；假 sink 记录调用即可）。
- **worktree 环境既有事实**：本 worktree 缺 gitignored 夹具 `data/test_docs/*`，`tests/parsers/` 会报 `FileNotFoundError` —— 属**环境性**、与本改动无关，**不要去修**，在报告里说明即可。`.venv` / `.env` 已软链到主仓。
- **提交命令形状（本仓约定）**：commit 单独一条命令；输出重定向到文件并**后台跑**（`> /tmp/xxx.log 2>&1`），完成后再读文件；**禁用** `... 2>&1 | tail -N`（输出被缓冲、看着像死住），也禁用 `... | tail -N; echo "RC=$?"`（`$?` 取的是 tail 的退出码）。pre-commit 会跑 doc/ADR 钩子，属正常。不要 `grep -r` 扫全仓（有 500M+ 缓存），查受跟踪文件用 `git grep`。
- **Spike 后置（本阶段的已知不确定性）**：E1（快照 or 追加）/ E6（帧节奏）/ E7（6min 时限）/ E10（feedback 承载）/ E11（首帧前时限）**均未跑**。本阶段按参考项目先验值实现（100ms / 85 / 200000 / 240s / 2s），参数全部经环境变量可调，Spike 后只调值不改结构。**若 E1 反证为追加**，须**先修订** `wecom-stream-projection` 的「快照式流式投影」需求再实施，**不得静默偏离、不得预留双模式**（design D5）。

---

## 阶段地图（本文件只展开「阶段 2」；后续阶段留指针）

| 阶段 | 范围（对应 `openspec/changes/wecom-agent-bridge/tasks.md` 分组） | 状态 |
|---|---|---|
| 阶段 1 | 组 1 的 1.2/1.3/1.4：编排下沉 + 依赖注入 | **已合回 `dev-wsl`（`cf7c83a`）** |
| **阶段 2（本文件）** | **组 2（2.1/2.2）+ 组 4（4.1–4.12）：通道协议与配置 + 输出投影层** | 本次实施 |
| 阶段 3 | 组 3（3.1–3.9）：桥接 handler（会话 UUIDv5 / msgid 去重 / trace_id / 装配 `wecom_service`） | 未开始 |
| 阶段 4 | 组 5（5.1/5.2）驱动可靠性补丁 + 组 6（6.1–6.4）澄清回填 + 组 1 的 **1.5**（澄清答案应用下沉） | 未开始 |
| 阶段 5 | 组 7（7.1–7.5）验证与文档 + **组 0 Spike（E1–E13）** | 未开始 |

**阶段 2 的验收面**：`WeComPresenter` 在假 sink 下覆盖投影 spec 的全部 8 条需求场景；`ReplySink` 的 `feedback` 参数被两个驱动实现正确承接；**不改站点任何行为**（本阶段不碰 `api/`、不碰 `services/`、不碰驱动逻辑）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/config/wecom_presenter.py` | 企微投影层的可调参数（7 项，env 可覆盖）与用户可见文案 | **新建** |
| `src/channels/wecom/presenter.py` | `WeComPresenter`：事件流 → 企微流式帧的投影层 | **新建** |
| `src/channels/base.py` | `ReplySink` 协议：`reply_stream` 增可选 `feedback` | 修改（3 行签名 + docstring） |
| `src/channels/wecom/long_connection.py` | `_WsSink`：把 `feedback` 透传给 SDK | 修改（`reply_stream`） |
| `src/channels/wecom/callback.py` | `_CallbackSink`：接受并忽略 `feedback`（回调无承载） | 修改（`reply_stream`） |
| `tests/config/test_wecom_presenter.py` | 配置默认值与 env 覆盖、footer 强制开启规则、文案非空 | **新建** |
| `tests/channels/test_reply_sink_feedback.py` | 两个 sink 实现的 `feedback` 行为 | **新建** |
| `tests/channels/test_wecom_presenter.py` | 投影层全部规范场景（本阶段主力测试文件） | **新建** |
| `tests/channels/test_callback_driver.py` | 既有假 sink 签名同步 | 修改 |
| `tests/channels/test_long_connection_driver.py` | 既有假 SDK 客户端签名同步 | 修改 |
| `tests/services/test_wecom_service.py` | 既有假 sink 签名同步 | 修改 |
| `docs/agents/code-map.md` | 登记两个新模块 | 修改（Task 5） |
| `docs/agents/defensive-patterns.md` | 登记「投影层保活不得取消上游在途取值」 | 修改（Task 5） |

> `tests/api/test_wecom.py` 用的是真实 `CallbackDriver` + 真实 `_CallbackSink`，不在同步清单内。

---

### Task 1: 通道协议扩可选 `feedback` + 企微投影层配置与文案

**Files:**
- Create: `src/config/wecom_presenter.py`
- Modify: `src/channels/base.py`（`ReplySink.reply_stream`，约 26–31 行）
- Modify: `src/channels/wecom/long_connection.py`（`_WsSink.reply_stream`，约 46–48 行）
- Modify: `src/channels/wecom/callback.py`（`_CallbackSink.reply_stream`，约 49–51 行）
- Modify: `tests/channels/test_callback_driver.py:35`、`tests/channels/test_long_connection_driver.py:31`、`tests/services/test_wecom_service.py:299`（假实现签名）
- Test: `tests/config/test_wecom_presenter.py`（新建）、`tests/channels/test_reply_sink_feedback.py`（新建）

**Interfaces:**
- Consumes: 无（本任务无前置）。
- Produces:
  - `ReplySink.reply_stream(content: str, finish: bool, feedback: dict | None = None) -> None`
  - `src.config.wecom_presenter`：
    - `MIN_SEND_INTERVAL_SECONDS: float`（默认 `0.1`）
    - `MAX_INTERMEDIATE_FRAMES: int`（默认 `85`）
    - `MAX_STREAM_CHARS: int`（默认 `200000`）
    - `KEEPALIVE_INTERVAL_SECONDS: float`（默认 `240`）
    - `FIRST_FRAME_TIMEOUT_SECONDS: float`（默认 `2`）
    - `TRACE_FOOTER_ENABLED: bool`（默认 `True`）
    - `FEEDBACK_ID_ENABLED: bool`（默认 `False`）
    - `TRACE_FOOTER_EFFECTIVE: bool`（= `TRACE_FOOTER_ENABLED or not FEEDBACK_ID_ENABLED`）
    - `WeComPresenterTexts`：`PLACEHOLDER_TEXT` / `FALLBACK_TEXT` / `ERROR_TEXT` / `ABSTENTION_TEXT` / `SOURCES_TITLE` / `TRACE_FOOTER_TEMPLATE`

- [ ] **Step 1: 写配置模块**

创建 `src/config/wecom_presenter.py`：

```python
"""企微流式投影层的可调参数与用户可见文案（design D4/D6/D9/D16/D18）。

归属：既不放 `settings.py`（385 行、逼近 400 行红线），也不放 `const.py`
（440 行、已越线），而独立成 config 子模块——与 `wecom_bots.py` 同例。
本模块文案不经 SSE 管线，故不属 `SSEInteractionTexts` 的 SSE 交互文案。
"""

import os

# ── 可调参数（Spike E6/E7/E10 实测后可经环境变量调整，不必改代码）──

# 发送最小间隔（秒）：企微每帧等 ack 且同 req_id 串行，逐 token 发会堆队列
MIN_SEND_INTERVAL_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_MIN_INTERVAL_SECONDS", "0.1")
)
# 承载正文的中间帧数上限（保活帧与占位帧不计入）
MAX_INTERMEDIATE_FRAMES: int = int(os.getenv("WECOM_PRESENTER_MAX_FRAMES", "85"))
# 单流累计正文长度上限（超出保留尾部）
MAX_STREAM_CHARS: int = int(os.getenv("WECOM_PRESENTER_MAX_CHARS", "200000"))
# 保活间隔（秒）：须小于「首帧起 6 分钟」的收尾时限
KEEPALIVE_INTERVAL_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_KEEPALIVE_SECONDS", "240")
)
# 首帧最迟时间（秒）：超过则先发占位帧，不空等到超时
FIRST_FRAME_TIMEOUT_SECONDS: float = float(
    os.getenv("WECOM_PRESENTER_FIRST_FRAME_TIMEOUT", "2")
)
# 终态 trace_id footer 开关
TRACE_FOOTER_ENABLED: bool = os.getenv(
    "WECOM_TRACE_FOOTER_ENABLED", "true"
).lower() in ("1", "true", "yes")
# 首帧反馈标识是否可用（Spike E10 结论落地前默认关）
FEEDBACK_ID_ENABLED: bool = os.getenv(
    "WECOM_FEEDBACK_ID_ENABLED", "false"
).lower() in ("1", "true", "yes")
# 终态 footer 的有效开关：反馈标识不可用时强制开启（footer 成唯一人工可读路）
TRACE_FOOTER_EFFECTIVE: bool = TRACE_FOOTER_ENABLED or not FEEDBACK_ID_ENABLED


class WeComPresenterTexts:
    """企微投影层对用户可见的文案（不散落在投影逻辑里）。"""

    # 占位首帧默认文案：首个状态事件到达前使用
    PLACEHOLDER_TEXT: str = "正在处理，请稍候…"
    # 终态兜底文案：整轮无正文时使用（避免空白关闭或悬挂流）
    FALLBACK_TEXT: str = "抱歉，本次未能生成回答，请重试或转人工咨询。"
    # 脱敏错误文案：原始异常只进日志，不外泄给用户
    ERROR_TEXT: str = "暂时无法回答，请稍后重试或转人工咨询。"
    # 转人工提示：拒答（abstention）时追加到终态正文
    ABSTENTION_TEXT: str = "未在文档中找到相关数据，可尝试转人工咨询。"
    # 文末来源段标题
    SOURCES_TITLE: str = "参考来源"
    # 终态 footer 模板（用 .format(trace_id) 填充）
    TRACE_FOOTER_TEMPLATE: str = "trace_id: {}"
```

- [ ] **Step 2: 写配置模块的失败测试**

创建 `tests/config/test_wecom_presenter.py`：

```python
"""企微投影层配置：默认值存在且合理、footer 强制开启规则、文案非空。"""

import importlib

from src.config import wecom_presenter


def test_defaults_are_positive():
    assert wecom_presenter.MIN_SEND_INTERVAL_SECONDS > 0
    assert wecom_presenter.MAX_INTERMEDIATE_FRAMES > 0
    assert wecom_presenter.MAX_STREAM_CHARS > 0
    assert wecom_presenter.KEEPALIVE_INTERVAL_SECONDS > 0
    assert wecom_presenter.FIRST_FRAME_TIMEOUT_SECONDS > 0


def test_footer_forced_on_when_feedback_unavailable(monkeypatch):
    monkeypatch.setenv("WECOM_TRACE_FOOTER_ENABLED", "false")
    monkeypatch.setenv("WECOM_FEEDBACK_ID_ENABLED", "false")
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.TRACE_FOOTER_EFFECTIVE is True
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)


def test_footer_can_be_disabled_when_feedback_available(monkeypatch):
    monkeypatch.setenv("WECOM_TRACE_FOOTER_ENABLED", "false")
    monkeypatch.setenv("WECOM_FEEDBACK_ID_ENABLED", "true")
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.TRACE_FOOTER_EFFECTIVE is False
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)


def test_texts_are_present():
    texts = wecom_presenter.WeComPresenterTexts
    assert texts.PLACEHOLDER_TEXT
    assert texts.FALLBACK_TEXT
    assert texts.ERROR_TEXT
    assert texts.ABSTENTION_TEXT
    assert texts.SOURCES_TITLE
    assert "{}" in texts.TRACE_FOOTER_TEMPLATE
```

- [ ] **Step 3: 跑配置测试**

Run: `POSTGRES_HOST=localhost pytest tests/config/test_wecom_presenter.py -q`
Expected: 4 passed。（Step 1 已先建模块，故此处直接 GREEN；若先删模块则会 `ModuleNotFoundError` —— 本任务把配置模块视作"契约常量"，先建后测。）

- [ ] **Step 4: 写 `ReplySink` 扩展的失败测试**

创建 `tests/channels/test_reply_sink_feedback.py`：

```python
"""ReplySink 的可选 feedback 参数：长连接透传、回调忽略。"""

import pytest

from src.channels.wecom.callback import _CallbackSink
from src.channels.wecom.long_connection import _WsSink


class _FakeSdkClient:
    """伪 SDK 客户端：记录 reply_stream 的全部参数。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply_stream(
        self,
        frame: dict,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback: dict | None = None,
    ) -> dict:
        self.calls.append(
            {
                "frame": frame,
                "stream_id": stream_id,
                "content": content,
                "finish": finish,
                "feedback": feedback,
            }
        )
        return {}


@pytest.mark.asyncio
async def test_ws_sink_passes_feedback_to_sdk():
    client = _FakeSdkClient()
    sink = _WsSink(client, {"body": {}}, "stream-1")

    await sink.reply_stream("甲", False, feedback={"id": "trace_1"})

    assert client.calls[0]["feedback"] == {"id": "trace_1"}


@pytest.mark.asyncio
async def test_ws_sink_without_feedback_sends_none():
    client = _FakeSdkClient()
    sink = _WsSink(client, {"body": {}}, "stream-1")

    await sink.reply_stream("甲", False)

    assert client.calls[0]["feedback"] is None


@pytest.mark.asyncio
async def test_callback_sink_accepts_and_ignores_feedback():
    sink = _CallbackSink()

    await sink.reply_stream("甲", True, feedback={"id": "trace_1"})

    assert sink.reply is not None
    assert sink.reply.content == "甲"
    assert sink.reply.finish is True
```

- [ ] **Step 5: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_reply_sink_feedback.py -q`
Expected: FAIL —— `_FakeSdkClient` 之外的断言先不成立：`_WsSink.reply_stream()` 现为两参签名，调用带 `feedback=` 会抛 `TypeError: reply_stream() got an unexpected keyword argument 'feedback'`。

- [ ] **Step 6: 扩协议与两个实现**

`src/channels/base.py`，把 `ReplySink.reply_stream` 换成：

```python
    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """回复流式消息。

        Args:
            content: 本帧内容（企微为累积全文：每次刷新整段替换气泡）
            finish: True 表示结束本轮
            feedback: 反馈信息（仅首帧有效，值为 {"id": trace_id}）；
                不支持该能力的通道忽略之
        """
        ...
```

`src/channels/wecom/long_connection.py`，把 `_WsSink.reply_stream` 换成：

```python
    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """发送流式回复。

        Args:
            content: 本帧内容（累积全文）
            finish: 是否结束流式消息
            feedback: 反馈信息（仅首帧设置；None 表示不带）
        """
        await self._client.reply_stream(
            self._frame, self._stream_id, content, finish, feedback=feedback
        )
```

`src/channels/wecom/callback.py`，把 `_CallbackSink.reply_stream` 换成：

```python
    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        """记录一条流式回复。

        回调模式只支持首次同步回包，回包体也没有反馈字段，故 `feedback` 被
        忽略（保留参数以满足 `ReplySink` 协议）。
        """
        self.reply = OutboundReply(kind="stream", content=content, finish=finish)
```

- [ ] **Step 7: 同步既有假实现签名**

`tests/channels/test_callback_driver.py:35`：

```python
    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.replies.append((content, finish))
```

`tests/services/test_wecom_service.py:299`：

```python
    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.replies.append((content, finish))
```

`tests/channels/test_long_connection_driver.py:31`（伪 SDK 客户端；不透传 feedback 会导致 `_WsSink` 调用时 `TypeError`）：

```python
    async def reply_stream(
        self,
        frame: dict,
        stream_id: str,
        content: str,
        finish: bool = False,
        feedback: dict | None = None,
    ) -> dict:
        self.replies.append((frame, stream_id, content, finish))
        return {}
```

- [ ] **Step 8: 跑相关测试确认全绿**

Run: `POSTGRES_HOST=localhost pytest tests/channels/ tests/config/ tests/services/test_wecom_service.py tests/api/test_wecom.py -q`
Expected: 全 passed。

- [ ] **Step 9: 全量回归（确认站点行为未变）**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 通过集合与本阶段基线一致（唯一允许的失败是 `tests/parsers/` 的 `FileNotFoundError`，环境性）。先在动手前记录一次基线：`POSTGRES_HOST=localhost pytest tests/ -q > /tmp/stage2_baseline.log 2>&1; echo RC=$?`（后台跑完后读文件取汇总行）。

- [ ] **Step 10: 提交**

```bash
git add src/config/wecom_presenter.py src/channels/base.py src/channels/wecom/long_connection.py src/channels/wecom/callback.py tests/config/test_wecom_presenter.py tests/channels/test_reply_sink_feedback.py tests/channels/test_callback_driver.py tests/channels/test_long_connection_driver.py tests/services/test_wecom_service.py
git commit -m "feat(channels): ReplySink 增可选 feedback 参数，并立企微投影层配置模块" > /tmp/commit_stage2_t1.log 2>&1; echo RC=$?
```

---

### Task 2: `WeComPresenter` 骨架 — 快照累积 + 节流 + 未变/空白跳过

**Files:**
- Create: `src/channels/wecom/presenter.py`
- Test: `tests/channels/test_wecom_presenter.py`（新建）

**Interfaces:**
- Consumes: `ReplySink`（Task 1 的三参签名）；`src.config.wecom_presenter.MIN_SEND_INTERVAL_SECONDS` / `WeComPresenterTexts.PLACEHOLDER_TEXT`。
- Produces: `WeComPresenter(sink, trace_id, *, min_interval_seconds=MIN_SEND_INTERVAL_SECONDS, monotonic=time.monotonic)`，方法 `run(events: AsyncIterator[SSEEvent]) -> None`、`update(event: SSEEvent) -> None`、`finalize() -> None`；模块级 `is_blank(text: str) -> bool`。

> 说明：本任务的 `run` 是**顺序消费**版本（`async for`），仅供本任务自成闭环地验证投影核心；Task 5 会把它替换为「队列泵 + 超时保活」版本（design D6 要求的消费形态）。`__init__` 的参数面按任务逐步扩（Task 3 加 `max_frames`/`max_chars`，Task 4 加 `footer_enabled`/`feedback_enabled`，Task 5 加 `keepalive_seconds`/`first_frame_timeout`），**不预留未使用的参数**。

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/test_wecom_presenter.py`：

```python
"""WeComPresenter 投影层单测：全部用假 sink，不发真实网络。"""

from collections.abc import AsyncIterator

import pytest

from src.channels.wecom.presenter import WeComPresenter, is_blank
from src.config.wecom_presenter import WeComPresenterTexts
from src.utils.sse import (
    SSEDoneEvent,
    SSEReasoningDeltaEvent,
    SSEStatusEvent,
    SSETokenEvent,
    SSEEvent,
)


class _RecordingSink:
    """记录每次 reply_stream 调用的假 sink。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, dict | None]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.calls.append((content, finish, feedback))

    @property
    def contents(self) -> list[str]:
        return [content for content, _finish, _fb in self.calls]


def _frozen_clock(value: float = 1000.0):
    """返回恒定时钟（配合非零节流间隔，可稳定断言"被跳过"）。"""

    def _now() -> float:
        return value

    return _now


async def _stream(events: list[SSEEvent]) -> AsyncIterator[SSEEvent]:
    for event in events:
        yield event


def test_is_blank_covers_whitespace_and_zero_width():
    assert is_blank("") is True
    assert is_blank("   ") is True
    assert is_blank("\u200b\u200d\ufeff") is True
    assert is_blank("甲") is False


@pytest.mark.asyncio
async def test_frames_are_cumulative_snapshots():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSETokenEvent(token="丙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[:3] == ["甲", "甲乙", "甲乙丙"]
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_throttle_merges_mid_stream_frames():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0.1, monotonic=_frozen_clock()
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSETokenEvent(token="丙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    mid_stream = [content for content, finish, _fb in sink.calls if not finish]
    assert mid_stream == ["甲"]
    assert sink.contents[-1].startswith("甲乙丙")


@pytest.mark.asyncio
async def test_unchanged_content_is_skipped():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEReasoningDeltaEvent(reasoning_delta="想…"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 未变内容不产生第二个中间帧；总帧数 = 1 中间帧 + 1 终态帧
    assert len(sink.calls) == 2
    assert sink.contents[0] == "甲"


@pytest.mark.asyncio
async def test_blank_and_zero_width_never_sent_as_answer():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="\u200b"),
                SSETokenEvent(token="\u200b\u200b"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == WeComPresenterTexts.PLACEHOLDER_TEXT
    assert all("\u200b" not in content for content in sink.contents)


@pytest.mark.asyncio
async def test_status_event_becomes_placeholder_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEStatusEvent(stage="retrieve", message="正在检索相关文档..."),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == "正在检索相关文档..."
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.channels.wecom.presenter'`。

- [ ] **Step 3: 写最小实现**

创建 `src/channels/wecom/presenter.py`：

```python
"""企微流式投影层：把结构化 SSEEvent 流投影成企微流式帧（design D4）。

三段式（start/update/finalize）对标 openakita StreamPresenter：累积快照与共享
节流收敛在本层；业务侧只喂事件，不关心通道渲染差异。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable

from src.channels.base import ReplySink
from src.config.wecom_presenter import (
    MIN_SEND_INTERVAL_SECONDS,
    WeComPresenterTexts,
)
from src.utils.sse import (
    SSEDoneEvent,
    SSEErrorEvent,
    SSEEvent,
    SSEStatusEvent,
    SSETokenEvent,
)

# 零宽字符：仅含这些字符的内容视为空白（不得产生空气泡）
_ZERO_WIDTH_CHARS: tuple[str, ...] = ("\u200b", "\u200c", "\u200d", "\ufeff")


def is_blank(text: str) -> bool:
    """判断文本是否为空白或仅含零宽字符。

    Args:
        text: 待判断文本

    Returns:
        True 表示去掉空白与零宽字符后为空
    """
    stripped = text
    for ch in _ZERO_WIDTH_CHARS:
        stripped = stripped.replace(ch, "")
    return not stripped.strip()


class WeComPresenter:
    """把 SSEEvent 流投影成企微流式帧（每帧发累积全文）。"""

    def __init__(
        self,
        sink: ReplySink,
        trace_id: str,
        *,
        min_interval_seconds: float = MIN_SEND_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            sink: 回复出口
            trace_id: 本轮 trace_id（终态 footer 与首帧反馈标识用）
            min_interval_seconds: 发送最小间隔（节流下限）
            monotonic: 单调时钟（测试注入以稳定断言节流）
        """
        self._sink = sink
        self._trace_id = trace_id
        self._min_interval_seconds = min_interval_seconds
        self._monotonic = monotonic

        self._text: str = ""
        self._placeholder: str = WeComPresenterTexts.PLACEHOLDER_TEXT
        self._final: bool = False
        self._last_sent: str | None = None
        self._last_sent_at: float = float("-inf")

    async def run(self, events: AsyncIterator[SSEEvent]) -> None:
        """消费上游事件流并完成一轮投影。

        终态由 `done`/`error` 事件或事件流自然结束触发。

        Args:
            events: 结构化事件流（如 `TurnHandle.events`）
        """
        async for event in events:
            await self.update(event)
            if isinstance(event, (SSEDoneEvent, SSEErrorEvent)):
                break
        await self.finalize()

    async def update(self, event: SSEEvent) -> None:
        """投影单个事件：累积正文 / 记录占位，必要时发送一帧。

        Args:
            event: 单个结构化事件
        """
        if isinstance(event, SSETokenEvent):
            self._text += event.token
        elif isinstance(event, SSEStatusEvent):
            if is_blank(self._text):
                self._placeholder = event.message
        await self._flush()

    async def finalize(self) -> None:
        """发送终态帧（幂等）。"""
        if self._final:
            return
        self._final = True
        await self._send(self._current_content(), finish=True)

    def _render_answer(self) -> str:
        """当前累积正文（本任务未做长度截断，见 Task 3）。"""
        return self._text

    def _has_answer(self) -> bool:
        """累积正文是否非空白。"""
        return not is_blank(self._render_answer())

    def _current_content(self) -> str:
        """当前应发送的内容：有正文用正文，否则用占位文案。"""
        if self._has_answer():
            return self._render_answer()
        return self._placeholder

    async def _flush(self) -> None:
        """按节流与「内容未变跳过」规则决定是否发送当前快照。"""
        if self._final:
            return
        content = self._current_content()
        if content == self._last_sent:
            return
        if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
            return
        await self._send(content, finish=False)

    async def _send(self, content: str, finish: bool) -> None:
        """发送一帧并记录发送内容与时刻。

        Args:
            content: 本帧内容
            finish: 是否为终态帧
        """
        await self._sink.reply_stream(content, finish)
        self._last_sent = content
        self._last_sent_at = self._monotonic()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: 6 passed。

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/presenter.py tests/channels/test_wecom_presenter.py
git commit -m "feat(channels): WeComPresenter 投影层骨架（快照累积 + 节流 + 未变/空白跳过）" > /tmp/commit_stage2_t2.log 2>&1; echo RC=$?
```

---

### Task 3: 帧数与长度上限 + 无渲染事件丢弃

**Files:**
- Modify: `src/channels/wecom/presenter.py`
- Test: `tests/channels/test_wecom_presenter.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `WeComPresenter`；`MAX_INTERMEDIATE_FRAMES` / `MAX_STREAM_CHARS`。
- Produces: `WeComPresenter.__init__` 增参 `max_frames: int = MAX_INTERMEDIATE_FRAMES`、`max_chars: int = MAX_STREAM_CHARS`；正文帧计数只在**承载正文**的帧上递增（保活帧/占位帧不计入 —— 保活帧在 Task 5 引入）。

- [ ] **Step 1: 写失败测试（追加到 `tests/channels/test_wecom_presenter.py`）**

```python
@pytest.mark.asyncio
async def test_frame_cap_stops_answer_frames_but_finalizes():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, max_frames=2
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="a"),
                SSETokenEvent(token="b"),
                SSETokenEvent(token="c"),
                SSETokenEvent(token="d"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 中间帧：前两帧（承载正文）后触顶；终态帧仍发全量
    assert len(sink.calls) == 3
    assert sink.contents[:2] == ["a", "ab"]
    assert sink.contents[-1].startswith("abcd")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_long_content_keeps_tail():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, max_chars=3
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="abcdef"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.contents[0] == "def"


@pytest.mark.asyncio
async def test_unrendered_events_are_dropped_without_interrupt():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSEReasoningDeltaEvent(reasoning_delta="思考"),
                SSEDelegateEvent(delegate_id="d1", action="start", skill="s"),
                SSETaskEvent(action="created", task={"type": "skill"}),
                SSEModelInfoEvent(model="m1", is_fallback=False),
                SSEAgentUsedEvent(agent="finance"),
                SSETokenEvent(token="甲"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert len(sink.calls) == 2
    assert sink.contents[0] == "甲"
```

并在文件顶部 import 补上 `SSEAgentUsedEvent`、`SSEDelegateEvent`、`SSEModelInfoEvent`、`SSETaskEvent`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: FAIL —— `TypeError: __init__() got an unexpected keyword argument 'max_frames'`（帧上限与长度截断未实现）。

- [ ] **Step 3: 实现上限与显式丢弃**

`src/channels/wecom/presenter.py` 的 `__init__` 签名与状态（在 Task 2 版本上增量改写）：

- import 增 `MAX_INTERMEDIATE_FRAMES, MAX_STREAM_CHARS`
- 签名增 `max_frames: int = MAX_INTERMEDIATE_FRAMES`、`max_chars: int = MAX_STREAM_CHARS`
- docstring 增两条 Args 说明
- 状态增 `self._max_frames = max_frames`、`self._max_chars = max_chars`、`self._frames_sent: int = 0`

`_render_answer` 改为按上限**保留尾部**：

```python
    def _render_answer(self) -> str:
        """当前累积正文；超过长度上限时保留尾部。"""
        if len(self._text) > self._max_chars:
            return self._text[-self._max_chars :]
        return self._text
```

`_flush` 改为（加承载正文帧计数与上限判定）：

```python
    async def _flush(self) -> None:
        """按节流与上限规则决定是否发送当前快照。"""
        if self._final:
            return
        carries_answer = self._has_answer()
        content = self._current_content()
        if content == self._last_sent:
            return
        if carries_answer and self._frames_sent >= self._max_frames:
            return
        if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
            return
        await self._send(content, finish=False)
        if carries_answer:
            self._frames_sent += 1
```

`update` 改为显式丢弃无渲染通道的事件（把"隐式不处理"变成"显式契约"）：

```python
    async def update(self, event: SSEEvent) -> None:
        """投影单个事件：累积正文 / 记录占位；无渲染通道的事件丢弃。

        丢弃类事件（思考过程 / 子代理过程 / 任务看板 / 模型信息 / 会话绑定
        智能体）在企微无对应渲染，丢弃且不得中断本轮流。

        Args:
            event: 单个结构化事件
        """
        if isinstance(event, SSETokenEvent):
            self._text += event.token
        elif isinstance(event, SSEStatusEvent):
            if is_blank(self._text):
                self._placeholder = event.message
        elif isinstance(
            event,
            (
                SSEReasoningDeltaEvent,
                SSEDelegateEvent,
                SSETaskEvent,
                SSEModelInfoEvent,
                SSEAgentUsedEvent,
            ),
        ):
            return
        await self._flush()
```

（import 补 `SSEAgentUsedEvent, SSEDelegateEvent, SSEModelInfoEvent, SSEReasoningDeltaEvent, SSETaskEvent`。）

> 注：`done` / `error` / `citation` / `abstention` / `ask_user` 分支在 Task 4 补齐；本任务只处理已列出的丢弃类。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: 9 passed。

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/presenter.py tests/channels/test_wecom_presenter.py
git commit -m "feat(channels): 投影层加帧数/长度上限并显式丢弃无渲染事件" > /tmp/commit_stage2_t3.log 2>&1; echo RC=$?
```

---

### Task 4: 引用来源 + trace_id 三路收尾 + 空回答/abstention 兜底 + 流式失败降级

**Files:**
- Modify: `src/channels/wecom/presenter.py`
- Test: `tests/channels/test_wecom_presenter.py`（追加）

**Interfaces:**
- Consumes: Task 1 的三参 `reply_stream`；`TRACE_FOOTER_EFFECTIVE` / `FEEDBACK_ID_ENABLED` / `WeComPresenterTexts`。
- Produces: `WeComPresenter.__init__` 增参 `footer_enabled: bool = TRACE_FOOTER_EFFECTIVE`、`feedback_enabled: bool = FEEDBACK_ID_ENABLED`；私有 `_render_final() -> str`、`_render_sources() -> str`、`_feedback_arg() -> dict | None`、`_send_raw(content, finish) -> None`；`_send` 改为返回 `bool`（失败置 `_degraded`）。

- [ ] **Step 1: 写失败测试（追加）**

```python
@pytest.mark.asyncio
async def test_sources_only_in_final_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSECitationEvent(source="财报.pdf", page=3, snippet="…"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert "参考来源" not in sink.contents[0]
    assert "财报.pdf" not in sink.contents[0]
    assert "参考来源" in sink.contents[-1]
    assert "财报.pdf" in sink.contents[-1]
    assert "第 3 页" in sink.contents[-1]


@pytest.mark.asyncio
async def test_no_sources_no_sources_block():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))

    assert "参考来源" not in sink.contents[-1]


@pytest.mark.asyncio
async def test_trace_footer_in_final_frame_and_switchable():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_abc", min_interval_seconds=0, footer_enabled=True
    )
    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))
    assert "trace_abc" in sink.contents[-1]

    sink2 = _RecordingSink()
    presenter2 = WeComPresenter(
        sink2, "trace_abc", min_interval_seconds=0, footer_enabled=False
    )
    await presenter2.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))
    assert "trace_abc" not in sink2.contents[-1]


@pytest.mark.asyncio
async def test_feedback_id_only_on_first_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, feedback_enabled=True
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.calls[0][2] == {"id": "trace_1"}
    assert sink.calls[1][2] is None


@pytest.mark.asyncio
async def test_error_is_desensitized():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEErrorEvent(error="psycopg: connection refused to 10.0.0.5"),
            ]
        )
    )

    final = sink.contents[-1]
    assert WeComPresenterTexts.ERROR_TEXT in final
    assert "psycopg" not in final
    assert "10.0.0.5" not in final
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_abstention_appends_transfer_hint():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="未在文档中找到"),
                SSEAbstentionEvent(),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert WeComPresenterTexts.ABSTENTION_TEXT in sink.contents[-1]


@pytest.mark.asyncio
async def test_empty_answer_falls_back_and_never_hangs():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSEDoneEvent(trace_id="trace_1")]))

    assert sink.calls, "终态帧必发，不得悬挂"
    assert WeComPresenterTexts.FALLBACK_TEXT in sink.contents[-1]
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_stream_failure_degrades_to_single_final_send():
    class _FailingSink(_RecordingSink):
        async def reply_stream(
            self, content: str, finish: bool, feedback: dict | None = None
        ) -> None:
            if not finish:
                raise RuntimeError("reply ack timeout")
            await super().reply_stream(content, finish, feedback)

    sink = _FailingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, footer_enabled=False
    )

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSETokenEvent(token="乙"),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    assert sink.calls == [("甲乙", True, None)]
```

> `footer_enabled=False` 是为了让终态正文等于纯答案、断言可精确比较；默认（`TRACE_FOOTER_EFFECTIVE`）为 True，终态会多一段 footer。

import 补 `SSEAbstentionEvent`、`SSECitationEvent`、`SSEErrorEvent`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: FAIL —— 多条新用例失败（引用不出现、footer 缺失、错误文案透传、空回答无兜底、`footer_enabled`/`feedback_enabled` 是未知关键字参数）。

- [ ] **Step 3: 实现收尾与降级**

`src/channels/wecom/presenter.py` 增量改写：

- import 增 `TRACE_FOOTER_EFFECTIVE, FEEDBACK_ID_ENABLED`、`SSEAbstentionEvent, SSECitationEvent, SSEErrorEvent`，并**增 `from loguru import logger`**（本任务新增的脱敏与发送失败日志要用它）
- 签名增 `footer_enabled: bool = TRACE_FOOTER_EFFECTIVE`、`feedback_enabled: bool = FEEDBACK_ID_ENABLED`
- 状态增 `self._sources: list[SSECitationEvent] = []`、`self._abstained: bool = False`、`self._error: str | None = None`、`self._degraded: bool = False`、`self._feedback_sent: bool = False`；并保存 `self._footer_enabled = footer_enabled`、`self._feedback_enabled = feedback_enabled`

`update` 换为（补齐引用/拒答/错误分支，并让 `done` 不再触发中间帧）：

```python
    async def update(self, event: SSEEvent) -> None:
        """投影单个事件：累积正文 / 收集引用 / 记录终态标记，必要时发送一帧。

        丢弃类事件（思考过程 / 子代理过程 / 任务看板 / 模型信息 / 会话绑定
        智能体）在企微无对应渲染，丢弃且不得中断本轮流。

        Args:
            event: 单个结构化事件
        """
        if isinstance(event, SSETokenEvent):
            self._text += event.token
        elif isinstance(event, SSEStatusEvent):
            if is_blank(self._text):
                self._placeholder = event.message
        elif isinstance(event, SSECitationEvent):
            self._sources.append(event)
        elif isinstance(event, SSEAbstentionEvent):
            self._abstained = True
        elif isinstance(event, SSEErrorEvent):
            # 脱敏：原始异常只进日志，不得外泄给企微用户
            logger.error("[wecom] presenter got error event err={}", event.error)
            self._error = WeComPresenterTexts.ERROR_TEXT
        elif isinstance(
            event,
            (
                SSEReasoningDeltaEvent,
                SSEDelegateEvent,
                SSETaskEvent,
                SSEModelInfoEvent,
                SSEAgentUsedEvent,
            ),
        ):
            return
        if isinstance(event, SSEDoneEvent):
            # done 意味着本轮已收尾，这里不再发中间帧，统一由 finalize 发终态帧
            return
        await self._flush()
```

`finalize` 改为：

```python
    async def finalize(self) -> None:
        """发送终态帧（含引用/兜底/footer）；流式失败时退化为一次性收尾。"""
        if self._final:
            return
        self._final = True
        content = self._render_final()
        if self._degraded:
            await self._send_raw(content, finish=True)
            return
        sent = await self._send(content, finish=True)
        if not sent:
            await self._send_raw(content, finish=True)
```

新增三个渲染/辅助方法：

```python
    def _render_final(self) -> str:
        """终态正文：正文 + 脱敏错误 + 转人工提示 + 参考来源 + trace_id footer。"""
        parts: list[str] = []
        body = self._render_answer()
        if not is_blank(body):
            parts.append(body)
        if self._error is not None:
            parts.append(self._error)
        if self._abstained:
            parts.append(WeComPresenterTexts.ABSTENTION_TEXT)
        if not parts:
            parts.append(WeComPresenterTexts.FALLBACK_TEXT)
        sources = self._render_sources()
        if sources:
            parts.append(sources)
        if self._footer_enabled:
            parts.append(
                WeComPresenterTexts.TRACE_FOOTER_TEMPLATE.format(self._trace_id)
            )
        return "\n\n".join(parts)

    def _render_sources(self) -> str:
        """文末「参考来源」段；无引用返回空串。"""
        if not self._sources:
            return ""
        lines: list[str] = [WeComPresenterTexts.SOURCES_TITLE]
        for item in self._sources:
            lines.append(f"- {item.source}（第 {item.page} 页）")
        return "\n".join(lines)

    def _feedback_arg(self) -> dict | None:
        """首帧的反馈标识；未开启或已发过则返回 None。"""
        if not self._feedback_enabled:
            return None
        if self._feedback_sent:
            return None
        return {"id": self._trace_id}
```

`_send` 改为返回 `bool` 并置降级标志，`_flush` 适配：

```python
    async def _send(self, content: str, finish: bool) -> bool:
        """发送一帧；失败时置降级标志并返回 False（不抛异常）。

        Args:
            content: 本帧内容
            finish: 是否为终态帧

        Returns:
            True 表示发送成功
        """
        try:
            await self._sink.reply_stream(
                content, finish, feedback=self._feedback_arg()
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter reply failed finish={} err={}", finish, e)
            self._degraded = True
            return False
        self._feedback_sent = True
        self._last_sent = content
        self._last_sent_at = self._monotonic()
        return True

    async def _send_raw(self, content: str, finish: bool) -> None:
        """降级收尾：单次发送最终内容，失败只记日志。"""
        try:
            await self._sink.reply_stream(
                content, finish, feedback=self._feedback_arg()
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter fallback reply failed err={}", e)
```

> 降级语义（明确，供评审）：发送失败后**只停发中间帧**，消费继续到终态事件（`done`/`error`/流结束），收尾时做一次 `_send_raw(content, finish=True)`。**不提前中断本轮**——后台生成任务与投影层无关，提前退出只会丢掉更完整的答案；且 `_degraded` 会让 `_flush` 直接早退，不会形成失败重试风暴。

`_flush` 增首行守卫与返回处理：

```python
    async def _flush(self) -> None:
        """按节流与上限规则决定是否发送当前快照。"""
        if self._final or self._degraded:
            return
        carries_answer = self._has_answer()
        content = self._current_content()
        if content == self._last_sent:
            return
        if carries_answer and self._frames_sent >= self._max_frames:
            return
        if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
            return
        sent = await self._send(content, finish=False)
        if sent and carries_answer:
            self._frames_sent += 1
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: 17 passed。

- [ ] **Step 5: 提交**

```bash
git add src/channels/wecom/presenter.py tests/channels/test_wecom_presenter.py
git commit -m "feat(channels): 投影层补齐引用来源、trace_id 三路收尾、兜底与失败降级" > /tmp/commit_stage2_t4.log 2>&1; echo RC=$?
```

---

### Task 5: 占位首帧 + 长流保活（队列泵）+ 单测补全覆盖 + 文档登记

**Files:**
- Modify: `src/channels/wecom/presenter.py`
- Modify: `tests/channels/test_wecom_presenter.py`（追加）
- Modify: `docs/agents/code-map.md`、`docs/agents/defensive-patterns.md`

**Interfaces:**
- Consumes: Task 2–4 的 `WeComPresenter`；`KEEPALIVE_INTERVAL_SECONDS` / `FIRST_FRAME_TIMEOUT_SECONDS`。
- Produces: `WeComPresenter.__init__` 增参 `keepalive_seconds: float = KEEPALIVE_INTERVAL_SECONDS`、`first_frame_timeout: float = FIRST_FRAME_TIMEOUT_SECONDS`；模块级 `_EVENTS_END` 哨兵；`_pump(events, queue)`、`_consume(queue)`；`run` 改为队列泵形态。

- [ ] **Step 1: 写失败测试（追加）**

```python
import asyncio


async def _slow_stream(
    events: list[SSEEvent], delays: list[float]
) -> AsyncIterator[SSEEvent]:
    """按给定延迟逐个产出事件（用于保活与首帧超时用例）。"""
    for event, delay in zip(events, delays):
        await asyncio.sleep(delay)
        yield event


@pytest.mark.asyncio
async def test_keepalive_fires_during_silence():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, keepalive_seconds=0.05
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 静默期内至少重发一次当前快照，且内容与上一帧相同
    assert sink.contents.count("甲") >= 2


@pytest.mark.asyncio
async def test_keepalive_does_not_terminate_upstream():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, keepalive_seconds=0.05
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 保活发生过之后，上游后续事件仍被消费到终态
    assert sink.contents[-1].startswith("甲乙")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_placeholder_frame_when_first_frame_is_late():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        first_frame_timeout=0.05,
        keepalive_seconds=1.0,
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSEDoneEvent()], [0.2, 0.0]
        )
    )

    assert sink.contents[0] == WeComPresenterTexts.PLACEHOLDER_TEXT


@pytest.mark.asyncio
async def test_run_ends_when_stream_ends_without_terminal_event():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(_stream([SSETokenEvent(token="甲")]))

    assert sink.contents[-1].startswith("甲")
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_keepalive_frames_not_counted_toward_cap():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_1",
        min_interval_seconds=0,
        max_frames=1,
        keepalive_seconds=0.05,
    )

    await presenter.run(
        _slow_stream(
            [SSETokenEvent(token="甲"), SSETokenEvent(token="乙"), SSEDoneEvent()],
            [0.0, 0.2, 0.0],
        )
    )

    # 正文帧已触顶（"甲乙" 不再作为中间帧发出），但保活帧仍照发（豁免上限）
    mid_stream = [content for content, finish, _fb in sink.calls if not finish]
    assert "甲乙" not in mid_stream
    assert mid_stream.count("甲") >= 2
    assert sink.contents[-1].startswith("甲乙")


@pytest.mark.asyncio
async def test_run_returns_even_if_upstream_never_ends():
    async def _endless() -> AsyncIterator[SSEEvent]:
        yield SSETokenEvent(token="甲")
        yield SSEDoneEvent(trace_id="trace_1")
        await asyncio.sleep(30)
        yield SSETokenEvent(token="不该到达")

    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    async with asyncio.timeout(2):
        await presenter.run(_endless())

    assert sink.calls[-1][1] is True
    assert all("不该到达" not in content for content, _f, _fb in sink.calls)


@pytest.mark.asyncio
async def test_run_finalizes_when_consume_loop_raises(monkeypatch):
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    async def _boom(event: SSEEvent) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(presenter, "update", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        await presenter.run(_stream([SSETokenEvent(token="甲")]))

    # 投影 spec 禁止悬挂未结束的流：异常路径也必须发出终态帧
    assert sink.calls
    assert sink.calls[-1][1] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: FAIL —— `keepalive_seconds` / `first_frame_timeout` 是未知关键字参数；`test_run_returns_when_sink_fails_during_silence` 会因顺序 `async for` 阻塞在 `asyncio.sleep(10)` 而超时。

- [ ] **Step 3: 实现队列泵与保活**

`src/channels/wecom/presenter.py` 增量改写：

- import 增 `asyncio`、`KEEPALIVE_INTERVAL_SECONDS, FIRST_FRAME_TIMEOUT_SECONDS`、`logger`
- 模块级新增哨兵：

```python
# 队列哨兵：上游事件流已结束（不再有事件入队）
_EVENTS_END: object = object()
```

- 签名增 `keepalive_seconds: float = KEEPALIVE_INTERVAL_SECONDS`、`first_frame_timeout: float = FIRST_FRAME_TIMEOUT_SECONDS`（docstring 说明"保活间隔须小于首帧起 6 分钟的收尾时限"）；状态增 `self._keepalive_seconds`、`self._first_frame_timeout`

- `run` 替换为：

```python
    async def run(self, events: AsyncIterator[SSEEvent]) -> None:
        """消费上游事件流并完成一轮投影。

        事件流以队列暴露、对「取下一个事件」施加超时：直接对上游生成器的
        在途取值施超时取消会终结生成器（`_subscribe_events` 不捕
        `CancelledError`），恰在需要保活时把长流静默截断。

        Args:
            events: 结构化事件流（如 `TurnHandle.events`）
        """
        queue: asyncio.Queue = asyncio.Queue()
        pump = asyncio.create_task(self._pump(events, queue))
        try:
            await self._consume(queue)
        except Exception:
            # 消费循环意外抛出（不是 error 事件路径）时仍须收尾：投影 spec
            # 禁止留下未结束的悬挂流；先补发终态帧，再把异常交给调用方
            await self.finalize()
            raise
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def _pump(self, events: AsyncIterator[SSEEvent], queue: asyncio.Queue) -> None:
        """后台泵：把上游事件入队；上游结束或出错时补哨兵。

        Args:
            events: 上游结构化事件流
            queue: 投影层消费的队列
        """
        try:
            async for event in events:
                queue.put_nowait(event)
        except Exception as e:  # noqa: BLE001
            logger.error("[wecom] presenter pump aborted err={}", e)
        finally:
            queue.put_nowait(_EVENTS_END)

    async def _consume(self, queue: asyncio.Queue) -> None:
        """消费循环：超时发保活帧（不取消上游），终态或哨兵后收尾。

        Args:
            queue: 事件队列
        """
        first = True
        while True:
            if first:
                timeout = self._first_frame_timeout
            else:
                timeout = self._keepalive_seconds
            try:
                item = await asyncio.wait_for(queue.get(), timeout)
            except asyncio.TimeoutError:
                await self._flush(keepalive=True)
                first = False
                continue
            if item is _EVENTS_END:
                break
            first = False
            await self.update(item)
            if isinstance(item, (SSEDoneEvent, SSEErrorEvent)):
                break
        await self.finalize()
```

- `_flush` 增 `keepalive` 形参与豁免：

```python
    async def _flush(self, *, keepalive: bool = False) -> None:
        """按节流与上限规则决定是否发送当前快照。

        保活帧（`keepalive=True`）豁免「内容未变跳过」「空白不发送」「帧数上限」
        与节流：长静默期累积正文与上一帧相同，不豁免则保活无从发出；保活即
        重发当前快照，不新造可见文案。

        Args:
            keepalive: 是否保活帧
        """
        if self._final or self._degraded:
            return
        carries_answer = self._has_answer()
        content = self._current_content()
        if not keepalive:
            if content == self._last_sent:
                return
            if carries_answer and self._frames_sent >= self._max_frames:
                return
            if self._monotonic() - self._last_sent_at < self._min_interval_seconds:
                return
        sent = await self._send(content, finish=False)
        if sent and carries_answer and not keepalive:
            self._frames_sent += 1
```

> 帧计数必须排除保活帧（`and not keepalive`）：否则长静默期的保活会把正文帧额度吃光，"保活帧不计入上限"被违反，正文反而更早触顶。

> `_send` 返回 `False` 时会置 `_degraded`，此后 `_flush` 直接早退（不再重试发送），消费侧只等哨兵/终态后收尾，故"发送失败"不会造成失败重试风暴。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: 24 passed。

- [ ] **Step 5: 全量回归**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 与 Step 9（Task 1）记录的基线一致，无新增失败。

- [ ] **Step 6: 文档登记（一事一档）**

`docs/agents/code-map.md`：在通道层落点处补两行——
- `src/channels/wecom/presenter.py`：企微流式投影层（事件流 → 企微帧；快照累积 / 节流 / 上限 / 保活）
- `src/config/wecom_presenter.py`：企微投影层可调参数与文案

`docs/agents/defensive-patterns.md`：在 SSE/流式类下追加一条**可复发缺陷**：

```markdown
### 投影层保活不得对上游在途取值施加超时取消
- **症状**：长静默期（澄清等待 / 出网调用）收不到任何事件；想靠 `asyncio.wait_for(anext(gen), t)` 加保活，结果超时点把上游生成器终结，后续取值抛 `StopAsyncIteration` —— 恰在需要保活时把长流静默截断。
- **根因**：`_subscribe_events` 是异步生成器，体内不捕 `CancelledError`；取消其 `anext` 会关掉生成器。
- **正确形态**：事件流以 `asyncio.Queue` 暴露（后台 pump 入队），消费侧只对 `queue.get()` 施加超时。
- **落点**：`src/channels/wecom/presenter.py`（`run` / `_pump` / `_consume`）。
```

- [ ] **Step 7: 提交**

```bash
git add src/channels/wecom/presenter.py tests/channels/test_wecom_presenter.py docs/agents/code-map.md docs/agents/defensive-patterns.md
git commit -m "feat(channels): 投影层加占位首帧与队列保活，登记 code-map/defensive-patterns" > /tmp/commit_stage2_t5.log 2>&1; echo RC=$?
```

---

## 本阶段覆盖对照（自我检查用，不入提交）

| 规范条目 | 落点 |
|---|---|
| tasks 2.1 `ReplySink` 可选 `feedback` | Task 1 |
| tasks 2.2 可配置项（footer/节流/帧上限/长度上限/保活） | Task 1 |
| 4.1 三段式骨架 | Task 2 |
| 4.2 快照累积 + 节流 + 未变/空白跳过 | Task 2 |
| 4.3 帧数与长度上限（保留尾部） | Task 3 |
| 4.5 无渲染事件丢弃且不中断 | Task 3 |
| 4.4 引用降级为文末来源 | Task 4 |
| 4.6 终态收尾（footer / 脱敏 / 转人工 / 空兜底） | Task 4 |
| 4.7 首帧 `feedback.id` | Task 4 |
| 4.8 流式失败降级为一次性收尾 | Task 4 |
| 4.11 空回答 / abstention 兜底 | Task 4 |
| 4.9 占位首帧 | Task 5 |
| 4.10 长流保活（队列 + 超时，不取消上游）+ 异常路径仍收尾 | Task 5 |
| 4.12 投影层单测（假 sink 覆盖各场景） | Task 2–5 累积（24 个用例） |

## 不在本阶段范围（留给后续阶段）

- 组 3 桥接 handler（会话 UUIDv5 / msgid 去重 / trace_id 生成与日志锚点 / `wecom_service` 换 handler）→ 阶段 3。
- 组 5 驱动可靠性补丁（认证等待 / 被顶停重连）、组 6 澄清回填与 1.5 答案应用下沉 → 阶段 4。
- 组 7 验证与文档（E2E / 灰度 / runbook）、组 0 Spike（E1–E13）→ 阶段 5。
- `feedback_event` 的消费与解析分流（tasks 3.8/3.9）：依赖 Spike E10 的字段结论，属阶段 3。
