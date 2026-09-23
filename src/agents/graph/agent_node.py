"""Agent 循环节点 — model↔tools 条件循环 + 收尾。

循环：agent_model（bind_tools 调用 LLM）→ route_agent → tools（ToolNode）→ 回 agent_model。
末轮无 tool_calls → agent_finalize（提取 answer + 读入本轮材料）→ format。
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langfuse.decorators import langfuse_context, observe
from langfuse.model import ModelUsage
from langgraph.prebuilt import ToolNode

from src.agents.graph.message_payload import _extract_text, _messages_payload
from src.agents.graph.state import AgentState
from src.agents.skills.prefix import clean_prefix
from src.config import settings
from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_TOKEN_RATIO,
    MAX_DELEGATE_BONUS,
    SKILL_INJECTION_PREFIX,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.request_context import current_request_ctx
from src.infra.llm.token_usage import estimate_usage
from src.rag.prompt import build_prompt


def _truncate_history(
    history: list[ChatMessage],
    max_turns: int = HISTORY_MAX_TURNS,
    token_ratio: float = HISTORY_TOKEN_RATIO,
    context_window: int = 8000,
) -> list[ChatMessage]:
    """历史窗口截断：保留最近 N 轮 + token 双上限，最近 1 轮完整保留。

    Args:
        history: 完整对话历史（user/assistant 交替排列）
        max_turns: 保留的最近轮数（每轮 user+assistant 两条消息）
        token_ratio: 历史消息 token 占 context 窗口的上限比例
        context_window: 模型 context 窗口大小（token）

    Returns:
        截断后的历史列表：先按轮数保留最近 max_turns 轮，总 token 超出预算时
        从最旧逐条弹出直到达标，最近 1 轮（最后 2 条）始终不截。
        token 粗估为 len(content) // 2，与 estimate_usage 一致。
        返回新列表，不修改入参。
    """
    if len(history) > max_turns * 2:
        recent = history[-(max_turns * 2) :]
    else:
        recent = list(history)
    budget = int(context_window * token_ratio)
    total = sum(len(m.content) // 2 for m in recent)
    while total > budget and len(recent) > 2:
        dropped = recent.pop(0)
        total -= len(dropped.content) // 2
    return recent


def _initial_messages(
    state: AgentState, prompt_manager, tool_names: frozenset[str]
) -> list[BaseMessage]:
    """组装首轮 LLM 消息列表：system 段 + 注入隐藏消息 + 普通历史 + 当前 query。

    注入型隐藏消息（内容带 SKILL_INJECTION_PREFIX 标记的 user 行）从历史中
    抽出为独立 HumanMessage，放在主 system 段之后、普通对话历史之前——既不进
    人设层/环境约束层，也避免"对话中途插 system 消息"的模型兼容风险。当前 query
    经 clean_prefix 剥掉已注册技能名前缀后再组装（读时清洗，落库保留原文）。

    Args:
        state: 图状态（读 query / kb_id / _history）
        prompt_manager: PromptManager，提供用户消息模板
        tool_names: 本轮实际注册的工具名（段组装的条件注入判据）

    Returns:
        LLM 消息列表：system（+未绑定时追加会话指令）+ 注入消息 + 历史 + 当前 user
    """
    # 历史窗口截断（最近 N 轮 + token 双上限）后再组装初始消息；
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
    injected: list[BaseMessage] = []
    normal: list[ChatMessage] = []
    for msg in history:
        if msg.role == "user" and msg.content.startswith(SKILL_INJECTION_PREFIX):
            injected.append(HumanMessage(content=msg.content))
        else:
            normal.append(msg)
    # 读时清洗：对 user/assistant 历史剥掉已注册技能前缀（落库保留原文）；
    # 其他角色原样保留（build_prompt 会跳过，无需清洗）
    cleaned_normal: list[ChatMessage] = []
    for msg in normal:
        if msg.role in ("user", "assistant"):
            cleaned_normal.append(
                ChatMessage(role=msg.role, content=clean_prefix(msg.content, known))
            )
        else:
            cleaned_normal.append(msg)
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
    # 来自 SKILL_INJECTION_PREFIX 抽取，history 段为清洗后的普通历史（不含当前 query）
    core_logging.log_event(
        Event.PROMPT_MESSAGES,
        system_msgs=sum(1 for m in messages if isinstance(m, SystemMessage)),
        injected_msgs=len(injected),
        history_msgs=len(cleaned_normal),
    )
    return messages


def make_agent_model_node(llm, tools, prompt_manager) -> Callable:
    """创建 agent 模型节点工厂：bind_tools + 初始消息注入 + 迭代计数。

    工具名集合由入参 `tools` 就地派生（各工具的 .name），作为段组装条件注入判据。

    Args:
        llm: 聊天模型实例（bind_tools 后调用）
        tools: 可调用工具列表（绑定给模型的工具）
        prompt_manager: PromptManager，用于首轮 messages 为空时组装初始消息

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新 messages/_agent_iterations
    """
    # 本轮实际注册的工具名（段组装的条件注入判据）。取各工具的 .name
    # （LangChain BaseTool 契约）；缺 name 是编程错误，装配期即暴露。
    tool_names = frozenset(str(t.name) for t in tools)
    model = llm.bind_tools(tools)

    # capture_output=False：模型只发 tool_calls、文本为空时，显式写入的 output 为空串
    # （falsy），会走 SDK 的自动捕获回落；关掉自动捕获后回落得到 None，避免把节点返回的
    # state dict（messages/_agent_iterations/...）写进 trace。显式非空 output 仍优先。
    @observe(
        name="agent_turn",
        as_type="generation",
        capture_input=False,
        capture_output=False,
    )
    async def agent_model(state: AgentState) -> dict:
        if state.messages:
            messages = state.messages
        else:
            messages = _initial_messages(state, prompt_manager, tool_names)
        iteration = state._agent_iterations + 1
        core_logging.log_event(
            Event.ITERATION_DONE, iteration=iteration, msgs=len(messages)
        )
        # 流式聚合：astream 逐块产出，经 AIMessageChunk 的 += 合并 content 与
        # tool_call_chunks，最终消息带 tool_calls（若模型发起工具调用），
        # 同时驱动 on_chat_model_stream 事件把 token 流式下发前端。
        # 注意：per-call extra_body 在 langchain-openai 1.3.3 中整体覆盖构造时的
        # extra_body（_get_request_payload 浅合并），故本模型不宜在 LLM_KWARGS
        # 里配置其他 extra_body 参数（会被本处覆盖丢弃）。
        turn_start = time.monotonic()
        # 采样温度分档（chat-temperature-policy）：未绑 KB → 非 KB 档（默认 0.6）；
        # 绑 KB → 不传 temperature，沿用模型构造温度 LLM_TEMPERATURE（默认 0.1），
        # 同请求档位恒定（kb_id 首轮即固定）。日志按下列局部变量上报温度，并以
        # temp_source 标注取值来源（design D11 #1）：未绑 KB 时该变量同时传给 astream
        # 与日志（显式传参）；绑 KB 时该变量只进日志，astream 不传参、实际取值为模型
        # 构造默认（默认值即 LLM_TEMPERATURE，故上报该常量）
        chunks = []
        # 临时取证埋点（systematic-debugging 走 A）：记录首个 chunk 到达时刻（TTFB），
        # 用于区分"服务端排队/首字节慢"与"生成本身长"；定位完成后删除。
        first_chunk_ms = -1
        first_chunk_at: datetime | None = None
        if state.kb_id:
            temperature = settings.LLM_TEMPERATURE
            temp_source = "default"
            async for chunk in model.astream(
                messages, extra_body={"enable_thinking": state.deep_thinking}
            ):
                if first_chunk_ms < 0:
                    first_chunk_ms = int((time.monotonic() - turn_start) * 1000)
                    first_chunk_at = datetime.now(UTC)
                chunks.append(chunk)
        else:
            temperature = settings.NON_KB_MAIN_TEMPERATURE
            temp_source = "explicit"
            async for chunk in model.astream(
                messages,
                extra_body={"enable_thinking": state.deep_thinking},
                temperature=temperature,
            ):
                if first_chunk_ms < 0:
                    first_chunk_ms = int((time.monotonic() - turn_start) * 1000)
                    first_chunk_at = datetime.now(UTC)
                chunks.append(chunk)
        result = chunks[0]
        for chunk in chunks[1:]:
            result = result + chunk
        # 主 agent 每轮推理 model turn 摘要：usage 优先取流聚合后的真实计数，
        # 缺失时以文本长度估算兜底并标注 usage_estimated（成本口径区分估算值）。
        meta = result.usage_metadata
        if meta and (meta.get("input_tokens") or meta.get("output_tokens")):
            usage_in = int(meta.get("input_tokens") or 0)
            usage_out = int(meta.get("output_tokens") or 0)
            usage_estimated = False
        else:
            est = estimate_usage(messages, _extract_text(result))
            usage_in = est.prompt_tokens
            usage_out = est.completion_tokens
            usage_estimated = True
        resp_meta = result.response_metadata
        if isinstance(resp_meta, dict):
            model_name = resp_meta.get("model_name", "")
            if not isinstance(model_name, str):
                model_name = resp_meta.get("model", "")
        else:
            model_name = ""
        if not isinstance(model_name, str):
            model_name = ""
        # generation 字段回填（D8：input 必须显式写，不能靠自动捕获）
        langfuse_context.update_current_observation(
            model=model_name,
            input=_messages_payload(messages),
            output=_extract_text(result),
            # ModelUsage 是 TypedDict 且字段声明为 Optional（键仍算必填），
            # pyright 误判部分键构造非法；运行时 TypedDict 调用即普通 dict，SDK 接受
            usage=ModelUsage(  # type: ignore[reportCallIssue]
                input=usage_in,
                output=usage_out,
                total=usage_in + usage_out,
            ),
            completion_start_time=first_chunk_at,
            metadata={
                "iteration": iteration,
                "usage_estimated": usage_estimated,
                "temperature": temperature,
                "temp_source": temp_source,
                "kb_bound": bool(state.kb_id),
            },
        )
        core_logging.log_event(
            Event.MODEL_TURN,
            model=model_name,
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated=usage_estimated,
            fallback=False,
            latency_ms=int((time.monotonic() - turn_start) * 1000),
            iteration=iteration,
            temperature=temperature,
            temp_source=temp_source,
            kb_bound=bool(state.kb_id),
        )
        # 临时取证埋点（systematic-debugging 走 A）：定位完成后删除
        core_logging.logger.info(
            "[agent] TIMING model_turn ttfb_ms={} total_ms={} msgs={} iteration={}",
            first_chunk_ms,
            int((time.monotonic() - turn_start) * 1000),
            len(messages),
            iteration,
        )
        delegate_used = any(
            call.get("name") == "delegate_task"
            for call in (result.tool_calls or [])
            if isinstance(call, dict)
        )
        # delegate 轮放宽上限（design D15）：本轮或此前已 delegate → 上限 +MAX_DELEGATE_BONUS
        if delegate_used or state._delegate_used:
            effective_max = state._max_agent_iterations + MAX_DELEGATE_BONUS
        else:
            effective_max = state._max_agent_iterations
        if iteration >= effective_max:
            core_logging.log_event(
                Event.ITERATION_LIMIT, query=state.query, iteration=iteration
            )
        if state.messages:
            update_messages = [result]
        else:
            update_messages = [*messages, result]
        update = {
            "messages": update_messages,
            "_agent_iterations": iteration,
        }
        if delegate_used:
            # delegate 轮置位：route_agent 据此放宽迭代上限（design D15）
            update["_delegate_used"] = True
        return update

    return agent_model


def make_agent_tools_node(tools) -> Callable:
    """创建工具节点：ToolNode 包装（handle_tool_errors 错误回喂）。

    Args:
        tools: 可调用工具列表

    Returns:
        异步节点函数，接收 AgentState，返回 ToolNode 执行结果（messages 追加 ToolMessage）
    """
    node = ToolNode(tools, handle_tool_errors=True)

    async def agent_tools(state: AgentState) -> dict:
        return await node.ainvoke(state)

    return agent_tools


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


def route_agent(state: AgentState) -> str:
    """agent 条件边：有 tool_calls 且未超限 → tools；否则 → agent_finalize。

    超限判定含 delegate 放宽：_delegate_used 置位时上限 +MAX_DELEGATE_BONUS
    （delegate 后主 agent 需整合子代理结果，design D15）；单请求总上限仍由
    verify 保险丝 + 图级 recursion_limit 兜底。

    Args:
        state: 当前图状态

    Returns:
        下一节点名："tools" 或 "agent_finalize"
    """
    if state._delegate_used:
        effective_max = state._max_agent_iterations + MAX_DELEGATE_BONUS
    else:
        effective_max = state._max_agent_iterations
    if state._agent_iterations >= effective_max:
        return "agent_finalize"
    if not state.messages:
        return "agent_finalize"
    last = state.messages[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "agent_finalize"
