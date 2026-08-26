"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

import json

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.infra.db.models.chat import MessageModel, SessionModel
from src.infra.db.models.feedback import FeedbackModel
from src.infra.db.models.kb import KbModel


class ChatRepo:
    """会话/消息 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_session(self, session) -> None:
        """session: 带 .id .user_id .title .kb_id 属性的对象。

        幂等：同一 session_id 已存在（多轮对话重复持久化）时静默跳过，
        不抛主键冲突异常。
        """
        async with self._sf() as s:
            try:
                s_obj = SessionModel(
                    id=session.id,
                    user_id=session.user_id,
                    title=session.title,
                    kb_id=session.kb_id,
                )
                s.add(s_obj)
                await s.commit()
            except IntegrityError:
                await s.rollback()
                existing = await s.get(SessionModel, session.id)
                if existing is None:
                    raise
                # 已存在 → 幂等跳过（首轮已创建，多轮对话不重复插入）

    async def get_sessions(self, user_id: str = "") -> list:
        """返回 Row 对象（支持 .id 属性访问，兼容旧 SessionListItem 用法）。"""
        async with self._sf() as session:
            stmt = (
                select(
                    SessionModel.id,
                    SessionModel.title,
                    SessionModel.kb_id,
                    SessionModel.created_at,
                    SessionModel.updated_at,
                    func.coalesce(KbModel.name, "所有知识库").label("kb_name"),
                    func.count(MessageModel.id).label("message_count"),
                )
                .outerjoin(
                    KbModel,
                    (SessionModel.kb_id == KbModel.id) & (SessionModel.kb_id != ""),
                )
                .outerjoin(MessageModel, MessageModel.session_id == SessionModel.id)
                .where(SessionModel.is_deleted == 0)
            )

            if user_id:
                stmt = stmt.where(SessionModel.user_id == user_id)

            stmt = (
                stmt.group_by(SessionModel.id)
                .order_by(SessionModel.updated_at.desc())
                .limit(50)
            )

            result = await session.execute(stmt)
            return list(result.all())

    async def get_session_by_id(self, session_id: str) -> SessionModel | None:
        async with self._sf() as session:
            return await session.get(SessionModel, session_id)

    async def get_messages(self, session_id: str) -> list[MessageModel]:
        async with self._sf() as session:
            stmt = (
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def save_message(self, msg) -> None:
        """msg: 带 .session_id .role .content .sources 等属性的对象。"""
        async with self._sf() as session:
            sources_json = (
                json.dumps(msg.sources, ensure_ascii=False)
                if getattr(msg, "sources", None)
                else None
            )
            m = MessageModel(
                session_id=msg.session_id,
                kb_id=getattr(msg, "kb_id", ""),
                role=msg.role,
                content=msg.content,
                sources=sources_json,
                prompt_tokens=getattr(msg, "prompt_tokens", 0),
                completion_tokens=getattr(msg, "completion_tokens", 0),
                total_tokens=getattr(msg, "total_tokens", 0),
                model_name=getattr(msg, "model_name", ""),
            )
            session.add(m)
            await session.commit()

    async def save_feedback(
        self,
        session_id: str,
        message_index: int,
        rating: str,
        comment: str,
    ) -> None:
        """写入一条答案反馈记录到 feedback 表。

        Args:
            session_id: 会话 ID
            message_index: 会话内消息序号（前端消息数组索引，从 0 起）
            rating: 评分（positive/negative）
            comment: 用户评论
        """
        async with self._sf() as session:
            fb = FeedbackModel(
                session_id=session_id,
                message_index=message_index,
                rating=rating,
                comment=comment,
            )
            session.add(fb)
            await session.commit()

    async def delete_session_and_messages(self, session_id: str) -> bool:
        async with self._sf() as session:
            await session.execute(
                delete(MessageModel).where(MessageModel.session_id == session_id)
            )
            session_obj = await session.get(SessionModel, session_id)
            deleted = session_obj is not None
            if session_obj:
                await session.delete(session_obj)
            await session.commit()
            return deleted
