"""应用业务逻辑编排入口。

组合 KBService、DocumentService 两个子 service，
对外提供统一的业务接口。
"""

import asyncio

from loguru import logger

from src.chat.manager import ChatManager
from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.infra.db.mysql_db import (
    ChatRepo,
    DocumentRepo,
    EvalRepo,
    KbRepo,
    UserRepo,
)
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkQueryResult
from src.infra.search.bm25_index import BM25Index
from src.parsers.router import DocRouter
from src.services.agent_service import AgentService
from src.services.auth_service import AuthService
from src.services.document_service import DocumentService
from src.services.kb_service import KBService


class AppService:
    """UI 与后端之间的业务逻辑编排层。"""

    def __init__(
        self,
        session_factory=None,
        vector_store: VectorStore | None = None,
        router: DocRouter | None = None,
        chat_manager: ChatManager | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        if session_factory is None:
            from src.infra.db.engine import session_factory as _sf

            session_factory = _sf
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.chat_manager = chat_manager or ChatManager()
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )

        # Create repos from pool
        self._kb_repo = KbRepo(session_factory)
        self._doc_repo = DocumentRepo(session_factory)
        self._chat_repo = ChatRepo(session_factory)
        self._user_repo = UserRepo(session_factory)
        self._eval_repo = EvalRepo(session_factory)

        self.agent_service = agent_service or AgentService(
            vector_store=self.vector_store,
            bm25=self.bm25,
            chat_manager=self.chat_manager,
        )
        self.kb = KBService(self._kb_repo)
        self.document = DocumentService(self._doc_repo, self.vector_store, self.router)
        self._auth_service: AuthService | None = None

    # ==================== 认证 ====================

    @property
    def auth_service(self) -> AuthService:
        """延迟初始化的 AuthService 单例。"""
        if self._auth_service is None:
            from src.infra.redis_client import get_redis_client

            self._auth_service = AuthService(
                user_repo=self._user_repo,
                redis_client=get_redis_client(),
            )
        return self._auth_service

    # ==================== 知识库 ====================

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        return await self.kb.list_knowledge_bases(user_id)

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
    ) -> tuple[str, bool]:
        return await self.kb.create_knowledge_base(name, description, user_id)

    async def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库：软删文档 → 删 ChromaDB 集合 → 软删 KB。"""
        await self._doc_repo.soft_delete_documents_by_kb(kb_id)
        try:
            await asyncio.to_thread(self.vector_store.delete_collection, kb_id)
            logger.info("ChromaDB delete_collection: kb_id={}", kb_id)
        except Exception:  # noqa: BLE001
            logger.warning("ChromaDB delete collection failed for kb={}", kb_id)
        ok = await self._kb_repo.soft_delete_kb(kb_id)
        if ok:
            logger.info("Knowledge base soft-deleted: {}", kb_id)
            return True, "知识库已删除"
        logger.warning("Knowledge base '{}' not found for deletion", kb_id)
        return False, "知识库不存在"

    # ==================== 文档 ====================

    async def get_documents(self, kb_id: str) -> list[dict]:
        return await self.document.get_documents(kb_id)

    async def delete_document(
        self,
        kb_id: str,
        doc_id: str,
        user_id: str,
    ) -> dict:
        return await self.document.delete_document(kb_id, doc_id, user_id)

    # ==================== 问答 ====================

    # ===== Session/Message Delegates =====

    async def get_sessions(self, user_id: str = "") -> list[dict]:
        """获取最近 50 个会话。

        Args:
            user_id: 用户 ID，用于过滤属于该用户的会话
        """
        items = await self._chat_repo.get_sessions(user_id)
        return [
            {
                "id": s.id,
                "title": s.title,
                "kb_id": s.kb_id,
                "kb_name": s.kb_name,
                "message_count": s.message_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
            for s in items
        ]

    async def get_session_by_id(self, session_id: str) -> dict | None:
        """按 ID 查询会话。"""
        session = await self._chat_repo.get_session_by_id(session_id)
        if session is None:
            return None
        return {
            "id": session.id,
            "title": session.title,
            "kb_id": session.kb_id,
            "user_id": session.user_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    async def get_messages(self, session_id: str) -> list[dict]:
        """获取会话消息。"""
        msgs = await self._chat_repo.get_messages(session_id)
        return [
            {
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "kb_id": m.kb_id,
                "sources": m.sources,
                "prompt_tokens": m.prompt_tokens,
                "completion_tokens": m.completion_tokens,
                "total_tokens": m.total_tokens,
                "model_name": m.model_name,
                "created_at": m.created_at,
            }
            for m in msgs
        ]

    async def delete_session_and_messages(self, session_id: str) -> bool:
        """删除会话及其消息。"""
        await self.chat_manager.clear_history_async(session_id)
        return await self._chat_repo.delete_session_and_messages(session_id)

    async def set_chat_repo(self) -> None:
        """设置 chat_manager 的 ChatRepo。"""
        self.chat_manager.set_chat_repo(self._chat_repo)

    @property
    def chat_repo(self) -> ChatRepo:
        """返回会话/消息 Repo，供 API 层写入答案反馈等。"""
        return self._chat_repo

    async def save_session_async(
        self, session_id: str, title: str, kb_id: str, user_id: str = ""
    ) -> None:
        """持久化保存会话。"""
        await self.chat_manager.save_session_async(session_id, title, kb_id, user_id)

    async def save_user_async(self, session_id: str, kb_id: str, user_msg: str) -> None:
        """请求开始时同步写入单条 user 消息到 MySQL。"""
        await self.chat_manager.save_user_async(session_id, kb_id, user_msg)

    async def save_assistant_async(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[str] | None = None,
        status: str = "complete",
    ) -> None:
        """流结束后异步写入单条 assistant 消息到 MySQL。"""
        await self.chat_manager.save_assistant_async(
            session_id, kb_id, assistant_msg, sources, status
        )

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: list[str] | None = None,
    ) -> None:
        """批量持久化保存消息。"""
        await self.chat_manager.save_messages_async(
            session_id, kb_id, user_msg, assistant_msg, sources
        )

    # ==================== 配置 ====================

    @property
    def settings(self):
        """返回配置模块，允许 api 层通过 svc.settings.X 访问配置。"""
        import src.config.settings as _settings

        return _settings

    # ==================== 评估报告 ====================

    async def get_latest_eval_report(self, kb_id: str) -> dict | None:
        """获取知识库最新的 RAGAS 评估报告。"""
        report = await self._eval_repo.get_latest_report(kb_id)
        if report is None:
            return None
        return {
            "id": report.id,
            "kb_id": report.kb_id,
            "run_type": report.run_type,
            "qa_count": report.qa_count,
            "faithfulness": report.faithfulness,
            "answer_relevancy": report.answer_relevancy,
            "context_precision": report.context_precision,
            "context_recall": report.context_recall,
            "overall_score": report.overall_score,
            "passed": report.passed,
            "report_path": report.report_path,
            "triggered_by": report.triggered_by,
            "detail_json": report.detail_json,
            "eval_date": report.eval_date,
        }

    async def insert_eval_report(self, report) -> None:
        """插入 RAGAS 评估报告。"""
        await self._eval_repo.insert_report(report)

    # ==================== 知识库名称查询 ====================

    async def get_kb_by_name(self, user_id: str, name: str) -> str | None:
        """按名称查询知识库 ID。"""
        return await self._kb_repo.get_kb_by_name(user_id, name)

    # ==================== 分块查询 ====================

    async def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ):
        """分页查询文档的分块内容。"""
        result: ChunkQueryResult = await asyncio.to_thread(
            self.vector_store.get_chunks_paginated,
            doc_id,
            kb_id,
            page=page,
            page_size=page_size,
        )
        return result
