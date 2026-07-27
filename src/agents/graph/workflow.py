# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.nodes import (
    classify_node,
    rewrite_node,
    grader_node,
    format_node,
    make_retrieve_node,
    make_rerank_node,
    make_generate_node,
)
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.config.const import LangGraph


def route_by_intent(state: AgentState) -> str:
    """根据意图路由到不同路径。"""
    return state.intent.route or "medium"


def route_by_grader(state: AgentState) -> str:
    """根据 grader 分数决定继续还是重试（纯条件函数，不修改 state）。"""
    if state.downgraded:
        logger.info("route_by_grader: downgraded=true -> pass")
        return "pass"
    score = state.grader_score or 0
    retries = state.retrieval_retries
    if score >= 0.5:
        logger.info("route_by_grader: score={:.2f} >= 0.5 -> pass", score)
        return "pass"
    if retries < 3:
        logger.info(
            "route_by_grader: score={:.2f} retries={} < 3 -> rewrite", score, retries
        )
        return "rewrite"
    logger.info(
        "route_by_grader: score={:.2f} retries={} >= 3 -> pass (downgrade)",
        score,
        retries,
    )
    return "pass"


def build_graph(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    reranker,
    prompt_manager,
    tracer,
) -> CompiledStateGraph:
    """构建并编译 StateGraph。"""
    builder = StateGraph(AgentState)

    # ── 用工厂函数创建带依赖的节点 ────────────────
    builder.add_node(LangGraph.NODE_CLASSIFY, classify_node)
    builder.add_node(LangGraph.NODE_REWRITE, rewrite_node)
    builder.add_node(LangGraph.NODE_RETRIEVE, make_retrieve_node(vector_store, bm25))
    builder.add_node(LangGraph.NODE_GRADER, grader_node)
    builder.add_node(LangGraph.NODE_RERANK, make_rerank_node(reranker))
    builder.add_node(LangGraph.NODE_GENERATE, make_generate_node(llm, prompt_manager, tracer))
    builder.add_node(LangGraph.NODE_FORMAT, format_node)

    # ── 条件边：三级路由 ──────────────────────────
    builder.set_entry_point(LangGraph.NODE_CLASSIFY)
    builder.add_conditional_edges(
        LangGraph.NODE_CLASSIFY,
        route_by_intent,
        {
            "simple": LangGraph.NODE_RETRIEVE,  # 直接检索，不需要改写
            "medium": LangGraph.NODE_REWRITE,  # Enhanced RAG
            "complex": LangGraph.NODE_REWRITE,  # Agentic RAG
        },
    )

    # medium + complex → rewrite → retrieve
    builder.add_edge(LangGraph.NODE_REWRITE, LangGraph.NODE_RETRIEVE)

    # retrieve → grader
    builder.add_edge(LangGraph.NODE_RETRIEVE, LangGraph.NODE_GRADER)

    # grader 条件边：通过 → rerank，不通过 → rewrite 重试
    builder.add_conditional_edges(
        LangGraph.NODE_GRADER,
        route_by_grader,
        {
            "pass": LangGraph.NODE_RERANK,
            "rewrite": LangGraph.NODE_REWRITE,
        },
    )

    builder.add_edge(LangGraph.NODE_RERANK, LangGraph.NODE_GENERATE)
    builder.add_edge(LangGraph.NODE_GENERATE, LangGraph.NODE_FORMAT)
    builder.add_edge(LangGraph.NODE_FORMAT, END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: 7 nodes, 3-tier routing")
    return graph
