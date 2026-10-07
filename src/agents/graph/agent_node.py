"""Agent 外层节点 — 首轮消息组装（产 system / 非 system 两半）+ 装配产物调用 + 收尾。

循环本体已内化进 create_agent 装配产物（agent_factory.build_agent）；本文件的
agent 节点只负责：首轮组装消息并拆两半 → seed 子图 → ainvoke → 按外层条数回写。
末轮收尾由 agent_finalize 提取 answer + 读入本轮材料，再交给 format。
"""

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.agents.graph.message_payload import _extract_text
from src.agents.graph.state import AgentState
from src.agents.skills.prefix import clean_prefix
from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_TOKEN_BUDGET,
    SKILL_INJECTION_PREFIX,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.request_context import current_request_ctx
from src.infra.llm.token_count import count_tokens
from src.rag.prompt import build_prompt


def _truncate_history(
    history: list[ChatMessage],
    max_turns: int = HISTORY_MAX_TURNS,
    token_budget: int = HISTORY_TOKEN_BUDGET,
) -> list[ChatMessage]:
    """历史窗口截断：保留最近 N 轮 + 绝对 token 预算，最近 1 轮完整保留。

    Args:
        history: 完整对话历史（user/assistant 交替排列）
        max_turns: 保留的最近轮数（每轮 user+assistant 两条消息）
        token_budget: 历史消息总 token 上限（绝对值，集中 `const.py`）

    Returns:
        截断后的历史列表：先按轮数保留最近 max_turns 轮，总 token 超出预算时
        从最旧逐条弹出直到达标，最近 1 轮（最后 2 条）始终不截。
        计数走 `count_tokens`（分词器近似）。返回新列表，不修改入参。
    """
    if len(history) > max_turns * 2:
        recent = history[-(max_turns * 2) :]
    else:
        recent = list(history)
    total = sum(count_tokens(m.content) for m in recent)
    while total > token_budget and len(recent) > 2:
        dropped = recent.pop(0)
        total -= count_tokens(dropped.content)
    if total > token_budget:
        core_logging.log_event(
            Event.HISTORY_BUDGET_EXCEEDED,
            budget=token_budget,
            used=total,
            kept=len(recent),
        )
    return recent


def _split_history(
    history: list[ChatMessage], known: set[str]
) -> tuple[list[BaseMessage], list[ChatMessage]]:
    """把截断后的历史拆成「注入消息段」与「清洗后的普通历史段」。

    注入消息段 = 内容带 SKILL_INJECTION_PREFIX 标记的 user 行，抽为独立
    HumanMessage（既不进人设层/环境约束层，也避免"对话中途插 system 消息"的模型
    兼容风险）；普通历史段中 user/assistant 剥掉已注册技能前缀（读时清洗，落库
    保留原文），其他角色原样保留（build_prompt 会跳过，无需清洗）。

    Args:
        history: 截断后的对话历史
        known: 已注册技能的可见名集合（clean_prefix 判据）

    Returns:
        (注入消息列表, 清洗后的普通历史列表)
    """
    injected: list[BaseMessage] = []
    normal: list[ChatMessage] = []
    for msg in history:
        if msg.role == "user" and msg.content.startswith(SKILL_INJECTION_PREFIX):
            injected.append(HumanMessage(content=msg.content))
        else:
            normal.append(msg)
    cleaned_normal: list[ChatMessage] = []
    for msg in normal:
        if msg.role in ("user", "assistant"):
            cleaned_normal.append(
                ChatMessage(role=msg.role, content=clean_prefix(msg.content, known))
            )
        else:
            cleaned_normal.append(msg)
    return injected, cleaned_normal


