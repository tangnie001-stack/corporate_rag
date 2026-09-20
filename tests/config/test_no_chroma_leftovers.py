"""退役 ChromaDB 与一次性搬迁产物的残留检查。

P1–P3 已把全部存储迁到 PostgreSQL（关系表 + pgvector + 全文检索），ChromaDB 与
进程内 BM25 索引不再参与运行时。本文件是那次退役的守卫 —— 只扫代码与配置，
不扫 docs（变更文档里必然会引用 Chroma 这个名字）。
"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "tests", "scripts", "alembic", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")
# 仓库根的环境文件不属于任何扫描目录，但同属「配置」，单独显式扫描
ROOT_DOTFILES = (".env", ".env.example", ".env.template")
# 仓库根的依赖/镜像/compose 文件是 D5/D7 的直接落点，同样不在任何扫描目录里
ROOT_CONFIG_FILES = (
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.prod.yml",
)

ALLOWED = {
    "tests/config/test_no_chroma_leftovers.py",
    "tests/config/test_no_bm25_leftovers.py",
}
CHROMA_KEY = re.compile(r"\bCHROMA_[A-Z_]+\b")
PATTERNS = (
    re.compile(r"^\s*(from|import)\s+chromadb", re.MULTILINE),
    CHROMA_KEY,
    re.compile(r"\bdeploy/chroma\b"),
)
# 根配置文件专属：非注释行出现的裸 chroma / chromadb（依赖声明、卷路径、VOLUME 条目）。
# 要求行首非 `#`，避免误伤 pyproject 里解释 bcrypt 依赖来历时对 chromadb 的提及。
ROOT_CONFIG_BARE = re.compile(
    r"^\s*[^#\s].*\bchromadb?\b", re.MULTILINE | re.IGNORECASE
)


def _offenders(paths: list[Path], patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    """在给定文件里逐一匹配 patterns，返回「相对路径: 模式」列表（每个文件最多一条）。"""
    found: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        rel = str(path.relative_to(REPO))
        if rel in ALLOWED or "__pycache__" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for regex in patterns:
            if regex.search(text):
                found.append(f"{rel}: {regex.pattern}")
                break
    return found


def test_chroma_dependency_is_gone():
    """依赖与镜像里都不该再有 chromadb。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("chromadb")


def test_rank_bm25_dependency_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("rank_bm25")


def test_one_shot_scripts_are_gone():
    for name in (
        "migrate_chroma_to_pg.py",
        "dense_equivalence_check.py",
        "lexical_probe.py",
        "lexical_probe_report.py",
    ):
        assert not (REPO / "scripts" / name).exists(), f"{name} 应随 Chroma 退役"


def test_rewrite_content_seg_is_kept():
    """分词器变更的操作协议必须保留（它不是一次性产物）。"""
    assert (REPO / "scripts" / "rewrite_content_seg.py").exists()


def test_deploy_chroma_is_gone():
    assert not (REPO / "deploy" / "chroma").exists()


def test_no_chroma_leftovers_in_code():
    paths: list[Path] = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.exists():
            continue
        for pattern in SCAN_GLOBS:
            paths.extend(root.rglob(pattern))
    offenders = _offenders(paths, PATTERNS)
    assert offenders == [], f"仍有 Chroma 残留：{offenders}"


def test_no_chroma_leftovers_in_root_config_files():
    """D5/D7 的落点：pyproject 依赖、compose 卷、Dockerfile VOLUME 都不得含 Chroma。"""
    paths = [REPO / name for name in ROOT_CONFIG_FILES]
    offenders = _offenders(paths, PATTERNS + (ROOT_CONFIG_BARE,))
    assert offenders == [], f"根配置文件仍有 Chroma 残留：{offenders}"


def test_no_chroma_leftovers_in_root_dotfiles():
    """仓库根环境文件里的 CHROMA_* 键必须一并清空。"""
    offenders: list[str] = []
    for name in ROOT_DOTFILES:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if CHROMA_KEY.search(text):
            offenders.append(name)
    assert offenders == [], f"环境文件仍有 Chroma 键：{offenders}"


def test_settings_no_longer_exports_chroma():
    settings = importlib.import_module("src.config.settings")
    for name in (
        "CHROMA_HOST",
        "CHROMA_PORT",
        "CHROMA_COLLECTION_PREFIX",
        "CHROMA_PERSIST_DIR",
    ):
        assert not hasattr(settings, name), f"settings 仍导出 {name}"
