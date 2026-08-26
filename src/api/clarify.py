"""挂起澄清答案解析端点 — POST /chat/clarify-answer。

SSE 请求（ask_user 工具）经进程级 pending_asks 注册表登记挂起 Future，
前端弹出澄清问题后经本端点提交答案，resolve 该 Future 让 agent 继续。
POST 与 SSE 是独立 HTTP 请求，contextvar 不跨请求，因此直接消费
request_context 模块级 pending_asks（session_id -> asyncio.Future）。
"""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.schema import ResponseModel
from src.config.prompts import CLARIFY_ANSWER_NOT_FOUND_TEXT
from src.infra.llm.request_context import pending_asks

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
async def clarify_answer(body: ClarifyAnswerBody):
    """解析挂起的 ask_user Future；查无或已结束返回 404。

    从进程级 pending_asks 中 pop 该 session 的 Future（pop 保证单次消费：
    无论成功解析还是已超时，注册表只允许被消费一次，避免重复回答）。

    Args:
        body: 澄清答案请求体，含 session_id 与 answers

    Returns:
        ResponseModel: data=True 表示已成功解析挂起澄清

    Raises:
        HTTPException: 404 — 该澄清问题已超时或不存在（查无 Future 或 Future 已结束）
    """
    fut = pending_asks.pop(body.session_id, None)
    if fut is None or fut.done():
        raise HTTPException(status_code=404, detail=CLARIFY_ANSWER_NOT_FOUND_TEXT)
    try:
        fut.set_result(body.answers)
    except asyncio.InvalidStateError:
        # 竞态：pop 与 set_result 之间 ask_user 侧已超时/取消 Future（set_result 对
        # 已结束 Future 抛 InvalidStateError），此时按已超时处理返回 404
        raise HTTPException(status_code=404, detail=CLARIFY_ANSWER_NOT_FOUND_TEXT)
    return ResponseModel(data=True)
