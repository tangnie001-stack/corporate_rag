"""文档上传模块 — 页面集成测试。

覆盖正常上传（PDF / DOCX / TXT / 混合格式）和异常场景（未选 KB、损坏文件）。
每个测试用例进行组件 + 数据库双重验证。
"""

from __future__ import annotations

import os

import pytest
from gradio_client import Client
from loguru import logger

from src.app_service import AppService
from src.infra.db.mysql_db import MySQLDB

from .conftest import get_test_doc_path

# 文档处理可能较慢（PyMuPDF 解析 + ChromaDB 写入）
UPLOAD_TIMEOUT = 120


def _get_doc_table_ids(doc_table) -> int:
    """从 doc_table DataFrame 结果中获取行数。

    gradio_client 返回的 DataFrame 可能是 list 或 dict 格式。
    """
    if doc_table is None:
        return 0
    if isinstance(doc_table, list):
        return len(doc_table)
    if isinstance(doc_table, dict):
        # 可能包含 data/headers 键的 dict
        data = doc_table.get("data") or doc_table.get("value") or []
        return len(data)
    return 0


def _has_ready_doc(doc_table) -> bool:
    """检查文档表格中是否有状态为 ready 的行。"""
    if doc_table is None:
        return False
    rows = []
    if isinstance(doc_table, list):
        rows = doc_table
    elif isinstance(doc_table, dict):
        rows = doc_table.get("data") or doc_table.get("value") or []
    # 状态在第 4 列（index 3），包含 ✅ 或 ready 字样
    for row in rows:
        if len(row) >= 4:
            status_str = str(row[3])
            if "✅" in status_str or "ready" in status_str:
                return True
    return False


# ==================== 正常上传 ====================


class TestFileUploadNormal:
    """正常文档上传场景集成测试。"""

    @pytest.fixture(autouse=True)
    def _setup_kb(
        self,
        client: Client,
        service: AppService,
        test_kb_name: str,
    ) -> None:
        """每个测试用例前创建一个测试知识库。"""
        self.kb_name = test_kb_name
        client.predict(test_kb_name, api_name="/handle_create_kb")
        self.kb_id = service.db.get_kb_by_name(test_kb_name)
        assert self.kb_id is not None

    def _upload_and_verify(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
        file_path: str,
        file_type: str,
    ) -> None:
        """上传文件并验证结果的辅助方法。"""
        filename = os.path.basename(file_path)
        logger.info("  上传文件: {} ({})", filename, file_type)

        # 上传（handle_upload 接收 kb_id）
        status, doc_table = client.predict(
            self.kb_id,
            [file_path],
            api_name="/handle_upload",
        )

        # 组件验证
        logger.info("  upload status: {}", status)
        assert "✅" in status, f"上传应成功: {status}"
        assert _has_ready_doc(doc_table), (
            f"文档表格应显示 ready 状态\n表格原文: {doc_table}"
        )

        # 数据库验证
        docs = mysql_db.get_documents(self.kb_id)
        matched = [d for d in docs if d.get("filename") == filename]
        assert len(matched) == 1, f"MySQL 应有一条 '{filename}' 记录"
        doc = matched[0]
        assert doc.get("status") == "ready", (
            f"文档状态应为 ready，实际: {doc.get('status')}"
        )
        assert doc.get("chunk_count", 0) > 0, (
            f"chunk_count 应 > 0，实际: {doc.get('chunk_count')}"
        )
        logger.info("  ✓ chunk_count={}", doc.get("chunk_count"))

    def test_upload_pdf(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
    ) -> None:
        """TC10: 上传 PDF 文件。"""
        file_path = get_test_doc_path("sample.pdf")
        self._upload_and_verify(client, service, mysql_db, file_path, "pdf")

    def test_upload_docx(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
    ) -> None:
        """TC11: 上传 DOCX 文件。"""
        file_path = get_test_doc_path("sample.docx")
        self._upload_and_verify(client, service, mysql_db, file_path, "docx")

    def test_upload_txt(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
    ) -> None:
        """TC12: 上传 TXT 文件（UTF-8）。"""
        file_path = get_test_doc_path("sample.txt")
        self._upload_and_verify(client, service, mysql_db, file_path, "txt")

    def test_upload_gbk_txt(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
    ) -> None:
        """TC13: 上传 GBK 编码的 TXT 文件。"""
        file_path = get_test_doc_path("sample_gbk.txt")
        self._upload_and_verify(client, service, mysql_db, file_path, "txt")

    def test_upload_multiple(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
    ) -> None:
        """TC14: 同时上传 PDF + DOCX + TXT 多个文件。"""
        files = [
            get_test_doc_path("sample.pdf"),
            get_test_doc_path("sample.docx"),
            get_test_doc_path("sample.txt"),
        ]
        filenames = [os.path.basename(f) for f in files]
        logger.info("TC14: 多文件上传: {}", filenames)

        # 上传所有文件（handle_upload 接收 kb_id）
        status, doc_table = client.predict(
            self.kb_id,
            files,
            api_name="/handle_upload",
        )

        # 组件验证
        logger.info("  upload status:\n{}", status)
        # 每个文件应有 ✅ 标识
        for filename in filenames:
            assert f"✅ {filename}" in status or "处理完成" in status, (
                f"状态消息应包含 {filename} 的处理结果"
            )

        # 数据库验证：3 条记录
        docs = mysql_db.get_documents(self.kb_id)
        uploaded_names = {d.get("filename") for d in docs}
        for filename in filenames:
            assert filename in uploaded_names, f"MySQL 应包含 '{filename}'"
        ready_docs = [d for d in docs if d.get("status") == "ready"]
        assert len(ready_docs) == len(filenames), (
            f"所有 {len(filenames)} 个文档应为 ready 状态，实际: {len(ready_docs)}"
        )


