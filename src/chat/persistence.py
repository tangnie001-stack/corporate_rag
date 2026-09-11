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
        agent: str = "",
    ) -> None:
        """异步创建会话记录。

        Args:
            session_id: 会话 ID
            title: 会话标题（截取首条消息前 20 字）
            kb_id: 关联的知识库 ID
            user_id: 所属用户 ID
            agent: 会话绑定的智能体预设名（空=未绑定）
        """
        try:
            from src.infra.db.models.chat import SessionModel

            session = SessionModel(
                id=session_id, user_id=user_id, title=title, kb_id=kb_id, agent=agent
            )
            await self._chat_repo.create_session(session)
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(Event.SESSION_SAVE_FAILED, err=str(e))

    async def bind_session_agent(self, session_id: str, agent: str) -> bool:
        """委托 repo 首次绑定会话智能体（bind-once）。

        Args:
            session_id: 会话 ID
            agent: 已校验的智能体预设名（ASCII slug）

        Returns:
            True=本次写入成功（此前未绑定）；False=已绑定，未改动
        """
        return await self._chat_repo.bind_session_agent(session_id, agent)

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
        process_json: str | None = None,
        model_name: str | None = None,
    ) -> None:
        """写入一条 assistant 消息（完成/中止时调用，status 区分完整与中断）。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            assistant_msg: 助理回答内容
            sources: 来源引用列表
            status: 消息状态（complete/interrupted）
            process_json: 过程事件JSON（历史回放，None=存量语义）
            model_name: 实际回答模型名（复用既有列，None 落空串）
        """
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
                    process=process_json,
                    model_name=model_name or "",
                )
            )
        except Exception as e:  # noqa: BLE001
            core_logging.log_event(
                Event.MESSAGE_SAVE_FAILED, role="assistant", err=str(e)
            )

    def cleanup_session(self, session_id: str) -> None:
        """清理会话相关数据。"""
