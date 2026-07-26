from dataclasses import dataclass, field
from typing import Optional
from src.infra.db.entities import ChunkResult
from src.rag.context import RAGContext


@dataclass
class RAGQueryIntent:
    route: str = ""
    rewritten: bool = False


@dataclass
class AgentState:
    session_id: str = ""
    kb_id: str = ""
    query: str = ""
    trace_id: str = "unknown"
    intent: RAGQueryIntent = field(default_factory=RAGQueryIntent)
    rewritten_query: str = ""
    retrieval_results: list[ChunkResult] = field(default_factory=list)
    contexts: list[RAGContext] = field(default_factory=list)
    grader_score: Optional[float] = None
    retrieval_retries: int = 0
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    downgraded: bool = False
    downgrade_reason: str = ""
    _history: list[dict] = field(default_factory=list)
    _token_usage: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)

    @classmethod
    def make_initial_state(cls, session_id, kb_id, query, trace_id, history):
        return cls(session_id=session_id, kb_id=kb_id, query=query,
                   trace_id=trace_id, _history=history)


# 模块级别名，兼容 from ... import make_initial_state 的已有调用方
make_initial_state = AgentState.make_initial_state
