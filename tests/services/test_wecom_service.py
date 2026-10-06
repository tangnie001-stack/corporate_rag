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
