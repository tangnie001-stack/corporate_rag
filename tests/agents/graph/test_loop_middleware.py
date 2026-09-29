"""agent 循环四个 middleware 的单元测试（Task 3/4/5/6）。"""

from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.middleware import ModelParamsMiddleware, SystemMessagesMiddleware
from src.config import settings


class _RecordingModel(GenericFakeChatModel):
    """记录每次调用实收消息与 kwargs 的假模型。"""

    seen: ClassVar[list] = []
    seen_kwargs: ClassVar[list] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _RecordingModel.seen.append(list(messages))
        _RecordingModel.seen_kwargs.append(dict(kwargs))
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


@pytest.mark.asyncio
async def test_bound_kb_omits_temperature_key():
    """绑 KB 档：不带 temperature 键（沿用模型构造温度）。"""
    from src.infra.llm.request_context import RequestContext, current_request_ctx

    ctx = RequestContext(session_id="s", kb_id="kb-1", kb_bound=True)
    token = current_request_ctx.set(ctx)
    try:
        _RecordingModel.seen_kwargs = []
        model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
        agent = build_agent(
            model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
        )
        await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": "kb-1"})
        kw = _RecordingModel.seen_kwargs[-1]
        assert "temperature" not in kw
        assert kw["extra_body"] == {"enable_thinking": False}
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_unbound_kb_passes_explicit_temperature():
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "kb_id": ""})
    kw = _RecordingModel.seen_kwargs[-1]
    assert kw["temperature"] == settings.NON_KB_MAIN_TEMPERATURE


@pytest.mark.asyncio
async def test_deep_thinking_switch_comes_from_seeded_state():
    """思考开关取 seeded 的 deep_thinking（漏 seed 会静默关闭）。"""
    _RecordingModel.seen_kwargs = []
    model = _RecordingModel(messages=iter([AIMessage(content="ok")]))
    agent = build_agent(
        model, tools=[], system="S", middleware_extra=[ModelParamsMiddleware()]
    )
    await agent.ainvoke(
        {"messages": [HumanMessage(content="hi")], "kb_id": "", "deep_thinking": True}
    )
    assert _RecordingModel.seen_kwargs[-1]["extra_body"] == {"enable_thinking": True}
