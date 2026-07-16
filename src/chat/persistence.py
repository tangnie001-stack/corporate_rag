"""对话历史持久化 — MySQL 异步写入。"""

from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB


class PersistenceService:
    """对话历史 MySQL 持久化。

    负责将会话和消息异步写入 MySQL，失败只记日志不抛异常。
    """

    def __init__(self, mysql_db: MySQLDB) -> None:
        self._mysql_db = mysql_db

    async def save_session(
        self, session_id: str, title: str, kb_id: str,
    ) -> None:
        """异步创建会话记录。

        Args:
            session_id: 会话 ID
            title: 会话标题
            kb_id: 关联的知识库 ID
        """
        try:
            await self._mysql_db.create_session(session_id, title, kb_id)
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
        """异步写入 user + assistant 消息。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            user_msg: 用户消息内容
            assistant_msg: 助理回答内容
            sources: 来源引用列表
        """
        try:
            await self._mysql_db.save_message(
                session_id, kb_id, "user", user_msg, None,
            )
            await self._mysql_db.save_message(
                session_id, kb_id, "assistant", assistant_msg, sources,
            )
        except Exception as e:
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据（当前委托给 ChatManager 的 clear_history）。"""
        pass
