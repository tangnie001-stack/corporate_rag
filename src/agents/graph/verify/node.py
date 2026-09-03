"""verify 节点主函数 — 按会话 kb 绑定状态分派两态校验流程。

挂在 agent_finalize → format 之间：态 A（未绑定 KB）经 web_citation_guard 做联网引用
标注引导；态 B（绑定 KB）做年份完整性缺失的联网询问/注入重生成 + 最终答案忠实度 judge。
"""

from langchain_core.messages import SystemMessage
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.verify import ask_confirm, faithfulness
from src.agents.graph.verify.checks import (
    completeness_check,
    last_search_web_queries,
    queries_cover_missing,
)
from src.agents.graph.verify.guardrails import web_citation_guard
from src.config import settings
from src.config.const import MAX_VERIFY_REGENERATIONS, VERIFY_GUIDANCE_MARKER
from src.infra.llm.request_context import current_request_ctx


async def verify_node(state: AgentState) -> dict:
    """验证循环节点：未绑定 KB 直通（态 A 联网引用引导）；绑定 KB 跑完整性/忠实度。

    Args:
        state: 当前图状态

    Returns:
        {"answer": 答案, "messages": [SystemMessage], "_needs_regenerate": bool,
         "_unsupported": list}；_needs_regenerate=True 时条件边回 agent 重生成
    """
    if not settings.VERIFY_ENABLED:
        return {"answer": state.answer or "", "_needs_regenerate": False}
    ctx = current_request_ctx.get()
    # ── 态 A：未绑定 KB（纯对话）──
    if not state.kb_id:
        decision = await web_citation_guard(state, ctx)
        if decision is not None:
            return decision
        # 纯对话未联网 / 已带引用 / 已达保险丝上限 / 已引导过一次：直通 format
        logger.info("verify skipped (no kb bound) session_id={}", state.session_id)
        return {"answer": state.answer or "", "_needs_regenerate": False}

    # ── 态 B：绑定 KB（年份完整性缺失联网询问 / 注入重生成；最终答案跑 judge）──
    answer = state.answer or ""
    required = ctx.temporal_years if ctx is not None else []
    missing = completeness_check(required, answer) if required else []
    logger.info(
        "verify_node session_id={} kb_id={} required={} missing={} answer_len={}",
        state.session_id,
        state.kb_id,
        required,
        missing,
        len(answer),
    )
    if missing:
        # ── 1. 询问/记住用户联网意愿 ──
        confirmed = False
        if ctx is not None and not ctx.web_confirmed:
            confirmed = await ask_confirm._ask_web_confirm(state, missing)
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
            logger.info(
                "verify regen stop (web exhausted) session_id={} missing={}",
                state.session_id,
                missing,
            )
            covered = [y for y in required if y not in missing]
            answer = f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
            return {"answer": answer, "_needs_regenerate": False}

        # ── 3. 保险丝：修订次数达上限 → 标注直通（防 agent 反复不执行/带漏）──
        if state._verify_regenerations >= MAX_VERIFY_REGENERATIONS:
            logger.info(
                "verify regen stop (fuse) session_id={} missing={} regenerations={}",
                state.session_id,
                missing,
                state._verify_regenerations,
            )
            covered = [y for y in required if y not in missing]
            answer = f"{answer}\n\n> 注：知识库与网络均未覆盖 {missing}，仅 {covered} 有数据。"
            return {"answer": answer, "_needs_regenerate": False}
        state._verify_regenerations += 1

        # ── 4. 注入/重申联网指引 → regen ──
        # 防重复注入：指引已在 messages 时只置重生成信号不追加（遍历查重而非看末条，
        # 因指引后的 agent/tools 产出会追加到末尾）；queries 带漏缺失年份时首注即附
        # "一次带全"提示，保险丝兜底 agent 持续不执行。计数随返回 dict 持久化。
        already_guided = any(
            isinstance(m, SystemMessage) and VERIFY_GUIDANCE_MARKER in (m.content or "")
            for m in state.messages
        )
        if last_queries is not None and not queries_covered:
            hint = (
                "（注意：search_web 支持一次传入多个查询，请一次带全以上所有缺失年份）"
            )
        else:
            hint = ""
        if already_guided:
            return {
                "answer": answer,
                "_needs_regenerate": True,
                "_verify_regenerations": state._verify_regenerations,
            }
        guidance = SystemMessage(
            content=(
                f"知识库缺失年份 {missing}，{VERIFY_GUIDANCE_MARKER}，"
                f"请调用 search_web 工具补充这些年份的数据后再回答。{hint}"
            )
        )
        return {
            "answer": answer,
            "messages": [guidance],
            "_needs_regenerate": True,
            "_verify_regenerations": state._verify_regenerations,
        }
    # 最终答案跑忠实度 judge（仅标记，不驱动流程；P1 输出护栏消费）
    contexts = ctx.tool_contexts if ctx is not None else []
    unsupported = await faithfulness.faithfulness_check(answer, contexts)
    if unsupported:
        return {
            "answer": answer,
            "_unsupported": unsupported,
            "_needs_regenerate": False,
        }
    return {"answer": answer, "_needs_regenerate": False}
