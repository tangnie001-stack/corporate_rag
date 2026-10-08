# 企微接入 Agent 管线 — 阶段 3（桥接 handler）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把企微入站长连接消息真正喂进**站点同款 Agent 管线**（`services.turn_runner.start_turn`），并把其结构化事件流交给阶段 2 的 `WeComPresenter` 呈现，使三台机器人给出与站点一致的 Agent 回答。

**Architecture:** 新增 `channels/wecom/handler.py` 的 `RagChannelHandler`（`MessageHandler` 协议）——它只做五件事：**标识推导 → msgid 去重 → trace_id 三路 → 事件分流（事件帧旁路 / 旁路登记澄清触发者）→ 调 `start_turn` 并把事件流喂投影层**；编排本身（落库前置 / 原子闸门 / 失败语义 / 收尾）已在 `services.turn_runner`（阶段 1）。`wecom_service`（services 层）负责装配并把它接到**长连接**驱动上；**回调模式保持占位 handler**。

**Tech Stack:** Python 3.11+ / asyncio / loguru / pytest（`@pytest.mark.asyncio`）/ `uuid.uuid5` / LangGraph 侧无需改动。

**Spec:** `openspec/changes/wecom-agent-bridge/design.md`（**D1** 复用站点管线 / **D2** UUIDv5 会话标识 / **D9** trace_id 三路 / **D10** msgid 去重 / **D12** 下沉入口 / **D14** 澄清仅触发者 / **D15** 桥接仅长连接 / **D18** 脱敏 / **D19** 会话不隔离但可区分 / **D21** 消费 feedback_event）+ `specs/wecom-agent-bridge/spec.md`。执行者两份都要读；**SDK 实测行为以 `docs/agents/wecom-sdk-facts.md` 为准**。

**Worktree:** `/root/code/corporate_rag-wecom-stage3`（分支 `feat/wecom-agent-bridge-stage3`，基线 `23bd526`）。

## Global Constraints

以下为项目级硬约束，**每个任务的要求都隐含包含本节**；值一律照抄。

- **层间规则**：`channels/` **不得** import `api/`；`services/` 可以 import `channels/`（既有方向，`wecom_service` 就是这么装配驱动的），故 `channels/wecom/handler.py` **只允许**依赖 `src.channels.*`、`src.config.*`、`src.core.*`、`src.infra.llm.*`（trace_id/contextvar）、`src.services.{app_service,turn_runner}`（**叶子模块**，不得 import `src.services.wecom_service`——那会与 `wecom_service → handler` 形成环）、`src.utils.sse`、`loguru`、标准库。
- **文件与函数红线**：单文件 ≤ 400 行；单函数 ≤ 80 行。
- **代码风格**：不用三元表达式（写完整 if/else）；类型不确定处用显式 `isinstance` / `is not None`，不用 `getattr(x, "attr", default)` 兜底。
- **配置集中**：通道层参数与文案入 `src/config/wecom_channel.py`（新建）；`WECOM_NS` 与通道事件名入 `src/config/const.py`；投影层参数仍归 `src/config/wecom_presenter.py`。
- **日志**：英文 `k=v` + `[wecom]` 前缀；**不得 `print`**。脱敏：**原始异常文本只进日志，绝不进发给企微的内容**。
- **有界**：任何进程内累积结构都必须 TTL + 容量双淘汰（不得裸 `dict`）。
- **测试**：宿主侧一律 `POSTGRES_HOST=localhost pytest ...`；异步用例用 `@pytest.mark.asyncio`（本仓未开 `asyncio_mode=auto`）。**不得削弱既有断言**（本阶段只许改"符号来源/构造参数"这类等价调整）。
- **worktree 环境既有事实**：缺 gitignored 夹具 `data/test_docs/*` → `tests/parsers/` 的 `FileNotFoundError` 属**环境性**，别修；`.venv` / `.env` 已软链到主仓。
- **提交命令形状**：`git add` 单独一条命令并**先核对** `git diff --cached --name-only`，再 `git commit`（不要用 `add && commit` 串联：add 失败会静默吞掉 commit 与日志）。**注意 `openspec/` 是软链**，改 change 工件必须用真实路径 `docs/openspec/...`。
- **SDK 实测事实（不得凭猜测编码）**：`enter_chat` 的 req_id **不能**用于 `reply_stream`（实测 `errcode=846605`）⇒ 事件帧一律不回复；反馈回执字段路径 = `body.event.feedback_event.id`；被顶/认证/ack 等行为见 `docs/agents/wecom-sdk-facts.md`。

---

## 阶段地图（本文件只展开「阶段 3」）

| 阶段 | 范围（`tasks.md` 分组） | 状态 |
|---|---|---|
| 0 | Spike（E1–E13） | ✅ 已完成（确认点 A 已签） |
| 1 | 组 1 的 1.2/1.3/1.4：编排下沉 + 依赖注入 | ✅ 已合入（`cf7c83a`） |
| 2 | 组 2 + 组 4：通道协议与配置 + 输出投影层 | ✅ 已合入（`75e0aff`） |
| — | 组 5.2 被顶处置（驱动部分） | ✅ 已合入（`23bd526`） |
| **3（本文件）** | **组 3：桥接 handler（3.1–3.8；3.9 已删）** | 本次实施 |
| 4 | 组 5（5.1 认证等待/注册表语义）+ 组 6（澄清回填）+ 1.5（澄清答案下沉） | 未开始 |
| 5 | 组 7 验证与文档（E2E / 灰度 / runbook） | 未开始 |

