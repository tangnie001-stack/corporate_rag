"""PostgreSQL 连接配置与 DSN 拼装的守卫测试。"""

import pytest

from src.config import settings


def test_dsn_scheme_is_asyncpg(monkeypatch):
    """DSN 必须用 asyncpg 驱动。"""
    monkeypatch.setattr(settings, "POSTGRES_HOST", "db.example")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(settings, "POSTGRES_USER", "appuser")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "p@ss")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "corporate_rag")

    dsn = settings.build_postgres_dsn()

    assert dsn.startswith("postgresql+asyncpg://")
    assert "db.example:5432/corporate_rag" in dsn


def test_password_with_special_chars_is_quoted(monkeypatch):
    """密码含 @ / : 与空格时必须按 URL 规则转义，否则 DSN 会被解析错。"""
    monkeypatch.setattr(settings, "POSTGRES_HOST", "h")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(settings, "POSTGRES_USER", "u")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "p@ss:wo rd/x")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "d")

    dsn = settings.build_postgres_dsn()

    assert "p%40ss%3Awo%20rd%2Fx" in dsn
    # SQLAlchemy 用 unquote（非 unquote_plus）解析 userinfo，空格必须编成 %20；
    # "+" 不会被还原成空格，故 userinfo 段不得出现 "+"。
    userinfo = dsn.split("://", 1)[1].split("@", 1)[0]
    assert "+" not in userinfo


def test_missing_password_fails_fast(monkeypatch):
    """密码为空必须报错，不能静默拼出一个连不上的 DSN。"""
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "")
    with pytest.raises(RuntimeError):
        settings.build_postgres_dsn()
