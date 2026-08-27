"""测试 agent 循环节点 — model 调用/迭代计数、tools 路由、finalize 收尾提取。

fake LLM（MockChatModel）与 stub PromptManager 均为内存实现，不发真实网络调用。
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from src.agents.graph.agent_node import (
    make_agent_finalize_node,
    make_agent_model_node,
    route_agent,
)
from src.agents.graph.state import AgentState
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.context import RAGContext


class MockChatModel:
    """极简 fake LLM：bind_tools 原样返回，astream 返回固定响应（单块）。"""

    def __init__(self, response):
        """记录固定响应。"""
        self.response = response

    def bind_tools(self, tools):
        """绑定工具：fake 直接返回自身。"""
        return self

    async def astream(self, messages, **kwargs):
        """以单块流式序列返回固定响应，忽略 extra_body 等额外参数。"""
        yield self.response


class StubPromptManager:
    """极简 PromptManager stub：只提供 build_prompt 需要的两个方法。"""

    def get_system_prompt(self):
        """返回固定系统指令。"""
        return "system prompt"

    def get_user_template(self, context="", query=""):
        """返回含 query 的用户模板。"""
        return f"user template: {query}"


@pytest.mark.asyncio
async def test_finalize_extracts_answer_and_contexts():
    """finalize 应提取末次消息文本为 answer，并把 tool_contexts 读入 state。"""
    ctx = RequestContext(session_id="s1")
    ctx.tool_contexts.append(
        RAGContext(
            content="x", source="a.pdf", page=1, doc_id="d1", chunk_id="d1:0", score=0.9
        )
    )
    token = current_request_ctx.set(ctx)
    try:
        state = AgentState.make_initial_state("s1", "kb1", "q", [])
        state.messages = [HumanMessage(content="q"), AIMessage(content="答案是X [1]")]
        node = make_agent_finalize_node()
        out = await node(state)
        assert out["answer"] == "答案是X [1]"
        assert len(out["tool_contexts"]) == 1
    finally:
        current_request_ctx.reset(token)


@pytest.mark.asyncio
async def test_finalize_content_blocks():
    """content 为 list[dict(type=text)] 时按序拼接为纯文本。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state.messages = [
        HumanMessage(content="q"),
        AIMessage(
            content=[
                {"type": "text", "text": "第一部分"},
                {"type": "text", "text": "第二部分"},
            ]
        ),
    ]
    node = make_agent_finalize_node()
    out = await node(state)
    assert out["answer"] == "第一部分第二部分"


@pytest.mark.asyncio
async def test_finalize_no_messages_returns_empty_answer():
    """messages 为空时 answer 为空字符串，不抛异常。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    node = make_agent_finalize_node()
    out = await node(state)
    assert out["answer"] == ""
    assert out["tool_contexts"] == []


def test_route_agent():
    """有 tool_calls → tools；无 → agent_finalize；超限 → agent_finalize。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state.messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "retrieve_kb", "args": {}, "id": "c1", "type": "tool_call"}
            ],
        )
    ]
    assert route_agent(state) == "tools"
    state.messages = [AIMessage(content="直接回答")]
    assert route_agent(state) == "agent_finalize"
    state._agent_iterations = 99
    assert route_agent(state) == "agent_finalize"  # 超限强制收尾


def test_route_agent_non_ai_message_finalizes():
    """末条消息非 AIMessage（无 tool_calls 属性）时安全收尾。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state.messages = [HumanMessage(content="q")]
    assert route_agent(state) == "agent_finalize"


@pytest.mark.asyncio
async def test_agent_model_increments_iterations():
    """model 节点后续轮次应自增迭代计数并仅追加模型输出消息。"""
    fake_response = AIMessage(content="模型回答")
    llm = MockChatModel(fake_response)
    node = make_agent_model_node(llm, [], StubPromptManager())

    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    state.messages = [HumanMessage(content="q")]  # 已有消息，模拟后续轮次
    out = await node(state)

    assert out["_agent_iterations"] == state._agent_iterations + 1
    assert len(out["messages"]) == 1
    assert out["messages"][0] == fake_response


@pytest.mark.asyncio
async def test_agent_model_first_round_persists_initial_messages():
    """首轮 messages 为空时，返回应持久化初始消息（system + 原始 query）及模型输出。"""
    fake_response = AIMessage(content="模型回答")
    llm = MockChatModel(fake_response)
    node = make_agent_model_node(llm, [], StubPromptManager())

    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    out = await node(state)

    msgs = out["messages"]
    assert len(msgs) == 3  # system + user(query) + 模型 AIMessage
    assert msgs[0].type == "system"
    assert isinstance(msgs[1], HumanMessage)
    assert "q" in msgs[1].content
    assert msgs[2] == fake_response


@pytest.mark.asyncio
async def test_agent_model_injects_initial_messages():
    """messages 为空时首轮注入 system + 历史 + query 后调用模型。"""
    captured = {}

    class CapturingChatModel(MockChatModel):
        """记录 astream 收到的 messages。"""

        async def astream(self, messages, **kwargs):
            captured["messages"] = messages
            yield self.response

    llm = CapturingChatModel(AIMessage(content="ok"))
    node = make_agent_model_node(llm, [], StubPromptManager())

    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    await node(state)

    msgs = captured["messages"]
    assert len(msgs) == 2  # system + user（无历史时）
    assert msgs[0].type == "system"
    assert "q" in msgs[-1].content


@pytest.mark.asyncio
async def test_agent_model_astream_merges_chunks():
    """astream 多块经 += 聚合：content 拼接且 tool_call_chunks 合并为 tool_calls。"""
    chunks = [
        AIMessageChunk(content="需要"),
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "retrieve_kb",
                    "args": '{"query": "营收"}',
                    "id": "c1",
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        ),
    ]

    class MultiChunkModel(MockChatModel):
        """astream 返回多个 chunk 的 fake。"""

        async def astream(self, messages, **kwargs):
            for chunk in self.response:
                yield chunk

    llm = MultiChunkModel(chunks)
    node = make_agent_model_node(llm, [], StubPromptManager())

    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    out = await node(state)

    result = out["messages"][-1]  # 首轮注入 system+user，模型输出在末尾
    assert result.content == "需要"
    assert result.tool_calls and result.tool_calls[0]["name"] == "retrieve_kb"


@pytest.mark.asyncio
async def test_agent_model_passes_enable_thinking():
    """model 节点应按 state.deep_thinking 向 astream 传 extra_body.enable_thinking。"""
    captured = {}

    class CapturingThinkingModel(MockChatModel):
        """记录 astream 收到的 kwargs。"""

        async def astream(self, messages, **kwargs):
            captured["extra_body"] = kwargs.get("extra_body")
            yield self.response

    llm = CapturingThinkingModel(AIMessage(content="ok"))
    node = make_agent_model_node(llm, [], StubPromptManager())

    state = AgentState.make_initial_state("s1", "kb1", "q", [], deep_thinking=True)
    await node(state)

    assert captured["extra_body"] == {"enable_thinking": True}


def test_make_initial_state_deep_thinking_default_false():
    """未传 deep_thinking 时默认 False。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [])
    assert state.deep_thinking is False


def test_make_initial_state_deep_thinking_true():
    """传 deep_thinking=True 时状态字段为 True。"""
    state = AgentState.make_initial_state("s1", "kb1", "q", [], deep_thinking=True)
    assert state.deep_thinking is True
