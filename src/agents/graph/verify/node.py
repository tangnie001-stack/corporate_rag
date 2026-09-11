"""verify 节点主函数 — 按会话 kb 绑定状态分派两态校验流程。

挂在 agent_finalize → format 之间：态 A（未绑定 KB）经 web_citation_guard 做联网引用
标注引导；态 B（绑定 KB）先做年份完整性比对，缺失年份走完整性决策化
（regen_decision.decide_missing_web：询问/记住联网意愿 → 据 agent 上一轮 search_web
queries 决策 regen/标注直通），完整性通过后经 KB 溯源护栏（kb_citation_guardrail）
即直通 format（在线忠实度 judge 已移除，质量评估转离线另行规划）。
"""

from src.agents.graph.state import AgentState
from src.agents.graph.verify.checks import completeness_check
from src.agents.graph.verify.guardrails import kb_citation_guardrail, web_citation_guard
from src.agents.graph.verify.regen_decision import decide_missing_web
from src.config import settings
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx


async def verify_node(state: AgentState) -> dict:
    """验证循环节点：未绑定 KB 直通（态 A 联网引用引导）；绑定 KB 跑完整性/引用护栏。

    Args:
        state: 当前图状态

    Returns:
        {"answer": 答案, "messages": [SystemMessage], "_needs_regenerate": bool}；
        _needs_regenerate=True 时条件边回 agent 重生成
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
        core_logging.log_event(Event.SKIP, reason="guard_pass")
        return {"answer": state.answer or "", "_needs_regenerate": False}

    # ── 态 B：绑定 KB（年份完整性 → 缺失走决策化；通过后 KB 溯源护栏）──
    answer = state.answer or ""
    required = state.verify_temporal_years
    missing = completeness_check(required, answer) if required else []
    core_logging.log_event(
        Event.COMPLETENESS_CHECK,
        kb_id=state.kb_id,
        required=required,
        missing=missing,
        answer_len=len(answer),
    )
    if missing:
        # 年份缺失：委托决策化（询问联网意愿 → 网络穷尽/保险丝标注直通 → 注入指引/hint
        # 重生成），返回其决策 dict
        return await decide_missing_web(state, ctx, required, missing, answer)
    # KB 溯源护栏：检索到 KB context 但答案无 [n] 且非拒答 → 引导补标 regen
    guardrail = await kb_citation_guardrail(state)
    if guardrail is not None:
        return guardrail
    # 完整性 + 引用护栏通过 → 直通 format
    return {"answer": answer, "_needs_regenerate": False}
