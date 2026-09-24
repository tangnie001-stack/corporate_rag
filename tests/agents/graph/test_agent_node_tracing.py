"""generation 字段回填契约（D4 / D8）。"""

from typing import ClassVar

from src.agents.graph import agent_node
from src.agents.graph.message_payload import _messages_payload, _observation_output


def _msg_payload_keys_are_whitelisted(payload: list[dict]) -> bool:
    """input 只允许出现 role / content / tool_calls / name 四个键。"""
    allowed = {"role", "content", "tool_calls", "name"}
    return all(set(item.keys()) <= allowed for item in payload)


def test_messages_payload_shape_normalizes_roles():
    """role 用 OpenAI 形态（human→user、ai→assistant），不是 LangChain 类型名。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    payload = _messages_payload(
        [SystemMessage(content="sys"), HumanMessage(content="hi")]
    )
    assert payload == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert _msg_payload_keys_are_whitelisted(payload)


def test_messages_payload_carries_tool_calls_and_tool_name():
    """assistant 条目补 tool_calls，tool 条目补 name —— 否则「模型要调什么」读不出。"""
    from langchain_core.messages import AIMessage, ToolMessage

    payload = _messages_payload(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "retrieve_kb", "args": {"query": "q"}, "id": "call_1"}
                ],
            ),
            ToolMessage(content="[1] 来源…", tool_call_id="call_1", name="retrieve_kb"),
        ]
    )
    assert payload[0]["role"] == "assistant"
    assert payload[0]["tool_calls"] == [
        {"id": "call_1", "name": "retrieve_kb", "args": {"query": "q"}}
    ]
    assert payload[1]["role"] == "tool"
    assert payload[1]["name"] == "retrieve_kb"
    assert _msg_payload_keys_are_whitelisted(payload)


def test_observation_output_prefers_text_over_tool_calls():
    """文本非空时 output 取文本，即使同轮也带了 tool_calls。"""
    from langchain_core.messages import AIMessage

    message = AIMessage(
        content="答案",
        tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "call_1"}],
    )
    assert _observation_output(message) == "答案"


def test_observation_output_empty_when_no_text_and_no_tool_calls():
    """文本与 tool_calls 皆空时 output 为空串。"""
    from langchain_core.messages import AIMessage

    assert _observation_output(AIMessage(content="")) == ""


def test_observe_decorator_disables_capture():
    """agent_model 闭包必须以 capture_input=False 与 capture_output=False 装饰。

    用源码检查而非运行检查：闭包在工厂内定义、无独立引用可拿，
    而这条约束一旦丢失是**静默**的（内部对象被序列化进 trace）。
    capture_output=False 在「一轮既无文本也无 tool_calls」时生效：此时显式 output
    为空串，SDK 会走自动捕获回落，把节点返回的 state dict 写进 trace。
    """
    import inspect

    src = inspect.getsource(agent_node.make_agent_model_node)
    assert "capture_input=False" in src
    assert "capture_output=False" in src
    assert 'as_type="generation"' in src


class _EmptyTextToolCallChunk:
    """替身 chunk：文本为空、只带 tool_calls（agent 循环里模型只发工具调用的常见一轮）。"""

    content = ""  # 纯 tool_calls 轮，无文本
    usage_metadata = None  # 触发 estimate_usage 兜底
    response_metadata: ClassVar[dict] = {"model_name": "fake-model"}
    tool_calls: ClassVar[list] = [
        {"name": "search", "args": {"q": "x"}, "id": "call_1"}
    ]


class _ToolCallOnlyLLM:
    """替身 LLM：bind_tools 返回自身，astream 只产出一个 tool_calls-only chunk。"""

    def bind_tools(self, tools):
        """返回自身，跳过真实工具绑定。"""
        return self

    async def astream(self, messages, **kwargs):
        """只产出 tool_calls-only 的替身 chunk。"""
        yield _EmptyTextToolCallChunk()


def test_empty_text_output_does_not_fall_back_to_state_dict(monkeypatch):
    """模型只发 tool_calls、文本为空时，generation 的 output 写 tool_calls 结构，不落成 state dict。

    该轮显式 output 为非空的 `{"tool_calls": [...]}`，SDK 不再走自动捕获回落
    （capture_output=False 只在显式 output 为空时才生效）；本用例断言显式写入的
    output 抵达 observation，且不是节点返回的 `{"messages": ...}` state dict。
    """
    import asyncio

    from langchain_core.messages import HumanMessage

    from src.agents.graph.state import AgentState

    # 替身 1：spy 显式写入（langfuse_context），确认显式 output 是 tool_calls 结构而非 state dict
    explicit_updates: list[dict] = []
    real_update = agent_node.langfuse_context.update_current_observation

    def _spy_update(**kwargs):
        """记录显式回填参数后转发真实实现（保证 observation 参数照常入上下文）。"""
        explicit_updates.append(kwargs)
        return real_update(**kwargs)

    monkeypatch.setattr(
        agent_node.langfuse_context, "update_current_observation", _spy_update
    )

    # 替身 2：捕获 SDK 最终落到 observation 上的 output（不发网络）
    recorded: list[dict] = []

    def _spy_end(self, **kwargs):
        """记录 SDK 收尾时落到 observation 上的字段，替代真实上报。"""
        recorded.append(kwargs)

    monkeypatch.setattr("langfuse.client.StatefulGenerationClient.end", _spy_end)

    node = agent_node.make_agent_model_node(_ToolCallOnlyLLM(), [], None)
    state = AgentState(
        kb_id="",
        query="q",
        messages=[HumanMessage(content="hi")],
    )
    asyncio.run(node(state))

    assert explicit_updates, "应发生一次 generation 字段回填"
    assert explicit_updates[0]["output"] == {
        "tool_calls": [{"id": "call_1", "name": "search", "args": {"q": "x"}}]
    }

    generation_outputs = [r["output"] for r in recorded if "model" in r]
    assert generation_outputs, "应有一条 generation 结束记录"
    assert generation_outputs == [
        {"tool_calls": [{"id": "call_1", "name": "search", "args": {"q": "x"}}]}
    ]
    assert not any(isinstance(o, dict) and "messages" in o for o in generation_outputs)
