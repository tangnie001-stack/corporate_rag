"""对话历史持久化 — MySQL 异步写入。"""

import json

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
        user_id: str = "",
    ) -> None:
        """异步创建会话记录。

        Args:
            session_id: 会话 ID
            title: 会话标题（截取首条消息前 20 字）
            kb_id: 关联的知识库 ID
            user_id: 所属用户 ID
        """
        try:
            from src.infra.db.models.chat import SessionModel

            session = SessionModel(
                id=session_id, user_id=user_id, title=title, kb_id=kb_id
            )
            await self._chat_repo.create_session(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save session async: {}", e)

    async def save_messages(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: list[str] | None = None,
    ) -> None:
        """异步写入 user + assistant 消息。"""
        try:
            from src.infra.db.models.chat import MessageModel

            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id, kb_id=kb_id, role="user", content=user_msg
                )
            )
            sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="assistant",
                    content=assistant_msg,
                    sources=sources_json,
                )
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据。"""
