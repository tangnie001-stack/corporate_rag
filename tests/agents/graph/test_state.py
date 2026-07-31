from src.agents.graph.state import AgentState
from src.rag.context import RAGContext


def test_agent_state_defaults():
    state = AgentState(query="2024年营收多少", kb_id="kb-1")
    assert state.query == "2024年营收多少"
    assert state.kb_id == "kb-1"


def test_agent_state_with_contexts():
    state = AgentState(
        query="净利润多少",
        contexts=[
            RAGContext(
                content="净利润100亿",
                source="财报.pdf",
                page=5,
                doc_id="doc-1",
                chunk_id="chunk-1",
                score=0.95,
            )
        ],
    )
    assert len(state.contexts) == 1
    assert state.contexts[0].content == "净利润100亿"


def test_agent_state_downgrade_fields():
    state = AgentState(query="test", downgraded=True, downgrade_reason="rerank_empty")
    assert state.downgraded is True
    assert state.downgrade_reason == "rerank_empty"


def test_agent_state_trace_id():
    state = AgentState(query="test", trace_id="trace_abc123")
    assert state.trace_id == "trace_abc123"


def test_agent_state_intent_fields():
    """验证新增的意图理解字段默认值。"""
    state = AgentState()
    assert state.extracted_entities == []
    assert state.missing_entities == []
    assert state.classification_confidence == 0.0


def test_agent_state_intent_fields_with_values():
    """验证 intent 字段可以正常赋值。"""
    from src.agents.graph.state import RAGQueryIntent

    state = AgentState(
        intent=RAGQueryIntent(route="medium"),
        extracted_entities=[{"type": "year", "value": "2024"}],
        missing_entities=[{"type": "year"}],
        classification_confidence=0.85,
    )
    assert len(state.extracted_entities) == 1
    assert len(state.missing_entities) == 1
    assert state.classification_confidence == 0.85


def test_agent_state_skip_retrieval_default_false():
    """skip_retrieval 默认应为 False。"""
    from src.agents.graph.state import AgentState

    state = AgentState.make_initial_state("s1", "kb1", "营收多少", [])
    assert state.skip_retrieval is False
