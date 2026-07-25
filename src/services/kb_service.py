"""知识库管理服务 — KB 的创建、查询、删除。"""

from src.infra.db.mysql_db import KbRepo


class KBService:
    """知识库 CRUD 操作。"""

    def __init__(self, kb_repo: KbRepo) -> None:
        self._kb_repo = kb_repo

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        """列出所有知识库（含文档计数）。"""
        kbs = await self._kb_repo.get_all_kb(user_id)
        return [
            {"id": kb.id, "name": kb.name, "doc_count": kb.doc_count}
            for kb in kbs
        ]

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
    ) -> tuple[str, bool]:
        """创建知识库，已存在则直接返回。"""
        return await self._kb_repo.get_or_create_kb(user_id, name, description)

    async def soft_delete(self, kb_id: str) -> bool:
        """软删除知识库。"""
        return await self._kb_repo.soft_delete_kb(kb_id)

    async def get_kb_name_by_id(self, kb_id: str) -> str | None:
        """按 ID 查询知识库名称。"""
        return await self._kb_repo.get_kb_name_by_id(kb_id)
