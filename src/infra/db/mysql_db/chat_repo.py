"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

import json
from typing import Optional
from sqlalchemy import select, func, delete
from src.infra.db.models.kb import KbModel
from src.infra.db.models.chat import SessionModel, MessageModel


class ChatRepo:
    """会话/消息 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_session(self, session) -> None:
        """session: 带 .id .user_id .title .kb_id 属性的对象。"""
        async with self._sf() as s:
            s_obj = SessionModel(
                id=session.id,
                user_id=session.user_id,
                title=session.title,
                kb_id=session.kb_id,
            )
            s.add(s_obj)
            await s.commit()

    async def get_sessions(self, user_id: str = "") -> list:
        """返回 Row 对象（支持 .id 属性访问，兼容旧 SessionListItem 用法）。"""
        async with self._sf() as session:
            stmt = select(
                SessionModel.id,
                SessionModel.title,
                SessionModel.kb_id,
                SessionModel.created_at,
                SessionModel.updated_at,
                func.coalesce(KbModel.name, "所有知识库").label("kb_name"),
                func.count(MessageModel.id).label("message_count"),
            ).outerjoin(
                KbModel,
                (SessionModel.kb_id == KbModel.id) & (SessionModel.kb_id != ""),
            ).outerjoin(
                MessageModel, MessageModel.session_id == SessionModel.id
            ).where(SessionModel.is_deleted == 0)

            if user_id:
                stmt = stmt.where(SessionModel.user_id == user_id)

            stmt = stmt.group_by(SessionModel.id).order_by(
                SessionModel.updated_at.desc()
            ).limit(50)

            result = await session.execute(stmt)
            return list(result.all())

    async def get_session_by_id(self, session_id: str) -> Optional[SessionModel]:
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
