"""/api/chat/stream 流程改造测试 — user 同步落库（POST）。

验证：流式请求开始即同步写 user（MySQL），端点方法为 POST（body 为
ChatStreamRequest）；并发防护先查进程内注册表再取 Redis 锁。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from src.api.chat import chat_stream
from src.api.model.request import ChatStreamRequest
from src.chat.streaming import streaming_manager


def test_chat_stream_persists_user_before_stream(auth_client, mock_app_service):
    """请求开始即写 user（MySQL 同步），再进入流式生成。"""
    mock_app_service.set_chat_repo = AsyncMock()
    mock_app_service.agent_service.stream_chat = AsyncMock()
    mock_app_service.agent_service.stream_chat.return_value = iter(())
    mock_app_service.save_user_async = AsyncMock()
    mock_app_service.save_session_async = AsyncMock()

    resp = auth_client.post(
        "/api/chat/stream",
        json={"session_id": "s1", "kb_id": "kb1", "query": "营收多少"},
    )
    assert resp.status_code == 200
    mock_app_service.save_session_async.assert_called_once()
    mock_app_service.save_user_async.assert_called_once_with("s1", "kb1", "营收多少")


@pytest.mark.asyncio
async def test_background_task_finalizes_assistant_on_cancel():
    """后台任务收到 abort（task.cancel）后，已产出 token 落 interrupted 并写 done 终态。"""
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    partial_holder = {"text": "部分回答"}
    mgr = StreamingRunManager()
    entered = asyncio.Event()

    async def answer_builder():
        entered.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(
        _run_with_finalize(
            fake_svc,
            "s1",
            "kb1",
            partial_holder,
            answer_builder,
            mgr,
            asyncio.Event(),
            lambda: None,
            RequestContext(session_id="s1"),
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("assistant", "interrupted") in calls
    assert mgr.has_terminal("s1") is True


@pytest.mark.asyncio
async def test_background_task_finalizes_assistant_on_complete():
    """后台任务正常结束：完整回答落 complete 并写 done 终态。"""
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    partial_holder = {"text": ""}
    mgr = StreamingRunManager()

    async def answer_builder():
        partial_holder["text"] = "完整回答"
        return "完整回答"

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )
    assert ("assistant", "complete") in calls
    assert mgr.has_terminal("s1") is True


@pytest.mark.asyncio
async def test_background_task_error_event_round_trips_from_payload():
    """answer_builder 抛异常时，error 终态事件 payload 可被 from_payload 正常回放。

    回归防线：error 事件必须以 dict {"error": str} 入缓冲（与实时 sse_error 的
    data: 同构），否则 resume/status 回放路径在 from_payload 处抛
    TypeError: string indices must be integers。
    """
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.utils.sse import SSEErrorEvent, from_payload

    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock()
    partial_holder = {"text": "部分回答"}
    mgr = StreamingRunManager()

    async def answer_builder():
        raise RuntimeError("LLM generation failed")

    await _run_with_finalize(
        fake_svc,
        "s1",
        "kb1",
        partial_holder,
        answer_builder,
        mgr,
        asyncio.Event(),
        lambda: None,
        RequestContext(session_id="s1"),
    )

    assert mgr.has_terminal("s1") is True
    error_events = [
        (seq, etype, payload)
        for seq, etype, payload in mgr.get_events_since("s1", 0)
        if etype == "error"
    ]
    assert len(error_events) == 1
    _, etype, payload = error_events[0]
    assert isinstance(payload, dict)
    restored = from_payload(etype, payload)
    assert isinstance(restored, SSEErrorEvent)
    assert restored.error == "LLM generation failed"


@pytest.mark.asyncio
async def test_background_task_done_cancelled_round_trips():
    """answer_builder 抛 CancelledError（abort 触达生产者）→ 落 interrupted + done(cancelled) 入缓冲。

    验证 F1+F2 链路：生产者 abort 抛 CancelledError → _run_with_finalize 捕获 →
    部分回答落 interrupted → 写 done 终态事件 payload {"cancelled": True} →
    from_payload 还原保留 cancelled 标记 → to_sse 序列化 "cancelled": true。
    """
    from src.api.chat import _run_with_finalize
    from src.chat.streaming import StreamingRunManager
    from src.infra.llm.request_context import RequestContext
    from src.utils.sse import SSEDoneEvent, from_payload, to_sse

    calls = []
    fake_svc = MagicMock()
    fake_svc.save_assistant_async = AsyncMock(
        side_effect=lambda *a, **k: calls.append(("assistant", a[4]))
    )
    partial_holder = {"text": "部分回答"}
    mgr = StreamingRunManager()

    async def answer_builder():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _run_with_finalize(
            fake_svc,
            "s1",
            "kb1",
            partial_holder,
            answer_builder,
            mgr,
            asyncio.Event(),
            lambda: None,
            RequestContext(session_id="s1"),
        )

    assert ("assistant", "interrupted") in calls
    assert mgr.has_terminal("s1") is True
    done_events = [
        (seq, etype, payload)
        for seq, etype, payload in mgr.get_events_since("s1", 0)
        if etype == "done"
    ]
    assert len(done_events) == 1
    _, etype, payload = done_events[0]
    assert payload == {"cancelled": True}
    restored = from_payload(etype, payload)
    assert isinstance(restored, SSEDoneEvent)
    assert restored.cancelled is True
    assert '"cancelled": true' in to_sse(restored)


@pytest.mark.asyncio
async def test_chat_stream_conflict_returns_409(mock_app_service):
    """注册表已有活跃任务 → chat_stream 先查注册表返回 409（不依赖 Redis 锁）。

    在模块级 streaming_manager 单例上登记一个未完成的任务，端点必须
    在取 Redis 锁之前被 is_running 拦下。用唯一 session_id 并在
    teardown 注销/取消，避免污染其它测试。
    """
    session_id = "s-conflict-409"
    task = asyncio.create_task(asyncio.sleep(10))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        assert streaming_manager.is_running(session_id) is True
        with pytest.raises(HTTPException) as exc_info:
            await chat_stream(
                request=Request({"type": "http", "method": "POST"}),
                body=ChatStreamRequest(
                    session_id=session_id, kb_id="kb1", query="营收多少"
                ),
                svc=mock_app_service,
            )
        assert exc_info.value.status_code == 409
    finally:
        streaming_manager.unregister(session_id)
        task.cancel()
