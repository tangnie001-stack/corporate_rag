"""测试共享 fixture 和配置。

提供：
  - AppService / MySQLDB / VectorStore 实例
  - 测试知识库生命周期（创建 / 销毁）
  - 测试文档路径和数据库验证辅助函数
"""

from __future__ import annotations

import os
import uuid
from typing import Generator

import pytest
from loguru import logger

from src.services.app_service import AppService
from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore

# ==================== 路径常量 ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_DOCS_DIR = os.path.join(PROJECT_ROOT, "data", "test_docs")

# ==================== 数据库直连验证 ====================


@pytest.fixture(scope="session")
def service() -> Generator[AppService, None, None]:
    """提供 AppService 实例，用于直接验证数据库状态。"""
    svc = AppService()
    yield svc


@pytest.fixture(scope="session")
def mysql_db() -> Generator[MySQLDB, None, None]:
    """提供 MySQLDB 实例，用于直接查询 MySQL 验证数据一致性。"""
    db = MySQLDB()
    yield db


@pytest.fixture(scope="session")
def vector_store() -> Generator[VectorStore, None, None]:
    """提供 VectorStore 实例，用于验证 ChromaDB 状态。"""
    vs = VectorStore()
    yield vs


# ==================== 测试知识库生命周期 ====================


@pytest.fixture
def test_kb_name() -> Generator[str, None, None]:
    """生成唯一的测试知识库名称，teardown 时自动删除。

    每次调用生成形如 __test__<uuid6> 的唯一名称，
    确保并发测试时不会撞名。

    删除时同时清理 MySQL 记录和 ChromaDB 向量数据。
    """
    unique_id = uuid.uuid4().hex[:8]
    name = f"__test__{unique_id}"
    yield name

    # Teardown：清理测试知识库
    _cleanup_kb(name)


def _cleanup_kb(name: str) -> None:
    """根据知识库名称删除对应的数据库和向量数据。"""
    try:
        svc = AppService()
        kb_id = svc.db.get_kb_by_name(name)
        if kb_id:
            svc.db.delete_kb(kb_id)
            svc.vector_store.delete_collection(kb_id)
            logger.info("Cleaned up test KB: {} ({})", name, kb_id)
    except Exception:
        logger.exception("Failed to cleanup test KB: {}", name)


# ==================== 测试文档路径辅助 ====================


def get_test_doc_path(filename: str) -> str:
    """获取测试文档的完整路径。

    Args:
        filename: 测试文档的文件名（如 sample.pdf）

    Returns:
        文件的完整路径

    Raises:
        FileNotFoundError: 文件不存在
    """
    path = os.path.join(TEST_DOCS_DIR, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"测试文档不存在: {path}")
    return path


@pytest.fixture
def corrupted_file_path(tmp_path) -> str:
    """生成一个损坏的文件（非法内容），用于测试异常上传场景。"""
    filepath = os.path.join(tmp_path, "corrupted.pdf")
    with open(filepath, "wb") as f:
        # 写入非 PDF 二进制头，不足以通过解析器校验
        f.write(b"\x00\x00\x00\x00corrupted content")
    return filepath
