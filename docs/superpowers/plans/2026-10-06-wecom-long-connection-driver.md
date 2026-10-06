# 企微长连接驱动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增企业微信智能机器人**长连接**驱动（封装官方 `aibot` SDK），与既有**回调**驱动并存、由 `WECOM_BOT_MODE` 切换；仍返回写死回复，不接业务。

**Architecture:** 新增 `src/channels/wecom/long_connection.py`（`LongConnectionDriver` + `_WsSink`），实现既有 `ChannelDriver` 接口；**复用** `parse_inbound`、`InboundMessage`、`ReplySink`、`MessageHandler`、`_default_handler` 与 `main.py` lifespan（不改）。`wecom_service` 按 `WECOM_BOT_MODE` 构造对应驱动；回调路由在非 callback 模式返回 404。

**Tech Stack:** Python 3.12 / FastAPI 0.138 / 官方 `wecom-aibot-python-sdk==1.0.2`（导入名 `aibot`）/ pytest + monkeypatch（mock `WSClient`，不发真实网络）。

**Spec:** `docs/superpowers/specs/2026-10-06-wecom-long-connection-driver-design.md`（本 plan 的每个"为什么"以它为准）

## Global Constraints

- 注释/文档一律**中文**；**禁用三元表达式**（写完整 if/else）；类型不确定用显式 `isinstance`，不用 `getattr(x, "a", default)`。
- 日志事件消息用英文 `k=v` + `[wecom]` 前缀（该前缀已登记）。
- 单文件 ≤ 400 行；单函数 ≤ 80 行。
- **本 worktree 内禁用 `git add -A` / `git add .`**（`.venv` 是 symlink）；一律显式路径。
- 测试（从 worktree 根跑）：`POSTGRES_HOST=localhost pytest tests/ -v`。
- **禁止调用 SDK 的 `run()`**（它自建事件循环）；只用 `await connect()` / `disconnect()`。
- 本轮**不接业务**：reply 仍写死；事件不回包（复用 `_default_handler`）。
- 单连接互斥**只做配置约定 + 文档**，不加代码级锁。

---

### Task 1: 依赖与长连接驱动（`channels/wecom/long_connection.py`）

**Files:**
- Modify: `pyproject.toml`（`dependencies` 增加 `wecom-aibot-python-sdk==1.0.2`）
- Create: `src/channels/wecom/long_connection.py`
- Test: `tests/channels/test_long_connection_driver.py`

**Interfaces:**
- Consumes: `parse_inbound`（既有）、`InboundMessage` / `ReplySink` / `MessageHandler` / `ChannelDriver`（既有 `channels/base.py`）
- Produces:
  - `LongConnectionDriver(bot_id: str, secret: str, handler: MessageHandler)`，属性 `name == "wecom_long_connection"`，方法 `async start() -> None` / `async stop() -> None`
  - 模块级 `_WsSink(client, frame, stream_id)`（实现 `ReplySink`）

- [ ] **Step 1: 加依赖并安装**

在 `pyproject.toml` 的 `dependencies` 列表中（`"alembic==1.19.0",` 之前或之后均可，保持在列表内）加入：

```toml
    # 企微智能机器人长连接：官方客户端 SDK（WebSocket，含认证/心跳/重连/流式/事件分发）
    "wecom-aibot-python-sdk==1.0.2",
```

Run: `.venv/bin/pip install wecom-aibot-python-sdk==1.0.2`
Expected: `Requirement already satisfied` 或 `Successfully installed ...`

- [ ] **Step 2: 写失败测试**

创建 `tests/channels/test_long_connection_driver.py`：

