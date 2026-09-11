"""图入口分派：direct_skill 非空 → skill_direct；空 → 常规 agent 轮。"""

from src.agents.graph.skill_direct import route_entry
from src.agents.graph.state import AgentState


def test_entry_routes_direct_when_skill_selected():
    """命令行直出：direct_skill 非空 → skill_direct。"""
    assert (
        route_entry(AgentState(session_id="s1", direct_skill="finance-analyst"))
        == "skill_direct"
    )


def test_entry_routes_agent_when_no_skill():
    """常规轮：direct_skill 为空 → agent（既有行为不变）。"""
    assert route_entry(AgentState(session_id="s1")) == "agent"
