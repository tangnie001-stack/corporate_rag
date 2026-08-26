"""测试 /chat/clarify-answer 端点 — 解析挂起澄清 Future。

直接调用端点函数（而非 TestClient）：TestClient 在独立事件循环线程运行应用，
跨 loop 对 Future 调 set_result 存在 loop 亲和性风险；端点函数与测试同 loop
执行，与 tests/agents/tools/test_ask_user.py 共用进程级 pending_asks 的契约。
"""

import asyncio

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


@pytest.mark.asyncio
async def test_resolve_success():
    """注册 Future 后调用端点：Future 有结果、pending_asks 已清理、返回成功。"""
    fut = asyncio.get_running_loop().create_future()
    pending_asks["s1"] = fut
    answers = [{"id": "q1", "selected": ["2024年"]}]

    result = await clarify_answer(ClarifyAnswerBody(session_id="s1", answers=answers))

    assert fut.done()
    assert fut.result() == answers
    assert "s1" not in pending_asks
    assert result.data is True


@pytest.mark.asyncio
async def test_resolve_missing_404():
    """无挂起 Future 时返回 404。"""
    with pytest.raises(HTTPException) as exc_info:
        await clarify_answer(ClarifyAnswerBody(session_id="nope", answers=[]))
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_already_done():
    """Future 已 done（超时/取消）时返回 404，不抛异常。"""
    fut = asyncio.get_running_loop().create_future()
    fut.set_result("old")
    pending_asks["s1"] = fut

    with pytest.raises(HTTPException) as exc_info:
        await clarify_answer(ClarifyAnswerBody(session_id="s1", answers=[]))
    assert exc_info.value.status_code == 404
