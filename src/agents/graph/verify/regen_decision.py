"""态 B 完整性缺失决策化 — 询问/记住用户联网意愿，据 agent 上一轮 search_web queries 决策。

缺失年份存在时 verify_node 委托本模块决定"注入指引重生成 / 标注直通"。控制流：
先经 ask_confirm 询问用户是否联网（本轮已确认则跳过；拒绝/超时/槽被占 → 标注直通）；
随后看 agent 上一轮 search_web 的 queries 是否带全缺失年份：带全仍缺 → 网络已穷尽标注
直通；带漏 → 完整指引按 VERIFY_GUIDANCE_MARKER 查重注入 + 独立 hint 按 VERIFY_HINT_MARKER
至多补发一次，regen 轮复位主循环预算（_agent_iterations=0）。保险丝
（_verify_regenerations >= MAX_VERIFY_REGENERATIONS）兜底防 verify→agent 无限往返。
"""

from langchain_core.messages import SystemMessage

from src.agents.graph.state import AgentState
from src.agents.graph.verify import ask_confirm
from src.agents.graph.verify.checks import (
    last_search_web_queries,
    queries_cover_missing,
)
from src.config.const import (
    MAX_VERIFY_REGENERATIONS,
    VERIFY_GUIDANCE_MARKER,
    VERIFY_HINT_MARKER,
)
from src.config.prompts import VERIFY_GUIDANCE_PROMPT, VERIFY_HINT_PROMPT
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import RequestContext


def _marker_message_sent(state: AgentState, marker: str) -> bool:
    """查重：messages 中是否已存在含指定标记短语的 SystemMessage。

    遍历查重而非只看末条：指引/hint 注入后，agent 与 tools 的产出会追加到消息末尾，
    只看末条会误判"未注入过"。注入与查重共用同一标记短语常量，文案改动不破坏查重。

    Args:
        state: 当前图状态
        marker: 标记短语（const.py 中 *_MARKER 常量）

    Returns:
        True 已存在含该短语的 SystemMessage
    """
    return any(
        isinstance(m, SystemMessage) and marker in (m.content or "")
        for m in state.messages
    )


