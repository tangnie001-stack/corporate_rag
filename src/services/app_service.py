"""应用业务逻辑编排入口。

组合 KBService、DocumentService、ChatService 三个子 service，
对外提供统一的业务接口。
"""

import asyncio
from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore
from src.parsers.router import DocRouter
from src.rag.chain import RAGChain, RAGContext
from src.services.kb_service import KBService
from src.services.document_service import DocumentService
from src.services.chat_service import ChatService


class AppService:
    """UI 与后端之间的业务逻辑编排层。

    持有 KBService / DocumentService / ChatService 三个子 service，
    编排跨子 service 的多步骤操作。
    """

    def __init__(
        self,
        mysql_db: Optional[MySQLDB] = None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        rag_chain: Optional[RAGChain] = None,
    ) -> None:
        self.db = mysql_db or MySQLDB()
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.rag_chain = rag_chain or RAGChain()

        self.kb = KBService(self.db)
        self.document = DocumentService(self.db, self.vector_store, self.router)
        self.chat = ChatService(self.rag_chain)

    # ==================== 知识库 ====================

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        return await self.kb.list_knowledge_bases(user_id)

    async def create_knowledge_base(
        self, name: str, description: str = "", user_id: str = "",
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
        self, kb_id: str, doc_id: str, user_id: str,
    ) -> dict:
        return await self.document.delete_document(kb_id, doc_id, user_id)

    def upload_and_process(
        self, kb_id: str, file_path: str, filename: str,
    ) -> dict:
        return self.document.upload_and_process(kb_id, file_path, filename)

    # ==================== 问答 ====================

    def chat(
        self, kb_id: str, session_id: str, query: str,
    ) -> tuple[str, list[RAGContext]]:
        return self.chat.chat(kb_id, session_id, query)
