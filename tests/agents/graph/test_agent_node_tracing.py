"""generation 字段回填契约（D4 / D8）。"""

from src.agents.graph import agent_node


def _msg_payload_has_no_internal_objects(payload: list[dict]) -> bool:
    """input 只允许出现 role / content 两个键。"""
    allowed = {"role", "content"}
    return all(set(item.keys()) <= allowed for item in payload)


def test_messages_payload_shape():
    """消息载荷是 [{role, content}]，不夹带对象引用。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    payload = agent_node._messages_payload(
        [SystemMessage(content="sys"), HumanMessage(content="hi")]
    )
    assert payload == [
        {"role": "system", "content": "sys"},
        {"role": "human", "content": "hi"},
    ]
    assert _msg_payload_has_no_internal_objects(payload)


def test_observe_decorator_disables_input_capture():
    """agent_model 闭包必须以 capture_input=False 装饰。

    用源码检查而非运行检查：闭包在工厂内定义、无独立引用可拿，
    而这条约束一旦丢失是**静默**的（内部对象被序列化进 trace）。
    """
    import inspect

    src = inspect.getsource(agent_node.make_agent_model_node)
    assert "capture_input=False" in src
    assert 'as_type="generation"' in src
