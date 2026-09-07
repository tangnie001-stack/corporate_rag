"""delegate_task 工具 — 主 agent 按需委派 skill（inline/fork 双执行路径）。

工具职责：
1. 调用前 reload registry（懒重载，design D16）
2. 按 skill 命中分发：unknown → 返回"skill 不存在"+ 可用列表；inline → 返回
   方法论（主 agent 自己答）；fork → SkillExecutor 跑零工具子代理
3. fork 执行期间经 ctx.clarify_channel 推 STAGE_DELEGATE 状态（start/end），
   inline 命中不推（design D14）；不走外层 astream_events 映射
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext
from src.agents.skills.registry import SkillRegistry
from src.config.const import SSEInteractionTexts
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
        if ctx is not None:
            await ctx.clarify_channel.put(
                {
                    "type": "status",
                    "stage": SSEInteractionTexts.STAGE_DELEGATE,
                    "phase": "start",
                }
            )
        try:
            return await executor.execute(record, task)
        finally:
            if ctx is not None:
                await ctx.clarify_channel.put(
                    {
                        "type": "status",
                        "stage": SSEInteractionTexts.STAGE_DELEGATE,
                        "phase": "end",
                    }
                )

    return delegate_task
