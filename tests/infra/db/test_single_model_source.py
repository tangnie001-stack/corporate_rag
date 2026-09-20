"""ORM 模型与迁移来源必须唯一。"""

import importlib

import pytest

import src.infra.db.models  # noqa: F401  —— 必须先 import 才会填充 Base.metadata
from src.infra.db.base import Base


def test_dead_model_package_is_gone():
    """不生效的那套模型目录必须已删除。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.db.repos.models")


def test_dead_alembic_dir_is_gone():
    """不生效的那套 alembic 目录必须已删除。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.db.repos.alembic.env")


def test_metadata_has_all_business_tables():
    """Base.metadata 必须含 7 张业务表（chunks 由 alembic baseline 建，不在 ORM 里）。"""
    expected = {
        "users",
        "knowledge_base",
        "document",
        "sessions",
        "conversation_history",
        "eval_report",
        "feedback",
    }
    assert expected <= set(Base.metadata.tables)
