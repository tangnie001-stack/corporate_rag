"""企微投影层配置：默认值存在且合理、footer 强制开启规则、文案非空。"""

import importlib

from src.config import wecom_presenter


def test_defaults_are_positive():
    assert wecom_presenter.MIN_SEND_INTERVAL_SECONDS > 0
    assert wecom_presenter.MAX_INTERMEDIATE_FRAMES > 0
    assert wecom_presenter.MAX_STREAM_CHARS > 0
    assert wecom_presenter.KEEPALIVE_INTERVAL_SECONDS > 0
    assert wecom_presenter.FIRST_FRAME_TIMEOUT_SECONDS > 0


def test_footer_forced_on_when_feedback_unavailable(monkeypatch):
    monkeypatch.setenv("WECOM_TRACE_FOOTER_ENABLED", "false")
    monkeypatch.setenv("WECOM_FEEDBACK_ID_ENABLED", "false")
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.TRACE_FOOTER_EFFECTIVE is True
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)


def test_footer_can_be_disabled_when_feedback_available(monkeypatch):
    monkeypatch.setenv("WECOM_TRACE_FOOTER_ENABLED", "false")
    monkeypatch.setenv("WECOM_FEEDBACK_ID_ENABLED", "true")
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.TRACE_FOOTER_EFFECTIVE is False
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)


def test_texts_are_present():
    texts = wecom_presenter.WeComPresenterTexts
    assert texts.PLACEHOLDER_TEXT
    assert texts.FALLBACK_TEXT
    assert texts.ERROR_TEXT
    assert texts.ABSTENTION_TEXT
    assert texts.SOURCES_TITLE
    assert "{}" in texts.TRACE_FOOTER_TEMPLATE


def test_feedback_id_enabled_by_default(monkeypatch):
    monkeypatch.delenv("WECOM_FEEDBACK_ID_ENABLED", raising=False)
    reloaded = importlib.reload(wecom_presenter)
    try:
        assert reloaded.FEEDBACK_ID_ENABLED is True
    finally:
        monkeypatch.undo()
        importlib.reload(wecom_presenter)
