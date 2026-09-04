"""对话历史持久化 — MySQL 异步写入。"""

import json

from src.core import logging as core_logging
from src.core.log_events import Event
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
            core_logging.log_event(Event.SESSION_SAVE_FAILED, err=str(e))

    async def save_messages(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: list[dict] | None = None,
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
            core_logging.log_event(Event.MESSAGE_SAVE_FAILED, role="both", err=str(e))

    async def save_user_message(
        self, session_id: str, kb_id: str, user_msg: str
    ) -> None:
        """写入一条 user 消息（请求开始时调用）。"""
        try:
            from src.infra.db.models.chat import MessageModel

            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id, kb_id=kb_id, role="user", content=user_msg
                )
            )
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.MESSAGE_SAVE_FAILED, role="user", err=str(e))

    async def save_assistant_message(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[dict] | None = None,
        status: str = "complete",
    ) -> None:
        """写入一条 assistant 消息（完成/中止时调用，status 区分完整与中断）。"""
        try:
            from src.infra.db.models.chat import MessageModel

            sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="assistant",
                    content=assistant_msg,
                    sources=sources_json,
                    status=status,
                )
            )
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(
                Event.MESSAGE_SAVE_FAILED, role="assistant", err=str(e)
            )

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据。"""
