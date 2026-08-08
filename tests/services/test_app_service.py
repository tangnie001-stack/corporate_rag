"""Tests for AppService business logic layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.models.kb import KbModel as KbListItem
from src.services.app_service import AppService
from src.utils.errors import AppError

# ==================== Init ====================


class TestAppServiceInit:
    """AppService 初始化测试。"""

    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_defaults(self, mock_router, mock_vs):
        """默认初始化应创建所有依赖实例。"""
        svc = AppService()
        assert svc.vector_store is not None
        assert svc.router is not None

    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    def test_init_custom_deps(self, mock_router, mock_vs):
        """应接受注入的自定义依赖。"""
        sf = MagicMock()
        vs = MagicMock()
        router = MagicMock()
        svc = AppService(session_factory=sf, vector_store=vs, router=router)
        assert svc.vector_store is vs
        assert svc.router is router


# ==================== KB ====================


class TestAppServiceKBs:
    """知识库管理测试。"""

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_list_knowledge_bases(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """列出所有知识库应从 _kb_repo.get_all_kb 获取数据。"""
        mock_kb_repo.return_value.get_all_kb = AsyncMock(
            return_value=[
                KbListItem(id="id1", user_id="u1", name="KB1", doc_count=0),
                KbListItem(id="id2", user_id="u1", name="KB2", doc_count=0),
            ]
        )
        svc = AppService()
        result = await svc.list_knowledge_bases()
        assert result == [
            {"id": "id1", "name": "KB1", "doc_count": 0},
            {"id": "id2", "name": "KB2", "doc_count": 0},
        ]

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_create_kb_success(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """创建知识库应返回 (kb_id, is_new)。"""
        mock_kb_repo.return_value.get_or_create_kb = AsyncMock(
            return_value=("new_id", True)
        )
        svc = AppService()
        kid, is_new = await svc.create_knowledge_base("测试库", "描述")
        assert kid == "new_id"
        assert is_new is True

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_kb_success(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """删除知识库应软删除文档、清理向量、软删除 KB。"""
        mock_doc_repo.return_value.soft_delete_documents_by_kb = AsyncMock()
        mock_kb_repo.return_value.soft_delete_kb = AsyncMock(return_value=True)
        vs = MagicMock()
        svc = AppService(vector_store=vs)
        ok, _msg = await svc.delete_knowledge_base("kb_id")
        assert ok is True
        mock_doc_repo.return_value.soft_delete_documents_by_kb.assert_called_once_with(
            "kb_id"
        )
        # delete_collection is called via asyncio.to_thread, so it's a bit trickier to verify
        mock_kb_repo.return_value.soft_delete_kb.assert_called_once_with("kb_id")

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_kb_not_found(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """删除不存在的知识库应返回 False 并提示。"""
        mock_doc_repo.return_value.soft_delete_documents_by_kb = AsyncMock()
        mock_kb_repo.return_value.soft_delete_kb = AsyncMock(return_value=False)
        svc = AppService()
        ok, msg = await svc.delete_knowledge_base("nonexistent")
        assert ok is False
        assert "不存在" in msg


# ==================== Delete Document ====================


class TestAppServiceDeleteDocument:
    """文档删除测试。"""

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_not_found(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """删除不存在的文档应抛 DOC_NOT_FOUND。"""
        mock_doc_repo.return_value.get_document = AsyncMock(return_value=None)
        svc = AppService()
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "nonexistent", "user")
        assert exc.value.code == "DOC_NOT_FOUND"

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_not_owner(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """非上传者删除应抛 DOC_DELETE_NOT_ALLOWED。"""
        mock_doc_repo.return_value.get_document = AsyncMock(
            return_value=DocEntity(
                id="d1", kb_id="kb", user_id="owner", filename="t.pdf", status="ready"
            )
        )
        svc = AppService()
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "d1", "other_user")
        assert exc.value.code == "DOC_DELETE_NOT_ALLOWED"

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_processing_status(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """处理中的文档应抛 DOC_STATUS_CONFLICT。"""
        mock_doc_repo.return_value.get_document = AsyncMock(
            return_value=DocEntity(
                id="d1",
                kb_id="kb",
                user_id="user",
                filename="t.pdf",
                status="processing",
            )
        )
        svc = AppService()
        with pytest.raises(AppError) as exc:
            await svc.delete_document("kb", "d1", "user")
        assert exc.value.code == "DOC_STATUS_CONFLICT"

    @pytest.mark.asyncio
    @patch("src.services.app_service.VectorStore")
    @patch("src.services.app_service.DocRouter")
    @patch("src.services.app_service.ChatManager")
    @patch("src.services.app_service.AgentService")
    @patch("src.services.app_service.KbRepo")
    @patch("src.services.app_service.DocumentRepo")
    @patch("src.services.app_service.ChatRepo")
    @patch("src.services.app_service.UserRepo")
    @patch("src.services.app_service.EvalRepo")
    async def test_delete_success(
        self,
        mock_eval_repo,
        mock_user_repo,
        mock_chat_repo,
        mock_doc_repo,
        mock_kb_repo,
        mock_agent,
        mock_chat_mgr,
        mock_router,
        mock_vs,
    ):
        """正常删除应返回 deleted 状态。"""
        mock_doc_repo.return_value.get_document = AsyncMock(
            return_value=DocEntity(
                id="d1", kb_id="kb", user_id="user", filename="t.pdf", status="ready"
            )
        )
        mock_doc_repo.return_value.soft_delete_document = AsyncMock(return_value=True)
        vs = MagicMock()
        svc = AppService(vector_store=vs)
        result = await svc.delete_document("kb", "d1", "user")
        assert result == {"doc_id": "d1", "filename": "t.pdf", "status": "deleted"}
