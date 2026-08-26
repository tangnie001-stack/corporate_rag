"""测试 /chat/clarify-answer 端点 — 解析挂起澄清 Future。

直接调用端点函数（而非 TestClient）：TestClient 在独立事件循环线程运行应用，
跨 loop 对 Future 调 set_result 存在 loop 亲和性风险；端点函数与测试同 loop
执行，与 tests/agents/tools/test_ask_user.py 共用进程级 pending_asks 的契约。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.clarify import ClarifyAnswerBody, clarify_answer
from src.infra.llm.request_context import pending_asks


@pytest.fixture(autouse=True)
def clean_pending_asks():
    """每个测试前后清空进程级挂起注册表，避免跨测试污染。"""
    pending_asks.clear()
    yield
    pending_asks.clear()


def _make_svc() -> AsyncMock:
    """构造最小 svc：chat_manager.add_message_async 为 AsyncMock。"""
    svc = AsyncMock()
    svc.chat_manager.add_message_async = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_resolve_success():
    """注册 Future 后调用端点：Future 有结果、pending_asks 已清理、返回成功。"""
    fut = asyncio.get_running_loop().create_future()
    pending_asks["s1"] = fut
    answers = [{"id": "q1", "selected": ["2024年"]}]
    svc = _make_svc()

    result = await clarify_answer(
        ClarifyAnswerBody(session_id="s1", answers=answers), svc
    )

    assert fut.done()
    assert fut.result() == answers
    assert "s1" not in pending_asks
    assert result.data is True
    # 答案作为 user 消息写入 Redis 历史（设计 D11 两轨并存）
    svc.chat_manager.add_message_async.assert_awaited_once_with("s1", "user", "2024年")


@pytest.mark.asyncio
async def test_resolve_success_skips_empty_text():
    """answers 无有效内容（全空 selected/custom）时不写历史。"""
    fut = asyncio.get_running_loop().create_future()
    pending_asks["s1"] = fut
    svc = _make_svc()

    result = await clarify_answer(
        ClarifyAnswerBody(session_id="s1", answers=[{"id": "q1", "selected": []}]),
        svc,
    )

    assert result.data is True
    svc.chat_manager.add_message_async.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_missing_404():
    """无挂起 Future 时返回 404。"""
    with pytest.raises(HTTPException) as exc_info:
        await clarify_answer(
            ClarifyAnswerBody(session_id="nope", answers=[]), _make_svc()
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_already_done():
    """Future 已 done（超时/取消）时返回 404，不抛异常。"""
    fut = asyncio.get_running_loop().create_future()
    fut.set_result("old")
    pending_asks["s1"] = fut

    with pytest.raises(HTTPException) as exc_info:
        await clarify_answer(
            ClarifyAnswerBody(session_id="s1", answers=[]), _make_svc()
        )
    assert exc_info.value.status_code == 404
