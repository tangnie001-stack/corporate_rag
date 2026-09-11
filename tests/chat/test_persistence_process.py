"""process 列四层透传测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.chat.persistence import PersistenceService


@pytest.mark.asyncio
class TestProcessPersistsThroughChain:
    async def test_persistence_sets_process_on_model(self):
        """save_assistant_message 传入 process_json/model_name 时应写入 MessageModel 对应字段。"""
        repo = MagicMock()
        repo.save_message = AsyncMock()
        persistence = PersistenceService(repo)
        await persistence.save_assistant_message(
            "sess_1",
            "kb_1",
            "正文",
            process_json='{"format_version": 1, "events": []}',
            model_name="qwen3.7-flash",
        )
        saved = repo.save_message.call_args[0][0]
        assert saved.process == '{"format_version": 1, "events": []}'
        assert saved.model_name == "qwen3.7-flash"

    async def test_defaults_backward_compatible(self):
        """不传新参数时 process 为 None、model_name 落空串，存量语义不变。"""
        repo = MagicMock()
        repo.save_message = AsyncMock()
        persistence = PersistenceService(repo)
        await persistence.save_assistant_message("sess_1", "kb_1", "正文")
        saved = repo.save_message.call_args[0][0]
        assert saved.process is None
        assert saved.model_name == ""


@pytest.mark.asyncio
class TestSessionAgentPersistsThroughChain:
    async def test_save_session_passes_agent(self):
        """save_session 传入 agent 时应写入 SessionModel 对应字段。"""
        repo = MagicMock()
        repo.create_session = AsyncMock()
        persistence = PersistenceService(repo)
        await persistence.save_session(
            "sess_1", "标题", "kb_1", user_id="u1", agent="finance-expert"
        )
        saved = repo.create_session.call_args[0][0]
        assert saved.agent == "finance-expert"

    async def test_bind_session_agent_delegates(self):
        """bind_session_agent 委托 repo 并透传返回值。"""
        repo = MagicMock()
        repo.bind_session_agent = AsyncMock(return_value=True)
        persistence = PersistenceService(repo)
        result = await persistence.bind_session_agent("sess_1", "finance-expert")
        assert result is True
        repo.bind_session_agent.assert_awaited_once_with("sess_1", "finance-expert")
