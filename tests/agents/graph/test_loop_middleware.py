"""agent 循环四个 middleware 的单元测试（Task 3/4/5/6）。"""

from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.middleware import SystemMessagesMiddleware


class _RecordingModel(GenericFakeChatModel):
    """记录每次调用实收消息的假模型。"""

    seen: ClassVar[list] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RecordingModel.seen.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def _build_recording_agent(*system_contents):
    _RecordingModel.seen = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model,
        tools=[],
        system=None,
        middleware_extra=[SystemMessagesMiddleware()],
    )
    return agent


def _sysmsgs(*contents):
    return [SystemMessage(content=c) for c in contents]


@pytest.mark.asyncio
async def test_unbound_kb_sends_two_system_messages_in_order():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("FIRST", "SECOND-UNBOUND"),
        }
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:3]] == [
        "SystemMessage",
        "SystemMessage",
        "HumanMessage",
    ]
    assert sent[0].content == "FIRST"
    assert sent[1].content == "SECOND-UNBOUND"


@pytest.mark.asyncio
async def test_bound_kb_sends_single_system_message():
    agent = _build_recording_agent()
    await agent.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "_system_messages": _sysmsgs("FIRST"),
        }
    )
    sent = _RecordingModel.seen[-1]
    assert [type(m).__name__ for m in sent[:2]] == ["SystemMessage", "HumanMessage"]
    assert sent[0].content == "FIRST"
