"""知识库管理服务 — KB 的创建、查询、删除、领域维护。"""

from src.config.prompts import loader
from src.config.response_codes import Code
from src.infra.db.repos import KbRepo
from src.utils.errors import BusinessError


class KBService:
    """知识库 CRUD 操作。"""

    def __init__(self, kb_repo: KbRepo) -> None:
        self._kb_repo = kb_repo

    def _require_known_domain(self, domain: str) -> None:
        """领域合法性校验：判据是"存在对应的 base 模板"。

        非法值在**写入前**拒绝，不是读取时静默回退（spec「知识库领域绑定」）。

        Args:
            domain: 待校验的领域标识

        Raises:
            BusinessError: 不存在对应的 base 模板
        """
        if loader.has_domain(domain):
            return
        raise BusinessError(Code.VALIDATION_ERROR, Code.VALIDATION_ERROR_MSG, 400)

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        """列出所有知识库（含文档计数与领域）。"""
        kbs = await self._kb_repo.get_all_kb(user_id)
        return [
            {
                "id": kb.id,
                "name": kb.name,
                "doc_count": kb.doc_count,
                "domain": kb.domain,
            }
            for kb in kbs
        ]

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
        domain: str = "general",
    ) -> tuple[str, bool]:
        """创建知识库，已存在则直接返回。"""
        self._require_known_domain(domain)
        return await self._kb_repo.get_or_create_kb(user_id, name, description, domain)

    async def set_domain(self, kb_id: str, domain: str) -> bool:
        """更新知识库领域（最小写入口，spec 的领域 Scenario 据此可端到端验收）。

        Args:
            kb_id: 知识库 ID
            domain: 新领域标识

        Returns:
            True = 更新成功；False = 知识库不存在
        """
        self._require_known_domain(domain)
        return await self._kb_repo.update_kb_domain(kb_id, domain)

    async def soft_delete(self, kb_id: str) -> bool:
        """软删除知识库。"""
        return await self._kb_repo.soft_delete_kb(kb_id)

    async def get_kb_name_by_id(self, kb_id: str) -> str | None:
        """按 ID 查询知识库名称。"""
        return await self._kb_repo.get_kb_name_by_id(kb_id)
