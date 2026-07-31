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


@pytest.mark.asyncio
async def test_stream_chat_yields_generate_tokens_from_metadata():
    """generate 节点的 CHAT_MODEL_STREAM token 应通过 metadata.langgraph_node 识别并流出。"""
    from src.services.agent_service import AgentService
    from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode
    from src.utils.sse import SSETokenEvent
    from langchain_core.messages import AIMessageChunk

    service = AgentService.__new__(AgentService)
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    async def fake_astream(*args, **kwargs):
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
            LangGraphKey.NAME: "ChatOpenAI",  # 事件 name 是模型类名，不是节点名
            "metadata": {"langgraph_node": LangGraphNode.Generate.NAME},
            LangGraphKey.DATA: {"chunk": AIMessageChunk(content="你好")},
        }

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events = []
    async for event in service.stream_chat("kb1", "session1", "阿里巴巴"):
        events.append(event)

    token_events = [e for e in events if isinstance(e, SSETokenEvent)]
    assert len(token_events) == 1
    assert token_events[0].token == "你好"


@pytest.mark.asyncio
async def test_stream_chat_yields_abstention_answer_as_token():
    """generate 返回静态 answer 时（abstention），应作为 token 送达且 citations 为空。"""
    from src.services.agent_service import AgentService
    from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode
    from src.utils.sse import SSETokenEvent, SSECitationEvent, SSEStatusEvent
    from src.config.prompts import ABSTENTION_TEXT

    service = AgentService.__new__(AgentService)
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    async def fake_astream(*args, **kwargs):
        # rerank 产出空 contexts → generate 走 abstention 静态文案
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Rerank.NAME,
            LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"contexts": []}},
        }
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Generate.NAME,
            LangGraphKey.DATA: {
                LangGraphKey.OUTPUT: {
                    "answer": ABSTENTION_TEXT,
                    "model_used": "",
                    "is_fallback": False,
                }
            },
        }
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Format.NAME,
            LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"citations": []}},
        }

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events = []
    async for event in service.stream_chat("kb1", "session1", "阿里巴巴"):
        events.append(event)

    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    statuses = [e for e in events if isinstance(e, SSEStatusEvent)]
    assert any(t.token == ABSTENTION_TEXT for t in tokens)
    assert citations == []
    assert len(statuses) >= 1  # abstention 状态提示
    # 持久化到 chat_manager（开头还有一次 user 消息调用，用 assert_any_call 匹配 assistant 那次）
    service._chat_manager.add_message_async.assert_any_call(
        "session1", "assistant", ABSTENTION_TEXT
    )