**阶段 3 的验收面**：单测覆盖 3.1–3.8；`wecom_service` 长连接改用桥接 handler 且回调不受影响；**真实 e2e（确认点 B）** = 本地 app 连上 `dev` 后 @ 它给出站点同款 Agent 回答（见文末「确认点 B 的前置」）。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/channels/wecom/session.py` | 会话/用户标识派生（UUIDv5，36 字符）与可读标题 | **新建** |
| `src/channels/wecom/bounded_map.py` | 进程内有界 TTL 映射（msgid 去重与澄清触发者映射共用底座） | **新建** |
| `src/channels/wecom/handler.py` | `RagChannelHandler`：入站 → 管线 → 投影 | **新建** |
| `src/config/wecom_channel.py` | 通道层参数（去重/触发者映射的 TTL 与容量）与用户可见文案 | **新建** |
| `src/config/const.py` | `WECOM_NS`（固定字面 UUID）+ 通道事件名常量 | 修改 |
| `src/config/wecom_presenter.py` | `FEEDBACK_ID_ENABLED` 默认值翻转（E10 实测支持） | 修改 |
| `src/channels/wecom/presenter.py` | 收口：`_pump` 异常置脱敏错误、`ask_user` 显式丢弃、footer 实例层守卫、取消语义说明 | 修改 |
| `src/services/wecom_service.py` | 长连接装配桥接 handler（回调保持占位） | 修改 |
| `tests/channels/test_wecom_session.py` / `test_wecom_bounded_map.py` / `test_wecom_handler.py` | 各单元的测试 | **新建** |
| `tests/channels/test_wecom_presenter.py` / `tests/config/test_wecom_presenter.py` | 收口项与默认值的测试 | 修改 |
| `tests/services/test_wecom_service.py` | 装配测试（桥接 handler 接到长连接、回调不变） | 修改 |
| `docs/agents/logging-rules.md` / `docs/agents/code-map.md` | 前缀归属与落点登记 | 修改（Task 5） |

---

### Task 1: 投影层收口（阶段 2 评审延后项 + E10 默认值）

**Files:**
- Modify: `src/channels/wecom/presenter.py`
- Modify: `src/config/wecom_presenter.py`
- Modify: `tests/channels/test_wecom_presenter.py`
- Modify: `tests/config/test_wecom_presenter.py`

**Interfaces:**
- Consumes: 阶段 2 已有 API（`WeComPresenter(sink, trace_id, *, …, footer_enabled, feedback_enabled)`、`run(events)`、`WeComPresenterTexts`）。
- Produces: 无新 API；仅收口既有行为。行为变更点两条：**上游异常 → 终态帧带脱敏错误**；**`footer_enabled=False` 且 `feedback_enabled=False` 时 footer 仍强制开启**。

- [ ] **Step 1: 写四条失败测试（追加到 `tests/channels/test_wecom_presenter.py`）**

```python
@pytest.mark.asyncio
async def test_upstream_exception_yields_desensitized_terminal():
    async def _boom():
        yield SSETokenEvent(token="甲")
        raise RuntimeError("psycopg: connection refused to 10.0.0.5")

    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink, "trace_1", min_interval_seconds=0, footer_enabled=False
    )

    await presenter.run(_boom())

    final = sink.contents[-1]
    assert WeComPresenterTexts.ERROR_TEXT in final
    assert "psycopg" not in final
    assert "10.0.0.5" not in final
    assert sink.calls[-1][1] is True


@pytest.mark.asyncio
async def test_ask_user_event_produces_no_frame():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEAskUserEvent(questions=[{"id": "q1", "question": "?"}]),
                SSEDoneEvent(trace_id="trace_1"),
            ]
        )
    )

    # 契约：澄清事件不产帧（二期由组 6 处理）；帧数 = 1 中间帧 + 1 终态帧
    assert len(sink.calls) == 2
    assert sink.contents[-1].startswith("甲")


@pytest.mark.asyncio
async def test_footer_forced_on_when_feedback_unavailable_at_instance_level():
    sink = _RecordingSink()
    presenter = WeComPresenter(
        sink,
        "trace_abc",
        min_interval_seconds=0,
        footer_enabled=False,
        feedback_enabled=False,
    )

    await presenter.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))

    assert "trace_abc" in sink.contents[-1]


@pytest.mark.asyncio
async def test_cancelled_done_event_still_finalizes():
    sink = _RecordingSink()
    presenter = WeComPresenter(sink, "trace_1", min_interval_seconds=0)

    await presenter.run(
        _stream(
            [
                SSETokenEvent(token="甲"),
                SSEDoneEvent(trace_id="trace_1", cancelled=True),
            ]
        )
    )

    assert sink.contents[-1].startswith("甲")
    assert sink.calls[-1][1] is True
```

同文件顶部 import 需补 `SSEAskUserEvent`（若尚无）。

- [ ] **Step 2: 改一条既有测试的构造参数（等价调整）**

`test_trace_footer_in_final_frame_and_switchable` 里"footer 可关"的那一半，必须显式声明反馈标识**可用**，否则与"反馈不可用 ⇒ footer 强制开启"冲突：

```python
    sink2 = _RecordingSink()
    presenter2 = WeComPresenter(
        sink2,
        "trace_abc",
        min_interval_seconds=0,
        footer_enabled=False,
        feedback_enabled=True,
    )
    await presenter2.run(_stream([SSETokenEvent(token="甲"), SSEDoneEvent()]))
    assert "trace_abc" not in sink2.contents[-1]
```

断言不变，只补构造参数。

- [ ] **Step 3: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py -q`
Expected: **2 failed**（`test_upstream_exception_yields_desensitized_terminal`、`test_footer_forced_on_when_feedback_unavailable_at_instance_level`）。
另两条（`ask_user` 契约、取消语义）**改前改后都应通过**——它们是**契约固化**用例：钉住"澄清事件不产帧"与"`done(cancelled=True)` 仍收尾"这两条现状，防止后续被改坏。Step 2 的构造参数调整在改前也通过（那时 footer 由参数直控），它的意义在 Step 4 ③ 之后才显现。

- [ ] **Step 4: 实现收口（`src/channels/wecom/presenter.py`）**

① `_pump` 的异常分支置脱敏错误：

```python
        except Exception as e:  # noqa: BLE001
            # 上游抛异常（不是 error 事件路径）：置脱敏错误文案，收尾时由 finalize
            # 呈现；原始异常只进日志，绝不进发给企微的内容（D18）
            logger.error("[wecom] presenter pump aborted err={}", e)
            self._error = WeComPresenterTexts.ERROR_TEXT
        finally:
            queue.put_nowait(_EVENTS_END)
```

② `update` 增 `ask_user` 显式丢弃分支（与既有丢弃类并列）：

```python
        elif isinstance(event, SSEAskUserEvent):
            # 澄清事件不产帧：真正呈现与回填属组 6；本层只声明契约，
            # 触发者登记由 handler 旁路完成（见 handler.py）
            return
```

③ `__init__` 里 footer 的实例层守卫（把 SHALL 落到离用户最近处）：

```python
        self._footer_enabled = footer_enabled or not feedback_enabled
```

docstring 的 `footer_enabled` 说明补一句："传入 False 但反馈标识不可用时仍会强制开启（投影能力 SHALL）"。

④ `run` 的 docstring 补一句取消语义：

```
        取消由站点以 `SSEDoneEvent(cancelled=True)` 在带内送达，故本层不处理
        asyncio 级取消（外部取消 `run` 会走 `finally` 的泵清理，不发终态帧）。
```

- [ ] **Step 5: 翻转 `FEEDBACK_ID_ENABLED` 默认值（`src/config/wecom_presenter.py`）**

