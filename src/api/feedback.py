"""答案反馈端点 — POST /feedback。

接收前端对单条答案的评分（positive/negative）与可选评论，写入 MySQL
feedback 表。落库失败仅记日志不报错（与对话持久化 _persist_conversation
的容错模式一致），保证反馈动作不阻塞、不影响前端交互。
"""

from typing import Literal

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.services.app_service import AppService

router = APIRouter()


class FeedbackBody(BaseModel):
    """答案反馈请求体。

    字段说明:
        session_id: 会话 ID，定位反馈所属会话
        message_index: 会话内消息序号（前端消息数组索引，从 0 起）
        rating: 评分，仅允许 positive（点赞）/ negative（点踩），
            Pydantic Literal 自动校验，非法值返回 422
        comment: 用户评论，可为空字符串
        trace_id: 全链路追踪 ID（前端从 SSE done 事件记录，随反馈回传，
            用于经 trace_id 还原该答案的生成链路）
    """

    session_id: str
    message_index: int
    rating: Literal["positive", "negative"]
    comment: str = ""
    trace_id: str = ""


async def _save_feedback(
    repo,
    session_id: str,
    message_index: int,
    rating: str,
    comment: str,
    trace_id: str = "",
) -> None:
    """异步写入一条答案反馈，失败仅记日志不抛异常。

    Args:
        repo: ChatRepo 实例，提供 save_feedback 方法
        session_id: 会话 ID
        message_index: 会话内消息序号
        rating: 评分（positive/negative）
        comment: 用户评论
        trace_id: 全链路追踪 ID（空串表示前端未捕获到）
    """
    try:
        await repo.save_feedback(
            session_id=session_id,
            message_index=message_index,
            rating=rating,
            comment=comment,
            trace_id=trace_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to save feedback: {}", e)


@router.post("/feedback", response_model=ResponseModel)
async def submit_feedback(
    body: FeedbackBody,
    svc: AppService = Depends(get_app_service),
):
    """保存用户对单条答案的反馈。

    rating 合法性由 FeedbackBody 的 Pydantic Literal 校验保证（非法返回 422）。
    落库失败只记日志，始终返回成功，不因存储问题影响前端交互。

    Args:
        body: 反馈请求体，含 session_id/message_index/rating/comment/trace_id
        svc: AppService 实例（FastAPI 注入），经其 chat_repo 访问存储

    Returns:
        ResponseModel: data=True 表示反馈已受理（含存储失败降级场景）
    """
    await _save_feedback(
        repo=svc.chat_repo,
        session_id=body.session_id,
        message_index=body.message_index,
        rating=body.rating,
        comment=body.comment,
        trace_id=body.trace_id,
    )
    return ResponseModel(data=True)
