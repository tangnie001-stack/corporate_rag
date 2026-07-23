"""Tests for AgentState definition."""
from src.agents.graph.state import AgentState, RAGContextItem


def test_agent_state_defaults():
    """AgentState SHALL allow partial initialization (total=False)."""
    state: AgentState = {"query": "2024年营收多少", "kb_id": "kb-1"}
    assert state["query"] == "2024年营收多少"
    assert state["kb_id"] == "kb-1"


def test_agent_state_with_contexts():
    """AgentState SHALL hold RAGContextItem list."""
    state: AgentState = {
        "query": "净利润多少",
        "contexts": [{"content": "净利润100亿", "source": "财报.pdf",
                      "page": 5, "doc_id": "doc-1", "chunk_id": "chunk-1",
                      "score": 0.95}],
    }
    assert len(state["contexts"]) == 1
    assert state["contexts"][0]["content"] == "净利润100亿"


def test_agent_state_downgrade_fields():
    """AgentState SHALL support downgrade tracking."""
    state: AgentState = {"query": "test", "downgraded": True,
                         "downgrade_reason": "rerank_empty"}
    assert state["downgraded"] is True
    assert state["downgrade_reason"] == "rerank_empty"


def test_agent_state_trace_id():
    """AgentState SHALL carry trace_id."""
    state: AgentState = {"query": "test", "trace_id": "trace_abc123"}
    assert state["trace_id"] == "trace_abc123"