```python
# 首帧反馈标识是否可用：Spike E10 实测**可用**（回执帧 `body.event.feedback_event.id`
# 原样回传，见 docs/agents/wecom-sdk-facts.md），故默认开启；置 false 则退化为 footer+日志两路
FEEDBACK_ID_ENABLED: bool = os.getenv("WECOM_FEEDBACK_ID_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
```

并在 `tests/config/test_wecom_presenter.py` 追加：

```python
def test_feedback_id_enabled_by_default(monkeypatch):
    monkeypatch.delenv("WECOM_FEEDBACK_ID_ENABLED", raising=False)
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.FEEDBACK_ID_ENABLED is True
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_presenter.py tests/config/ -q`
Expected: 全 passed（presenter 用例数 25 → 29）。

- [ ] **Step 7: 提交**

```bash
git add src/channels/wecom/presenter.py src/config/wecom_presenter.py tests/channels/test_wecom_presenter.py tests/config/test_wecom_presenter.py
git commit -m "fix(channels): 投影层收口（上游异常脱敏收尾 / ask_user 显式丢弃 / footer 实例层守卫）并按 E10 默认开启反馈标识"
```

---

### Task 2: 会话与用户标识派生（UUIDv5）

**Files:**
- Modify: `src/config/const.py`（新增 `WECOM_NS` 与通道事件名常量）
- Create: `src/channels/wecom/session.py`
- Test: `tests/channels/test_wecom_session.py`

**Interfaces:**
- Consumes: 无（本任务无前置）。
- Produces:
  - `src.config.const.WECOM_NS: Final[uuid.UUID]`（**固定字面常量**）
  - `src.config.const.WECOM_EVENT_FEEDBACK: Final[str] = "feedback_event"`
  - `derive_session_id(*, bot_key: str, chattype: str, chatid: str | None, from_userid: str) -> str`
  - `derive_user_id(from_userid: str) -> str`
  - `build_session_title(*, bot_key: str, chattype: str, chatid: str | None, from_userid: str) -> str`

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/test_wecom_session.py`：

```python
"""会话/用户标识派生：36 字符、确定性、跨进程稳定（design D2）。"""

import subprocess
import sys

from src.channels.wecom.session import (
    build_session_title,
    derive_session_id,
    derive_user_id,
)


def test_session_id_is_36_chars_and_stable():
    first = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT1", from_userid="U1"
    )
    second = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT1", from_userid="U2"
    )
    assert len(first) == 36
    assert first == second  # 同群：不同成员得到同一会话


def test_single_chat_keys_on_userid():
    a = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    b = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U2"
    )
    c = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert a == c
    assert a != b  # 单聊：按人区分


def test_bot_key_participates_in_session_id():
    a = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    b = derive_session_id(
        bot_key="support", chattype="single", chatid=None, from_userid="U1"
    )
    assert a != b  # 三台机器人同一用户不共会话


def test_user_id_is_36_chars_and_deterministic():
    assert len(derive_user_id("U1")) == 36
    assert derive_user_id("U1") == derive_user_id("U1")
    assert derive_user_id("U1") != derive_user_id("U2")


def test_session_id_and_user_id_differ_for_same_identifier():
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert session_id != derive_user_id("U1")


def test_title_is_readable_and_bounded():
    title = build_session_title(
        bot_key="finance", chattype="group", chatid="CHAT9", from_userid="U9"
    )
    assert "finance" in title
    assert "CHAT9" in title
    assert len(title) <= 256  # sessions.title 列宽


