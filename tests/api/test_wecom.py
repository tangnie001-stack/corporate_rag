"""企微回调路由单测：透传、状态码、query 解码。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import wecom as wecom_routes
from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom.callback import CallbackDriver
from src.channels.wecom.crypto import WeComCrypto
from src.config import settings
from src.services import wecom_service

_TOKEN = "token123"
_AES_KEY = "a" * 43


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(wecom_routes.router, prefix="/api")
    return app


@pytest.fixture
def client(monkeypatch):
    crypto = WeComCrypto(_TOKEN, _AES_KEY, "")

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        await sink.reply_stream("已收到", finish=True)

    monkeypatch.setattr(
        wecom_service, "get_callback_driver", lambda: CallbackDriver(crypto, _handler)
    )
    return TestClient(_app()), crypto


def test_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    resp = TestClient(_app()).get(
        "/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2&echostr=z"
    )
    assert resp.status_code == 404


def test_verify_ok(monkeypatch, client):
    test_client, crypto = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    ts, nonce = "100", "200"
    echostr = crypto.encrypt("echo-xyz")
    sig = crypto.signature(ts, nonce, echostr)
    resp = test_client.get(
        f"/api/wecom/callback?msg_signature={sig}&timestamp={ts}&nonce={nonce}&echostr={echostr}"
    )
    assert resp.status_code == 200
    assert resp.text == "echo-xyz"


def test_receive_message_ok(monkeypatch, client):
    test_client, crypto = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    ts, nonce = "100", "200"
    payload = {
        "msgid": "M1",
        "chattype": "single",
        "from": {"userid": "U1"},
        "msgtype": "text",
        "text": {"content": "hi"},
    }
    encrypt = crypto.encrypt(json.dumps(payload, ensure_ascii=False))
    sig = crypto.signature(ts, nonce, encrypt)
    resp = test_client.post(
        f"/api/wecom/callback?msg_signature={sig}&timestamp={ts}&nonce={nonce}",
        json={"encrypt": encrypt},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["msgsignature"] == crypto.signature(ts, nonce, out["encrypt"])


def test_receive_bad_signature_403(monkeypatch, client):
    test_client, _ = client
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    resp = test_client.post(
        "/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2", json={"encrypt": "y"}
    )
    assert resp.status_code == 403


def test_long_connection_mode_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    client = TestClient(_app())
    assert (
        client.get(
            "/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2&echostr=z"
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/wecom/callback?msg_signature=x&timestamp=1&nonce=2",
            json={"encrypt": "y"},
        ).status_code
        == 404
    )
