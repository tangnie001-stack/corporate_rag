"""generation 字段回填契约（D4 / D8）。"""

from typing import ClassVar

from src.agents.graph import agent_node
from src.agents.graph.message_payload import _messages_payload


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


def test_observe_decorator_disables_input_capture():
    """agent_model 闭包必须以 capture_input=False 装饰。

    用源码检查而非运行检查：闭包在工厂内定义、无独立引用可拿，
    而这条约束一旦丢失是**静默**的（内部对象被序列化进 trace）。
    """
    import inspect

    src = inspect.getsource(agent_node.make_agent_model_node)
    assert "capture_input=False" in src
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
    """模型只发 tool_calls、文本为空时，generation 的 output 不得落成节点返回的 state dict。

    显式写入的 output 在此轮为空串（falsy），会走 SDK 的自动捕获回落；本用例把回落
    结果捕获下来，断言它不是 `{"messages": ...}`。若去掉 `capture_output=False`，
    回落拿到的就是节点返回的 state dict，本用例即失败——以此锁住新旧行为的区分。
    """
    import asyncio

    from langchain_core.messages import HumanMessage

    from src.agents.graph.state import AgentState

    # 替身 1：spy 显式写入（langfuse_context），确认显式 output 为空串而非 state dict
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
    assert explicit_updates[0]["output"] == ""

    generation_outputs = [r["output"] for r in recorded if "model" in r]
    assert generation_outputs, "应有一条 generation 结束记录"
    assert all(o is None for o in generation_outputs)
    assert not any(isinstance(o, dict) and "messages" in o for o in generation_outputs)
