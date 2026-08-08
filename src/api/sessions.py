"""会话管理 API 路由。

提供会话的列表、查看和删除端点。
会话持久化在 MySQL 中，并缓存于 Redis。
"""

from fastapi import APIRouter, Depends, Request
from loguru import logger

from src.api.dependencies import get_app_service
from src.api.model.request import SessionDeleteRequest, SessionMessagesRequest
from src.api.model.response import MessageItem, SessionDeleteResponse, SessionItem
from src.api.schema import ResponseModel
from src.config.response_codes import Code
from src.services.app_service import AppService
from src.utils.errors import BusinessError

router = APIRouter()


@router.post("/sessions/list", response_model=ResponseModel)
async def list_sessions(
    request: Request,
    svc: AppService = Depends(get_app_service),
):
    """列出最近 50 个会话（仅当前用户的）。

    始终返回 200 + 数组，无会话时返回 []。

    Args:
        request: FastAPI 请求（从中提取 user_id）
        svc: 应用服务实例（由 FastAPI 注入）

    Returns:
        ResponseModel: data 为 SessionItem 列表
    """
    user_id = getattr(request.state, "user_id", "")
    sessions = await svc.get_sessions(user_id)
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
    return ResponseModel(data=result)


@router.post("/sessions/messages", response_model=ResponseModel)
async def get_session_messages(
    request: Request,
    body: SessionMessagesRequest,
    svc: AppService = Depends(get_app_service),
):
    """获取会话消息历史。

    先验证会话存在且属于当前用户，再返回消息列表。
    不存在的 session_id 或无权访问返回 404。

    Args:
        request: FastAPI 请求（从中提取 user_id）
        body: 会话消息请求体，含 session_id
        svc: 应用服务实例（由 FastAPI 注入）

    Returns:
        ResponseModel: data 为 MessageItem 列表

    Raises:
        BusinessError: 会话不存在或无权访问时返回 404
    """
    user_id = getattr(request.state, "user_id", "")
    session_id = body.session_id
    session = await svc.get_session_by_id(session_id)
    if not session:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)
    if session.get("user_id") and session["user_id"] != user_id:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    messages = await svc.get_messages(session_id)
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
    return ResponseModel(data=result)


@router.post("/sessions/delete", response_model=ResponseModel)
async def delete_session(
    request: Request,
    body: SessionDeleteRequest,
    svc: AppService = Depends(get_app_service),
):
    """删除会话及其所有消息。

    执行顺序:
    1. 清理 Redis key（尽力而为，失败只记日志）
    2. 删除 MySQL sessions 记录
    3. 级联删除 conversation_history 消息
    事务保证 MySQL 操作的原子性。

    Args:
        request: FastAPI 请求（从中提取 user_id）
        body: 会话删除请求体，含 session_id
        svc: 应用服务实例（由 FastAPI 注入）

    Returns:
        ResponseModel: 删除结果

    Raises:
        BusinessError: 会话不存在或无权访问时返回 404
    """
    user_id = getattr(request.state, "user_id", "")
    session_id = body.session_id

    # 验证所有权
    session = await svc.get_session_by_id(session_id)
    if not session:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)
    if session.get("user_id") and session["user_id"] != user_id:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    # 清理 Redis + 删除 MySQL 记录
    ok = await svc.delete_session_and_messages(session_id)
    if not ok:
        raise BusinessError(Code.SESSION_NOT_FOUND, Code.SESSION_NOT_FOUND_MSG, 404)

    logger.info("Deleted session: {} (user={})", session_id, user_id)
    return ResponseModel(data=SessionDeleteResponse(success=True))
