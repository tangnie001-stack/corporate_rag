"""退役 MySQL 的残留检查。"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# 只扫代码与配置，不扫 docs —— 变更文档里必然会引用 mysql+aiomysql 这个名字
SCAN_DIRS = ("src", "alembic", "tests", "scripts", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")
MYSQL_URL = re.compile(r"mysql\+(aio)?mysql|mysql\+pymysql")


def test_mysql_drivers_not_importable():
    """三个 MySQL 驱动都应从依赖里移除。"""
    for mod in ("aiomysql", "pymysql", "mysql.connector"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_no_mysql_settings():
    """settings 不应再导出 MYSQL_* 常量。"""
    settings = importlib.import_module("src.config.settings")
    assert not hasattr(settings, "MYSQL_HOST")


def test_deploy_mysql_dir_gone():
    assert not (REPO / "deploy" / "mysql").exists()


def test_no_mysql_url_left():
    """源码与配置里不得再有 MySQL 方言的 async 连接串。"""
    this_file = Path(__file__).resolve()
    offenders = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.exists():
            continue
        for pattern in SCAN_GLOBS:
            for path in root.rglob(pattern):
                # 跳过本文件 —— 它自身持有用于比对的连接串正则
                if path.resolve() == this_file:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if MYSQL_URL.search(text):
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"仍有 MySQL 连接串：{offenders}"
