"""会话管理 API 路由。

提供会话的列表、查看和删除端点。
会话持久化在 MySQL 中，并缓存于 Redis。
"""

import asyncio

from fastapi import APIRouter
from loguru import logger

from src.api.model.request import SessionMessagesRequest, SessionDeleteRequest
from src.api.model.response import SessionItem, MessageItem, SessionDeleteResponse
from src.app_service import AppService
from src.config.response_codes import Code
from src.infra.errors import BusinessError

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    """获取 AppService 单例实例。

    延迟初始化：首次调用时创建实例，后续复用。
    避免模块导入阶段产生网络或数据库连接。

    Returns:
        AppService 全局唯一实例
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service


@router.post("/sessions/list")
async def list_sessions() -> list[SessionItem]:
    """列出最近 50 个会话。

    始终返回 200 + 数组，无会话时返回 []。

    Returns:
        list[SessionItem]: 会话列表
    """
    svc = _get_service()
    sessions = await svc.db.get_sessions()
    result = []
    for row in sessions:
        result.append(
            SessionItem(
                id=row["id"],
                title=row["title"],
                kb_id=row["kb_id"],
                kb_name=row["kb_name"],
                message_count=row["message_count"],
                created_at=row["created_at"].isoformat()
                if row.get("created_at")
                else None,
                updated_at=row["updated_at"].isoformat()
                if row.get("updated_at")
                else None,
            )
        )
    return result


@router.post("/sessions/messages")
async def get_session_messages(body: SessionMessagesRequest) -> list[MessageItem]:
    """获取会话消息历史。

    先验证会话存在，再返回消息列表。
    不存在的 session_id 返回 404。

    Args:
        body: 会话消息请求体，含 session_id

    Returns:
        list[MessageItem]: 消息列表

    Raises:
        BusinessError: 会话不存在时返回 404
    """
    svc = _get_service()
    session_id = body.session_id
    session = await svc.db.get_session_by_id(session_id)
    if not session:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    messages = await svc.db.get_messages(session_id)
    result = []
    for row in messages:
        result.append(
            MessageItem(
                role=row["role"],
                content=row["content"],
                sources=row.get("sources"),
                created_at=row["created_at"].isoformat()
                if row.get("created_at")
                else None,
            )
        )
    return result


@router.post("/sessions/delete")
async def delete_session(body: SessionDeleteRequest) -> SessionDeleteResponse:
    """删除会话及其所有消息。

    执行顺序:
    1. 清理 Redis key（尽力而为，失败只记日志）
    2. 删除 MySQL sessions 记录
    3. 级联删除 conversation_history 消息
    事务保证 MySQL 操作的原子性。

    Args:
        body: 会话删除请求体，含 session_id

    Returns:
        SessionDeleteResponse: 删除结果

    Raises:
        BusinessError: 会话不存在时返回 404
    """
    svc = _get_service()
    session_id = body.session_id

    # 清理 Redis（同步操作，通过 asyncio.to_thread 委托到线程池）
    await asyncio.to_thread(svc.rag_chain.chat_manager.cleanup_session, session_id)

    # 删除 MySQL 记录
    ok = await svc.db.delete_session_and_messages(session_id)
    if not ok:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    logger.info("Deleted session: {}", session_id)
    return SessionDeleteResponse(success=True)
