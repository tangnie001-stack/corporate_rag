"""企微编排单测：启停、凭证 fail-fast、默认 handler 行为。"""

import pytest

from src.channels.base import InboundMessage
from src.config import settings
from src.services import wecom_service


def _msg(
    msgtype: str, text: str | None, event_type: str | None = None
) -> InboundMessage:
    return InboundMessage(
        msgid="M1",
        aibotid="B1",
        chatid=None,
        chattype="single",
        from_userid="U1",
        msgtype=msgtype,
        text=text,
        event_type=event_type,
        raw={},
    )


class _Sink:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, content: str, finish: bool) -> None:
        self.replies.append((content, finish))


@pytest.fixture(autouse=True)
def _reset_driver():
    wecom_service._driver = None
    yield
    wecom_service._driver = None


@pytest.mark.asyncio
async def test_disabled_start_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_driver()


@pytest.mark.asyncio
async def test_enabled_but_bad_aes_key_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "short")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_enabled_start_builds_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "a" * 43)
    await wecom_service.start()
    driver = wecom_service.get_driver()
    assert driver.name == "wecom_callback"


@pytest.mark.asyncio
async def test_default_handler_replies_for_message(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    sink = _Sink()
    await wecom_service._default_handler(_msg("text", "hi"), sink)
    assert len(sink.replies) == 1
    assert sink.replies[0][1] is True


@pytest.mark.asyncio
async def test_default_handler_silent_for_event(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    sink = _Sink()
    await wecom_service._default_handler(_msg("event", None, "enter_chat"), sink)
    assert sink.replies == []


@pytest.mark.asyncio
async def test_mode_long_connection_builds_ws_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "BOTID")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "SECRET")
    started: list[str] = []
    # 记录构造实参，用于断言凭据透传形状
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _StubDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            started.append("start")

        async def stop(self) -> None:
            started.append("stop")

    def _factory(*args: object, **kwargs: object) -> _StubDriver:
        captured.append((args, kwargs))
        return _StubDriver()

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)

    await wecom_service.start()
    assert wecom_service.get_driver().name == "wecom_long_connection"
    assert started == ["start"]
    # 实现以位置参数传入 (bot_id, secret, handler)
    assert len(captured) == 1
    assert captured[0][0][0] == "BOTID"
    assert captured[0][0][1] == "SECRET"


@pytest.mark.asyncio
async def test_long_connection_missing_credentials_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_start_failure_degrades_not_blocks(monkeypatch):
    """长连接驱动 start() 抛异常时不阻塞启动，降级置空 _driver。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_ID", "BOTID")
    monkeypatch.setattr(settings, "WECOM_BOT_SECRET", "SECRET")

    class _FailingDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            raise RuntimeError("connect failed")

        async def stop(self) -> None:
            return

    monkeypatch.setattr(
        wecom_service, "LongConnectionDriver", lambda *a, **k: _FailingDriver()
    )

    # 连接失败不应向外抛，应用启动得以继续
    await wecom_service.start()
    # 已降级置空，取驱动时抛 RuntimeError 而非返回半死的驱动
    with pytest.raises(RuntimeError):
        wecom_service.get_driver()


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

    monkeypatch.setattr(
        wecom_service, "LongConnectionDriver", lambda *a, **k: _AsyncStub()
    )

    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_when_not_started(monkeypatch):
    """callback 模式但未调用 start()，_driver 为 None → RuntimeError。"""
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    # 不调用 start()；autouse fixture 已把 _driver 归零
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_wrong_driver_type(monkeypatch):
    """callback 模式但单例里塞的是非 CallbackDriver → RuntimeError。"""
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")

    class _WrongDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    # 直接注入类型不符的 stub；测试用匿名 fake，接口与驱动对齐即可
    wecom_service._driver = _WrongDriver()  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            wecom_service.get_callback_driver()
    finally:
        # 显式还原为 None（autouse fixture 亦会归零，此处更直观）
        wecom_service._driver = None
