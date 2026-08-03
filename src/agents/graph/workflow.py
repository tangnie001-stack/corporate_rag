# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.nodes import (
    make_classify_node,
    rewrite_node,
    grader_node,
    format_node,
    make_kb_router_node,
    make_retrieve_node,
    make_rerank_node,
    make_generate_node,
)
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.config.const import LangGraphNode


def route_by_intent(state: AgentState) -> str:
    """根据意图路由到不同路径，缺失实体时返回 clarify。"""
    if state.missing_entities:
        logger.info("route_by_intent: missing_entities detected -> clarify")
        return "clarify"
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
    classify_llm,
    reranker,
    embed_fn,
    prompt_manager,
) -> CompiledStateGraph:
    """构建并编译 StateGraph。"""
    builder = StateGraph(AgentState)

    # ── 用工厂函数创建带依赖的节点 ────────────────
    # classify_llm 用于 KBRouter 和 classify 节点（小模型，无思考模式）
    # llm 用于 generate 节点（全功能大模型）
    builder.add_node(
        LangGraphNode.KbRouter.NAME, make_kb_router_node(embed_fn, classify_llm)
    )
    builder.add_node(LangGraphNode.Classify.NAME, make_classify_node(classify_llm))
    builder.add_node(LangGraphNode.Rewrite.NAME, rewrite_node)
    builder.add_node(
        LangGraphNode.Retrieve.NAME, make_retrieve_node(vector_store, bm25)
    )
    builder.add_node(LangGraphNode.Grader.NAME, grader_node)
    builder.add_node(LangGraphNode.Rerank.NAME, make_rerank_node(reranker))
    builder.add_node(
        LangGraphNode.Generate.NAME, make_generate_node(llm, prompt_manager)
    )
    builder.add_node(LangGraphNode.Format.NAME, format_node)

    # ── 设置入口点和边：kb_router → classify → ... ──
    builder.set_entry_point(LangGraphNode.KbRouter.NAME)
    builder.add_edge(LangGraphNode.KbRouter.NAME, LangGraphNode.Classify.NAME)

    # ── 条件边：三级路由（与 classify 的输出直连，不变） ──
    builder.add_conditional_edges(
        LangGraphNode.Classify.NAME,
        route_by_intent,
        {
            "simple": LangGraphNode.Retrieve.NAME,  # 直接检索，不需要改写
            "medium": LangGraphNode.Rewrite.NAME,  # Enhanced RAG
            "complex": LangGraphNode.Rewrite.NAME,  # Agentic RAG
            "clarify": END,  # 缺失实体 → 澄清
        },
    )

    # medium + complex → rewrite → retrieve
    builder.add_edge(LangGraphNode.Rewrite.NAME, LangGraphNode.Retrieve.NAME)

    # retrieve → grader
    builder.add_edge(LangGraphNode.Retrieve.NAME, LangGraphNode.Grader.NAME)

    # grader 条件边：通过 → rerank，不通过 → rewrite 重试
    builder.add_conditional_edges(
        LangGraphNode.Grader.NAME,
        route_by_grader,
        {
            "pass": LangGraphNode.Rerank.NAME,
            "rewrite": LangGraphNode.Rewrite.NAME,
        },
    )

    builder.add_edge(LangGraphNode.Rerank.NAME, LangGraphNode.Generate.NAME)
    builder.add_edge(LangGraphNode.Generate.NAME, LangGraphNode.Format.NAME)
    builder.add_edge(LangGraphNode.Format.NAME, END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: 8 nodes, KB router + 3-tier routing")
    return graph
