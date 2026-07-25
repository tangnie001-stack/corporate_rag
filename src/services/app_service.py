"""应用业务逻辑编排入口。

组合 KBService、DocumentService 两个子 service，
对外提供统一的业务接口。
"""

import asyncio
from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore
from src.parsers.router import DocRouter
from src.chat.manager import ChatManager
from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.infra.search.bm25_index import BM25Index
from src.services.agent_service import AgentService
from src.services.auth_service import AuthService
from src.services.document_service import DocumentService
from src.services.kb_service import KBService


class AppService:
    """UI 与后端之间的业务逻辑编排层。

    持有 KBService / DocumentService、agent_service 三个子 service，
    编排跨子 service 的多步骤操作。
    """

    def __init__(
        self,
        mysql_db: Optional[MySQLDB] = None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        chat_manager: Optional[ChatManager] = None,
        agent_service: Optional[AgentService] = None,
    ) -> None:
        self.db = mysql_db or MySQLDB()
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.chat_manager = chat_manager or ChatManager()
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )
        self.agent_service = agent_service or AgentService(
            vector_store=self.vector_store,
            bm25=self.bm25,
            chat_manager=self.chat_manager,
        )
        self.kb = KBService(self.db)
        self.document = DocumentService(self.db, self.vector_store, self.router)
        self._auth_service: Optional[AuthService] = None

    # ==================== 认证 ====================

    @property
    def auth_service(self) -> AuthService:
        """延迟初始化的 AuthService 单例。

        Returns:
            AuthService 实例（使用 AppService 的 db 和 Redis 连接）
        """
        if self._auth_service is None:
            from src.infra.redis_client import get_redis_client

            self._auth_service = AuthService(
                db=self.db,
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
        await self.kb.soft_delete_documents_by_kb(kb_id)
        try:
            await asyncio.to_thread(self.vector_store.delete_collection, kb_id)
            logger.info("ChromaDB delete_collection: kb_id={}", kb_id)
        except Exception:
            logger.warning("ChromaDB delete collection failed for kb={}", kb_id)
        ok = await self.kb.soft_delete(kb_id)
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

    def upload_and_process(
        self,
        kb_id: str,
        file_path: str,
        filename: str,
    ) -> dict:
        return self.document.upload_and_process(kb_id, file_path, filename)

    # ==================== 问答 ====================

    # ===== Session/Message Delegates =====

    async def get_sessions(self) -> list[dict]:
        """获取最近 50 个会话。"""
        return await self.db.get_sessions()

    async def get_session_by_id(self, session_id: str) -> Optional[dict]:
        """按 ID 查询会话。"""
        return await self.db.get_session_by_id(session_id)

    async def get_messages(self, session_id: str) -> list[dict]:
        """获取会话消息。"""
        return await self.db.get_messages(session_id)

    async def delete_session_and_messages(self, session_id: str) -> bool:
        """删除会话及其消息。"""
        await self.chat_manager.clear_history_async(session_id)
        return await self.db.delete_session_and_messages(session_id)

    async def set_mysql_db(self, db: MySQLDB) -> None:
        """设置 chat_manager 的 MySQL DB 实例。"""
        await self.chat_manager.set_mysql_db(db)

    async def save_session_async(
        self, session_id: str, title: str, kb_id: str, user_id: str = ""
    ) -> None:
        """持久化保存会话。"""
        await self.chat_manager.save_session_async(session_id, title, kb_id)

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        """批量持久化保存消息。"""
        await self.chat_manager.save_messages_async(
            session_id, kb_id, user_msg, assistant_msg, sources
        )
