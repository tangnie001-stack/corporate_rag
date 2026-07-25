"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

from typing import Optional
import json
from src.config.queries import (
    INSERT_SESSION,
    SELECT_SESSIONS,
    SELECT_SESSION_BY_ID,
    SELECT_MESSAGES_BY_SESSION,
    INSERT_MESSAGE,
    DELETE_SESSION,
    DELETE_MESSAGES_BY_SESSION,
)
from src.infra.db.entities import SessionEntity, SessionListItem, MessageEntity


class ChatRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def create_session(self, session: SessionEntity) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_SESSION,
                    (session.id, session.user_id, session.title, session.kb_id),
                )
            await conn.commit()

    async def get_sessions(self) -> list[SessionListItem]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSIONS)
                rows = await cursor.fetchall()
        return [
            SessionListItem(
                id=r["id"],
                title=r["title"],
                kb_id=r["kb_id"],
                kb_name=r["kb_name"],
                message_count=r["message_count"],
                created_at=r.get("created_at"),
                updated_at=r.get("updated_at"),
            )
            for r in rows
        ]

    async def get_session_by_id(self, session_id: str) -> Optional[SessionEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSION_BY_ID, (session_id,))
                row = await cursor.fetchone()
        if not row:
            return None
        return SessionEntity(**row)

    async def get_messages(self, session_id: str) -> list[MessageEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_MESSAGES_BY_SESSION, (session_id,))
                rows = await cursor.fetchall()
        return [MessageEntity(**r) for r in rows]

    async def save_message(self, msg: MessageEntity) -> None:
        pool = await self._pool_getter()
        sources_json = (
            json.dumps(msg.sources, ensure_ascii=False) if msg.sources else None
        )
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_MESSAGE,
                    (
                        msg.session_id,
                        msg.kb_id,
                        msg.role,
                        msg.content,
                        sources_json,
                        msg.prompt_tokens,
                        msg.completion_tokens,
                        msg.total_tokens,
                        msg.model_name,
                    ),
                )
            await conn.commit()

    async def delete_session_and_messages(self, session_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_MESSAGES_BY_SESSION, (session_id,))
                await cursor.execute(DELETE_SESSION, (session_id,))
                deleted = cursor.rowcount > 0
            await conn.commit()
        return deleted
