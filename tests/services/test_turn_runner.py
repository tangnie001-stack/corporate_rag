"""start_turn 的闸门与失败语义（不触真实 DB/Redis）。"""

import asyncio

import pytest

from src.chat.streaming import streaming_manager
from src.services.turn_runner import TurnBusy, start_turn


class _FakeAgentService:
    def __init__(self, raise_on_stream_chat: bool = False) -> None:
        self.raise_on_stream_chat = raise_on_stream_chat
        self.calls = 0

    async def stream_chat(self, kb_id, session_id, query, deep_thinking, agent=""):
        self.calls += 1
        if self.raise_on_stream_chat:
            raise RuntimeError("boom")
        raise AssertionError("本用例不应走到订阅成功路径")


class _FakeChatManager:
    def __init__(self) -> None:
        self._redis = None  # 触发"Redis 不可用"分支：跳过锁，不阻塞
        self.saved: list[tuple] = []

    async def save_session_async(self, session_id, title, kb_id, user_id, agent=""):
        self.saved.append(("session", session_id, title, kb_id, user_id))

    async def save_user_async(self, session_id, kb_id, user_msg):
        self.saved.append(("user", session_id, kb_id, user_msg))


class _FakeSvc:
    def __init__(self, **kw) -> None:
        self.chat_manager = _FakeChatManager()
        self.agent_service = _FakeAgentService(**kw)
        self.chat_repo_set = 0

    async def set_chat_repo(self) -> None:
        self.chat_repo_set += 1

    async def save_session_async(self, session_id, title, kb_id, user_id, agent=""):
        await self.chat_manager.save_session_async(session_id, title, kb_id, user_id)

    async def save_user_async(self, session_id, kb_id, user_msg):
        await self.chat_manager.save_user_async(session_id, kb_id, user_msg)


@pytest.mark.asyncio
async def test_raises_turn_busy_when_session_running():
    """注册表已有该会话任务 → TurnBusy（不双开）。"""
    session_id = "sess-busy"
    task = asyncio.create_task(asyncio.sleep(5))
    streaming_manager.register(session_id, task, asyncio.Event())
    try:
        svc = _FakeSvc()
        with pytest.raises(TurnBusy):
            # 结构化假 svc：只需 start_turn 用到的接口，不构造真 AppService
            await start_turn(svc, session_id=session_id, kb_id="", query="hi")  # type: ignore[reportArgumentType]
    finally:
        task.cancel()
        streaming_manager.unregister_if_current(session_id, task)


@pytest.mark.asyncio
async def test_set_chat_repo_called_before_persist():
    """落库前置必须先注入 repo（否则 save_* 静默跳过）。"""
    svc = _FakeSvc(raise_on_stream_chat=True)
    handle = await start_turn(svc, session_id="sess-1", kb_id="", query="你好世界")  # type: ignore[reportArgumentType]
    assert svc.chat_repo_set == 1
    assert ("session", "sess-1", "你好世界", "", "") in svc.chat_manager.saved

    events = []
    async for ev in handle.events:
        events.append(type(ev).__name__)
    # stream_chat 失败 → 不抛，产 error + done 终止态
    assert "SSEErrorEvent" in events
    assert events[-1] == "SSEDoneEvent"
