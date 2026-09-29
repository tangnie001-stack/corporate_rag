# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.graph.agent_factory import build_agent
from src.agents.graph.agent_node import (
    AgentLoopBundle,
    make_agent_finalize_node,
    make_agent_loop_node,
)
from src.agents.graph.middleware import (
    AgentSpanMiddleware,
    ModelParamsMiddleware,
    SystemMessagesMiddleware,
)
from src.agents.graph.nodes import format_node
from src.agents.graph.skill_direct import route_entry, unavailable_skill_direct
from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.graph.verify import verify_node
from src.agents.tools.rag_tools import make_rag_tools
from src.config.const import MAX_AGENT_ITERATIONS
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
    """构建并编译 agent 循环图：START → (agent|skill_direct) → agent_finalize → verify → format → END。

    节点职责：
    - agent：外层节点，组装首轮消息 → seed 装配产物（build_agent 内化 model↔tools
      循环）→ invoke → 按外层条数回写（无独立 tools 节点）
    - agent_finalize：提取末次回答为 answer 并读入 tool_contexts / 年份
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

    # 装配产物：主角色四件套 middleware；AgentSpan 必须在最内层（看得到已施加的
    # system 与 model_settings）。子角色不在此处装配（executor 侧另行调用）。
    loop_agent = build_agent(
        llm,
        rag_tools,
        system=None,  # 主角色：system 经运行态携带，由 SystemMessagesMiddleware 施加
        max_turns=MAX_AGENT_ITERATIONS,
        middleware_extra=[
            SystemMessagesMiddleware(),
            ModelParamsMiddleware(),
            # AgentTurnBudget 由 build_agent 依 max_turns 自行追加（Ruling O/P）；
            # 这里**不要**再传——create_agent 按 middleware.name 拒绝重复实例
            AgentSpanMiddleware(),
        ],
    )
    bundle = AgentLoopBundle(
        agent=loop_agent,
        prompt_manager=prompt_manager,
        tool_names=frozenset(str(t.name) for t in rag_tools),
    )

    builder.add_node("agent", make_agent_loop_node(bundle))
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
    # 注：ToolTraceCollector 的 langgraph_node == "tools" 判据**仍然有效**——
    # 工具事件来自装配产物内部的同名节点，不是外层图。
    # 循环已内化进装配产物：agent 出边**直连** agent_finalize（不再有 route_agent 条件边）
    builder.add_edge("agent", "agent_finalize")
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
