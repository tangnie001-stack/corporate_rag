"""退役进程内词法索引（BM25）的残留检查。

`architecture-tidy` 的「无独立词法索引组件」要求：无进程内索引对象、无索引文件
路径配置。本文件是那条要求的守卫 —— 只扫代码与配置，不扫 docs（变更文档里必然
会引用 BM25 这个名字）。
"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests", "scripts", "alembic", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")

# 允许的例外：本文件自身（持有用于比对的正则）、探针脚本（分组 A 的基线对照）
ALLOWED = {
    "tests/config/test_no_bm25_leftovers.py",
    "scripts/lexical_probe.py",
}
PATTERNS = (
    re.compile(r"from\s+src\.infra\.search\.bm25_index"),
    re.compile(r"\bBM25Index\b"),
    re.compile(r"\bBM25_INDEX_DIR\b"),
)


def test_bm25_index_module_is_gone():
    """模块文件与模块导入都必须消失。"""
    assert not (REPO / "src" / "infra" / "search" / "bm25_index.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.search.bm25_index")


def test_rebuild_bm25_cli_is_gone():
    assert not (REPO / "src" / "cli" / "rebuild_bm25.py").exists()


def test_no_bm25_leftovers_in_code():
    """源码与配置里不得再出现 BM25 组件名或索引目录配置。"""
    offenders: list[str] = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.exists():
            continue
        for pattern in SCAN_GLOBS:
            for path in root.rglob(pattern):
                rel = str(path.relative_to(REPO))
                if rel in ALLOWED or "__pycache__" in rel:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for regex in PATTERNS:
                    if regex.search(text):
                        offenders.append(f"{rel}: {regex.pattern}")
                        break
    assert offenders == [], f"仍有 BM25 残留：{offenders}"


def test_settings_no_longer_exports_bm25_index_dir():
    settings = importlib.import_module("src.config.settings")
    assert not hasattr(settings, "BM25_INDEX_DIR")
