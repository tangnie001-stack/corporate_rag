"""验证循环节点 — 完整性/忠实度校验 + 缺失联网询问。

挂在 agent_finalize → format 之间；仅在会话绑定 KB 时生效（纯对话跳过）。
防死循环两个关键（grilling 决策）：① 确认联网后向 messages 注入 SystemMessage
驱动 agent 调 search_web；② verify 自查 _agent_iterations 超限标注缺失直通
（route_agent 的上限检查管不到 verify→agent 边）。
"""

import asyncio
import re

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.tools.ask_tools import wait_with_abort_and_timeout
from src.config import settings
from src.config.const import (
    ASK_USER_TIMEOUT,
    MAX_VERIFY_ASK_PER_TURN,
    VERIFY_GUIDANCE_MARKER,
)
from src.infra.llm.request_context import current_request_ctx, pending_asks

_YEAR_PATTERN = re.compile(r"20\d{2}")


def extract_years(answer: str) -> set[int]:
    """正则提取答案文本中的 4 位年份。

    Args:
        answer: 答案文本（AgentState.answer）

    Returns:
        年份集合（可能为空）
    """
    return {int(m) for m in _YEAR_PATTERN.findall(answer)}


def completeness_check(required: list[int], answer: str) -> list[int]:
    """比对要求覆盖年份与答案实际覆盖年份，返回缺失。

    Args:
        required: 问题要求覆盖年份（RequestContext.temporal_years）
        answer: 答案文本

    Returns:
        缺失年份列表（升序）
    """
    covered = extract_years(answer)
    missing = [y for y in required if y not in covered]
    return sorted(missing)


async def faithfulness_check(answer: str, contexts: list) -> list[str]:
    """用 RAGAS_LLM_MODEL judge 核对答案事实点是否被引用上下文支撑。

    Args:
        answer: 答案文本
        contexts: 引用上下文（RequestContext.tool_contexts 的 content 列表）

    Returns:
        无支撑句子清单（judge 只标记，不删内容）
    """
    if not contexts:
        return []
    from src.models import get_llm

    llm = get_llm(
        model=settings.RAGAS_LLM_MODEL, temperature=0
    )  # 评估专用模型（RAGAS_LLM_MODEL，非 get_classify_llm）
    evidence_parts = []
    for c in contexts:
        if hasattr(c, "content"):
            evidence_parts.append(c.content)
        else:
            evidence_parts.append(str(c))
    evidence = "\n".join(evidence_parts)[:8000]
    prompt = (
        "检查回答中的每个事实点是否被引用证据支撑。\n"
        f"引用证据:\n{evidence}\n回答:\n{answer}\n"
        '输出无支撑句子清单（JSON {"unsupported": ["句子1", ...]}，全部有支撑则空数组）'
    )
    from langchain_core.messages import HumanMessage

    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)], temperature=0)
        if resp is not None:
            content = resp.content
        else:
            content = None
        if isinstance(content, str):
            raw = content.strip()
        else:
            raw = ""
        import json

        data = json.loads(raw)
        unsupported = data.get("unsupported", [])
        if not isinstance(unsupported, list):
            return []
        return [s for s in unsupported if s.strip()]
    except Exception as exc:  # noqa: BLE001  # judge 失败不阻断流程，降级返回无标记，留日志
        logger.warning(
            "judge=faithfulness_check 调用或解析失败，降级返回空标记 answer_len={} err={}",
            len(answer),
            exc,
        )
        return []


