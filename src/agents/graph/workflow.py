# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.graph.agent_node import (
    make_agent_finalize_node,
    make_agent_model_node,
    make_agent_tools_node,
    route_agent,
)
from src.agents.graph.nodes import format_node
from src.agents.graph.skill_direct import route_entry, unavailable_skill_direct
from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.graph.verify import verify_node
from src.agents.tools.rag_tools import make_rag_tools
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.db.vector_store import VectorStore


def route_verify(state: AgentState) -> str:
    """verify 条件边：需重生成回 agent（直出轮回 skill_direct），否则进 format。

    Args:
        state: 当前图状态

    Returns:
        下一节点名："agent"（常规轮重生成）/ "skill_direct"（直出轮重生成）或 "format"
    """
    if state._needs_regenerate:
        if state.direct_skill:
            return LangGraphNode.SkillDirect.NAME
        return "agent"
    return LangGraphNode.Format.NAME


def build_graph(
    vector_store: VectorStore,
    llm,
    reranker,
    prompt_manager,
    tools=None,
    delegate_task: BaseTool | None = None,
    tool_sink: list | None = None,
    skill_direct_node=None,
) -> CompiledStateGraph:
    """构建并编译 agent 循环图：START → (agent|skill_direct) → verify → format → END。

    节点职责：
    - agent：bind_tools 调用 LLM，产出消息或工具调用
    - tools：执行 agent 声明的工具调用，结果回喂 messages
    - agent_finalize：末轮无工具调用时提取 answer 并读入 tool_contexts
    - skill_direct：命令行直出（/xxx 命中 fork skill）零 LLM 轮跑子代理并搬材料
    - verify：完整性校验/缺失联网询问；_needs_regenerate=True 时条件边回重生成
    - format：从回答中提取引用编号，组装 citations

    入口经 START 条件边分派：direct_skill 非空 → skill_direct（直出）；空 → agent
    （常规轮，等价于原 set_entry_point("agent")）。

    tools 参数可由调用方覆盖；缺省经 make_rag_tools 构建（retrieve_kb + ask_user 等）
    并追加 make_task_tools（task_create/get/list/update/output/stop，恒注册）。
    delegate_task 为可选委派工具（skill 库有内容时由 AgentService 注入），
    tools=None 默认分支原样透传给 make_rag_tools。
    tool_sink 非 None 时把本次启用的工具追加进去，供 SkillExecutor 的延迟 provider
    读取当前启用工具（打破工具集 ↔ executor 的循环依赖）。
    skill_direct_node 为直出节点函数（AgentService 装配 executor 时注入）；
    None 时用 unavailable_skill_direct 兜底（fail-open），恒注册避免条件边指向不存在的节点。
    """
    builder = StateGraph(AgentState)

    if tools is not None:
        rag_tools = tools
    else:
        base_tools = make_rag_tools(
            vector_store,
            reranker,
            prompt_manager,
            delegate_task=delegate_task,
        )
        from src.agents.tools.task_tools import make_task_tools

        # base_tools 经 list 归一化：make_rag_tools 生产必返回 list，但测试会
        # monkeypatch 成空 list（test_graph.py:726 fake_make_rag_tools return []），
        # 防御性 list() 防止 None 解包 TypeError
        rag_tools = [*(base_tools or []), *make_task_tools()]

    if tool_sink is not None:
        tool_sink.extend(rag_tools)

    builder.add_node("agent", make_agent_model_node(llm, rag_tools, prompt_manager))
    builder.add_node("tools", make_agent_tools_node(rag_tools))
    builder.add_node("agent_finalize", make_agent_finalize_node())
    builder.add_node("verify", verify_node)
    builder.add_node(LangGraphNode.Format.NAME, format_node)
    direct_node = unavailable_skill_direct
    if skill_direct_node is not None:
        direct_node = skill_direct_node
    builder.add_node(LangGraphNode.SkillDirect.NAME, direct_node)

    builder.add_conditional_edges(
        START,
        route_entry,
        {
            "agent": "agent",
            LangGraphNode.SkillDirect.NAME: LangGraphNode.SkillDirect.NAME,
        },
    )
    builder.add_conditional_edges(
        "agent",
        route_agent,
        {"tools": "tools", "agent_finalize": "agent_finalize"},
    )
    builder.add_edge("tools", "agent")
    # agent_finalize → verify → (通过→format / 需重生成→agent 或 skill_direct)
    builder.add_edge("agent_finalize", "verify")
    builder.add_edge(LangGraphNode.SkillDirect.NAME, "verify")
    builder.add_conditional_edges(
        "verify",
        route_verify,
        {
            "agent": "agent",
            LangGraphNode.SkillDirect.NAME: LangGraphNode.SkillDirect.NAME,
            "format": LangGraphNode.Format.NAME,
        },
    )
    builder.add_edge(LangGraphNode.Format.NAME, END)

    graph = builder.compile()
    core_logging.log_event(Event.GRAPH_COMPILED)
    return graph
