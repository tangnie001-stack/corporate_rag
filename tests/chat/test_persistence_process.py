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
