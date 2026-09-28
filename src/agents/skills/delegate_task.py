"""delegate_task 工具 — 主 agent 按需委派 skill（inline/fork 双执行路径）。

工具职责：
1. 调用前 reload registry（懒重载，design D16）
2. 按 skill 命中分发：unknown → 返回"skill 不存在"+ 可用列表；inline → 返回
   方法论（主 agent 自己答）；fork → SkillExecutor 跑子代理
3. fork 执行期间经 ctx.clarify_channel 推 delegate start/end 事件（带 delegate_id
   与 ok/reason 终态；增量由 executor 投 delegate delta），inline 命中不推
   （design D14）；不走外层 astream_events 映射
"""

import asyncio
import time
import uuid

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.agents.graph.verify.confirm_gate import strip_confirm_marker_prefix
from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext
from src.agents.skills.registry import SkillRegistry
from src.chat.task_registry import task_registry
from src.config.const import (
    DELEGATE_TASK_TITLE_TMPL,
    DELEGATE_VIA_DELEGATE,
    DelegateStopReason,
    SSEInteractionTexts,
    TaskStatus,
    TaskType,
)
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
            # 无请求上下文时没有父上下文可派生，按既有 fail-open 直接用主上下文执行；
            # 交回主 agent 的文本同样剥协议前缀（保留问题文本），不因走主上下文而外泄
            out = await executor.execute(record, task)
            return strip_confirm_marker_prefix(out)
        # 每次委派独占运行态（design D15）：子代理的检索与引用编号落子池，不污染主池；
        # 停止原因由 executor 写 run，终态判定从这里读（不再走主 ctx 的单值字段）
        delegate_id = uuid.uuid4().hex[:8]
        run = DelegateRun(
            delegate_id=delegate_id,
            skill_name=record.name,
            ctx=ctx.child(),
            via=DELEGATE_VIA_DELEGATE,
        )
        # 任务看板自动登记（task-board）：execution 条目 task_id=delegate_id，
        # stage 仅 coarse 边界更新（此处 start、finally 终态）
        task_registry.create_task(
            ctx.session_id,
            TaskType.EXECUTION,
            title=DELEGATE_TASK_TITLE_TMPL.format(skill=record.name),
            delegate_id=delegate_id,
            status=TaskStatus.RUNNING,
            stage="正在分析…",
        )
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
                out = await executor.execute(record, task, run)
                # 内部协议串不外泄；但保留问题文本——主 agent 据此自行决定是否向用户提问
                out = strip_confirm_marker_prefix(out)
                result_len = len(out)
            except asyncio.CancelledError:
                stop_reason = DelegateStopReason.CANCELLED
                ok = False
                raise
            except Exception:  # 异常同样收敛为 failed 终态后上抛（ToolNode 转错误回喂）
                stop_reason = DelegateStopReason.FAILED
                ok = False
                raise
            # 正常返回但 executor 曾中断（idle/total/turn）→ 原因已写在 run 上
            stop_reason = run.stop_reason
            ok = stop_reason is None
            return out
        finally:
            # run.stop_reason 契约上只承载 DelegateStopReason 值（None=未中断）；
            # 显式收敛为词表枚举，作为 reason 的静态类型依据
            if ok:
                reason = DelegateStopReason.NORMAL
            elif isinstance(stop_reason, DelegateStopReason):
                reason = stop_reason
            else:  # 非枚举值按 failed 收敛
                reason = DelegateStopReason.FAILED
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
            # execution 终态映射（展示状态与 DelegateStopReason 词表）
            if ok:
                terminal_status = TaskStatus.DONE
            elif reason is DelegateStopReason.CANCELLED:
                terminal_status = TaskStatus.CANCELLED
            elif reason is DelegateStopReason.FAILED:
                terminal_status = TaskStatus.FAILED
            else:  # idle / total / turn
                terminal_status = TaskStatus.TIMEOUT
            task_registry.mark_terminal(
                ctx.session_id,
                delegate_id,
                terminal_status,
                reason="" if ok else reason.value,
            )

    return delegate_task
