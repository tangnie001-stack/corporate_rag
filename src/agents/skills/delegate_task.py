"""delegate_task 工具 — 主 agent 按需委派 skill（inline/fork 双执行路径）。

工具职责：
1. 调用前 reload registry（懒重载，design D16）
2. 按 skill 命中分发：unknown → 返回"skill 不存在"+ 可用列表；inline → 返回
   方法论（主 agent 自己答）；fork → SkillExecutor 跑零工具子代理
3. fork 执行期间经 ctx.clarify_channel 推 delegate start/end 事件（带 delegate_id
   与 ok/reason 终态；增量由 executor 投 delegate delta），inline 命中不推
   （design D14）；不走外层 astream_events 映射
"""

import asyncio
import time
import uuid

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext
from src.agents.skills.registry import SkillRegistry
from src.config.const import DelegateStopReason, SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx


class DelegateTaskArgs(BaseModel):
    """delegate_task 工具参数（LLM 可见的入参契约）。"""

    task: str = Field(
        description="要委派的任务描述（fork 深度任务需带主 agent 预检索的材料）"
    )
    skill: str = Field(description="要调用的 skill 名（可用列表见工具描述）")


def make_delegate_task(skill_registry: SkillRegistry, executor: SkillExecutor):
    """构建 delegate_task 工具。

    Args:
        skill_registry: SkillRegistry（懒重载；调用前 reload；to_tool_description
            生成工具 description 列可用 skill）
        executor: SkillExecutor（inline/fork 双执行）

    Returns:
        langchain @tool delegate_task
    """

    @tool(
        "delegate_task",
        args_schema=DelegateTaskArgs,
        description=(
            "调用领域专家 skill 处理任务后返回结果。判断当前任务需要领域专家能力"
            "（深度分析/专用方法论）时调用；轻量领域问题优先自己答，不要为每个问题委派。"
            f"可用 skill：\n{skill_registry.to_tool_description()}"
        ),
    )
    async def delegate_task(task: str, skill: str) -> str:
        """调用领域专家 skill 处理任务后返回结果。

        何时调用：判断当前任务需要领域专家能力（深度分析/专用方法论）时调用；
        轻量领域问题优先自己答，不要为每个问题委派。
        可用 skill 见工具描述（本 docstring 不直接给 LLM 展示，description 参数覆盖）。
        skill 的 context 决定执行方式：inline 返回方法论由你自己执行；fork 生成
        独立子代理深度分析后返回文本，由你整合进最终回答（引用仍指向你的检索来源）。

        Args:
            task: 任务描述（fork 深度分析需把预检索材料一并放入）
            skill: 要调用的 skill 名

        Returns:
            inline：方法论文本；fork：子代理分析文本（纯文本，无 [n]）；未知
            skill 返回错误提示 + 可用列表
        """
        skill_registry.reload_if_changed()
        record = skill_registry.get(skill)
        if record is None:
            available = ", ".join(skill_registry.names()) or "无"
            return SSEInteractionTexts.DELEGATE_UNKNOWN_SKILL.format(
                skill=skill, available=available
            )
        if record.context == SkillContext.INLINE:
            return await executor.execute(record, task)

        ctx = current_request_ctx.get()
        if ctx is None:
            return await executor.execute(record, task)
        # fork 可观测（design D6/D8）：分配 delegate_id 贯穿 start/增量/end；
        # ctx.fork_stop_reason 由 executor 中断时写，finally 读取并复位
        delegate_id = uuid.uuid4().hex[:8]
        ctx.delegate_id = delegate_id
        ctx.fork_stop_reason = None
        await ctx.clarify_channel.put(
            {
                "type": "delegate",
                "action": "start",
                "delegate_id": delegate_id,
                "skill": record.name,
                "kind": "",
                "delta": "",
                "ok": True,
                "reason": "",
            }
        )
        core_logging.log_event(
            Event.DELEGATE_START,
            delegate_id=delegate_id,
            skill=record.name,
            thinking="true" if ctx.deep_thinking else "false",
            task_len=len(task),
        )
        started_at = time.monotonic()
        result_len = 0  # 正常路径更新为 len(out)；异常/取消早退保持 0（finally 记录用，勿用 dir() hack）
        stop_reason: str | None = None
        ok = True
        try:
            try:
                out = await executor.execute(record, task)
                result_len = len(out)
            except asyncio.CancelledError:
                stop_reason = DelegateStopReason.CANCELLED
                ok = False
                raise
            except Exception:  # 异常同样收敛为 failed 终态后上抛（ToolNode 转错误回喂）
                stop_reason = DelegateStopReason.FAILED
                ok = False
                raise
            # 正常返回但 executor 曾中断（idle/total/turn）→ reason 已写入 ctx
            stop_reason = ctx.fork_stop_reason
            ok = stop_reason is None
            return out
        finally:
            reason = (
                DelegateStopReason.NORMAL
                if ok
                else (stop_reason or DelegateStopReason.FAILED)
            )
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "end",
                    "delegate_id": delegate_id,
                    "skill": record.name,
                    "kind": "",
                    "delta": "",
                    "ok": ok,
                    # SSE 事件 reason 契约：正常完成 ok=True → reason 为空串；
                    # 中断/失败 ok=False → reason 取 DelegateStopReason 值（sse.py SSEDelegateEvent）
                    "reason": "" if ok else reason.value,
                }
            )
            core_logging.log_event(
                Event.DELEGATE_END,
                delegate_id=delegate_id,
                ok="true" if ok else "false",
                reason=reason.value,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                result_len=result_len,
            )
            ctx.delegate_id = ""
            ctx.fork_stop_reason = None

    return delegate_task
