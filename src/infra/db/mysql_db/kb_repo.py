"""知识库 Repo — knowledge_base 表 CRUD。"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.infra.db.models.document import DocModel
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
                # 先找同名的活跃记录
                stmt = select(KbModel).where(
                    KbModel.user_id == user_id,
                    KbModel.name == name,
                    KbModel.is_deleted == 0,
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is not None:
                    return existing.id, False
                # 同名但被软删了 → 恢复它
                stmt = select(KbModel).where(
                    KbModel.user_id == user_id, KbModel.name == name
                )
                result = await session.execute(stmt)
                deleted = result.scalar_one_or_none()
                if deleted is None:
                    raise RuntimeError(
                        f"IntegrityError on '{name}' but query returned None"
                    )
                deleted.is_deleted = 0
                deleted.description = description
                await session.commit()
                return deleted.id, True

    async def get_kb_by_name(self, user_id: str, name: str) -> str | None:
        async with self._sf() as session:
            stmt = select(KbModel).where(
                KbModel.user_id == user_id,
                KbModel.name == name,
                KbModel.is_deleted == 0,
            )
            result = await session.execute(stmt)
            kb = result.scalar_one_or_none()
            return kb.id if kb else None

    async def get_kb_name_by_id(self, kb_id: str) -> str | None:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            return kb.name if kb else None

    async def get_all_kb(self, user_id: str = "") -> list[KbModel]:
        """获取用户的所有知识库列表（doc_count 为实时统计）。

        doc_count 不读取静态列（该列无任何维护逻辑），而是通过子查询
        实时统计 document 表中未删除（is_deleted=0）的文档数。

        Args:
            user_id: 用户 UUID，为空时返回所有用户的知识库

        Returns:
            知识库列表，doc_count 为实时统计值
        """
        async with self._sf() as session:
            # 子查询：按 kb_id 统计未删除的文档数
            doc_count_subq = (
                select(
                    DocModel.kb_id.label("kb_id"),
                    func.count(DocModel.id).label("doc_count"),
                )
                .where(DocModel.is_deleted == 0)
                .group_by(DocModel.kb_id)
                .subquery()
            )
            stmt = (
                select(
                    KbModel,
                    func.coalesce(doc_count_subq.c.doc_count, 0).label("doc_count"),
                )
                .outerjoin(doc_count_subq, doc_count_subq.c.kb_id == KbModel.id)
                .where(KbModel.is_deleted == 0)
            )
            if user_id:
                stmt = stmt.where(KbModel.user_id == user_id)
            result = await session.execute(stmt.order_by(KbModel.created_at.desc()))
            kbs = []
            for kb, doc_count in result.all():
                kb.doc_count = doc_count
                kbs.append(kb)
            return kbs

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