```python
"""长连接驱动单测：mock 官方 aibot.WSClient，不发真实网络。"""

from typing import Any

import pytest

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom import long_connection as lc


class _FakeClient:
    """伪 WSClient：记录构造参数、注册的处理器与回复调用。"""

    def __init__(self, options: Any):
        self.options = options
        self.handlers: dict[str, Any] = {}
        self.connected = False
        self.replies: list[tuple[Any, str, str, bool]] = []

    def on(self, event: str, f: Any = None) -> Any:
        self.handlers[event] = f
        return f

    async def connect(self) -> "_FakeClient":
        self.connected = True
        return self

    def disconnect(self) -> None:
        self.connected = False

    async def reply_stream(
        self, frame: dict, stream_id: str, content: str, finish: bool = False
    ) -> dict:
        self.replies.append((frame, stream_id, content, finish))
        return {}


def _patch_client(monkeypatch):
    holder: dict[str, _FakeClient] = {}

    def _factory(options):
        client = _FakeClient(options)
        holder["client"] = client
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)
    return holder


def _text_frame() -> dict:
    return {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgid": "M1",
            "aibotid": "B1",
            "chattype": "single",
            "from": {"userid": "U1"},
            "msgtype": "text",
            "text": {"content": "hi"},
        },
    }


@pytest.mark.asyncio
async def test_start_connects_with_credentials_and_registers_handlers(monkeypatch):
    holder = _patch_client(monkeypatch)
    seen: list[InboundMessage] = []

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        seen.append(msg)

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    client = holder["client"]
    assert client.connected is True
    assert client.options.bot_id == "BOTID"
    assert client.options.secret == "SECRET"
    assert "message.text" in client.handlers
    assert "event.enter_chat" in client.handlers


@pytest.mark.asyncio
async def test_inbound_frame_is_parsed_and_dispatched(monkeypatch):
    holder = _patch_client(monkeypatch)
    seen: list[InboundMessage] = []

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        seen.append(msg)

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    await holder["client"].handlers["message.text"](_text_frame())

    assert len(seen) == 1
    assert seen[0].msgid == "M1"
    assert seen[0].msgtype == "text"
    assert seen[0].text == "hi"


@pytest.mark.asyncio
async def test_sink_reply_stream_calls_sdk(monkeypatch):
    holder = _patch_client(monkeypatch)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        await sink.reply_stream("已收到", finish=True)

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    frame = _text_frame()
    await holder["client"].handlers["message.text"](frame)

    replies = holder["client"].replies
    assert len(replies) == 1
    got_frame, stream_id, content, finish = replies[0]
    assert got_frame is frame
    assert stream_id  # 非空
    assert content == "已收到"
    assert finish is True


@pytest.mark.asyncio
async def test_stop_disconnects(monkeypatch):
    holder = _patch_client(monkeypatch)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    await driver.stop()

    assert holder["client"].connected is False
```

- [ ] **Step 3: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -v`
Expected: FAIL（`ModuleNotFoundError` / `AttributeError: module 'src.channels.wecom' has no attribute 'long_connection'`）

- [ ] **Step 4: 实现**

创建 `src/channels/wecom/long_connection.py`：

```python
"""企微智能机器人长连接驱动：包住官方 aibot SDK，实现 ChannelDriver。

由本服务主动连企微（wss://openws.work.weixin.qq.com），无需公网回调地址、无加解密。
仅使用 SDK 的公开接口，不调用 run()（它自建事件循环，与 FastAPI 冲突）。
"""

from typing import Any

from aibot import WSClient, WSClientOptions, generate_req_id
from loguru import logger

from src.channels.base import MessageHandler
from src.channels.wecom.parse import parse_inbound

# SDK 事件名：消息与事件共用同一处理器（入站 body 结构与回调一致）
_MESSAGE_EVENTS: tuple[str, ...] = (
    "message.text",
    "message.image",
    "message.mixed",
    "message.voice",
    "message.file",
)
_EVENT_EVENTS: tuple[str, ...] = (
    "event.enter_chat",
    "event.template_card_event",
    "event.feedback_event",
)


class _WsSink:
    """ReplySink 实现：把回复转成 SDK 的流式回复调用。"""

    def __init__(self, client: WSClient, frame: dict[str, Any], stream_id: str):
        """初始化。

        Args:
            client: 官方 SDK 客户端
            frame: 触发本次回复的入站帧（SDK 回复需透传）
            stream_id: 本条消息的流式 id（生成一次，多次回复复用）
        """
        self._client = client
        self._frame = frame
        self._stream_id = stream_id

    async def reply_stream(self, content: str, finish: bool) -> None:
        """发送流式回复。"""
        await self._client.reply_stream(self._frame, self._stream_id, content, finish)


