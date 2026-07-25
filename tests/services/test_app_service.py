"""Tests for AppService business logic layer."""

import pytest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from src.services.app_service import AppService
from src.utils.errors import AppError


class TestAppServiceInit:
    """AppService 初始化测试。"""

    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_defaults(self, mock_router, mock_vs, mock_db):
        """默认初始化应创建所有依赖实例。"""
        svc = AppService()
        assert svc.db is not None
        assert svc.vector_store is not None
        assert svc.router is not None

    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_custom_deps(self, mock_router, mock_vs, mock_db):
        """应接受注入的自定义依赖。"""
        db = MagicMock()
        vs = MagicMock()
        router = MagicMock()
        svc = AppService(mysql_db=db, vector_store=vs, router=router)
        assert svc.db is db
        assert svc.vector_store is vs
        assert svc.router is router


class TestAppServiceKBs:
    """知识库管理测试。"""

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_list_knowledge_bases(self, mock_router, mock_vs, mock_db):
        """列出所有知识库应从 db.get_all_kb 获取数据。"""
        db = MagicMock()
        db.get_all_kb = AsyncMock(return_value=[("id1", "KB1"), ("id2", "KB2")])
        svc = AppService(mysql_db=db)
        result = await svc.list_knowledge_bases()
        assert result == [("id1", "KB1"), ("id2", "KB2")]

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_create_kb_success(self, mock_router, mock_vs, mock_db):
        """创建知识库应返回 (kb_id, is_new)。"""
        db = MagicMock()
        db.get_or_create_kb = AsyncMock(return_value=("new_id", True))
        svc = AppService(mysql_db=db)
        kid, is_new = await svc.create_knowledge_base("测试库", "描述")
        assert kid == "new_id"
        assert is_new is True

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_kb_success(self, mock_router, mock_vs, mock_db):
        """删除知识库应软删除文档、清理向量、软删除 KB。"""
        db = MagicMock()
        db.soft_delete_documents_by_kb = AsyncMock()
        db.soft_delete_kb = AsyncMock(return_value=True)
        vs = MagicMock()
        vs.delete_collection = MagicMock(return_value=None)  # not async
        svc = AppService(mysql_db=db, vector_store=vs)
        ok, msg = await svc.delete_knowledge_base("kb_id")
        assert ok is True
        db.soft_delete_documents_by_kb.assert_called_once_with("kb_id")
        vs.delete_collection.assert_called_once_with("kb_id")
        db.soft_delete_kb.assert_called_once_with("kb_id")

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_kb_not_found(self, mock_router, mock_vs, mock_db):
        """删除不存在的知识库应返回 False 并提示。"""
        db = MagicMock()
        db.soft_delete_documents_by_kb = AsyncMock()
        db.soft_delete_kb = AsyncMock(return_value=False)
        svc = AppService(mysql_db=db)
        ok, msg = await svc.delete_knowledge_base("nonexistent")
        assert ok is False
        assert "不存在" in msg


class TestAppServiceDeleteDocument:
    """文档删除测试。"""

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_not_found(self, mock_router, mock_vs, mock_db):
        """删除不存在的文档应抛 DOC_NOT_FOUND。"""
        db = MagicMock()
        db.get_document = AsyncMock(return_value=None)
        svc = AppService(mysql_db=db)
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "nonexistent", "user")
        assert exc.value.code == "DOC_NOT_FOUND"

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_not_owner(self, mock_router, mock_vs, mock_db):
        """非上传者删除应抛 DOC_DELETE_NOT_ALLOWED。"""
        db = MagicMock()
        db.get_document = AsyncMock(
            return_value={
                "id": "d1",
                "user_id": "owner",
                "status": "ready",
                "filename": "t.pdf",
            }
        )
        svc = AppService(mysql_db=db)
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "d1", "other_user")
        assert exc.value.code == "DOC_DELETE_NOT_ALLOWED"

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_processing_status(self, mock_router, mock_vs, mock_db):
        """处理中的文档应抛 DOC_STATUS_CONFLICT。"""
        db = MagicMock()
        db.get_document = AsyncMock(
            return_value={
                "id": "d1",
                "user_id": "user",
                "status": "processing",
                "filename": "t.pdf",
            }
        )
        svc = AppService(mysql_db=db)
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "d1", "user")
        assert exc.value.code == "DOC_STATUS_CONFLICT"

    @pytest.mark.asyncio
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_success(self, mock_router, mock_vs, mock_db):
        """正常删除应返回 deleted 状态。"""
        db = MagicMock()
        db.get_document = AsyncMock(
            return_value={
                "id": "d1",
                "user_id": "user",
                "status": "ready",
                "filename": "t.pdf",
            }
        )
        db.soft_delete_document = AsyncMock(return_value=True)
        vs = MagicMock()
        svc = AppService(mysql_db=db, vector_store=vs)
        result = await svc.delete_document("kb", "d1", "user")
        assert result == {"doc_id": "d1", "filename": "t.pdf", "status": "deleted"}
        vs.delete_document.assert_called_once_with("kb", "d1")


class TestAppServiceUpload:
    """文档上传处理测试。"""

    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_upload_scanned_doc(self, mock_router, mock_vs, mock_db):
        """扫描件文档应返回错误并更新文档状态为 failed。"""
        db = MagicMock()
        db.add_document.return_value = "doc_id"
        vs = MagicMock()
        router = MagicMock()
        router.parse.return_value = MagicMock(
            chunks=[],
            total_pages=3,
            total_chars=10,
            file_type="pdf",
            is_scanned=True,
        )
        svc = AppService(mysql_db=db, vector_store=vs, router=router)
        result = svc.upload_and_process("test-kb-id", "/tmp/scan.pdf", "scan.pdf")
        assert result["success"] is False
        assert "扫描件" in result["error"]
        db.update_document_status.assert_called_once_with(
            "doc_id", "failed", error_msg=ANY
        )

    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_upload_parse_error(self, mock_router, mock_vs, mock_db):
        """解析抛出异常时应返回错误并记录失败状态。"""
        db = MagicMock()
        db.add_document.return_value = "doc_id"
        router = MagicMock()
        router.parse.side_effect = ValueError("Unsupported file type")
        svc = AppService(mysql_db=db, router=router)
        result = svc.upload_and_process("test-kb-id", "/tmp/bad.xyz", "bad.xyz")
        assert result["success"] is False
        assert "Unsupported" in result["error"]
        # 验证异常时更新文档状态为 failed
        db.update_document_status.assert_called_once_with(
            "doc_id", "failed", error_msg=ANY
        )


class TestAppServiceDocumentProcess:
    """文档异步处理测试。"""

    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @pytest.mark.asyncio
    async def test_process_document_success(self, mock_router, mock_vs, mock_db):
        """文档处理成功时状态更新为 ready。"""
        svc = AppService(mysql_db=mock_db, vector_store=mock_vs, router=mock_router)
        await svc.document.process_document(
            kb_id="test-kb",
            doc_id="test-doc",
            minio_key="path/to/file.pdf",
            filename="test.pdf",
            ext=".pdf",
        )
        mock_db.update_document_status.assert_called_with(
            "test-doc",
            "ready",
            chunk_count=ANY,
            processing_state="completed",
            processing_progress=100,
            processing_message=ANY,
            chunk_strategy=ANY,
        )
