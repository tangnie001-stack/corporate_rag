"""命令行直出节点（D22/D24/D26）。

/xxx 命中 fork skill 时，主 agent 零 LLM 轮：直接跑 fork 子代理，把子代理的
answer 与"本轮材料"（引用池 + 要求覆盖年份）搬进 AgentState，再交给 verify/format。
子代理跑在自己的 RequestContext 里，主 ctx 不被写入（D7/D24）。
"""

import uuid

from src.agents.graph.state import AgentState, LangGraphNode
from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.models import SkillContext
from src.config.const import SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx


def route_entry(state: AgentState) -> str:
    """入口分派：命中命令行直出 → skill_direct；其余 → 常规 agent 轮。"""
    if state.direct_skill:
        return LangGraphNode.SkillDirect.NAME
    return "agent"


def make_skill_direct_node(skill_registry, executor):
    """构造直出节点。

    Args:
        skill_registry: SkillRegistry（懒重载后按名解析 SkillRecord）
        executor: SkillExecutor（经 execute(record, task, run) 跑 fork 子代理）

    Returns:
        图节点函数：写 answer / tool_contexts / verify_temporal_years
    """

    async def skill_direct(state: AgentState) -> dict:
        main_ctx = current_request_ctx.get()
        if main_ctx is None:
            return {
                "answer": SSEInteractionTexts.SKILL_DIRECT_CTX_UNAVAILABLE,
                "_needs_regenerate": False,
            }
        skill_registry.reload_if_changed()
        record = skill_registry.get(state.direct_skill)
        if record is None:
            # 未注册 → fail-open 文案：显式告知"不存在 + 可用列表"
            names = [r.name for r in skill_registry.user_visible()]
            if names:
                available = "、".join(names)
            else:
                available = "（无）"
            core_logging.log_event(
                Event.SKILL_DIRECT_SKIP, skill=state.direct_skill, reason="not_found"
            )
            return {
                "answer": SSEInteractionTexts.UNKNOWN_SKILL_PREFIX.format(
                    skill=state.direct_skill, available=available
                ),
                "_needs_regenerate": False,
            }
        if not record.user_invocable:
            # 已注册但 user-invocable:false → spec:66 提示"只能由模型调用"，而非"不存在"
            core_logging.log_event(
                Event.SKILL_DIRECT_SKIP,
                skill=state.direct_skill,
                reason="not_user_invocable",
            )
            return {
                "answer": SSEInteractionTexts.SKILL_USER_DISABLED.format(
                    skill=state.direct_skill
                ),
                "_needs_regenerate": False,
            }
        if record.context != SkillContext.FORK:
            core_logging.log_event(
                Event.SKILL_DIRECT_SKIP, skill=state.direct_skill, reason="not_fork"
            )
            return {
                "answer": SSEInteractionTexts.SKILL_DIRECT_UNAVAILABLE,
                "_needs_regenerate": False,
            }
        run = DelegateRun(
            delegate_id=uuid.uuid4().hex[:8],
            skill_name=record.name,
            ctx=main_ctx.child(),
        )
        text = await executor.execute(record, state.query, run)
        return {
            "answer": text,
            "tool_contexts": run.ctx.tool_contexts,
            "verify_temporal_years": run.ctx.temporal_years,
        }

    return skill_direct


def unavailable_skill_direct(state: AgentState) -> dict:
    """未装配 executor 时的兜底直出节点（fail-open，让 verify/format 正常收尾）。"""
    return {
        "answer": SSEInteractionTexts.SKILL_DIRECT_UNAVAILABLE,
        "_needs_regenerate": False,
    }
