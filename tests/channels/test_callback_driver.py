"""回调驱动单测：URL 验证、消息/事件分流、空包、错误码、query 解码。"""

import json

import pytest

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver, _parse_query
from src.channels.wecom.crypto import WeComCrypto

_TOKEN = "token123"
_AES_KEY = "a" * 43


def _crypto() -> WeComCrypto:
    return WeComCrypto(_TOKEN, _AES_KEY, "")


def _query(msg_signature: str, timestamp: str, nonce: str, **extra: str) -> str:
    parts = [
        f"msg_signature={msg_signature}",
        f"timestamp={timestamp}",
        f"nonce={nonce}",
    ]
    parts.extend(f"{k}={v}" for k, v in extra.items())
    return "&".join(parts)


class _Recorder:
    """记录 handler 写入的回复。"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.replies.append((content, finish))


async def _handler_reply(msg: InboundMessage, sink: ReplySink) -> None:
    if msg.msgtype == "event":
        return
    await sink.reply_stream("已收到", finish=True)


async def _handler_raises(msg: InboundMessage, sink: ReplySink) -> None:
    raise RuntimeError("boom")


def test_parse_query_keeps_plus():
    # 裸 '+' 必须保留，不能被当成空格（echostr 是 base64）
    q = _parse_query("echostr=ab+cd%2Bef&timestamp=1")
    assert q["echostr"] == "ab+cd+ef"
    assert q["timestamp"] == "1"


def test_verify_returns_plaintext():
    c = _crypto()
    ts, nonce = "100", "200"
    echostr = c.encrypt("echo-123")
    driver = CallbackDriver(c, _handler_reply)
    resp = driver.verify(
        _query(c.signature(ts, nonce, echostr), ts, nonce, echostr=echostr)
    )
    assert resp.status_code == 200
    assert bytes(resp.body).decode("utf-8") == "echo-123"


def test_verify_bad_signature_returns_403():
    c = _crypto()
    driver = CallbackDriver(c, _handler_reply)
    resp = driver.verify(_query("deadbeef", "100", "200", echostr="x"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_handle_message_replies_and_is_verifiable():
    c = _crypto()
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M1",
        "aibotid": "B1",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    encrypt = c.encrypt(json.dumps(payload, ensure_ascii=False))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(
        _query(c.signature(ts, nonce, encrypt), ts, nonce), body
    )
    assert resp.status_code == 200
    out = json.loads(bytes(resp.body))
    # 回包可被独立按规则验签
    assert out["msgsignature"] == c.signature(ts, nonce, out["encrypt"])
    assert out["timestamp"] == int(ts) and out["nonce"] == nonce
    reply_plain = json.loads(c.decrypt(out["encrypt"]))
    assert reply_plain["msgtype"] == "stream"
    assert reply_plain["stream"]["finish"] is True
    assert reply_plain["stream"]["content"] == "已收到"


@pytest.mark.asyncio
async def test_handle_message_event_returns_empty_body():
    c = _crypto()
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M2",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "event",
        "event": {"eventtype": "enter_chat"},
    }
    encrypt = c.encrypt(json.dumps(payload, ensure_ascii=False))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(
        _query(c.signature(ts, nonce, encrypt), ts, nonce), body
    )
    assert resp.status_code == 200
    assert resp.body == b""


@pytest.mark.asyncio
async def test_handle_message_bad_json_returns_400():
    c = _crypto()
    driver = CallbackDriver(c, _handler_reply)
    resp = await driver.handle_message(_query("deadbeef", "100", "200"), b"not-json")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_handle_message_non_string_encrypt_returns_400():
    # {"encrypt": 123} 无需有效签名即可到达；不得让 signature 抛 TypeError → 500
    c = _crypto()
    driver = CallbackDriver(c, _handler_reply)
    resp = await driver.handle_message(
        _query("deadbeef", "100", "200"), b'{"encrypt": 123}'
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_handle_message_handler_exception_returns_empty_200():
    c = _crypto()
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M3",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    encrypt = c.encrypt(json.dumps(payload, ensure_ascii=False))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_raises)

    resp = await driver.handle_message(
        _query(c.signature(ts, nonce, encrypt), ts, nonce), body
    )
    assert resp.status_code == 200
    assert resp.body == b""


@pytest.mark.asyncio
async def test_handle_message_bad_signature_returns_403():
    c = _crypto()
    body = json.dumps({"encrypt": c.encrypt("x")}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(_query("deadbeef", "100", "200"), body)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_handle_message_decrypt_failure_returns_400():
    c = _crypto()
    ts, nonce = "100", "200"
    # 用正确签名签一个非法密文：过验签，解密阶段失败
    encrypt = "not-valid-base64!!"
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(
        _query(c.signature(ts, nonce, encrypt), ts, nonce), body
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_handle_message_missing_msgid_returns_400():
    c = _crypto()
    ts, nonce = "100", "200"
    # 合法 JSON 明文但缺 msgid：过验签与解密，解析阶段失败
    encrypt = c.encrypt(json.dumps({"msgtype": "text"}))
    body = json.dumps({"encrypt": encrypt}).encode("utf-8")
    driver = CallbackDriver(c, _handler_reply)

    resp = await driver.handle_message(
        _query(c.signature(ts, nonce, encrypt), ts, nonce), body
    )
    assert resp.status_code == 400