async def decide_missing_web(
    state: AgentState,
    ctx: RequestContext | None,
    required: list[int],
    missing: list[int],
    answer: str,
) -> dict:
    """完整性缺失决策化：询问/记住用户联网意愿 → 决策 regen 或标注直通。

    Args:
        state: 当前图状态（读 messages / _verify_regenerations；计数会原地自增并随返回持久化）
        ctx: 请求上下文（联网确认状态 web_confirmed 在此读写）
        required: 问题要求覆盖年份
        missing: 答案缺失年份（非空，升序）
        answer: 当前答案文本（原样透传或追加覆盖标注后作为决策 answer）

    Returns:
        决策 dict：regen（注入完整指引/hint，_needs_regenerate=True）或
        标注直通（拒绝/未确认/网络已穷尽/保险丝耗尽，_needs_regenerate=False）
    """
    # ── 1. 询问/记住用户联网意愿 ──
    confirmed = False
    if ctx is not None and not ctx.web_confirmed:
        confirmed = await ask_confirm._ask_web_confirm(state, missing)
        core_logging.log_event(
            Event.WEB_CONFIRM_RESULT,
            missing=missing,
            confirmed=confirmed,
        )
        if confirmed:
            ctx.web_confirmed = True
        else:
            # 用户拒绝/超时/槽被占：标注缺失后直通（不重生成）
            covered = [y for y in required if y not in missing]
            answer = (
                f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
            )
            return {"answer": answer, "_needs_regenerate": False}
    if ctx is None or not (confirmed or ctx.web_confirmed):
        covered = [y for y in required if y not in missing]
        answer = (
            f"{answer}\n\n> 注：知识库仅覆盖 {covered}，缺失 {missing} 未联网补充。"
        )
        return {"answer": answer, "_needs_regenerate": False}

    # ── 2. 决策化：看 agent 上一轮 search_web 的 queries 是否带全缺失年份 ──
    last_queries = last_search_web_queries(state.messages)
    queries_covered = queries_cover_missing(last_queries, missing)
    if last_queries is not None and queries_covered:
        # 上一轮已带全缺失年份调 search_web，答案仍缺 → 网络已穷尽 → 标注直通
        core_logging.log_event(
            Event.REGEN_STOP, reason="web_exhausted", missing=missing
        )
        covered = [y for y in required if y not in missing]
        answer = (
            f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
        )
        return {"answer": answer, "_needs_regenerate": False}

    # ── 3. 保险丝：修订次数达上限 → 标注直通（防 agent 反复不执行/带漏）──
    if state._verify_regenerations >= MAX_VERIFY_REGENERATIONS:
        core_logging.log_event(
            Event.REGEN_STOP,
            reason="fuse",
            missing=missing,
            regenerations=state._verify_regenerations,
        )
        covered = [y for y in required if y not in missing]
        answer = (
            f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
        )
        return {"answer": answer, "_needs_regenerate": False}
    state._verify_regenerations += 1
    if ctx is not None:
        # 标记 verify 指派联网：此后 agent 调 search_web 属正常完成步骤（补数据），
        # search_web 据此（web_guided=True）排除 to_web 自主降级缺陷信号误报
        ctx.web_guided = True

    # ── 4. 注入/重申联网指引 → regen ──
    # 完整指引只在未注入过时追加（_marker_message_sent 遍历查重）；hint 是独立
    # SystemMessage：agent 上一轮 search_web queries 带漏缺失年份时，即使完整指引
    # 已注入过也须补发（"还缺哪些年 + 一次带全再查一次"是新信息，不能静默重申），
    # 按 VERIFY_HINT_MARKER 短语查重至多发一次。计数随返回 dict 持久化；regen 轮
    # 带 _agent_iterations=0 复位主循环预算、ctx.web_count=0 复位 search_web 配额
    # （见下），route_agent 不因首轮迭代触顶而吞掉本轮 search_web 工具调用
    # （regen 总轮数由保险丝 + web-exhausted 语义封顶）。
    already_guided = _marker_message_sent(state, VERIFY_GUIDANCE_MARKER)
    hint_already_sent = _marker_message_sent(state, VERIFY_HINT_MARKER)
    regen_messages: list[SystemMessage] = []
    if not already_guided:
        regen_messages.append(
            SystemMessage(
                content=VERIFY_GUIDANCE_PROMPT.format(
                    missing=missing, marker=VERIFY_GUIDANCE_MARKER
                )
            )
        )
    if last_queries is not None and not queries_covered and not hint_already_sent:
        regen_messages.append(
            SystemMessage(
                content=VERIFY_HINT_PROMPT.format(
                    missing=missing, marker=VERIFY_HINT_MARKER
                )
            )
        )
    # regen 轮 = 一段全新主循环：除 _agent_iterations=0 复位迭代预算外，同步归零
    # search_web 请求级配额（web_count）。ctx.web_count 跨 verify regen 段累积会让
    # regen 轮的 search_web 达限返回 WEB_SEARCH_LIMIT_TEXT 而不执行，verify 据此误判
    # "知识库与网络均未覆盖"——与迭代预算未复位同属"regen 轮预算被首段耗尽"缺陷。
    if ctx is not None:
        ctx.web_count = 0
    result: dict = {
        "answer": answer,
        "_needs_regenerate": True,
        "_verify_regenerations": state._verify_regenerations,
        "_agent_iterations": 0,
        "_delegate_used": False,  # regen=全新 5 轮预算，不复位则 delegate 放宽 +2 会放大每段 regen 上限
    }
    if regen_messages:
        result["messages"] = regen_messages
    return result
