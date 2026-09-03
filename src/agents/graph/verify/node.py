"""verify 节点主函数 — 按会话 kb 绑定状态分派两态校验流程。

挂在 agent_finalize → format 之间：态 A（未绑定 KB）经 web_citation_guard 做联网引用
标注引导；态 B（绑定 KB）先做年份完整性比对，缺失年份走完整性决策化
（regen_decision.decide_missing_web：询问/记住联网意愿 → 据 agent 上一轮 search_web
queries 决策 regen/标注直通），完整性通过后经 KB 溯源护栏（kb_citation_guardrail）
再跑最终答案忠实度 judge。
"""

from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.verify import faithfulness
from src.agents.graph.verify.checks import completeness_check
from src.agents.graph.verify.guardrails import kb_citation_guardrail, web_citation_guard
from src.agents.graph.verify.regen_decision import decide_missing_web
from src.config import settings
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

    # ── 态 B：绑定 KB（年份完整性 → 缺失走决策化；通过后 KB 护栏 + 忠实度 judge）──
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
        # 年份缺失：委托决策化（询问联网意愿 → 网络穷尽/保险丝标注直通 → 注入指引/hint
        # 重生成），返回其决策 dict
        return await decide_missing_web(state, ctx, required, missing, answer)
    # KB 溯源护栏（judge 前）：检索到 KB context 但答案无 [n] 且非拒答 → 引导补标 regen
    guardrail = await kb_citation_guardrail(state, ctx)
    if guardrail is not None:
        return guardrail
    # 最终答案跑忠实度 judge（仅标记，不驱动流程；P1 输出护栏消费）
    contexts = ctx.tool_contexts if ctx is not None else []
    unsupported = await faithfulness.faithfulness_check(answer, contexts)
    if unsupported:
        from src.core.logging import retrieval_signal

        if state.kb_id:
            # unsupported 行为信号：绑 KB judge 标记无支撑句 → 检索质量缺陷
            # （检索上下文不足以支撑答案内容，供 P1 检索质量诊断）
            retrieval_signal(
                "unsupported",
                state.query,
                state._agent_iterations,
                kb_id=state.kb_id,
                unsupported_count=len(unsupported),
            )
        return {
            "answer": answer,
            "_unsupported": unsupported,
            "_needs_regenerate": False,
        }
    return {"answer": answer, "_needs_regenerate": False}