class LongConnectionDriver:
    """长连接驱动。"""

    name = "wecom_long_connection"

    def __init__(self, bot_id: str, secret: str, handler: MessageHandler):
        """初始化。

        Args:
            bot_id: 智能机器人 BotID
            secret: 长连接专用 Secret
            handler: 业务处理（吃入站事件，用 sink 回复）
        """
        self._bot_id = bot_id
        self._secret = secret
        self._handler = handler
        self._client: WSClient | None = None

    async def start(self) -> None:
        """建立长连接并注册事件处理器。"""
        client = WSClient(
            WSClientOptions(bot_id=self._bot_id, secret=self._secret)
        )
        handler = self._make_handler(client)
        for event in _MESSAGE_EVENTS + _EVENT_EVENTS:
            client.on(event, handler)
        self._client = client
        await client.connect()

    async def stop(self) -> None:
        """断开长连接。"""
        if self._client is None:
            return
        self._client.disconnect()
        self._client = None

    def _make_handler(self, client: WSClient):
        """构造 SDK 事件处理器：帧 → InboundMessage → 业务 handler。"""

        async def _handle(frame: dict[str, Any]) -> None:
            try:
                msg = parse_inbound(frame["body"])
            except Exception as e:  # noqa: BLE001
                logger.error("[wecom] parse inbound failed err={}", e)
                return
            sink = _WsSink(client, frame, generate_req_id("stream"))
            try:
                await self._handler(msg, sink)
            except Exception as e:  # noqa: BLE001
                logger.error("[wecom] handler failed msgid={} err={}", msg.msgid, e)

        return _handle
```

- [ ] **Step 5: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/channels/test_long_connection_driver.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/channels/wecom/long_connection.py tests/channels/test_long_connection_driver.py
git commit -m "feat(wecom): 新增长连接驱动（封装官方 aibot SDK）"
```

---

### Task 2: 模式切换与配置（`wecom_service` + settings）

**Files:**
- Modify: `src/config/settings.py`（新增 3 个变量）
- Modify: `src/services/wecom_service.py`（按 mode 构造驱动 + `get_callback_driver()`）
- Test: `tests/services/test_wecom_service.py`（新增用例，保留既有）

**Interfaces:**
- Consumes: `CallbackDriver`（既有）、`LongConnectionDriver`（Task 1）、`WeComCrypto`（既有）
- Produces:
  - `settings.WECOM_BOT_MODE` / `WECOM_BOT_ID` / `WECOM_BOT_SECRET`
  - `wecom_service.get_driver()`（返回当前驱动，两种之一）
  - `wecom_service.get_callback_driver() -> CallbackDriver`（非 callback 模式或未启动时抛 `RuntimeError`）

- [ ] **Step 1: 写失败测试（追加到既有文件）**

在 `tests/services/test_wecom_service.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_mode_long_connection_builds_ws_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "BOTID")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "SECRET")
    started: list[str] = []

    class _StubDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            started.append("start")

        async def stop(self) -> None:
            started.append("stop")

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", lambda *a, **k: _StubDriver())

    await wecom_service.start()
    assert wecom_service.get_driver().name == "wecom_long_connection"
    assert started == ["start"]


@pytest.mark.asyncio
async def test_long_connection_missing_credentials_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_unknown_mode_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "nonsense")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_get_callback_driver_rejects_non_callback_mode(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "BOTID")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "SECRET")

    class _AsyncStub:
        name = "wecom_long_connection"

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", lambda *a, **k: _AsyncStub())

    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()
```

> ⚠️ stub 的 `start`/`stop` **必须是协程**（`async def`）——`start()` 内部会 `await _driver.start()`；用同步 lambda 会 `await None` 抛 `TypeError`。

> 注：既有用例的 `_reset_driver` autouse fixture 会把 `_driver` 归零，无需改动。

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -v`
Expected: FAIL（`AttributeError: ... has no attribute 'LongConnectionDriver'` / `get_callback_driver`）

- [ ] **Step 3: 改配置**

在 `src/config/settings.py` 的企微变量块中追加：

```python
WECOM_BOT_MODE: str = os.getenv("WECOM_BOT_MODE", "callback")
WECOM_BOT_ID: str = os.getenv("WECOM_BOT_ID", "")
WECOM_BOT_SECRET: str = os.getenv("WECOM_BOT_SECRET", "")
```

- [ ] **Step 4: 改编排**

改 `src/services/wecom_service.py`：新增 import 与常量，改写 `start()`，新增 `get_callback_driver()`：

```python
from src.channels.wecom.long_connection import LongConnectionDriver

