"""Tests for configuration settings."""

import os
from importlib import reload
from unittest.mock import patch

import src.config.settings as config


def test_langfuse_secret_key_defaults_to_empty():
    """LANGFUSE_SECRET_KEY defaults to empty string."""
    assert hasattr(config, "LANGFUSE_SECRET_KEY")
    assert config.LANGFUSE_SECRET_KEY == ""


def test_langfuse_public_key_defaults_to_empty():
    """LANGFUSE_PUBLIC_KEY defaults to empty string."""
    assert hasattr(config, "LANGFUSE_PUBLIC_KEY")
    assert config.LANGFUSE_PUBLIC_KEY == ""


def test_langfuse_host_default():
    """LANGFUSE_HOST defaults to http://langfuse:3000 (Docker internal)."""
    assert config.LANGFUSE_HOST == "http://langfuse:3000"


def test_langfuse_enable_default_true():
    """LANGFUSE_ENABLE defaults to True."""
    assert config.LANGFUSE_ENABLE is True


def test_langfuse_host_override_from_env():
    """LANGFUSE_HOST can be overridden via environment variable."""
    import src.config.settings

    with patch.dict(
        os.environ, {"LANGFUSE_HOST": "http://localhost:3000"}, clear=False
    ):
        reloaded = reload(src.config.settings)
        assert reloaded.LANGFUSE_HOST == "http://localhost:3000"
