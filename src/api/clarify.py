"""挂起澄清答案解析端点 — POST /chat/clarify-answer。

SSE 请求（ask_user 工具）经进程级 pending_asks 注册表登记挂起 Future，
前端弹出澄清问题后经本端点提交答案，resolve 该 Future 让 agent 继续。
POST 与 SSE 是独立 HTTP 请求，contextvar 不跨请求，因此直接消费
request_context 模块级 pending_asks（session_id -> asyncio.Future）。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_app_service
from src.api.schema import ResponseModel
from src.config.prompts import CLARIFY_ANSWER_NOT_FOUND_TEXT
from src.infra.llm.request_context import pending_asks
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


def _format_answers_text(answers: list) -> str:
    """把用户答案数组拼成可读文本（与前端 formatAnswers 展示一致）。

    Args:
        answers: 用户答案列表，每条为 {"id", "selected": [...], "custom": "..."}

    Returns:
        可读文本：每条答案按 "选项1、选项2；自定义" 拼接，多条答案以 "；" 相连
    """
    parts = []
    for ans in answers:
        item_parts = []
        selected = ans.get("selected") or []
        if selected:
            item_parts.append("、".join(str(s) for s in selected))
        if ans.get("custom"):
            item_parts.append(str(ans["custom"]))
        if item_parts:
            parts.append("；".join(item_parts))
    return "；".join(parts)


@router.post("/chat/clarify-answer", response_model=ResponseModel)
async def clarify_answer(
    body: ClarifyAnswerBody,
    svc: AppService = Depends(get_app_service),
):
    """解析挂起的 ask_user Future；查无或已结束返回 404。

    从进程级 pending_asks 中 pop 该 session 的 Future（pop 保证单次消费：
    无论成功解析还是已超时，注册表只允许被消费一次，避免重复回答）。
    resolve 成功后把答案作为 user 消息写入 Redis 历史（chat_manager），
    与 stream_chat 入口写入原始 query 的轨道并存，保证跨 turn 上下文不丢。

    Args:
        body: 澄清答案请求体，含 session_id 与 answers
        svc: AppService 实例（FastAPI 注入），经 chat_manager 写入对话历史

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
    text = _format_answers_text(body.answers)
    if text:
        await svc.chat_manager.add_message_async(body.session_id, "user", text)
    return ResponseModel(data=True)
