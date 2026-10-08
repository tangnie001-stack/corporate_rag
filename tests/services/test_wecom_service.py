"""企微编排单测：驱动注册表启停、逐台降级、fail-fast、callback legacy。"""

import json

import pytest
from loguru import logger

from src.channels.base import InboundMessage
from src.config import settings
from src.config.const import WECOM_REPLY_PLACEHOLDER
from src.config.wecom_bots import CALLBACK_BOT_KEY
from src.services import wecom_service


def _bots_env(monkeypatch, items: list[tuple[str, str, str]]) -> None:
    """把 (key, bot_id, secret) 列表写进 WECOM_BOTS 环境变量（单行 JSON）。"""
    data = [{"key": k, "bot_id": i, "secret": s} for (k, i, s) in items]
    monkeypatch.setenv("WECOM_BOTS", json.dumps(data, separators=(",", ":")))


class _StubDriver:
    name = "wecom_long_connection"

    def __init__(
        self, bot_id: str, secret: str, handler: object, *, fail: bool = False
    ):
        self.bot_id = bot_id
        self.secret = secret
        self.handler = handler
        self.started = False
        self.stopped = False
        self._fail = fail

    async def start(self) -> None:
        if self._fail:
            raise RuntimeError("connect failed")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _StopFailingDriver(_StubDriver):
    async def stop(self) -> None:
        raise RuntimeError("disconnect failed")


class _StopFailingByBotIdDriver(_StubDriver):
    """stop() 按 bot_id 决定是否抛错：仅指定台失败，其余台正常断开。"""

    def __init__(self, bot_id: str, secret: str, handler: object, *, failing_id: str):
        super().__init__(bot_id, secret, handler)
        self._failing_id = failing_id

    async def stop(self) -> None:
        if self.bot_id == self._failing_id:
            raise RuntimeError("disconnect failed")
        self.stopped = True


def _install_stub(monkeypatch, *, fail_ids: set[str] | None = None):
    """把 LongConnectionDriver 换成 stub 工厂；返回创建记录列表。"""
    created: list[_StubDriver] = []
    fail_ids = fail_ids or set()

    def _factory(bot_id, secret, handler):
        driver = _StubDriver(bot_id, secret, handler, fail=bot_id in fail_ids)
        created.append(driver)
        return driver

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)
    return created


@pytest.fixture(autouse=True)
def _reset_state():
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}
    yield
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}


@pytest.mark.asyncio
async def test_disabled_start_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", False)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_driver("dev")


@pytest.mark.asyncio
async def test_callback_mode_builds_driver(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "a" * 43)
    await wecom_service.start()
    assert wecom_service.get_callback_driver().name == "wecom_callback"
    assert wecom_service.get_driver(CALLBACK_BOT_KEY).name == "wecom_callback"


@pytest.mark.asyncio
async def test_callback_bad_aes_key_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    monkeypatch.setattr(settings, "WECOM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "WECOM_BOT_ENCODING_AES_KEY", "short")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_builds_all_bots(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(
        monkeypatch,
        [("dev", "aibA", "s1"), ("support", "aibB", "s2")],
    )
    created = _install_stub(monkeypatch)

    await wecom_service.start()

    assert len(created) == 2
    assert all(d.started for d in created)
    # get_driver 返回 ChannelDriver 协议；断言前用 isinstance 收窄到 stub 以取 bot_id
    dev = wecom_service.get_driver("dev")
    support = wecom_service.get_driver("support")
    assert isinstance(dev, _StubDriver)
    assert isinstance(support, _StubDriver)
    assert dev.bot_id == "aibA"
    assert support.bot_id == "aibB"
    # 反查表已建立
    assert wecom_service._bot_key_by_aibotid == {"aibA": "dev", "aibB": "support"}


@pytest.mark.asyncio
async def test_long_connection_single_failure_degrades_others(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(
        monkeypatch,
        [("dev", "aibA", "s1"), ("bad", "aibB", "s2")],
    )
    _install_stub(monkeypatch, fail_ids={"aibB"})

    # 单台失败不向外抛
    await wecom_service.start()

    dev = wecom_service.get_driver("dev")
    assert isinstance(dev, _StubDriver)
    assert dev.bot_id == "aibA"
    with pytest.raises(RuntimeError):
        wecom_service.get_driver("bad")


@pytest.mark.asyncio
async def test_long_connection_empty_registry_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setenv("WECOM_BOTS", "")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_bad_json_is_fail_fast(monkeypatch):
    """配置错误必须 fail-fast，不得被逐台降级吞掉。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setenv("WECOM_BOTS", "[{")
    with pytest.raises(ValueError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_unknown_mode_is_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "nonsense")
    with pytest.raises(RuntimeError):
        await wecom_service.start()


@pytest.mark.asyncio
async def test_long_connection_logs_anchor(monkeypatch):
    """启动锚点日志：成功台数 / 总台数（供冒烟区分"端点不可达"与"未切模式"）。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1"), ("support", "aibB", "s2")])
    _install_stub(monkeypatch, fail_ids={"aibB"})

    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m), level="INFO")
    try:
        await wecom_service.start()
    finally:
        logger.remove(sink_id)

    anchor = [m for m in messages if "bots connected" in m]
    assert len(anchor) == 1
    assert "n=1" in anchor[0]
    assert "total=2" in anchor[0]


