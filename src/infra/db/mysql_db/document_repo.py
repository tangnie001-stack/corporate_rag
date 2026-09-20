"""文档 Repo — document 表 CRUD。"""

import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models.document import DocModel
from src.infra.db.transaction import session_scope


class DocumentRepo:
    """文档 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def add_document(self, doc) -> None:
        async with self._sf() as session:
            d = DocModel(
                id=doc.id,
                kb_id=doc.kb_id,
                filename=doc.filename,
                file_type=getattr(doc, "file_type", ""),
                file_size=getattr(doc, "file_size", 0),
                file_path=getattr(doc, "file_path", None),
                user_id=getattr(doc, "user_id", ""),
                md5=getattr(doc, "md5", None),
            )
            session.add(d)
            await session.commit()

    async def get_documents(self, kb_id: str) -> list[DocModel]:
        async with self._sf() as session:
            stmt = (
                select(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
                .order_by(DocModel.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_document(self, doc_id: str) -> DocModel | None:
        async with self._sf() as session:
            return await session.get(DocModel, doc_id)

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        if not doc_ids:
            return {}
        async with self._sf() as session:
            stmt = select(DocModel).where(DocModel.id.in_(doc_ids))
            result = await session.execute(stmt)
            return {d.id: d.filename for d in result.scalars().all()}

    async def update_document_status(
        self, doc_id: str, status: str, session: AsyncSession | None = None, **kwargs
    ) -> None:
        """更新文档状态，并把 kwargs 中同名属性一并写入。

        Args:
            doc_id: 文档 ID
            status: 目标状态
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交
            **kwargs: 附加字段，仅当文档存在同名属性时写入
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            doc.status = status
            for key, value in kwargs.items():
                if hasattr(doc, key):
                    setattr(doc, key, value)

    async def update_document_meta_info(self, doc_id: str, meta: dict) -> None:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            existing = json.loads(doc.meta_info) if doc.meta_info else {}
            existing.update(meta)
            doc.meta_info = json.dumps(existing, ensure_ascii=False)
            await session.commit()

    async def soft_delete_document(
        self, doc_id: str, session: AsyncSession | None = None
    ) -> bool:
        """软删文档；文档不存在返回 False。

        Args:
            doc_id: 文档 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            True = 标记成功；False = 文档不存在
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return False
            doc.is_deleted = 1
            return True

    async def soft_delete_documents_by_kb(
        self, kb_id: str, session: AsyncSession | None = None
    ) -> None:
        """软删某知识库下的全部未删文档。

        Args:
            kb_id: 知识库 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            stmt = (
                update(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
                .values(is_deleted=1)
            )
            await session.execute(stmt)

    async def get_documents_by_kb(self, kb_id: str) -> list[DocModel]:
        async with self._sf() as session:
            stmt = select(DocModel).where(
                DocModel.kb_id == kb_id, DocModel.is_deleted == 0
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    def transaction(self):
        """打开一个事务边界；其中的 Repo / 存储方法须传入 `session=`。

        Returns:
            `session_scope(self._sf)` 异步上下文管理器
        """
        return session_scope(self._sf)
