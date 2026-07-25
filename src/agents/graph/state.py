# src/agents/graph/state.py
"""AgentState — LangGraph 图状态定义。

包含 RAG 流水线完整状态（输入/中间态/输出）和图控制字段。
"""

from typing import TypedDict, Optional, List

from src.infra.db.entities import ChunkResult
from src.rag.context import RAGContext


class RAGQueryIntent(TypedDict, total=False):
    """查询意图分类结果。"""
    route: str  # "simple" | "medium" | "complex"
    rewritten: bool


class AgentState(TypedDict, total=False):
    """LangGraph 图执行状态。"""
    # ── 输入 ─────
    session_id: str
    kb_id: str
    query: str
    # ── 中间态 ───
    intent: RAGQueryIntent
    rewritten_query: Optional[str]
    retrieval_results: List[ChunkResult]
    contexts: List[RAGContext]
    grader_score: Optional[float]
    retrieval_retries: int
    # ── 输出 ─────
    answer: str
    citations: List[dict]
    # ── 可观测 ───
    trace_id: str
    timings: dict
    # ── 降级控制 ─
    downgraded: bool
    downgrade_reason: str
    # ── 内部 ─────
    _history: list
    _token_usage: dict