# ==================== 异常上传场景 ====================


class TestFileUploadError:
    """文档上传异常场景集成测试。"""

    def test_upload_no_kb(
        self,
        client: Client,
    ) -> None:
        """TC15: 未选择知识库时上传。"""
        logger.info("TC15: 未选 KB 上传")

        file_path = get_test_doc_path("sample.txt")
        status, doc_table = client.predict(
            "",
            [file_path],
            api_name="/handle_upload",
        )

        assert "请先选择" in status, f"未选 KB 应提示: {status}"
        # 数据库不应新增记录（但在 gradio_client 层面无法直接验证，因为 KB 名为空）

    def test_upload_corrupted_file(
        self,
        client: Client,
        service: AppService,
        mysql_db: MySQLDB,
        test_kb_name: str,
        corrupted_file_path: str,
    ) -> None:
        """TC16: 上传损坏的文件。

        先创建 KB，再上传一个无效的 PDF 文件。
        验证文档状态被标记为 failed。
        """
        logger.info("TC16: 上传损坏文件")

        # 创建 KB
        client.predict(test_kb_name, api_name="/handle_create_kb")
        kb_id = service.db.get_kb_by_name(test_kb_name)

        # 上传损坏文件（handle_upload 接收 kb_id）
        status, doc_table = client.predict(
            kb_id,
            [corrupted_file_path],
            api_name="/handle_upload",
        )

        # 组件验证：应有失败提示
        logger.info("  upload status: {}", status)
        assert "❌" in status or "失败" in status, f"损坏文件应提示失败: {status}"

        # 数据库验证：status = failed
        docs = mysql_db.get_documents(kb_id)
        assert len(docs) >= 1, "MySQL 应有文档记录"
        failed_docs = [d for d in docs if d.get("status") == "failed"]
        assert len(failed_docs) >= 1, "应有状态为 failed 的文档记录"
        logger.info("  ✓ 失败记录: {} 条", len(failed_docs))
