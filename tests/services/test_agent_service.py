"""AgentService 单元测试。"""

import pytest
from unittest.mock import AsyncMock, Mock

from src.utils.sse import SSEClarificationEvent, SSEDoneEvent


class TestStreamChatClarification:
    """stream_chat 追问功能测试。"""

    @pytest.mark.asyncio
    async def test_stream_chat_sends_clarification_when_missing_entities(self):
        from src.services.agent_service import AgentService
        from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode

        service = AgentService.__new__(AgentService)
        service._llm = Mock()
        service._chat_manager = AsyncMock()
        service._chat_manager.get_history_async.return_value = []
        service._chat_manager.add_message_async = AsyncMock()
        service._prompt_manager = Mock()
        service._tracer = Mock()

        # 模拟 graph.astream_events 返回 classify CHAIN_END 事件
        async def fake_astream(*args, **kwargs):
            yield {
                LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
                LangGraphKey.NAME: LangGraphNode.Classify.NAME,
                LangGraphKey.DATA: {
                    LangGraphKey.OUTPUT: {
                        "missing_entities": [
                            {
                                "type": "year",
                                "question": "请提供年份信息",
                            }
                        ]
                    }
                },
            }

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session1", "营收多少"):
            events.append(event)

        clarification_events = [
            e for e in events if isinstance(e, SSEClarificationEvent)
        ]
        assert len(clarification_events) > 0
        assert clarification_events[0].type == "entity_completion"
        assert clarification_events[0].missing_entities[0]["type"] == "year"
        # 验证在澄清事件后终止了流（finally 块再发一次 DONE）
        done_events = [e for e in events if isinstance(e, SSEDoneEvent)]
        assert len(done_events) == 2

    @pytest.mark.asyncio
    async def test_stream_chat_no_clarification_when_no_missing_entities(self):
        """当 classify 节点不输出 missing_entities 时不应发送追问事件。"""
        from src.services.agent_service import AgentService
        from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode

        service = AgentService.__new__(AgentService)
        service._llm = Mock()
        service._chat_manager = AsyncMock()
        service._chat_manager.get_history_async.return_value = []
        service._chat_manager.add_message_async = AsyncMock()
        service._prompt_manager = Mock()
        service._tracer = Mock()

        async def fake_astream(*args, **kwargs):
            yield {
                LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
                LangGraphKey.NAME: LangGraphNode.Classify.NAME,
                LangGraphKey.DATA: {
                    LangGraphKey.OUTPUT: {
                        "missing_entities": [],
                    },
                },
            }
            yield {
                LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
                LangGraphKey.NAME: LangGraphNode.Rerank.NAME,
                LangGraphKey.DATA: {
                    LangGraphKey.OUTPUT: {"contexts": []},
                },
            }
            yield {
                LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
                LangGraphKey.NAME: LangGraphNode.Generate.NAME,
                LangGraphKey.DATA: {
                    LangGraphKey.OUTPUT: {
                        "model_used": "gpt-4",
                        "is_fallback": False,
                    },
                },
            }

        service._graph = Mock()
        service._graph.astream_events = fake_astream

        events = []
        async for event in service.stream_chat("kb1", "session1", "营收多少"):
            events.append(event)

        clarification_events = [
            e for e in events if isinstance(e, SSEClarificationEvent)
        ]
        assert len(clarification_events) == 0