_MODE_CALLBACK = "callback"
_MODE_LONG_CONNECTION = "long_connection"


def _validate_long_connection_credentials() -> None:
    """长连接模式的启动期校验。"""
    if not settings.WECOM_BOT_ID:
        raise RuntimeError("WECOM_BOT_ID 未配置")
    if not settings.WECOM_BOT_SECRET:
        raise RuntimeError("WECOM_BOT_SECRET 未配置")


async def start() -> None:
    """启动通道：enabled 时按 mode 构造并启动驱动，否则 no-op。"""
    global _driver
    if not settings.WECOM_BOT_ENABLED:
        return
    mode = settings.WECOM_BOT_MODE
    if mode == _MODE_CALLBACK:
        _validate_credentials()
        crypto = WeComCrypto(
            settings.WECOM_BOT_TOKEN,
            settings.WECOM_BOT_ENCODING_AES_KEY,
            settings.WECOM_BOT_RECEIVE_ID,
        )
        _driver = CallbackDriver(crypto, _default_handler)
    elif mode == _MODE_LONG_CONNECTION:
        _validate_long_connection_credentials()
        _driver = LongConnectionDriver(
            settings.WECOM_BOT_ID, settings.WECOM_BOT_SECRET, _default_handler
        )
    else:
        raise RuntimeError(f"未知 WECOM_BOT_MODE: {mode}")
    await _driver.start()


def get_callback_driver() -> CallbackDriver:
    """取回调驱动；非 callback 模式或未启动时抛 RuntimeError。"""
    if settings.WECOM_BOT_MODE != _MODE_CALLBACK:
        raise RuntimeError("当前非 callback 模式")
    if _driver is None:
        raise RuntimeError("wecom driver 未启动")
    if not isinstance(_driver, CallbackDriver):
        raise RuntimeError("当前驱动不是 CallbackDriver")
    return _driver
```

`stop()` 保持原样（`_driver` 置空即可；如需对长连接显式断开，改为：非空时 `await _driver.stop()` 再置空——**本轮采用后者**）：

```python
async def stop() -> None:
    """停止通道。"""
    global _driver
    if _driver is not None:
        await _driver.stop()
    _driver = None
```

> **注意**：`get_driver()` 的返回类型标注保持 `CallbackDriver` 会与长连接驱动冲突，请改为 `CallbackDriver | LongConnectionDriver`。

- [ ] **Step 5: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/services/test_wecom_service.py -v`
Expected: PASS（既有 5 + 新增 4 = 9 passed）

- [ ] **Step 6: 提交**

```bash
git add src/config/settings.py src/services/wecom_service.py tests/services/test_wecom_service.py
git commit -m "feat(wecom): 按 WECOM_BOT_MODE 切换回调/长连接驱动"
```

---

### Task 3: 回调路由模式守卫（`api/wecom.py`）

**Files:**
- Modify: `src/api/wecom.py`
- Test: `tests/api/test_wecom.py`（新增用例）

**Interfaces:**
- Consumes: `wecom_service.get_callback_driver()`（Task 2）、`settings.WECOM_BOT_MODE`（Task 2）
- Produces: 无新接口（行为：非 callback 模式 → 404）

- [ ] **Step 1: 写失败测试（追加）**

在 `tests/api/test_wecom.py` 末尾追加：

```python
def test_long_connection_mode_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    client = TestClient(_app())
    assert client.get("/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2&echostr=z").status_code == 404
    assert client.post("/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2", json={"encrypt": "y"}).status_code == 404
```

> 注：既有用例默认 `WECOM_BOT_MODE` 为 `callback`（settings 默认值），不受影响。

- [ ] **Step 2: 运行测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_wecom.py -v`
Expected: FAIL（long_connection 模式下仍返回非 404）

- [ ] **Step 3: 实现守卫**

改 `src/api/wecom.py` 两个 handler：把原 `if not settings.WECOM_BOT_ENABLED:` 改为：

```python
    if not settings.WECOM_BOT_ENABLED or settings.WECOM_BOT_MODE != "callback":
        return Response(status_code=404)
