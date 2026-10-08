"""长连接驱动单测：mock 官方 aibot.WSClient，不发真实网络。"""

from typing import Any

import pytest

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom import long_connection as lc


class _FakeClient:
    """伪 WSClient：记录构造参数、注册的处理器与回复调用。

    默认模拟"健康且已认证"：`connect()` 建立连接后同步 `emit("authenticated")`，
    以对标真实 SDK 认证成功的默认路径（驱动在 connect() 之前已注册该处理器）。
    """

    def __init__(self, options: Any):
        self.options = options
        self.handlers: dict[str, Any] = {}
        self.connected = False
        self.disconnect_calls = 0
        self.replies: list[tuple[Any, str, str, bool]] = []

    def on(self, event: str, f: Any = None) -> Any:
        self.handlers[event] = f
        return f

    def emit(self, event: str, payload: Any = None) -> None:
        """同步触发已注册处理器（对标 pyee 的行为）。"""
        handler = self.handlers.get(event)
        if handler is None:
            return
        if payload is None:
            handler()
            return
        handler(payload)

    async def connect(self) -> "_FakeClient":
        self.connected = True
        self.emit("authenticated")
        return self

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

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


@pytest.mark.asyncio
async def test_repeated_start_is_idempotent(monkeypatch):
    created: list[_FakeClient] = []

    def _factory(options):
        client = _FakeClient(options)
        created.append(client)
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    first = driver._client
    await driver.start()

    # 只构造一个 client，且已启动后重复 start() 不会丢弃旧 client
    assert len(created) == 1
    assert driver._client is first
    assert created[0].connected is True


def _displaced_frame() -> dict:
    """被顶替事件帧（结构取自 Spike E4 实测）。"""
    return {
        "cmd": "aibot_event_callback",
        "body": {
            "msgid": "D1",
            "aibotid": "B1",
            "msgtype": "event",
            "event": {"eventtype": "disconnected_event"},
        },
    }


def _enter_chat_frame() -> dict:
    """欢迎语事件帧（对照组：普通事件仍须走业务 handler）。"""
    return {
        "cmd": "aibot_event_callback",
        "body": {
            "msgid": "E1",
            "aibotid": "B1",
            "chattype": "single",
            "from": {"userid": "U1"},
            "msgtype": "event",
            "event": {"eventtype": "enter_chat"},
        },
    }


@pytest.mark.asyncio
async def test_displaced_event_disconnects_and_is_not_forwarded(monkeypatch):
    holder = _patch_client(monkeypatch)
    seen: list[InboundMessage] = []

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        seen.append(msg)

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    client = holder["client"]

    # 必须订阅被顶事件（它是驱动级关注点，不在业务事件清单里）
    assert "event.disconnected_event" in client.handlers
    assert driver.is_displaced is False

    await client.handlers["event.disconnected_event"](_displaced_frame())

    assert seen == []  # 不交给业务 handler
    assert client.connected is False  # 主动断开（SDK 手动断开不触发重连）
    assert driver.is_displaced is True
    assert driver._client is None  # 已交接：stop() 变为 no-op


@pytest.mark.asyncio
async def test_displaced_handler_is_idempotent(monkeypatch):
    holder = _patch_client(monkeypatch)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    handler = holder["client"].handlers["event.disconnected_event"]

    await handler(_displaced_frame())
    await handler(_displaced_frame())  # 重复到达不应抛

    assert driver.is_displaced is True
    await driver.stop()  # 已断开 → no-op，不报错
    assert driver._client is None


@pytest.mark.asyncio
async def test_enter_chat_still_reaches_business_handler(monkeypatch):
    holder = _patch_client(monkeypatch)
    seen: list[InboundMessage] = []

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        seen.append(msg)

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    await holder["client"].handlers["event.enter_chat"](_enter_chat_frame())

    assert len(seen) == 1
    assert seen[0].event_type == "enter_chat"


class _FakeClientWithAuth(_FakeClient):
    """薄子类：仅当 `emit_auth=False` 时抑制 connect 时的认证事件。

    其余（emit / disconnect_calls / disconnect）均继承基类；`emit_auth=False`
    变体供超时与（Task 2）凭证失败用例复用。
    """

    def __init__(self, options: Any, *, emit_auth: bool = True):
        super().__init__(options)
        self._emit_auth = emit_auth

    async def connect(self) -> "_FakeClientWithAuth":
        self.connected = True
        if self._emit_auth:
            self.emit("authenticated")
        return self


def _auth_patch(monkeypatch, *, emit_auth: bool):
    holder: dict[str, _FakeClientWithAuth] = {}

    def _factory(options):
        client = _FakeClientWithAuth(options, emit_auth=emit_auth)
        holder["client"] = client
        return client

    monkeypatch.setattr(lc, "WSClient", _factory)
    return holder


@pytest.mark.asyncio
async def test_start_marks_ready_when_authenticated(monkeypatch):
    holder = _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    assert holder["client"].connected is True
    assert driver.is_ready is True


@pytest.mark.asyncio
async def test_auth_wait_timeout_degrades_without_disconnect(monkeypatch):
    """超时只降级：不计就绪、**不断开**（交 SDK 自愈），但连接已建立仍须登记。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()

    assert driver.is_ready is False
    assert holder["client"].connected is True  # 未断开
    assert holder["client"].disconnect_calls == 0


@pytest.mark.asyncio
async def test_late_authenticated_still_marks_ready(monkeypatch):
    """超时后认证才到达：仍应转为就绪（状态要如实）。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    assert driver.is_ready is False

    holder["client"].emit("authenticated")
    assert driver.is_ready is True


@pytest.mark.asyncio
async def test_displaced_driver_is_not_ready(monkeypatch):
    holder = _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    assert driver.is_ready is True

    await holder["client"].handlers["event.disconnected_event"](
        {
            "cmd": "aibot_event_callback",
            "body": {
                "msgid": "D1",
                "msgtype": "event",
                "event": {"eventtype": "disconnected_event"},
            },
        }
    )
    assert driver.is_ready is False


@pytest.mark.asyncio
async def test_stop_resets_ready(monkeypatch):
    _auth_patch(monkeypatch, emit_auth=True)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    await driver.stop()

    assert driver.is_ready is False
