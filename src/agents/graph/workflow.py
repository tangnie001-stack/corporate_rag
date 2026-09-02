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
from src.agents.graph.nodes import format_node
from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.graph.verify import verify_node
from src.agents.tools.rag_tools import make_rag_tools
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index


def route_verify(state: AgentState) -> str:
    """verify 条件边：需重生成回 agent，否则进 format。

    Args:
        state: 当前图状态

    Returns:
        下一节点名："agent"（重生成）或 "format"
    """
    if state._needs_regenerate:
        return "agent"
    return LangGraphNode.Format.NAME


def build_graph(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    reranker,
    prompt_manager,
    tools=None,
) -> CompiledStateGraph:
    """构建并编译 agent 循环图：agent → (tools|agent_finalize) → verify → format → END。

    节点职责：
    - agent：bind_tools 调用 LLM，产出消息或工具调用
    - tools：执行 agent 声明的工具调用，结果回喂 messages
    - agent_finalize：末轮无工具调用时提取 answer 并读入 tool_contexts
    - verify：完整性校验/缺失联网询问；_needs_regenerate=True 时条件边回 agent 重生成
    - format：从回答中提取引用编号，组装 citations

    tools 参数可由调用方覆盖；缺省经 make_rag_tools 构建（retrieve_kb + ask_user）。
    """
    builder = StateGraph(AgentState)

    if tools is not None:
        rag_tools = tools
    else:
        rag_tools = make_rag_tools(vector_store, bm25, reranker, prompt_manager)

    builder.add_node("agent", make_agent_model_node(llm, rag_tools, prompt_manager))
    builder.add_node("tools", make_agent_tools_node(rag_tools))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node("verify", verify_node)
    builder.add_node(LangGraphNode.Format.NAME, format_node)

    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent",
        route_agent,
        {"tools": "tools", "agent_finalize": "agent_finalize"},
    )
    builder.add_edge("tools", "agent")
    # agent_finalize → verify → (通过→format / 需重生成→agent)
    builder.add_edge("agent_finalize", "verify")
    builder.add_conditional_edges(
        "verify",
        route_verify,
        {"agent": "agent", "format": LangGraphNode.Format.NAME},
    )
    builder.add_edge(LangGraphNode.Format.NAME, END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: agent 循环 → verify → format")
    return graph