async def _ask_web_confirm(state: AgentState, missing_years: list[int]) -> bool:
    """经 clarify_channel 询问用户是否联网，返回确认结果（async，独立计数）。

    Args:
        state: 当前图状态（读 session_id）
        missing_years: 缺失年份列表

    Returns:
        True 用户确认联网；False 拒绝/超时/槽被占（按"未确认"处理）
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return False
    # 独立计数：不计入 MAX_ASK_PER_TURN（LLM 澄清额度），每轮最多询问 1 次
    if ctx.verify_ask_count >= MAX_VERIFY_ASK_PER_TURN:
        return False
    # 单槽保护：LLM 澄清 ask_user 已挂起时放弃询问，避免覆盖其 Future
    if state.session_id in pending_asks:
        return False
    ctx.verify_ask_count += 1
    logger.info(
        "verify web_confirm ask triggered session_id={} missing={}",
        state.session_id,
        missing_years,
    )
    payload = {
        "type": "ask_user",
        "questions": [
            {
                "id": "web_confirm",
                "question": (
                    f"知识库仅覆盖部分年份，缺失 {missing_years}，是否需要联网搜索补充？"
                ),
                "dimension": "free",
                "options": ["需要", "不需要"],
                "multi_select": False,
            }
        ],
    }
    loop = asyncio.get_running_loop()  # async 节点内禁止 run_until_complete
    fut = loop.create_future()
    pending_asks[state.session_id] = fut
    try:
        await ctx.clarify_channel.put(payload)
        answers = await wait_with_abort_and_timeout(
            fut, ctx.abort_signal, ASK_USER_TIMEOUT
        )
    finally:
        pending_asks.pop(state.session_id, None)
        fut.cancel()
    if not isinstance(answers, list) or not answers:
        return False
    first = answers[0]
    if not isinstance(first, dict):
        return False
    # 前端答案 selected 为数组（clarify.py 按数组消费）；对数组归一化判断，避免 list/str 恒不相等
    selected = first.get("selected") or []
    if isinstance(selected, list):
        return any(str(s) in ("需要", "需要联网") for s in selected)
    return False


async def verify_node(state: AgentState) -> dict:
    """验证循环节点：未绑定 KB 直通；完整性缺失询问/注入重生成；最终答案跑 judge。

    Args:
        state: 当前图状态

    Returns:
        {"answer": 答案, "messages": [SystemMessage], "_needs_regenerate": bool,
         "_unsupported": list}；_needs_regenerate=True 时条件边回 agent 重生成
    """
    if not settings.VERIFY_ENABLED:
        return {"answer": state.answer or "", "_needs_regenerate": False}
    ctx = current_request_ctx.get()
    if not state._resolved_kb_ids:
        logger.info("verify skipped (no kb bound) session_id={}", state.session_id)
        # 纯对话：跳过 verify（claude-code 式轻量自检由 prompt 准则覆盖）
        return {"answer": state.answer or "", "_needs_regenerate": False}

    answer = state.answer or ""
    required = ctx.temporal_years if ctx is not None else []
    missing = completeness_check(required, answer) if required else []
    logger.info(
        "verify_node session_id={} kb_ids={} required={} missing={} answer_len={}",
        state.session_id,
        state._resolved_kb_ids,
        required,
        missing,
        len(answer),
    )
    if missing:
        confirmed = False
        if ctx is not None and not ctx.web_confirmed:
            confirmed = await _ask_web_confirm(state, missing)
            logger.info(
                "verify web_confirm result session_id={} missing={} confirmed={}",
                state.session_id,
                missing,
                confirmed,
            )
            if confirmed:
                ctx.web_confirmed = True
            else:
                # 用户拒绝/超时/槽被占：标注缺失后直通（不重生成）
                covered = [y for y in required if y not in missing]
                answer = f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
                return {"answer": answer, "_needs_regenerate": False}
        if ctx is not None and (confirmed or ctx.web_confirmed):
            # 终止条件：迭代超限不再重生成，标注缺失直通（route_agent 上限检查管不到此边）
            if state._agent_iterations >= state._max_agent_iterations:
                covered = [y for y in required if y not in missing]
                answer = f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
                return {"answer": answer, "_needs_regenerate": False}
            # 防重复注入：同一条联网指引已存在于 messages 时只置重生成信号，不再追加
            # （LangGraph 节点读 state.messages 是上一轮值，指引后的 agent/tools 产出
            #   会追加到末尾，故遍历查找而非只看末条；否则循环每轮堆积相同 SystemMessage）
            already_guided = any(
                isinstance(m, SystemMessage)
                and VERIFY_GUIDANCE_MARKER in (m.content or "")
                for m in state.messages
            )
            if already_guided:
                return {"answer": answer, "_needs_regenerate": True}
            # 注入 SystemMessage 驱动 agent 调 search_web（add_messages reducer 自动追加）
            guidance = SystemMessage(
                content=(
                    f"知识库缺失年份 {missing}，{VERIFY_GUIDANCE_MARKER}，"
                    "请调用 search_web 工具补充这些年份的数据后再回答。"
                )
            )
            return {
                "answer": answer,
                "messages": [guidance],
                "_needs_regenerate": True,
            }
    # 最终答案跑忠实度 judge（仅标记，不驱动流程；P1 输出护栏消费）
    contexts = ctx.tool_contexts if ctx is not None else []
    unsupported = await faithfulness_check(answer, contexts)
    if unsupported:
        return {
            "answer": answer,
            "_unsupported": unsupported,
            "_needs_regenerate": False,
        }
    return {"answer": answer, "_needs_regenerate": False}
