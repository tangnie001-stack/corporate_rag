"""对话历史持久化 — MySQL 异步写入。"""

from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import ChatRepo


class PersistenceService:
    """对话历史 MySQL 持久化。"""

    def __init__(self, chat_repo: ChatRepo) -> None:
        self._chat_repo = chat_repo

    async def save_session(
        self,
        session_id: str,
        title: str,
        kb_id: str,
    ) -> None:
        """异步创建会话记录。"""
        try:
            await self._chat_repo.create_session(session_id, title, kb_id)
        except Exception as e:
            logger.warning("Failed to save session async: {}", e)

    async def save_messages(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        """异步写入 user + assistant 消息。"""
        try:
            await self._chat_repo.save_message(
                session_id,
                kb_id,
                "user",
                user_msg,
                None,
            )
            await self._chat_repo.save_message(
                session_id,
                kb_id,
                "assistant",
                assistant_msg,
                sources,
            )
        except Exception as e:
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据。"""
        pass
