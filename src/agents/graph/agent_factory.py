"""唯一的 agent 装配入口：主循环与 fork 子代理共用。

本模块是 src/agents/ 下唯一允许调用 create_agent 的地方（由
tests/agents/graph/test_agent_factory.py 的静态扫描断言守住）。
"""

from dataclasses import dataclass, field
from typing import Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import BaseMessage
from langgraph.graph.state import CompiledStateGraph


# langchain 的 AgentState 是 TypedDict，本类按 Ruling N 必须再挂 @dataclass 才能让
# 子类新增字段进入 __dataclass_fields__（否则状态守卫形同虚设）。pyright 无法建模
# 「TypedDict 子类 + @dataclass 字段赋值」这一混合形态，会在每个带默认值的字段上报
# reportGeneralTypeIssues；运行时形态已实测可用（create_agent 接受该 schema，
# messages 的 add_messages 追加语义保持），故逐字段就地抑制该误报——不用文件级开关，
# 以免掩盖本文件其他真实类型问题。
@dataclass
class LoopState(AgentState):
    """装配产物的图状态。

    ⚠️ 这里的默认值**不会**被自动填充：`create_agent` 的子图状态是映射，
    调用方未 seed 的键在状态里根本不存在。凡消费者依赖的键都必须由
    `make_agent_loop_node` 显式 seed（见 agent_node.make_agent_loop_node）。
    """

    _system_messages: list[BaseMessage] = field(  # type: ignore[reportGeneralTypeIssues]
        default_factory=list
    )  # system 段（来源：agent 节点首轮组装后写入；范围：整轮执行；用途：模型参数与 system 的施加通道；不带 reducer，写入即替换）
    _turn_count: int = 0  # type: ignore[reportGeneralTypeIssues]  # 已完成的模型调用数（来源：AgentTurnBudget 于 after_model 自增；范围：单轮执行；用途：回合上限判定）
    _delegate_used: bool = False  # type: ignore[reportGeneralTypeIssues]  # 本轮或此前是否声明过 delegate_task（来源：AgentTurnBudget 于 after_model 置位；范围：单轮执行；用途：放宽回合上限）
    kb_id: str = ""  # type: ignore[reportGeneralTypeIssues]  # 会话知识库 ID（来源：外层节点 seed；范围：单轮执行；用途：工具取数 + 档位判据回退）
    query: str = ""  # type: ignore[reportGeneralTypeIssues]  # 本轮用户问题（来源：外层节点 seed；范围：单轮执行；用途：ask_user 日志）
    deep_thinking: bool = False  # type: ignore[reportGeneralTypeIssues]  # 请求级深思考开关（来源：外层节点 seed；范围：单轮执行；用途：模型参数）


def build_agent(
    model: Any,
    tools: list[Any],
    *,
    system: str | None = None,
    max_turns: int | None = None,
    middleware_extra: list[AgentMiddleware] | None = None,
) -> CompiledStateGraph:
    """装配 agent 循环（主/子角色共用）。

    Args:
        model: 已解析的 LLM 或模型名
        tools: 工具面（主角色=全量；子角色=只读面筛选结果）
        system: system 提供方式——None=经运行态携带（主角色，由 prompt middleware
            施加）；str=静态串（子角色，人设 + 执行契约）
        max_turns: 主循环回合上限；None=不装配回合预算 middleware（子角色路径）
        middleware_extra: 额外 middleware 集合；子角色传空列表

    Returns:
        create_agent 的编译产物（可直接 ainvoke / 作为子图节点）

    Notes:
        本函数**不产出** `graph compiled` 日志——该事件由图装配层
        （build_graph）发一次；子角色每次委派都会调用本函数，若在此发日志
        每次委派都会多一条。
    """
    middleware: list[AgentMiddleware] = list(middleware_extra or [])
    return create_agent(
        model,
        tools=tools,
        system_prompt=system,
        middleware=middleware,
        state_schema=LoopState,
    )
