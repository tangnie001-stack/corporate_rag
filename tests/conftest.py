"""页面集成测试的共享 fixture 和配置。

提供：
  - gradio_client 实例（连接运行中的 Gradio 应用）
  - 测试知识库生命周期（创建 / 销毁）
  - --start-app CLI 选项（CI 自动启动模式）
  - 测试文档路径和数据库验证辅助函数
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from typing import Generator

import pytest
from gradio_client import Client
from loguru import logger

from src.app_service import AppService
from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore

# ==================== 路径常量 ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_DOCS_DIR = os.path.join(PROJECT_ROOT, "test_docs")

# ==================== CLI 选项 ====================


def pytest_addoption(parser: pytest.Parser) -> None:
    """添加 --start-app 选项：CI 模式下自动启动 Gradio 应用。"""
    parser.addoption(
        "--start-app",
        action="store_true",
        default=False,
        help="自动启动 Gradio 应用（用于 CI 环境）",
    )


# ==================== 应用生命周期（--start-app 模式） ====================


@pytest.fixture(scope="session")
def gradio_app_url(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    """提供 Gradio 应用 URL。

    如果指定了 --start-app，则自动启动应用进程；否则使用 GRADIO_URL 环境变量或默认地址。

    Yields:
        Gradio 应用 URL
    """
    url = os.getenv("GRADIO_URL", "http://127.0.0.1:7861")

    if request.config.getoption("--start-app"):
        # CI 模式：自动启动
        app_path = os.path.join(PROJECT_ROOT, "src", "app.py")
        env = os.environ.copy()
        env["PYTHONPATH"] = PROJECT_ROOT

        logger.info("Starting Gradio app at {} ...", app_path)
        proc = subprocess.Popen(
            ["python", app_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 等待应用就绪（最多 30 秒）
        max_wait = 30
        for attempt in range(max_wait):
            try:
                import urllib.request as req

                req.urlopen(f"{url}/", timeout=2)
                logger.info("Gradio app ready after {}s", attempt)
                break
            except Exception:
                time.sleep(1)
        else:
            # 超时：打印日志后仍继续，让测试自行失败更直观
            logger.error("Gradio app did not start within {}s", max_wait)

        yield url

        # 清理
        proc.terminate()
        proc.wait(timeout=10)
        logger.info("Gradio app stopped")
    else:
        # 本地模式：假设已手动启动
        yield url


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


# ==================== Gradio Client ====================


@pytest.fixture(scope="session")
def client(gradio_app_url: str) -> Generator[Client, None, None]:
    """提供 Gradio Client 实例，模拟前端用户操作。"""
    c = Client(gradio_app_url)
    # 等待应用实际就绪
    c.predict("")  # 空预测确保连接建立
    logger.info("Gradio Client connected to {}", gradio_app_url)
    yield c


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
