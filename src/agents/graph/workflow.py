# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.agents.graph.nodes import (
    format_node,
    make_classify_node,
    make_generate_node,
    make_kb_router_node,
    make_rerank_node,
    make_retrieve_node,
    rewrite_node,
)
from src.agents.graph.state import AgentState, LangGraphNode
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index


def route_by_intent(state: AgentState) -> str:
    """根据意图路由到不同路径，缺失实体时返回 clarify。

    评估模式（skip_clarify=True）下即使有缺失实体也跳过追问，
    直接按正常路由走检索+生成，避免批量评估因追问而空答。
    """
    if state.missing_entities and not state.skip_clarify:
        logger.info("route_by_intent: missing_entities detected -> clarify")
        return "clarify"
    return state.intent.route or "medium"


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

    # retrieve → rerank 直连（grader 节点已删除）
    builder.add_edge(LangGraphNode.Retrieve.NAME, LangGraphNode.Rerank.NAME)

    builder.add_edge(LangGraphNode.Rerank.NAME, LangGraphNode.Generate.NAME)
    builder.add_edge(LangGraphNode.Generate.NAME, LangGraphNode.Format.NAME)
    builder.add_edge(LangGraphNode.Format.NAME, END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: 7 nodes, KB router + 3-tier routing")
    return graph
