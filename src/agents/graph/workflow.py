# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import StateGraph, END
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.nodes import (
    classify_node, rewrite_node, grader_node, format_node,
    make_retrieve_node, make_rerank_node, make_generate_node,
)
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index


def route_by_intent(state: AgentState) -> str:
    """根据意图路由到不同路径。"""
    intent = state.get("intent", {})
    return intent.get("route", "medium")


def route_by_grader(state: AgentState) -> str:
    """根据 grader 分数决定继续还是重试。"""
    score = state.get("grader_score", 0)
    retries = state.get("retrieval_retries", 0)
    if score is not None and score >= 0.5:
        return "pass"
    if retries < 2:
        # route_by_grader 内部增加重试计数
        state["retrieval_retries"] = retries + 1  # type: ignore
        return "rewrite"
    # 重试用尽，降级到 Enhanced RAG
    state["downgraded"] = True  # type: ignore
    state["downgrade_reason"] = "grader_retries_exhausted"  # type: ignore
    return "pass"


def build_graph(vector_store: VectorStore, bm25: BM25Index | None,
                llm, reranker, prompt_manager, tracer) -> StateGraph:
    """构建并编译 StateGraph。"""
    builder = StateGraph(AgentState)

    # ── 用工厂函数创建带依赖的节点 ────────────────
    builder.add_node("classify", classify_node)
    builder.add_node("rewrite", rewrite_node)
    builder.add_node("retrieve", make_retrieve_node(vector_store, bm25))
    builder.add_node("grader", grader_node)
    builder.add_node("rerank", make_rerank_node(reranker))
    builder.add_node("generate", make_generate_node(llm, prompt_manager, tracer))
    builder.add_node("format", format_node)

    # ── 条件边：三级路由 ──────────────────────────
    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify", route_by_intent, {
            "simple":  "generate",    # Naive RAG：无检索
            "medium":  "rewrite",     # Enhanced RAG
            "complex": "rewrite",     # Agentic RAG
        }
    )

    # medium + complex → rewrite → retrieve
    builder.add_edge("rewrite", "retrieve")

    # complex 路径：retrieve → grader
    builder.add_edge("retrieve", "grader")

    # grader 条件边：通过 → rerank，不通过（+ retry < 2）→ rewrite
    builder.add_conditional_edges(
        "grader", route_by_grader, {
            "pass":    "rerank",
            "rewrite": "rewrite",
        }
    )

    builder.add_edge("rerank", "generate")
    builder.add_edge("generate", "format")
    builder.add_edge("format", END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: 7 nodes, 3-tier routing")
    return graph
