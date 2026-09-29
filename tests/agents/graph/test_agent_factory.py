import pathlib
import re

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from src.agents.graph.agent_factory import LoopState, build_agent


class _FakeModel(GenericFakeChatModel):
    """最小可绑定模型的假模型（create_agent 构建期会校验模型形态）。"""

    def bind_tools(self, tools, **kwargs):
        return self


def _fake_model() -> _FakeModel:
    """每次调用返回独立假模型（messages 是迭代器，只能消费一次）。"""
    return _FakeModel(messages=iter([AIMessage(content="ok")]))


def _echo(x: str) -> str:
    """最小工具（create_agent 仅在工具面非空时才建 tools 节点）。"""
    return x


def test_build_agent_accepts_state_schema_and_returns_compiled_graph():
    agent = build_agent(_fake_model(), tools=[_echo], system="sys")
    # 装配产物必须是可 invoke 的编译图
    assert hasattr(agent, "ainvoke")
    assert set(agent.get_graph().nodes) >= {"model", "tools"}


def test_loop_state_declares_every_key_consumers_depend_on():
    keys = set(LoopState.__dataclass_fields__)
    assert {
        "_system_messages",
        "_turn_count",
        "_delegate_used",
        "kb_id",
        "query",
        "deep_thinking",
    } <= keys


def test_build_agent_does_not_emit_graph_compiled(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        build_agent(_fake_model(), tools=[], system="sys")
    assert "graph compiled" not in caplog.text


def test_build_agent_wires_loop_state_channels():
    """接线断言：build_agent 产物真的把 LoopState 接上了（六键进入图 channels）。

    直接读 LoopState.__dataclass_fields__ 的用例在 build_agent 丢掉
    state_schema=LoopState 后仍会全绿 ⇒ 核心契约无护栏；本用例改看编译产物的
    实际 channels，删掉 state_schema 即失败（create_agent 默认 AgentState 只有
    messages/jump_to/structured_response）。
    """
    agent = build_agent(_fake_model(), tools=[_echo], system="sys")
    channels = set(agent.builder.channels)
    assert {
        "_system_messages",
        "_turn_count",
        "_delegate_used",
        "kb_id",
        "query",
        "deep_thinking",
    } <= channels


def test_create_agent_is_only_called_from_agent_factory():
    """「唯一装配」静态扫描断言：src/agents/ 下只有装配入口可以调 create_agent(。"""
    # src/agents/ 下仅 agent_factory.py 可调用 create_agent(
    ALLOWED = {"agent_factory.py"}
    root = pathlib.Path(__file__).resolve().parents[3] / "src" / "agents"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ALLOWED:
            continue
        if re.search(r"\bcreate_agent\s*\(", path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"绕过装配入口的 create_agent 调用：{offenders}"