def _split_initial_messages(
    state: AgentState, prompt_manager, tool_names: frozenset[str]
) -> tuple[list[SystemMessage], list[BaseMessage]]:
    """组装首轮 LLM 消息并拆成 system 半段 / 非 system 半段。

    注入型隐藏消息（内容带 SKILL_INJECTION_PREFIX 标记的 user 行）从历史中
    抽出为独立 HumanMessage，放在主 system 段之后、普通对话历史之前——既不进
    人设层/环境约束层，也避免"对话中途插 system 消息"的模型兼容风险。当前 query
    经 clean_prefix 剥掉已注册技能名前缀后再组装（读时清洗，落库保留原文）。

    「prompt messages」计数必须在**拆分之前**算出：拆分后 system 段被移出消息
    列表，再数会恒为 0。

    Args:
        state: 图状态（读 query / kb_id / _history）
        prompt_manager: PromptManager，提供用户消息模板
        tool_names: 本轮实际注册的工具名（段组装的条件注入判据）

    Returns:
        (system 半段, 非 system 半段)：system 半段全是 SystemMessage，非 system
        半段 = 注入消息 + 历史 + 当前 user
    """
    # 历史窗口截断（最近 N 轮 + 绝对 token 预算）后再组装初始消息；
    # kb_bound 由 kb_id 是否非空决定（未绑定 KB → 追加禁止检索指令）
    history = _truncate_history(state._history or [])
    ctx = current_request_ctx.get()
    if ctx is not None:
        persona = ctx.persona
        has_skills = ctx.has_skills
        kb_domain = ctx.kb_domain
        known = ctx.known_skill_names
    else:
        persona = ""
        has_skills = False
        kb_domain = "general"
        known = set()
    injected, cleaned_normal = _split_history(history, known)
    messages = build_prompt(
        clean_prefix(state.query, known),
        "",
        cleaned_normal,
        prompt_manager,
        kb_bound=bool(state.kb_id),
        persona=persona,
        has_skills=has_skills,
        tool_names=tool_names,
        kb_domain=kb_domain,
    )
    # 注入消息放在主 system 段之后、普通对话历史之前（不进人设层/环境约束层）
    if injected:
        insert_at = 0
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage):
                insert_at = i + 1
        messages[insert_at:insert_at] = injected
    # 首轮消息构成（design D11 #4）：system 段由 build_system_prompt 产出，注入段
    # 来自 SKILL_INJECTION_PREFIX 抽取，history 段为清洗后的普通历史（不含当前 query）。
    # 计数必须在拆分前算：拆分后 system 段被移出列表，再数会恒为 0
    core_logging.log_event(
        Event.PROMPT_MESSAGES,
        system_msgs=sum(1 for m in messages if isinstance(m, SystemMessage)),
        injected_msgs=len(injected),
        history_msgs=len(cleaned_normal),
    )
    system_half = [m for m in messages if isinstance(m, SystemMessage)]
    rest_half = [m for m in messages if not isinstance(m, SystemMessage)]
    return system_half, rest_half


@dataclass
class AgentLoopBundle:
    """外层 agent 节点需要的装配产物与上下文。"""

    agent: object  # build_agent 的产物（可直接 ainvoke 的编译图）
    prompt_manager: object  # PromptManager，首轮组装用户消息模板
    tool_names: frozenset[str]  # 本轮实际注册的工具名（段组装条件注入判据）


def make_agent_loop_node(bundle) -> Callable:
    """创建外层 agent 节点：组装首轮 → seed 子图 → invoke → 按外层条数回写。

    回写基准是**外层已有条数**（state.messages 进节点时的长度），不是喂给子图的
    输入长度：首轮外层为空 ⇒ 回写整份（组装段 + 新增段）；重生成轮外层已有整份
    组装结果 ⇒ 只回写新增段，模型请求仍含原始 query 与历史（否则 regen 轮模型
    看不到原始问题）。

    每次 invoke 都是全新 run：计数与委派标志一律 seed 字面初值，不跨 invoke 持久。

    Args:
        bundle: 装配产物束（agent=build_agent 产物 / prompt_manager / tool_names）

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新 messages/_system_messages/answer
    """

    inner = bundle.agent

    async def agent_loop(state: AgentState) -> dict:
        if state.messages:
            seed: list[BaseMessage] = list(state.messages)
            system_half = list(state._system_messages or [])
        else:
            system_half, seed = _split_initial_messages(
                state, bundle.prompt_manager, bundle.tool_names
            )
        subgraph_input = {
            "messages": seed,
            "_system_messages": system_half,
            "query": state.query,
            "kb_id": state.kb_id,
            "deep_thinking": state.deep_thinking,
            "_turn_count": 0,
            "_delegate_used": False,
        }
        out = await inner.ainvoke(subgraph_input)
        produced = list(out["messages"])
        # 基准是外层已有条数，不是喂给子图的输入长度：首轮外层为空 ⇒ 回写整份
        delta = produced[len(state.messages) :]
        last = produced[-1]
        return {
            "messages": delta,
            "_system_messages": system_half,
            "answer": _extract_text(last),
        }

    return agent_loop


def make_agent_finalize_node() -> Callable:
    """创建收尾节点：提取末次 AIMessage 为 answer + 读入本轮材料（tool_contexts / 年份）。

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新
        answer/tool_contexts/verify_temporal_years
    """

    async def agent_finalize(state: AgentState) -> dict:
        if state.messages:
            last = state.messages[-1]
            answer = _extract_text(last)
        else:
            answer = ""
        ctx = current_request_ctx.get()
        if ctx is not None:
            contexts = ctx.tool_contexts
            years = ctx.temporal_years
        else:
            contexts = []
            years = []
        return {
            "answer": answer,
            "tool_contexts": contexts,
            "verify_temporal_years": years,
        }

    return agent_finalize
