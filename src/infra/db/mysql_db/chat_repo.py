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
    """会话/消息 CRUD 仓库。

    封装 sessions 和 conversation_history 表的查询操作，
    返回 SessionEntity / SessionListItem / MessageEntity 类型对象。
    """

    def __init__(self, mysql_db):
        """初始化 ChatRepo。

        Args:
            mysql_db: MySQLDB 实例，用于获取连接池
        """
        self._pool_getter = mysql_db._get_pool

    async def create_session(self, session: SessionEntity) -> None:
        """创建一条新对话记录。

        Args:
            session: 待创建的对话实体
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    INSERT_SESSION,
                    (session.id, session.user_id, session.title, session.kb_id),
                )
            await conn.commit()

    async def get_sessions(self) -> list[SessionListItem]:
        """获取所有对话列表（含知识库名称和消息数）。"""
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
        """按 ID 查询对话详情。

        Args:
            session_id: 对话 UUID

        Returns:
            对话实体，不存在时返回 None
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSION_BY_ID, (session_id,))
                row = await cursor.fetchone()
        if not row:
            return None
        return SessionEntity(**row)

    async def get_messages(self, session_id: str) -> list[MessageEntity]:
        """查询指定对话的所有消息。

        Args:
            session_id: 对话 UUID

        Returns:
            消息实体列表
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_MESSAGES_BY_SESSION, (session_id,))
                rows = await cursor.fetchall()
        return [MessageEntity(**r) for r in rows]

    async def save_message(self, msg: MessageEntity) -> None:
        """保存一条消息记录（含来源引用 JSON）。

        Args:
            msg: 待保存的消息实体
        """
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
        """删除对话及其所有关联消息。

        Args:
            session_id: 对话 UUID

        Returns:
            是否成功删除了对话记录
        """
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_MESSAGES_BY_SESSION, (session_id,))
                await cursor.execute(DELETE_SESSION, (session_id,))
                deleted = cursor.rowcount > 0
            await conn.commit()
        return deleted
