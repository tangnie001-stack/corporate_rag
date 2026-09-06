"""测试文档防腐检查（src/cli/check_docs.py）— 防 docs/agents/* 引用漂移。

防腐哲学：文档对代码的"引用必须指向存在物"（单向校验）。本测试断言 error 档
为空——任何 docs/agents/ 文档引用了已删除/改名的代码路径或路由，pytest 即失败，
把"文档过时"变成可自动发现的信号（配合 pre-commit always_run hook 在提交前拦截）。
"""

from src.cli import check_docs
from src.cli.check_docs import (
    _check_path_anchors,
    _check_route_anchors,
    _collect_code_routes,
    _load_config,
)


def _scan_all_docs():
    """对全部受检文档跑三类锚点检查，返回 error 档列表。"""
    exclude_docs, exclude_paths, exclude_routes, _ = _load_config()
    code_routes = _collect_code_routes()
    errors = []
    for doc in check_docs._DOCS_DIR.glob("*.md"):
        if doc.name in exclude_docs:
            continue
        errors.extend(_check_path_anchors(doc, exclude_paths))
        errors.extend(_check_route_anchors(doc, code_routes, exclude_routes))
    return errors


def test_no_dangling_code_paths_in_docs():
    """docs/agents/*.md 引用的 src 路径必须存在（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "path"]
    assert errors == [], (
        "文档引用了不存在的代码路径（文档已腐化，需更新 docs/agents/）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_doc_routes_registered_in_code():
    """docs 声明的 /api 路由必须在 src/api/ 有 @router 注册（error 档为空）。"""
    errors = [e for e in _scan_all_docs() if e.kind == "route"]
    assert errors == [], (
        "文档声明了代码中不存在的路由（接口已删/改名，需更新文档）:\n"
        + "\n".join(
            f"  {e.doc_file}:{e.doc_line} {e.anchor} {e.message}" for e in errors
        )
    )


def test_load_config_defaults():
    """pyproject 排除表应能加载且含核心排除项（意向清单文档不校验）。"""
    exclude_docs, _exclude_paths, _exclude_routes, _exclude_symbols = _load_config()
    assert "requirements_pool.md" in exclude_docs  # 意向清单应被排除
    assert check_docs._DOCS_DIR.exists()
