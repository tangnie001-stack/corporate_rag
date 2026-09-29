"""消息载荷形态契约（Langfuse input/output 的 role 规范化与字段白名单）。"""

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
