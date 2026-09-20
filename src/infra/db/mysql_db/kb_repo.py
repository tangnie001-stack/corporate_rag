"""知识库 Repo — knowledge_base 表 CRUD。"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models.document import DocModel
from src.infra.db.models.kb import KbModel
from src.infra.db.transaction import session_scope


class KbRepo:
    """知识库 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        """按 (user_id, name) 取或建知识库。

        三态语义（返回值被 kb_service 消费，不可随意改）：
        - 新建 → (新 id, True)
        - 同名但被软删 → 复活原记录，返回 (原 id, True)
        - 同名且活跃 → (原 id, False)

        实现仍走"插入撞唯一键 → 回滚 → 回读"：三态用 ON CONFLICT DO UPDATE 表达不了
        （RETURNING 只能看到更新后的行，无法区分"原本活跃"与"刚被复活"），
        强行改写会改掉返回值语义。
        """
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

    async def soft_delete_kb(
        self, kb_id: str, session: AsyncSession | None = None
    ) -> bool:
        """软删知识库；知识库不存在返回 False。

        Args:
            kb_id: 知识库 ID
            session: 外部事务边界提供的会话；None 时本方法自开会话并提交

        Returns:
            True = 标记成功；False = 知识库不存在
        """
        async with session_scope(self._sf, session) as session:  # noqa: PLR1704  # 复用形参名 session 是刻意的：先取形参再绑定会话
            kb = await session.get(KbModel, kb_id)
            if kb is None:
                return False
            kb.is_deleted = 1
            return True

    def transaction(self):
        """打开一个事务边界；其中的 Repo / 存储方法须传入 `session=`。

        Returns:
            `session_scope(self._sf)` 异步上下文管理器
        """
        return session_scope(self._sf)
