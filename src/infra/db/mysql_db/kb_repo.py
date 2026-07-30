"""知识库 Repo — knowledge_base 表 CRUD。"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.infra.db.models.kb import KbModel


class KbRepo:
    """知识库 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        async with self._sf() as session:
            try:
                kb = KbModel(user_id=user_id, name=name, description=description)
                session.add(kb)
                await session.commit()
                return kb.id, True
            except IntegrityError:
                await session.rollback()
                stmt = select(KbModel).where(
                    KbModel.user_id == user_id, KbModel.name == name
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is None:
                    raise RuntimeError(
                        f"IntegrityError on '{name}' but query returned None"
                    )
                return existing.id, False

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        async with self._sf() as session:
            stmt = select(KbModel).where(
                KbModel.user_id == user_id, KbModel.name == name
            )
            result = await session.execute(stmt)
            kb = result.scalar_one_or_none()
            return kb.id if kb else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            return kb.name if kb else None

    async def get_all_kb(self, user_id: str = "") -> list[KbModel]:
        async with self._sf() as session:
            stmt = select(KbModel).where(KbModel.is_deleted == 0)
            if user_id:
                stmt = stmt.where(KbModel.user_id == user_id)
            result = await session.execute(stmt.order_by(KbModel.created_at.desc()))
            return list(result.scalars().all())

    async def delete_kb(self, kb_id: str) -> bool:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            if kb is None:
                return False
            await session.delete(kb)
            await session.commit()
            return True

    async def soft_delete_kb(self, kb_id: str) -> bool:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            if kb is None:
                return False
            kb.is_deleted = 1
            await session.commit()
            return True
