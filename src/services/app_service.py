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