def test_namespace_is_cross_process_stable():
    """WECOM_NS 必须是固定字面常量：换进程算出的 session_id 必须一致。"""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    code = (
        "from src.channels.wecom.session import derive_session_id;"
        "print(derive_session_id(bot_key='dev', chattype='single',"
        " chatid=None, from_userid='U1'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    expected = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert out.stdout.strip() == expected
```

顶部 import 需含 `import pathlib`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_session.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.channels.wecom.session'`。

- [ ] **Step 3: 加常量（`src/config/const.py`）**

在文件末尾「企业微信智能机器人回调」段之后追加（若顶部缺 `import uuid` / `from typing import Final` 则补上）：

```python
# ── 企业微信智能机器人通道 ──
# 会话/用户标识派生的固定命名空间：**必须是固定字面 UUID**（不得随机/进程内生成），
# 否则 session_id 每次重启都变、企微会话与历史每重启断链（design D2）。
WECOM_NS: Final[uuid.UUID] = uuid.UUID("a3f1c2d4-5e6b-4c7d-8e9f-0a1b2c3d4e5f")
# 反馈回执事件名（回执帧字段路径为 body.event.feedback_event.{id,type}；
# 实测见 docs/agents/wecom-sdk-facts.md 的 E10）
WECOM_EVENT_FEEDBACK: Final[str] = "feedback_event"
```

- [ ] **Step 4: 写实现（`src/channels/wecom/session.py`）**

```python
"""企微会话 / 用户标识派生（design D2）。

`session_id` / `user_id` 都会写进 `String(36)` 的列（sessions / conversation_history /
feedback），故必须派生为 36 字符的确定性标识；可读信息（bot_key / chatid / userid）
只进日志与 `sessions.title`。
"""

from __future__ import annotations

import uuid

from src.config.const import WECOM_NS


def derive_session_id(
    *, bot_key: str, chattype: str, chatid: str | None, from_userid: str
) -> str:
    """派生会话标识：群聊按 `chatid`、单聊按 `from_userid`。

    Args:
        bot_key: 机器人别名（三台机器人同一用户不共会话）
        chattype: 会话类型（"group" / "single"；非 group 一律按单聊）
        chatid: 群聊会话 id；单聊为 None
        from_userid: 触发者 userid

    Returns:
        36 字符 UUIDv5 字符串（同群/同人稳定）
    """
    if chattype == "group" and chatid:
        scope = "group"
        ident = chatid
    else:
        scope = "single"
        ident = from_userid
    return str(uuid.uuid5(WECOM_NS, f"wecom|{bot_key}|{scope}|{ident}"))


def derive_user_id(from_userid: str) -> str:
    """派生用户标识（`sessions.user_id` 契约亦为 UUID）。

    Args:
        from_userid: 触发者 userid（非超管场景为密文；密文同样稳定）

    Returns:
        36 字符 UUIDv5 字符串
    """
    return str(uuid.uuid5(WECOM_NS, f"wecom-user|{from_userid}"))


def build_session_title(
    *, bot_key: str, chattype: str, chatid: str | None, from_userid: str
) -> str:
    """构造可读会话标题（落 `sessions.title`，仅首次落库生效）。

    Args:
        bot_key: 机器人别名
        chattype: 会话类型
        chatid: 群聊会话 id
        from_userid: 触发者 userid

    Returns:
        形如 `[企微·finance] 群聊 CHAT9` 的标题
    """
    if chattype == "group":
        scope = "群聊"
        ident = chatid or ""
    else:
        scope = "单聊"
        ident = from_userid
    return f"[企微·{bot_key}] {scope} {ident}"
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_session.py -q`
Expected: 7 passed。

- [ ] **Step 6: 提交**

```bash
git add src/config/const.py src/channels/wecom/session.py tests/channels/test_wecom_session.py
git commit -m "feat(channels): 企微会话/用户标识按 UUIDv5 派生（固定 WECOM_NS，36 字符稳定）"
```

---

### Task 3: 通道配置 + 有界 TTL 映射

**Files:**
- Create: `src/config/wecom_channel.py`
- Create: `src/channels/wecom/bounded_map.py`
- Test: `tests/channels/test_wecom_bounded_map.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `src.config.wecom_channel`：`DEDUP_TTL_SECONDS` / `DEDUP_CAPACITY` / `TRIGGER_MAP_TTL_SECONDS` / `TRIGGER_MAP_CAPACITY`；`WeComChannelTexts.{BUSY_TEXT, UNSUPPORTED_TEXT}`
  - `src.channels.wecom.bounded_map.BoundedTtlMap`：`__init__(*, capacity, ttl_seconds, monotonic=time.monotonic)`、`mark_if_new(key: str, value: str) -> bool`、`put(key: str, value: str) -> None`、`get(key: str) -> str | None`、`__len__() -> int`

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/test_wecom_bounded_map.py`：

```python
"""有界 TTL 映射：TTL 淘汰、容量淘汰、去重语义（design D10/D14）。"""

from src.channels.wecom.bounded_map import BoundedTtlMap


def _clock():
    """可控时钟：返回 (取当前值, 前进) 两个可调用对象。"""
    now = {"t": 1000.0}

    def read() -> float:
        return now["t"]

    def advance(seconds: float) -> None:
        now["t"] += seconds

    return read, advance


def test_mark_if_new_reports_first_sight_only():
    read, _advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    assert table.mark_if_new("M1", "") is True
    assert table.mark_if_new("M1", "") is False
    assert table.mark_if_new("M2", "") is True


def test_ttl_eviction_allows_reprocessing_after_window():
    read, advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    assert table.mark_if_new("M1", "") is True
    advance(101.0)
    assert table.mark_if_new("M1", "") is True  # 过期后视为新消息


def test_capacity_eviction_drops_oldest():
    table = BoundedTtlMap(capacity=2, ttl_seconds=1000.0, monotonic=lambda: 1000.0)

    table.mark_if_new("M1", "")
    table.mark_if_new("M2", "")
    table.mark_if_new("M3", "")

    assert len(table) == 2
    assert table.mark_if_new("M1", "") is True  # 最旧已被淘汰


def test_put_get_roundtrip_and_expiry():
    read, advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    table.put("S1", "U1")
    assert table.get("S1") == "U1"
    advance(101.0)
    assert table.get("S1") is None


def test_stays_bounded_under_many_writes():
    table = BoundedTtlMap(capacity=5, ttl_seconds=10_000.0, monotonic=lambda: 0.0)

    for index in range(50):
        table.put(f"K{index}", "V")

    assert len(table) == 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_bounded_map.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.channels.wecom.bounded_map'`。

- [ ] **Step 3: 写配置模块（`src/config/wecom_channel.py`）**

```python
"""企微**通道层**的参数与用户可见文案（design D10/D14）。

与投影层参数分档：本模块放通道编排关心的时间窗与容量；投影层的节流/上限/保活
在 `src/config/wecom_presenter.py`。两者都独立于 `settings.py`（385 行，逼近红线）。
"""

import os

# ── msgid 去重（D10）──
# 去重窗口（秒）：窗口内同 msgid 视为重推并丢弃
DEDUP_TTL_SECONDS: float = float(os.getenv("WECOM_DEDUP_TTL_SECONDS", "600"))
# 去重表容量上限（超出淘汰最旧）
DEDUP_CAPACITY: int = int(os.getenv("WECOM_DEDUP_CAPACITY", "500"))

# ── 澄清"会话 → 触发者"登记（D14）──
# 登记存活时间（秒）：须 ≥ ask_user 等待超时（ASK_USER_TIMEOUT=120s），留清理余量
TRIGGER_MAP_TTL_SECONDS: float = float(
    os.getenv("WECOM_TRIGGER_MAP_TTL_SECONDS", "300")
)
# 登记表容量上限（超出淘汰最旧）
TRIGGER_MAP_CAPACITY: int = int(os.getenv("WECOM_TRIGGER_MAP_CAPACITY", "500"))


class WeComChannelTexts:
    """企微通道层对用户可见的文案。"""

    # 同会话已有进行中的生成时回给用户的提示（替站点 409）
    BUSY_TEXT: str = "正在处理上一条消息，请稍候再发。"
    # 非文本消息（图片/语音/文件等）的提示：管线只吃文本 query
    UNSUPPORTED_TEXT: str = "暂只支持文字提问，请把问题打成文字发给我。"
```

- [ ] **Step 4: 写实现（`src/channels/wecom/bounded_map.py`）**

```python
"""进程内有界 TTL 映射：TTL + 容量双淘汰（design D10/D14）。

两处共用：msgid 去重（`mark_if_new`）与澄清"会话 → 触发者"登记（`put`/`get`）。
裸 dict 会随消息量/会话数无界增长，故所有累积结构都必须走本类。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable


class BoundedTtlMap:
    """按写入时间做 TTL 淘汰、超容量时淘汰最旧条目的有界映射。"""

    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """初始化。

        Args:
            capacity: 容量上限（超出淘汰最旧）
            ttl_seconds: 条目存活秒数（自写入时刻起算，命中不刷新）
            monotonic: 单调时钟（测试注入以稳定断言过期）
        """
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def mark_if_new(self, key: str, value: str) -> bool:
        """记录并判断是否首次见到该 key。

        Args:
            key: 判重键（如 msgid）
            value: 随条目保存的值（判重场景可传空串）

        Returns:
            True 表示此前未见（已记录）；False 表示窗口内已见过
        """
        now = self._monotonic()
        self._evict_expired(now)
        if key in self._items:
            return False
        self._write(key, value, now)
        return True

    def put(self, key: str, value: str) -> None:
        """写入或覆盖一个条目。

        Args:
            key: 键
            value: 值
        """
        now = self._monotonic()
        self._evict_expired(now)
        self._write(key, value, now)

    def get(self, key: str) -> str | None:
        """读取条目；不存在或已过期返回 None。

        Args:
            key: 键

        Returns:
            值或 None
        """
        now = self._monotonic()
        self._evict_expired(now)
        item = self._items.get(key)
        if item is None:
            return None
        return item[1]

    def __len__(self) -> int:
        """当前条目数（不含未清理的过期项以外的新增）。"""
        return len(self._items)

    def _write(self, key: str, value: str, now: float) -> None:
        """写入并对容量做淘汰。"""
        self._items[key] = (now, value)
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def _evict_expired(self, now: float) -> None:
        """从最旧端淘汰已过期条目。"""
        while self._items:
            oldest_key = next(iter(self._items))
            written_at = self._items[oldest_key][0]
            if now - written_at <= self._ttl_seconds:
                return
            self._items.popitem(last=False)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_bounded_map.py -q`
Expected: 5 passed。

- [ ] **Step 6: 提交**

```bash
git add src/config/wecom_channel.py src/channels/wecom/bounded_map.py tests/channels/test_wecom_bounded_map.py
git commit -m "feat(channels): 通道层配置模块 + 有界 TTL 映射（去重与触发者登记共用底座）"
```

---

### Task 4: `RagChannelHandler`（桥接核心）

**Files:**
- Create: `src/channels/wecom/handler.py`
- Test: `tests/channels/test_wecom_handler.py`

**Interfaces:**
- Consumes: `derive_session_id` / `derive_user_id` / `build_session_title`（Task 2）；`BoundedTtlMap`（Task 3）；`wecom_channel.WeComChannelTexts`（Task 3）；`const.WECOM_EVENT_FEEDBACK`（Task 2）；`WeComPresenter` / `WeComPresenterTexts`（阶段 2 + Task 1）；`services.turn_runner.{start_turn, TurnBusy, TurnHandle}`；`services.app_service.get_app_service`；`new_trace_id` / `current_trace_id`。
- Produces:
  - `RagChannelHandler(*, start_turn, get_service, resolve_bot_key, dedup, triggers, kb_id="")`，可调用（`MessageHandler` 协议）
  - `RagChannelHandler.build_default(*, start_turn, get_service, resolve_bot_key) -> RagChannelHandler`（按配置装配两张表）

- [ ] **Step 1: 写失败测试**

创建 `tests/channels/test_wecom_handler.py`：

```python
"""桥接 handler 单测：标识推导、去重、事件分流、TurnBusy 翻译、投影接线。"""

from typing import Any

import pytest

from src.channels.base import InboundMessage
from src.channels.wecom.bounded_map import BoundedTtlMap
from src.channels.wecom.handler import RagChannelHandler
from src.config.const import WECOM_EVENT_FEEDBACK
from src.config.wecom_channel import WeComChannelTexts
from src.services import turn_runner
from src.utils.sse import SSEDoneEvent, SSETokenEvent


class _Sink:
    """记录回复的假 sink。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.calls.append((content, finish))


class _FakeTurnHandle:
    def __init__(self, session_id: str, events: list[Any]) -> None:
        self.session_id = session_id
        self.events = _aiter(events)


async def _aiter(events: list[Any]):
    for event in events:
        yield event


def _msg(**overrides: Any) -> InboundMessage:
    base = {
        "msgid": "M1",
        "aibotid": "AIB1",
        "chatid": None,
        "chattype": "single",
        "from_userid": "U1",
        "msgtype": "text",
        "text": "你好",
        "event_type": None,
        "raw": {},
    }
    base.update(overrides)
    return InboundMessage(**base)


def _handler(*, start_turn=None, events=None):
    """构造被测 handler，返回 (handler, 记录用的容器)。"""
    recorded: dict[str, Any] = {"start_turn_calls": []}
    if events is None:
        events = [SSETokenEvent(token="甲"), SSEDoneEvent()]

    async def _default_start_turn(svc, **kwargs):
        recorded["start_turn_calls"].append(kwargs)
        return _FakeTurnHandle(kwargs["session_id"], events)

    async def _get_service():
        return object()

    def _resolve_bot_key(aibotid: str) -> str | None:
        if aibotid == "AIB1":
            return "dev"
        return None

    handler = RagChannelHandler(
        start_turn=start_turn or _default_start_turn,
        get_service=_get_service,
        resolve_bot_key=_resolve_bot_key,
        dedup=BoundedTtlMap(capacity=10, ttl_seconds=600.0),
        triggers=BoundedTtlMap(capacity=10, ttl_seconds=300.0),
        kb_id="",
    )
    return handler, recorded


@pytest.mark.asyncio
async def test_text_message_runs_turn_and_replies():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(), sink)

    assert len(recorded["start_turn_calls"]) == 1
    call = recorded["start_turn_calls"][0]
    assert call["kb_id"] == ""
    assert call["query"] == "你好"
    assert call["title"].startswith("[企微·dev]")
    assert len(call["session_id"]) == 36
    assert len(call["user_id"]) == 36
    assert sink.calls[-1][1] is True
    assert sink.calls[-1][0].startswith("甲")


@pytest.mark.asyncio
async def test_unknown_bot_is_ignored():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(aibotid="UNKNOWN"), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


@pytest.mark.asyncio
async def test_duplicate_msgid_is_dropped():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(), sink)
    first_call_count = len(sink.calls)
    await handler(_msg(), sink)

    assert len(recorded["start_turn_calls"]) == 1
    assert len(sink.calls) == first_call_count  # 重推不产生任何新帧


@pytest.mark.asyncio
async def test_turn_busy_replies_busy_text():
    async def _busy(svc, **kwargs):
        raise turn_runner.TurnBusy()

    handler, _recorded = _handler(start_turn=_busy)
    sink = _Sink()

    await handler(_msg(), sink)

    assert sink.calls == [(WeComChannelTexts.BUSY_TEXT, True)]


@pytest.mark.asyncio
async def test_non_text_message_gets_unsupported_hint():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(msgtype="image", text=None), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == [(WeComChannelTexts.UNSUPPORTED_TEXT, True)]


@pytest.mark.asyncio
async def test_event_frame_does_not_start_turn():
    handler, recorded = _handler()
    sink = _Sink()

    await handler(_msg(msgtype="event", text=None, event_type="enter_chat"), sink)

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


@pytest.mark.asyncio
async def test_feedback_event_does_not_start_turn():
    handler, recorded = _handler()
    sink = _Sink()
    raw = {
        "event": {
            "eventtype": WECOM_EVENT_FEEDBACK,
            "feedback_event": {"id": "trace_abc", "type": 1},
        }
    }

    await handler(
        _msg(msgtype="event", text=None, event_type=WECOM_EVENT_FEEDBACK, raw=raw),
        sink,
    )

    assert recorded["start_turn_calls"] == []
    assert sink.calls == []


def test_extract_feedback_id_from_real_frame():
    """字段路径以 Spike E10 实测帧为准：body.event.feedback_event.id。"""
    raw = {
        "msgid": "76a963c5",
        "msgtype": "event",
        "event": {
            "eventtype": "feedback_event",
            "feedback_event": {"id": "trace_spike_e10", "type": 1},
        },
    }
    assert extract_feedback_id(raw) == "trace_spike_e10"


def test_extract_feedback_id_tolerates_malformed_frames():
    assert extract_feedback_id({}) is None
    assert extract_feedback_id({"event": None}) is None
    assert extract_feedback_id({"event": {"feedback_event": {}}}) is None
    assert extract_feedback_id({"event": {"feedback_event": {"type": 1}}}) is None


@pytest.mark.asyncio
async def test_ask_user_event_registers_trigger_and_does_not_reply():
    ask_user = SSEAskUserEvent(questions=[{"id": "q1", "question": "?"}])
    handler, _recorded = _handler(events=[ask_user, SSEDoneEvent()])
    sink = _Sink()

    await handler(_msg(chattype="group", chatid="CHAT9"), sink)

    session_id = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT9", from_userid="U1"
    )
    assert handler.registered_trigger(session_id) == "U1"
    # 澄清事件不产帧（二期由组 6 呈现问题）：帧 = 终态帧
    assert len(sink.calls) == 1
    assert sink.calls[-1][1] is True
```

文件顶部 import 需含：`from src.channels.wecom.handler import RagChannelHandler, extract_feedback_id`、`from src.channels.wecom.session import derive_session_id`、`from src.utils.sse import SSEAskUserEvent, SSEDoneEvent, SSETokenEvent`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_handler.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.channels.wecom.handler'`。

- [ ] **Step 3: 写实现（`src/channels/wecom/handler.py`）**

```python
"""企微入站 → 站点同款 Agent 管线的桥接 handler（design D1/D9/D12/D14/D19/D21）。

本层只做五件事：标识推导 → msgid 去重 → trace_id 三路 → 事件分流 → 调 `start_turn`
并把事件流交给投影层。单轮生成的编排（落库前置 / 原子闸门 / 失败语义 / 收尾）在
`services.turn_runner`（D12），呈现细节在 `channels/wecom/presenter.py`（D4）。

依赖方向：本模块可 import `services.{app_service,turn_runner}` 这类叶子模块，但
**不得** import `src.services.wecom_service`（后者要 import 本模块，会成环）。
仅用于长连接路径（回调 sink 只保留最后一次回包，跑不了几十秒的回合，D15）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from loguru import logger

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.bounded_map import BoundedTtlMap
from src.channels.wecom.presenter import WeComPresenter
from src.channels.wecom.session import (
    build_session_title,
    derive_session_id,
    derive_user_id,
)
from src.config import wecom_channel
from src.config.const import WECOM_EVENT_FEEDBACK
from src.config.wecom_presenter import WeComPresenterTexts
from src.core.logging import encode_value
from src.infra.llm.trace_context import current_trace_id
from src.infra.llm.tracing import new_trace_id
from src.services import turn_runner
from src.services.app_service import AppService
from src.utils.sse import SSEAskUserEvent, SSEEvent

# 单轮生成入口签名（生产传 `services.turn_runner.start_turn`；测试注入替身）
StartTurnCallable = Callable[..., Awaitable[turn_runner.TurnHandle]]
GetServiceCallable = Callable[[], Awaitable[AppService]]


def extract_feedback_id(raw: dict) -> str | None:
    """从反馈回执的原始帧取出承载值（即我们带出去的 trace_id）。

    字段路径以 Spike E10 实测帧为准：`body.event.feedback_event.id`
    （见 `docs/agents/wecom-sdk-facts.md`）。

    Args:
        raw: 入站原始明文（`InboundMessage.raw`）

    Returns:
        trace_id 字符串；结构缺失或无 id 时返回 None
    """
    event = raw.get("event")
    if not isinstance(event, dict):
        return None
    payload = event.get("feedback_event")
    if not isinstance(payload, dict):
        return None
    feedback_id = payload.get("id")
    if not isinstance(feedback_id, str) or not feedback_id:
        return None
    return feedback_id


class RagChannelHandler:
    """把企微入站消息喂进站点同款 Agent 管线，并把事件流投影成企微回复。"""

    def __init__(
        self,
        *,
        start_turn: StartTurnCallable,
        get_service: GetServiceCallable,
        resolve_bot_key: Callable[[str], str | None],
        dedup: BoundedTtlMap,
        triggers: BoundedTtlMap,
        kb_id: str = "",
    ) -> None:
        """初始化。

        Args:
            start_turn: 单轮生成入口（生产传 `services.turn_runner.start_turn`）
            get_service: AppService 取用器（生产传 `services.app_service.get_app_service`）
            resolve_bot_key: aibotid → bot_key（生产传 `wecom_service._resolve_bot_key`）
            dedup: msgid 去重表（有界）
            triggers: 澄清"会话 → 触发者"登记表（有界）
            kb_id: 本轮默认知识库（空串 = 不检索，与站点逻辑一致）
        """
        self._start_turn = start_turn
        self._get_service = get_service
        self._resolve_bot_key = resolve_bot_key
        self._dedup = dedup
        self._triggers = triggers
        self._kb_id = kb_id

    @classmethod
    def build_default(
        cls,
        *,
        start_turn: StartTurnCallable,
        get_service: GetServiceCallable,
        resolve_bot_key: Callable[[str], str | None],
    ) -> "RagChannelHandler":
        """按配置装配默认实例（去重/触发者映射取 `wecom_channel` 的窗口与容量）。

        Args:
            start_turn: 单轮生成入口
            get_service: AppService 取用器
            resolve_bot_key: aibotid → bot_key

        Returns:
            配置齐备的 RagChannelHandler
        """
        return cls(
            start_turn=start_turn,
            get_service=get_service,
            resolve_bot_key=resolve_bot_key,
            dedup=BoundedTtlMap(
                capacity=wecom_channel.DEDUP_CAPACITY,
                ttl_seconds=wecom_channel.DEDUP_TTL_SECONDS,
            ),
            triggers=BoundedTtlMap(
                capacity=wecom_channel.TRIGGER_MAP_CAPACITY,
                ttl_seconds=wecom_channel.TRIGGER_MAP_TTL_SECONDS,
            ),
        )

    def registered_trigger(self, session_id: str) -> str | None:
        """读澄清触发者登记（供组 6 回填校验来源；当前仅登记）。"""
        return self._triggers.get(session_id)

    async def __call__(self, msg: InboundMessage, sink: ReplySink) -> None:
        """处理一条入站消息（`MessageHandler` 协议）。

        Args:
            msg: 统一入站事件
            sink: 回复出口
        """
        bot_key = self._resolve_bot_key(msg.aibotid)
        if bot_key is None:
            logger.warning(
                "[wecom] unknown bot aibotid={}", encode_value(msg.aibotid)
            )
            return
        if not self._dedup.mark_if_new(msg.msgid, ""):
            logger.info(
                "[wecom] duplicate msgid dropped bot_key={} msgid={}",
                encode_value(bot_key),
                msg.msgid,
            )
            return
        if msg.msgtype == "event":
            self._handle_event(bot_key, msg)
            return
        if not msg.text:
            logger.info(
                "[wecom] unsupported msgtype bot_key={} msgtype={}",
                encode_value(bot_key),
                msg.msgtype,
            )
            await sink.reply_stream(
                wecom_channel.WeComChannelTexts.UNSUPPORTED_TEXT, finish=True
            )
            return
        await self._run_turn(bot_key, msg, sink)

    def _handle_event(self, bot_key: str, msg: InboundMessage) -> None:
        """处理事件帧：反馈回执落日志；其余事件不回复。

        事件帧一律不回复：实测 `enter_chat` 的 req_id 不能用于 `reply_stream`
        （`errcode=846605`），欢迎语须走 `reply_welcome`（见 sdk-facts 的「其他实测事实」）。

        Args:
            bot_key: 机器人别名
            msg: 入站事件
        """
        if msg.event_type != WECOM_EVENT_FEEDBACK:
            logger.debug(
                "[wecom] event ignored bot_key={} event_type={}",
                encode_value(bot_key),
                msg.event_type,
            )
            return
        feedback_id = extract_feedback_id(msg.raw)
        if feedback_id is None:
            logger.warning(
                "[wecom] feedback frame without id bot_key={} msgid={}",
                encode_value(bot_key),
                msg.msgid,
            )
            return
        logger.info(
            "[wecom] feedback received bot_key={} trace_id={} msgid={}",
            encode_value(bot_key),
            encode_value(feedback_id),
            msg.msgid,
        )

    async def _run_turn(
        self, bot_key: str, msg: InboundMessage, sink: ReplySink
    ) -> None:
        """跑一轮：派生标识 → 生成并设置 trace_id → 起生成 → 投影。

        Args:
            bot_key: 机器人别名
            msg: 入站消息（`text` 非空）
            sink: 回复出口
        """
        session_id = derive_session_id(
            bot_key=bot_key,
            chattype=msg.chattype,
            chatid=msg.chatid,
            from_userid=msg.from_userid,
        )
        user_id = derive_user_id(msg.from_userid)
        trace_id = new_trace_id()
        token = current_trace_id.set(trace_id)
        try:
            logger.info(
                "[wecom] inbound bot_key={} msgid={} chattype={} session_id={}"
                " trace_id={} text_len={}",
                encode_value(bot_key),
                msg.msgid,
                msg.chattype,
                encode_value(session_id),
                encode_value(trace_id),
                len(msg.text or ""),
            )
            svc = await self._get_service()
            handle = await self._start_turn(
                svc,
                session_id=session_id,
                kb_id=self._kb_id,
                query=msg.text,
                user_id=user_id,
                title=build_session_title(
                    bot_key=bot_key,
                    chattype=msg.chattype,
                    chatid=msg.chatid,
                    from_userid=msg.from_userid,
                ),
            )
        except turn_runner.TurnBusy:
            logger.warning(
                "[wecom] session busy bot_key={} session_id={} trace_id={}",
                encode_value(bot_key),
                encode_value(session_id),
                encode_value(trace_id),
            )
            await sink.reply_stream(
                wecom_channel.WeComChannelTexts.BUSY_TEXT, finish=True
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[wecom] turn start failed bot_key={} session_id={} err={}",
                encode_value(bot_key),
                encode_value(session_id),
                e,
            )
            await sink.reply_stream(WeComPresenterTexts.ERROR_TEXT, finish=True)
            return
        finally:
            current_trace_id.reset(token)

        presenter = WeComPresenter(sink, trace_id)
        await presenter.run(
            self._tap(handle.events, session_id=session_id, from_userid=msg.from_userid)
        )

    async def _tap(
        self,
        events: AsyncIterator[SSEEvent],
        *,
        session_id: str,
        from_userid: str,
    ) -> AsyncIterator[SSEEvent]:
        """旁路事件流：澄清挂起时登记"会话 → 触发者"，其余原样透传。

        Args:
            events: 上游结构化事件流
            session_id: 本会话标识
            from_userid: 触发者 userid

        Yields:
            原样透传的事件
        """
        async for event in events:
            if isinstance(event, SSEAskUserEvent):
                self._triggers.put(session_id, from_userid)
                logger.info(
                    "[wecom] clarify pending registered session_id={} trace_id={}",
                    encode_value(session_id),
                    encode_value(current_trace_id.get() or ""),
                )
            yield event
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_wecom_handler.py -q`
Expected: 10 passed。

- [ ] **Step 5: 全量回归**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 通过集合与动手前基线一致（唯一允许的失败是 `tests/parsers/` 的环境性缺失）。

- [ ] **Step 6: 提交**

```bash
git add src/channels/wecom/handler.py tests/channels/test_wecom_handler.py
git commit -m "feat(channels): 新增 RagChannelHandler（标识/去重/trace_id/事件分流/接投影层）"
```

---

### Task 5: 装配到 `wecom_service` + 文档登记

**Files:**
- Modify: `src/services/wecom_service.py`
- Modify: `tests/services/test_wecom_service.py`
- Modify: `docs/agents/logging-rules.md`（`[wecom]` 前缀归属补新模块）
- Modify: `docs/agents/code-map.md`（登记新的通道层模块落点）

**Interfaces:**
- Consumes: `RagChannelHandler.build_default`（Task 4）；`turn_runner.start_turn`；`app_service.get_app_service`；`_resolve_bot_key`（既有）。
- Produces: `wecom_service._build_bridge_handler() -> RagChannelHandler`；长连接模式下的驱动用桥接 handler，`callback` 模式仍用 `_default_handler`。

- [ ] **Step 1: 写失败测试（追加到 `tests/services/test_wecom_service.py`）**

```python
@pytest.mark.asyncio
async def test_long_connection_uses_bridge_handler(monkeypatch):
    """长连接改用桥接 handler（RagChannelHandler），不再用占位 handler。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    created = _install_stub(monkeypatch)

    await wecom_service.start()

    handler = created[0].handler
    assert isinstance(handler, RagChannelHandler)


@pytest.mark.asyncio
async def test_callback_mode_keeps_placeholder_handler(monkeypatch):
    """回调模式仍是占位 handler（桥接仅长连接，design D15）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "t")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "a" * 43)

    captured: dict[str, object] = {}
    real_driver = wecom_service.CallbackDriver

    def _factory(crypto, handler):
        captured["handler"] = handler
        return real_driver(crypto, handler)

    monkeypatch.setattr(wecom_service, "CallbackDriver", _factory)

    await wecom_service.start()

    assert captured["handler"] is wecom_service._default_handler
```

文件顶部 import 需补：`from src.channels.wecom.handler import RagChannelHandler`。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -q`
Expected: 2 failed（长连接 handler 仍是 `_default_handler`；`RagChannelHandler` 未被引用）。

- [ ] **Step 3: 实现装配（`src/services/wecom_service.py`）**

顶部 import 增：

```python
from src.channels.wecom.handler import RagChannelHandler
from src.services.app_service import get_app_service
from src.services import turn_runner
```

新增构造函数（放在 `_default_handler` 之后）：

```python
def _build_bridge_handler() -> RagChannelHandler:
    """构造长连接用的桥接 handler（每次 start 重建：去重/触发者表随进程生命周期）。

    依赖注入而非在通道层 import 本模块：`channels/wecom/handler.py` 只依赖
    `services` 的叶子模块，避免与 `wecom_service → handler` 形成环。

    Returns:
        配置齐备的 RagChannelHandler
    """
    return RagChannelHandler.build_default(
        start_turn=turn_runner.start_turn,
        get_service=get_app_service,
        resolve_bot_key=_resolve_bot_key,
    )
```

`start()` 的长连接分支改为使用它：

```python
    _bot_key_by_aibotid = {bot.bot_id: bot.key for bot in bots}
    _drivers = {}
    bridge_handler = _build_bridge_handler()
    for bot in bots:
        driver = LongConnectionDriver(bot.bot_id, bot.secret, bridge_handler)
```

（`callback` 分支的 `CallbackDriver(crypto, _default_handler)` **保持不变**。）

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/ tests/channels/ -q`
Expected: 全 passed（含既有 19 条 `test_wecom_service` 用例）。

- [ ] **Step 5: 文档登记（一事一档）**

`docs/agents/logging-rules.md` 的「前缀主表」里，把 `[wecom]` 行的归属补全为：

```
| `[wecom]` | 企业微信智能机器人通道（services/wecom_service + channels/wecom/{callback,long_connection,presenter,handler,session,bounded_map}） |
```

`docs/agents/code-map.md` 的通道层落点补四行：

```
> - `src/channels/wecom/handler.py`：入站 → 站点同款 Agent 管线的桥接 handler（标识/去重/trace_id/事件分流）
> - `src/channels/wecom/session.py`：企微会话与用户标识派生（UUIDv5）
> - `src/channels/wecom/bounded_map.py`：有界 TTL 映射（msgid 去重与澄清触发者登记共用）
> - `src/config/wecom_channel.py`：企微通道层参数（去重/触发者登记的 TTL 与容量）与通道文案
```

- [ ] **Step 6: 全量回归 + 提交**

Run: `POSTGRES_HOST=localhost pytest tests/ -q`
Expected: 与基线一致（仅 `tests/parsers/` 环境性失败）。

```bash
git add src/services/wecom_service.py tests/services/test_wecom_service.py docs/agents/logging-rules.md docs/agents/code-map.md
git commit -m "feat(services): 企微长连接装配桥接 handler（回调保持占位），并登记通道层落点"
```

---

## 本阶段覆盖对照（自我检查用，不入提交）

| 规范条目 | 落点 |
|---|---|
| 3.1 会话/用户标识（UUIDv5 + 固定 `WECOM_NS` + 跨进程稳定） | Task 2 |
| 3.2 msgid 去重（TTL + 上限双淘汰） | Task 3（底座）+ Task 4（接线） |
| 3.3 trace_id 生成 + set + 日志锚点（含 bot_key/msgid） | Task 4 |
| 3.4 `RagChannelHandler` 调 `start_turn` + 订阅 + 交投影层（kb 传空） | Task 4 |
| 3.5 并发冲突由 `start_turn` 闸门保证，通道只译 `TurnBusy` | Task 4 |
| 3.6 `wecom_service` 长连接换桥接 handler；callback 保持占位 | Task 5 |
| 3.7 澄清挂起登记 会话→触发者（TTL + 上限） | Task 3（底座）+ Task 4（旁路登记） |
| 3.8 消费 `feedback_event`（字段路径已实测） | Task 4 |
| 3.9 事件帧解析分流 | **已删**（Spike E10 实证不需要） |
| 阶段 2 延后项：`_pump` 异常置 `_error` / `ask_user` 显式丢弃 / footer 实例层守卫 / 取消语义 | Task 1 |
| 桥接 handler 必须跳过事件帧 | Task 4（`_handle_event` 不回复） |

## 不在本阶段范围（留给后续阶段）

- **组 5（5.1）**：认证等待（等 `authenticated`、判据 `errcode=853000`）、等待上界与并行启动、注册表语义拆分（`_drivers` 收所有已启动者、锚点只计认证就绪）、以及**消费 `is_displaced`**（被顶后不计入就绪）→ 阶段 4。
- **组 6 + 1.5**：澄清的**呈现**（文本回填：把问题渲染成"请回答 1)…"）、**仅触发者**回填、答案应用下沉 `resolve_clarify_answer` 并双写 → 阶段 4。本阶段只做**触发者登记**（3.7），不呈现问题。
- **组 7**：真实 e2e（确认点 B/C/D）、灰度、runbook → 阶段 5。
- 人设/KB 按机器人绑定（D22）→ 后续 change。

## 确认点 B 的前置（本阶段做完后执行，不属本文件任务）

要让本地 app 真的连上 `dev` 以验证「@ 它给出站点同款回答」：

1. 本地 `.env`：`WECOM_BOT_ENABLED=true`、`WECOM_BOTS` **只保留 `dev` 一台**（避免误连 `support`/`finance`）。
2. 生产 app 保持**停止**（`docker compose -f docker-compose.image.yml stop app`），否则两边互顶——被顶处置虽已修，但会互相抢归属。
3. 从 **worktree** 起本地服务时注意：compose 工程名/容器名/端口写死，会把 `app`/`nginx` 重建成指向 worktree 的 `src`（见 `cookbook.md`「并行会话（worktree）」）；`--reload` 在本机会 OOM，长跑改手工 `uvicorn`。
4. 判据：@ `dev` 后气泡出现**站点同款**回答（含检索/引用/澄清/委派的真实行为），且容器日志有 `[wecom] inbound bot_key=dev ... trace_id=...` 锚点。
