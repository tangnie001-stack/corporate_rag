# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.agents.graph.agent_node import (
    make_agent_finalize_node,
    make_agent_model_node,
    make_agent_tools_node,
    route_agent,
)
from src.agents.graph.nodes import format_node, make_kb_router_node
from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.tools.rag_tools import make_rag_tools
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index


def build_graph(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    classify_llm,
    reranker,
    embed_fn,
    prompt_manager,
    tools=None,
) -> CompiledStateGraph:
    """构建并编译 agent 循环图：kb_router → agent → (tools|agent_finalize) → format → END。

    节点职责：
    - kb_router：按 user 解析知识库，填充 _resolved_kb_ids
    - agent：bind_tools 调用 LLM，产出消息或工具调用
    - tools：执行 agent 声明的工具调用，结果回喂 messages
    - agent_finalize：末轮无工具调用时提取 answer 并读入 tool_contexts
    - format：从回答中提取引用编号，组装 citations

    tools 参数可由调用方覆盖；缺省经 make_rag_tools 构建（retrieve_kb + ask_user）。
    """
    builder = StateGraph(AgentState)

    if tools is not None:
        rag_tools = tools
    else:
        rag_tools = make_rag_tools(
            vector_store, bm25, reranker, prompt_manager, embed_fn
        )

    builder.add_node(
        LangGraphNode.KbRouter.NAME, make_kb_router_node(embed_fn, classify_llm)
    )
    builder.add_node("agent", make_agent_model_node(llm, rag_tools, prompt_manager))
    builder.add_node("tools", make_agent_tools_node(rag_tools))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node(LangGraphNode.Format.NAME, format_node)

    builder.set_entry_point(LangGraphNode.KbRouter.NAME)
    builder.add_edge(LangGraphNode.KbRouter.NAME, "agent")
    builder.add_conditional_edges(
        "agent",
        route_agent,
        {"tools": "tools", "agent_finalize": "agent_finalize"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("agent_finalize", LangGraphNode.Format.NAME)
    builder.add_edge(LangGraphNode.Format.NAME, END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: kb_router → agent 循环 → format")
    return graph
