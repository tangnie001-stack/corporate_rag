"""agent 循环四个 middleware 的单元测试（Task 3/4/5/6）。"""

import asyncio
from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.middleware import (
    AgentTurnBudget,
    ModelParamsMiddleware,
    SystemMessagesMiddleware,
)
from src.config import settings
from src.core.log_events import Event


def _capture_events(monkeypatch) -> list[dict]:
    """拦截 log_event 收集事件（日志走 loguru，caplog 抓不到）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.core.logging.log_event", fake_log_event)
    return calls


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


@tool
def echo(x: str) -> str:
    """echo。"""
    return x


@tool
def delegate_task(skill: str) -> str:
    """委派。"""
    return "delegated"


class _LoopingModel(GenericFakeChatModel):
    """前 tool_rounds 次调用发工具调用，之后正常收尾；记录调用次数。"""

    tool_rounds: int = 0
    tool_name: str = "echo"
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        _LoopingModel.calls += 1
        if _LoopingModel.calls <= _LoopingModel.tool_rounds:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": _LoopingModel.tool_name,
                        "args": {"x": "1", "skill": "s"},
                        "id": f"c{_LoopingModel.calls}",
                    }
                ],
            )
        else:
            msg = AIMessage(content="ANSWER")
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.mark.asyncio
async def test_hit_limit_skips_last_tool_and_keeps_tool_call_message():
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    executed = []

    @tool("echo")
    def _echo(x: str) -> str:
        """echo。"""
        executed.append(x)
        return x

    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model,
        tools=[_echo],
        system="S",
        max_turns=3,
        middleware_extra=[AgentTurnBudget(limit=3)],
    )
    out = await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _LoopingModel.calls == 3  # 模型调用 = 上限次
    assert len(executed) == 2  # 工具执行 = 上限 − 1
    last = out["messages"][-1]
    assert isinstance(last, AIMessage) and last.tool_calls  # 末条含 tool_calls
    assert (last.content or "") == ""  # 允许空串


@pytest.mark.asyncio
async def test_limit_hit_on_normal_finish_still_logs(monkeypatch):
    """上限轮恰好正常收尾（无 tool_calls）也产出 iteration limit。"""
    events = _capture_events(monkeypatch)
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 1
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model,
        tools=[echo],
        system="S",
        max_turns=2,
        middleware_extra=[AgentTurnBudget(limit=2)],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert any(c["event"] is Event.ITERATION_LIMIT for c in events)


@pytest.mark.asyncio
async def test_delegate_bonus_applies_in_the_same_round():
    """上限同轮声明的委派工具必须被执行（不能被跳过）。"""
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "delegate_task"
    ran = []

    @tool("delegate_task")
    def _delegate(skill: str) -> str:
        """委派。"""
        ran.append(skill)
        return "ok"

    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model,
        tools=[_delegate],
        system="S",
        max_turns=5,
        middleware_extra=[AgentTurnBudget(limit=5)],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert ran, "上限轮声明的委派工具必须被执行"


@pytest.mark.asyncio
async def test_budget_instance_shared_across_requests_does_not_bleed():
    """同一个 middleware 实例被两个并发请求使用，计数互不污染。"""
    mw = AgentTurnBudget(limit=3)
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(
        model, tools=[echo], system="S", max_turns=3, middleware_extra=[mw]
    )

    async def one(i):
        return await agent.ainvoke(
            {"messages": [HumanMessage(content=f"q{i}")], "query": f"q{i}"}
        )

    r1, r2 = await asyncio.gather(one(1), one(2))
    for r in (r1, r2):
        assert r["_turn_count"] == 3  # 各自独立从 0 起


@pytest.mark.asyncio
async def test_build_agent_max_turns_alone_enforces_budget():
    """Ruling O：只传 max_turns（不传预算 middleware）也必须生效。

    否则是「静默无回合上限」——评审实测的失败模式。
    """
    _LoopingModel.calls = 0
    _LoopingModel.tool_rounds = 10
    _LoopingModel.tool_name = "echo"
    model = _LoopingModel(messages=iter([]))
    agent = build_agent(model, tools=[echo], system="S", max_turns=3)
    out = await agent.ainvoke({"messages": [HumanMessage(content="hi")], "query": "q"})
    assert _LoopingModel.calls == 3
    assert out["_turn_count"] == 3
