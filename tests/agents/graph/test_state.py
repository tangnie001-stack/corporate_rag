from dataclasses import fields
from typing import Annotated, get_args, get_origin

from langgraph.graph.message import add_messages

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


def test_agent_state_no_grader_fields():
    state = AgentState(query="test")
    assert not hasattr(state, "grader_score")
    assert not hasattr(state, "retrieval_retries")
    assert not hasattr(state, "downgraded")


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


def test_state_has_messages_with_addmessages_reducer():
    """messages 字段应存在，且带追加语义 reducer（add_messages）注解。"""
    msg_field = next(f for f in fields(AgentState) if f.name == "messages")
    assert get_origin(msg_field.type) is Annotated
    assert add_messages in get_args(msg_field.type)


def test_state_has_agent_fields():
    """新增的 agent 循环护栏字段应存在。"""
    names = {f.name for f in fields(AgentState)}
    assert "tool_contexts" in names
    assert "_agent_iterations" in names
    assert "_max_agent_iterations" in names
    assert "_ask_count" in names


def test_old_fields_still_present():
    """旧字段应仍保留（本次改造只加不删）。"""
    names = {f.name for f in fields(AgentState)}
    assert "missing_entities" in names
    assert "intent" in names
    assert "rewritten_query" in names
    assert "retrieval_results" in names
    assert "contexts" in names
