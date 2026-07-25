"""Tests for configuration settings."""

import os
from importlib import reload
from unittest.mock import patch

import src.config.settings as config


def test_langfuse_secret_key_defaults_to_empty():
    """LANGFUSE_SECRET_KEY defaults to sk-lf-... (code default)."""
    assert hasattr(config, "LANGFUSE_SECRET_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ):
            os.environ.pop("LANGFUSE_SECRET_KEY", None)
            reloaded = reload(_s)
            assert reloaded.LANGFUSE_SECRET_KEY == "sk-lf-8665d453-271d-4ce2-9f3b-5b471dad5ce2"


def test_langfuse_public_key_defaults_to_empty():
    """LANGFUSE_PUBLIC_KEY defaults to pk-lf-... (code default)."""
    assert hasattr(config, "LANGFUSE_PUBLIC_KEY")
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ):
            os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
            reloaded = reload(_s)
            assert reloaded.LANGFUSE_PUBLIC_KEY == "pk-lf-96995ff8-f6e4-4205-b02d-eba6e5ed94c8"


def test_langfuse_host_default():
    """LANGFUSE_HOST defaults to http://langfuse:3000 (Docker internal)."""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ):
            os.environ.pop("LANGFUSE_HOST", None)
            reloaded = reload(_s)
            assert reloaded.LANGFUSE_HOST == "http://langfuse:3000"


def test_langfuse_enable_default_true():
    """LANGFUSE_ENABLE defaults to True."""
    import src.config.settings as _s

    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ):
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
