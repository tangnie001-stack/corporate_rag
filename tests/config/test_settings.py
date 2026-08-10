"""Tests for configuration settings."""

import os
from importlib import reload
from unittest.mock import patch

import pytest

import src.config.settings as config


def test_langfuse_secret_key_defaults_to_empty():
    """LANGFUSE_SECRET_KEY defaults to sk-lf-... (code default)."""
    assert hasattr(config, "LANGFUSE_SECRET_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_SECRET_KEY", None)
        reloaded = reload(_s)
        assert (
            reloaded.LANGFUSE_SECRET_KEY == "sk-lf-8665d453-271d-4ce2-9f3b-5b471dad5ce2"
        )


def test_langfuse_public_key_defaults_to_empty():
    """LANGFUSE_PUBLIC_KEY defaults to pk-lf-... (code default)."""
    assert hasattr(config, "LANGFUSE_PUBLIC_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        reloaded = reload(_s)
        assert (
            reloaded.LANGFUSE_PUBLIC_KEY == "pk-lf-96995ff8-f6e4-4205-b02d-eba6e5ed94c8"
        )


def test_langfuse_host_default():
    """LANGFUSE_HOST defaults to http://langfuse:3000 (Docker internal)."""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_HOST", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_HOST == "http://langfuse:3000"


def test_langfuse_enable_default_true():
    """LANGFUSE_ENABLE defaults to True."""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"), patch.dict(os.environ):
        os.environ.pop("LANGFUSE_ENABLE", None)
        reloaded = reload(_s)
        assert reloaded.LANGFUSE_ENABLE is True


def test_langfuse_host_override_from_env():
    """LANGFUSE_HOST can be overridden via environment variable."""
    import src.config.settings

    with patch.dict(
        os.environ, {"LANGFUSE_HOST": "http://localhost:3000"}, clear=False
    ):
        reloaded = reload(src.config.settings)
        assert reloaded.LANGFUSE_HOST == "http://localhost:3000"


def test_entity_llm_fallback_default_auto():
    """ENTITY_LLM_FALLBACK defaults to 'auto' (三态开关默认值)."""
    from src.config.settings import ENTITY_LLM_FALLBACK

    assert ENTITY_LLM_FALLBACK in ("off", "on", "auto")
    assert ENTITY_LLM_FALLBACK == "auto"


def test_entity_text_prefix_len_default():
    """ENTITY_TEXT_PREFIX_LEN defaults to 600（正文前缀字符数）."""
    from src.config.settings import ENTITY_TEXT_PREFIX_LEN

    assert ENTITY_TEXT_PREFIX_LEN == 600


def test_margin_constants_split():
    """HEADER_MARGIN/FOOTER_MARGIN 替换 HEADER_FOOTER_MARGIN（默认 45/80）。"""
    from src.config import FOOTER_MARGIN, HEADER_MARGIN

    assert HEADER_MARGIN == 45
    assert FOOTER_MARGIN == 80
    with pytest.raises(ImportError):
        from src.config import (
            HEADER_FOOTER_MARGIN,  # noqa: F401  # pyright: ignore[reportAttributeAccessIssue]
        )


def test_pdf_heading_subprocess_constants():
    """pm 标题树子进程超时与并发上限默认值。"""
    from src.config import (
        MAX_CONCURRENT_HEADING_SUBPROCESS,
        PDF_HEADING_SUBPROCESS_TIMEOUT,
    )

    assert PDF_HEADING_SUBPROCESS_TIMEOUT == 180
    assert MAX_CONCURRENT_HEADING_SUBPROCESS == 2
