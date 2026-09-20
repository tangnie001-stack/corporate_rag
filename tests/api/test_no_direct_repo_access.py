"""文档端点不得直取 Repo / 向量存储（api/ 只做参数校验与路由转发）。"""

import inspect

from src.api import documents


def test_document_routes_do_not_touch_repo_or_store_directly():
    """路由源码里不得出现 `_doc_repo` / `_kb_repo` / `vector_store` 直取。"""
    source = inspect.getsource(documents)
    for forbidden in ("_doc_repo", "_kb_repo", "vector_store"):
        assert forbidden not in source, f"api/documents.py 仍直取 {forbidden}"


def test_document_routes_do_not_import_infra():
    """api/ 不得 import infra/（分层调用规则）。"""
    source = inspect.getsource(documents)
    assert "from src.infra" not in source
    assert "import src.infra" not in source
