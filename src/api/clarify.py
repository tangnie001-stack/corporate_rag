"""挂起澄清答案解析端点 — POST /chat/clarify-answer。

SSE 请求（ask_user 工具）经进程级 pending_asks 注册表登记挂起 Future，
前端弹出澄清问题后经本端点提交答案，resolve 该 Future 让 agent 继续。
编排已下沉 services.clarify_service（design D12），本路由只做请求校验与
状态码映射。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.config.const import SSEInteractionTexts
from src.services import clarify_service
from src.services.app_service import AppService

router = APIRouter()


class ClarifyAnswerBody(BaseModel):
    """澄清答案提交请求体。

    字段说明:
        session_id: 会话 ID，用于定位挂起的澄清 Future（ask_user 登记时使用）
        answers: 用户答案列表，每条含 id/selected（可含 custom），
            原样写入 Future 作为 ask_user 的返回值
    """

    session_id: str
    answers: list


@router.post("/chat/clarify-answer", response_model=ResponseModel)
async def clarify_answer(
    body: ClarifyAnswerBody,
    svc: AppService = Depends(get_app_service),
):
    """解析挂起的 ask_user Future；查无或已结束返回 404。

    编排已下沉 services.clarify_service（design D12），本路由只做请求校验与
    状态码映射，行为与下沉前一致。

    Args:
        body: 澄清答案请求体，含 session_id 与 answers
        svc: AppService 实例（FastAPI 注入）

    Returns:
        ResponseModel: data=True 表示已成功解析挂起澄清

    Raises:
        HTTPException: 404 — 该澄清问题已超时或不存在（查无 Future 或 Future 已结束）
    """
    delivered = await clarify_service.resolve_clarify_answer(
        svc, session_id=body.session_id, answers=body.answers
    )
    if not delivered:
        raise HTTPException(
            status_code=404, detail=SSEInteractionTexts.CLARIFY_ANSWER_NOT_FOUND_TEXT
        )
    return ResponseModel(data=True)
