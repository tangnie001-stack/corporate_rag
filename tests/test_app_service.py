"""Tests for AppService business logic layer."""

import pytest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from src.services.app_service import AppService
from src.infra.api_error import ApiError


class TestAppServiceInit:
    """AppService 初始化测试。"""

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_defaults(self, mock_router, mock_vs, mock_db, mock_rag):
        """默认初始化应创建所有依赖实例。"""
        svc = AppService()
        assert svc.rag_chain is not None
        assert svc.db is not None
        assert svc.vector_store is not None
        assert svc.router is not None

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_custom_deps(self, mock_router, mock_vs, mock_db, mock_rag):
        """应接受注入的自定义依赖。"""
        db = MagicMock()
        vs = MagicMock()
        router = MagicMock()
        rag = MagicMock()
        svc = AppService(mysql_db=db, vector_store=vs, router=router, rag_chain=rag)
        assert svc.db is db
        assert svc.vector_store is vs
        assert svc.router is router
        assert svc.rag_chain is rag


class TestAppServiceKBs:
    """知识库管理测试。"""

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_list_knowledge_bases(self, mock_router, mock_vs, mock_db, mock_rag):
        """列出所有知识库应从 db.get_all_kb 获取数据。"""
        db = MagicMock()
        db.get_all_kb.return_value = [("id1", "KB1"), ("id2", "KB2")]
        svc = AppService(mysql_db=db)
        result = svc.list_knowledge_bases()
        assert result == [("id1", "KB1"), ("id2", "KB2")]

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_create_kb_success(self, mock_router, mock_vs, mock_db, mock_rag):
        """创建知识库应返回 (kb_id, is_new)。"""
        db = MagicMock()
        db.get_or_create_kb.return_value = ("new_id", True)
        svc = AppService(mysql_db=db)
        kid, is_new = svc.create_knowledge_base("测试库", "描述")
        assert kid == "new_id"
        assert is_new is True

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_kb_success(self, mock_router, mock_vs, mock_db, mock_rag):
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

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_kb_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
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

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
        """删除不存在的文档应抛 DOC_NOT_FOUND。"""
        db = MagicMock()
        db.get_document = AsyncMock(return_value=None)
        svc = AppService(mysql_db=db)
        with pytest.raises(ApiError) as exc:
            await svc.delete_document("kb", "nonexistent", "user")
        assert exc.value.code == "DOC_NOT_FOUND"

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_not_owner(self, mock_router, mock_vs, mock_db, mock_rag):
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
        with pytest.raises(ApiError) as exc:
            await svc.delete_document("kb", "d1", "other_user")
        assert exc.value.code == "DOC_DELETE_NOT_ALLOWED"

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_processing_status(
        self, mock_router, mock_vs, mock_db, mock_rag
    ):
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
        with pytest.raises(ApiError) as exc:
            await svc.delete_document("kb", "d1", "user")
        assert exc.value.code == "DOC_STATUS_CONFLICT"

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    async def test_delete_success(self, mock_router, mock_vs, mock_db, mock_rag):
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

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_upload_and_process(self, mock_router, mock_vs, mock_db, mock_rag):
        """正常上传文档应完成解析、向量化并更新状态为 ready。"""
        db = MagicMock()
        db.add_document.return_value = "doc_id"

        vs = MagicMock()
        vs.add_chunks.return_value = 5

        router = MagicMock()
        router.parse.return_value = MagicMock(
            chunks=[
                MagicMock(content="c1", metadata={}, chunk_id="c:0"),
                MagicMock(content="c2", metadata={}, chunk_id="c:1"),
            ],
            total_pages=1,
            total_chars=100,
            file_type="txt",
            is_scanned=False,
        )

        svc = AppService(mysql_db=db, vector_store=vs, router=router)
        result = svc.upload_and_process("test-kb-id", "/tmp/test.txt", "test.txt")

        assert result["success"] is True
        assert result["chunk_count"] == 5
        router.parse.assert_called_once_with("/tmp/test.txt")
        vs.add_chunks.assert_called_once()
        db.update_document_status.assert_called_once_with(
            "doc_id", "ready", chunk_count=5
        )

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_upload_scanned_doc(self, mock_router, mock_vs, mock_db, mock_rag):
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

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_upload_parse_error(self, mock_router, mock_vs, mock_db, mock_rag):
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


class TestAppServiceChat:
    """问答功能测试。"""

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_chat(self, mock_router, mock_vs, mock_db, mock_rag):
        """正常问答应返回拼接的回答和引用列表。"""
        rag = MagicMock()

        def mock_gen():
            yield "贵州"
            yield "茅台"
            yield "营收1,741亿元。"

        rag.chat_with_citations.return_value = (
            mock_gen(),
            [
                MagicMock(
                    source="年报.pdf",
                    page=3,
                    content="营收1,741亿元",
                    to_citation=lambda: "> citation",
                ),
            ],
        )

        svc = AppService(rag_chain=rag)
        answer, citations = svc.chat("test-kb-id", "sess_1", "营收多少？")

        assert "贵州茅台营收1,741亿元" in answer
        assert len(citations) == 1
        rag.chat_with_citations.assert_called_once_with(
            "test-kb-id", "sess_1", "营收多少？"
        )
        rag.chat_manager.add_message.assert_called_once_with(
            "sess_1",
            "assistant",
            "贵州茅台营收1,741亿元。",
            sources=ANY,
        )

    @patch("src.services.app_service.RAGChain")
    @patch("src.services.app_service.MySQLDB")
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_chat_kb_not_found(self, mock_router, mock_vs, mock_db, mock_rag):
        """知识库不存在的问答应返回错误信息。"""
        rag = MagicMock()
        rag.chat_with_citations.return_value = (
            (t for t in ["知识库 'xx' 不存在"]),
            [],
        )
        svc = AppService(rag_chain=rag)
        answer, citations = svc.chat("nonexistent-kb", "sess", "q")
        assert "不存在" in answer
        assert citations == []
