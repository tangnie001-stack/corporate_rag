"""澄清答案编排（下沉自 api 层，design D12 的同构下沉）。

站点与企微通道**共用同一实现**：定位并 resolve 进程级挂起的 ask_user Future，
然后把用户答案作为 user 消息写入 Redis 历史与 MySQL（对齐 chat_stream 入口的
用户消息落库模式，kb_id 从会话记录取）——仅写 Redis 时刷新后澄清回答即丢、
回放叙事断裂。

单次消费：`pop` 保证无论成功解析还是已超时，注册表只允许被消费一次。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from src.infra.llm.request_context import pending_asks
from src.services.app_service import AppService


def format_answers_text(answers: list) -> str:
    """把用户答案数组拼成可读文本（与前端 formatAnswers 展示一致）。

    Args:
        answers: 用户答案列表，每条为 {"id", "selected": [...], "custom": "..."}

    Returns:
        可读文本：每条答案按 "选项1、选项2；自定义" 拼接，多条答案以 "；" 相连
    """
    parts: list[str] = []
    for ans in answers:
        item_parts: list[str] = []
        selected = ans.get("selected") or []
        if selected:
            item_parts.append("、".join(str(s) for s in selected))
        if ans.get("custom"):
            item_parts.append(str(ans["custom"]))
        if item_parts:
            parts.append("；".join(item_parts))
    return "；".join(parts)


async def resolve_clarify_answer(
    svc: AppService, *, session_id: str, answers: list
) -> bool:
    """把答案投递给该会话挂起的 ask_user，并落库用户答案。

    Args:
        svc: AppService（Redis 历史 + MySQL 会话写入）
        session_id: 会话 ID（挂起 Future 的键）
        answers: 站点同构答案列表 `[{id, selected, custom}]`

    Returns:
        True 表示已投递；False 表示该会话没有挂起（或已结束），调用方应回落
    """
    future: asyncio.Future | None = pending_asks.pop(session_id, None)
    if future is None:
        logger.info("[clarify] resolve miss session_id={}", session_id)
        return False
    if future.done():
        logger.info("[clarify] resolve already done session_id={}", session_id)
        return False
    try:
        future.set_result(answers)
    except asyncio.InvalidStateError:
        logger.warning("[clarify] resolve raced session_id={}", session_id)
        return False

    text = format_answers_text(answers)
    if text:
        await svc.chat_manager.add_message_async(session_id, "user", text)
        session: dict[str, Any] | None = await svc.get_session_by_id(session_id)
        if session:
            await svc.save_user_async(session_id, session.get("kb_id") or "", text)
    logger.info("[clarify] resolved session_id={} text_len={}", session_id, len(text))
    return True
