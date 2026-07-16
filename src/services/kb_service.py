"""知识库管理服务 — KB 的创建、查询、删除。"""

from src.infra.db.mysql_db import MySQLDB


class KBService:
    """知识库 CRUD 操作。

    Attributes:
        db: MySQLDB 实例，所有知识库元数据操作委托给它
    """

    def __init__(self, db: MySQLDB) -> None:
        self.db = db

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        """列出所有知识库（含文档计数）。"""
        return await self.db.get_all_kb(user_id)

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
    ) -> tuple[str, bool]:
        """创建知识库，已存在则直接返回。

        Returns:
            (kb_id, is_new) 元组
        """
        return await self.db.get_or_create_kb(user_id, name, description)

    async def soft_delete_documents_by_kb(self, kb_id: str) -> None:
        """软删除知识库下所有文档。"""
        await self.db.soft_delete_documents_by_kb(kb_id)

    async def soft_delete(self, kb_id: str) -> bool:
        """软删除知识库。"""
        return await self.db.soft_delete_kb(kb_id)