```

并把 `wecom_service.get_driver()` 改为 `wecom_service.get_callback_driver()`。

- [ ] **Step 4: 运行测试确认通过**

Run: `POSTGRES_HOST=localhost pytest tests/api/test_wecom.py -v`
Expected: PASS（既有 4 + 新增 1 = 5 passed）

- [ ] **Step 5: 提交**

```bash
git add src/api/wecom.py tests/api/test_wecom.py
git commit -m "feat(wecom): 回调路由在非 callback 模式返回 404"
```

---

### Task 4: 文档登记

**Files:**
- Modify: `docs/agents/code-map.md`、`.env.example`、`.env.template`、`docs/agents/glossary.md`、`docs/agents/api_contract.md`、`docs/agents/cookbook.md`

- [ ] **Step 1: 代码地图**

在 `docs/agents/code-map.md` 的 `channels/` 行补充长连接驱动说明（照该行既有格式），例如把该行改为包含 `long_connection(长连接驱动，封装官方 aibot SDK)`。

- [ ] **Step 2: 环境变量**

在 `.env.example` 与 `.env.template` 的企微变量块追加：

```
# 接入方式：callback（URL 回调）| long_connection（长连接，需 BotID/Secret）
WECOM_BOT_MODE=callback
# 长连接模式必填
WECOM_BOT_ID=
WECOM_BOT_SECRET=
# ⚠ 长连接：每机器人同时只允许一条连接，新连接会踢掉旧连接 ——
#   多实例部署时，仅在一台实例开启 WECOM_BOT_ENABLED=true，其余置 false
```

- [ ] **Step 3: 术语**

在 `docs/agents/glossary.md` 的「接入通道」条目附近追加：

```markdown
- **长连接驱动（LongConnectionDriver）**：接入通道的另一种实现，由本服务主动连企微 WebSocket（封装官方 `aibot` SDK），无需公网回调地址与加解密；与回调驱动由 `WECOM_BOT_MODE` 二选一。见 `src/channels/wecom/long_connection.py`。
```

- [ ] **Step 4: 接口契约**

在 `docs/agents/api_contract.md` 的企微回调小节补一句：

```markdown
> 该端点仅在 `WECOM_BOT_MODE=callback` 时可用；`long_connection` 模式下返回 `404`（长连接不经 HTTP 回调）。
```

- [ ] **Step 5: 操作手册**

在 `docs/agents/cookbook.md` 已有的「企微智能机器人回调接入（开发期）」条目**之后**（同节内）追加一个 `###` 条目「企微智能机器人长连接接入（开发期）」，照该文件模板（`**场景**/**步骤**/**验证**/**注意事项**`），内容包含：在后台把机器人连接方式改为「使用长连接」并取 BotID/Secret → `.env` 置 `WECOM_BOT_MODE=long_connection` + `WECOM_BOT_ID`/`WECOM_BOT_SECRET` + `WECOM_BOT_ENABLED=true` → `--force-recreate` → 日志看连接/认证 → @机器人 收写死回复；**注意事项**写"每机器人仅一条连接、多实例只在一台开启"。

- [ ] **Step 6: 提交**

```bash
git add docs/agents/code-map.md .env.example .env.template docs/agents/glossary.md docs/agents/api_contract.md docs/agents/cookbook.md
git commit -m "docs(wecom): 登记长连接驱动、新变量与单连接约定"
```

---

## 收尾验收（人工）

- [ ] 全量门禁：`POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错；`pyright src/` 不新增 error。
- [ ] 无遗留 `print()` / TODO / 调试代码。
- [ ] 手工冒烟（需企微后台，可选）：`mode=long_connection` 下连接/认证成功、@机器人 收写死回复。
- [ ] 合并前用 `verification-before-completion` 取证，再 `requesting-code-review`。

## 未覆盖（本 plan 之外）

- Redis 单例租约（多实例防互踢）、业务接线（调 agent 跑 RAG）、媒体入站下载解密、模板卡片/欢迎语、凭证轮换与多机器人 —— 见 spec §8。
- 新依赖的镜像重建与云效 PyPI 代理仓 `repo-okxha` 预热（部署动作）。
