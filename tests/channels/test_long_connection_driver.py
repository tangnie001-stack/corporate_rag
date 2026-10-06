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
