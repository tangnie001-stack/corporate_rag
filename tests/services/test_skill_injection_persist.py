"""注入型隐藏消息：写 Redis + DB、本轮可见、后续轮可从历史读回。"""

from unittest.mock import AsyncMock

import pytest

from src.config.const import SKILL_INJECTION_PREFIX
from src.services.agent_service import AgentService


def _service() -> tuple[AgentService, AsyncMock]:
    """构造只带 _chat_manager 的 AgentService 替身（绕过重型 __init__）。"""
    svc = AgentService.__new__(AgentService)
    svc._chat_manager = AsyncMock()
    svc._chat_manager.add_message_async = AsyncMock()
    svc._chat_manager.save_user_async = AsyncMock()
    return svc, svc._chat_manager


@pytest.mark.asyncio
async def test_injection_writes_redis_and_db_with_marker():
    """注入同时写 Redis 与 DB，且内容带标记前缀。"""
    svc, chat_manager = _service()
    entry = await svc._inject_skill_message("sess_1", "kb1", "方法论正文")

    assert entry.content.startswith(SKILL_INJECTION_PREFIX)
    assert "方法论正文" in entry.content
    chat_manager.add_message_async.assert_awaited_once()
    saved = chat_manager.save_user_async.await_args[0]
    assert saved[0] == "sess_1"
    assert SKILL_INJECTION_PREFIX in saved[2]


@pytest.mark.asyncio
async def test_injection_returns_entry_for_current_turn():
    """返回的 ChatMessage 可直接追加进本轮 history（保证本轮就生效）。"""
    svc, _ = _service()
    entry = await svc._inject_skill_message("sess_1", "kb1", "方法论正文")
    assert entry.role == "user"
