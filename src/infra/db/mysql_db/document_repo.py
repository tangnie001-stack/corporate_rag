"""文档 Repo — document 表 CRUD。"""

import json
from typing import Optional
from sqlalchemy import select, update
from src.infra.db.models.document import DocModel


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

    async def get_document(self, doc_id: str) -> Optional[DocModel]:
        async with self._sf() as session:
            return await session.get(DocModel, doc_id)

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        if not doc_ids:
            return {}
        async with self._sf() as session:
            stmt = select(DocModel).where(DocModel.id.in_(doc_ids))
            result = await session.execute(stmt)
            return {d.id: d.filename for d in result.scalars().all()}

    async def update_document_status(self, doc_id: str, status: str, **kwargs) -> None:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            doc.status = status
            for key, value in kwargs.items():
                if hasattr(doc, key):
                    setattr(doc, key, value)
            await session.commit()

    async def update_document_meta_info(self, doc_id: str, meta: dict) -> None:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            existing = json.loads(doc.meta_info) if doc.meta_info else {}
            existing.update(meta)
            doc.meta_info = json.dumps(existing, ensure_ascii=False)
            await session.commit()

    async def soft_delete_document(self, doc_id: str) -> bool:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return False
            doc.is_deleted = 1
            await session.commit()
            return True

    async def soft_delete_documents_by_kb(self, kb_id: str) -> None:
        async with self._sf() as session:
            stmt = (
                update(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
                .values(is_deleted=1)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_documents_by_kb(self, kb_id: str) -> list[DocModel]:
        async with self._sf() as session:
            stmt = (
                select(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