@pytest.mark.asyncio
async def test_stop_disconnects_all_and_is_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    created = _install_stub(monkeypatch)

    await wecom_service.start()
    await wecom_service.stop()
    assert created[0].stopped is True
    assert wecom_service._drivers == {}
    # 幂等：再次 stop 不报错
    await wecom_service.stop()


@pytest.mark.asyncio
async def test_stop_tolerates_single_failure(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    monkeypatch.setattr(
        wecom_service,
        "LongConnectionDriver",
        lambda *a, **k: _StopFailingDriver(*a, **k),
    )

    await wecom_service.start()
    await wecom_service.stop()  # 不抛
    assert wecom_service._drivers == {}


@pytest.mark.asyncio
async def test_stop_two_bots_single_failure_keeps_other_disconnected(monkeypatch):
    """单台断开失败不得拖累其余台：另一台仍被断开，注册表清空。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1"), ("support", "aibB", "s2")])
    created: list[_StubDriver] = []

    def _factory(bot_id, secret, handler):
        driver = _StopFailingByBotIdDriver(bot_id, secret, handler, failing_id="aibA")
        created.append(driver)
        return driver

    monkeypatch.setattr(wecom_service, "LongConnectionDriver", _factory)

    await wecom_service.start()
    await wecom_service.stop()  # 单台失败不抛

    by_id = {d.bot_id: d for d in created}
    assert by_id["aibA"].stopped is False
    assert by_id["aibB"].stopped is True
    assert wecom_service._drivers == {}


@pytest.mark.asyncio
async def test_get_callback_driver_rejects_non_callback_mode(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    _bots_env(monkeypatch, [("dev", "aibA", "s1")])
    _install_stub(monkeypatch)
    await wecom_service.start()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_when_not_started(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


def test_get_callback_driver_rejects_wrong_driver_type(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")

    class _WrongDriver:
        name = "wecom_long_connection"

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

    wecom_service._drivers[CALLBACK_BOT_KEY] = _WrongDriver()
    with pytest.raises(RuntimeError):
        wecom_service.get_callback_driver()


class _RecorderSink:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(
        self, content: str, finish: bool, feedback: dict | None = None
    ) -> None:
        self.replies.append((content, finish))


def _inbound(aibotid: str, msgtype: str = "text") -> InboundMessage:
    return InboundMessage(
        msgid="M1",
        aibotid=aibotid,
        chatid=None,
        chattype="single",
        from_userid="U1",
        msgtype=msgtype,
        text="hi",
        event_type=None,
        raw={},
    )


def test_resolve_bot_key_callback_mode_returns_reserved_key(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "callback")
    assert wecom_service._resolve_bot_key("anything") == CALLBACK_BOT_KEY


def test_resolve_bot_key_long_connection_lookup(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    assert wecom_service._resolve_bot_key("aibA") == "dev"
    assert wecom_service._resolve_bot_key("unknown") is None


@pytest.mark.asyncio
async def test_handler_replies_for_known_bot(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("aibA"), sink)
    assert len(sink.replies) == 1
    assert sink.replies[0][0] == WECOM_REPLY_PLACEHOLDER
    assert sink.replies[0][1] is True


@pytest.mark.asyncio
async def test_handler_silent_for_unknown_bot(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("unknown"), sink)
    assert sink.replies == []


@pytest.mark.asyncio
async def test_handler_silent_for_event(monkeypatch):
    monkeypatch.setattr(settings, "WECOM_BOT_MODE", "long_connection")
    monkeypatch.setattr(settings, "WECOM_BOT_LOG_CONTENT", False)
    wecom_service._bot_key_by_aibotid = {"aibA": "dev"}
    sink = _RecorderSink()
    await wecom_service._default_handler(_inbound("aibA", msgtype="event"), sink)
    assert sink.replies == []
