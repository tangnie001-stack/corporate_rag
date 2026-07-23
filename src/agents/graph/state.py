# src/agents/graph/state.py
"""AgentState — LangGraph 图状态定义。

包含 RAG 流水线完整状态（输入/中间态/输出）和图控制字段。
"""

from typing import TypedDict, Optional, List


class RAGQueryIntent(TypedDict, total=False):
    """查询意图分类结果。"""
    route: str  # "simple" | "medium" | "complex"
    rewritten: bool


class RAGContextItem(TypedDict, total=False):
    """检索结果上下文项（对应 rag/context.py 中的 RAGContext）。"""
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    score: float


class AgentState(TypedDict, total=False):
    """LangGraph 图执行状态。"""
    # ── 输入 ─────
    session_id: str
    kb_id: str
    query: str
    # ── 中间态 ───
    intent: RAGQueryIntent
    rewritten_query: Optional[str]
    retrieval_results: List[dict]
    contexts: List[RAGContextItem]
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
