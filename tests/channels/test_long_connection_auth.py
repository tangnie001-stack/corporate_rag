"""长连接驱动「认证/就绪/错误判据」组单测：mock 官方 aibot.WSClient，不发真实网络。"""

import pytest

from src.channels.base import InboundMessage, ReplySink
from src.channels.wecom import long_connection as lc
from tests.channels.wecom_driver_fakes import _auth_patch


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


@pytest.mark.asyncio
async def test_credential_failure_disconnects_and_never_ready(monkeypatch):
    """凭证类失败（实测 errcode=853000）：主动断开、永不就绪；SDK 不抛不重连。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.01)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    client = holder["client"]

    client.emit(
        "error",
        Exception("Authentication failed: invalid bot_id or secret (code: 853000)"),
    )

    assert client.disconnect_calls == 1
    assert driver.is_ready is False
    assert driver._fatal is True

    # 迟到的 authenticated 不得把它翻回就绪
    client.emit("authenticated")
    assert driver.is_ready is False


@pytest.mark.asyncio
async def test_non_credential_error_does_not_disconnect(monkeypatch):
    """普通连接/接收错误不构成凭证类：不得断开、不得置致命，仍可认证就绪。"""
    holder = _auth_patch(monkeypatch, emit_auth=False)
    monkeypatch.setattr(lc, "_AUTH_TIMEOUT_SECONDS", 0.05)

    async def _handler(msg: InboundMessage, sink: ReplySink) -> None:
        return

    driver = lc.LongConnectionDriver("BOTID", "SECRET", _handler)
    await driver.start()
    client = holder["client"]

    client.emit("error", Exception("websocket keepalive timeout"))

    assert client.disconnect_calls == 0
    assert driver._fatal is False

    client.emit("authenticated")
    assert driver.is_ready is True
