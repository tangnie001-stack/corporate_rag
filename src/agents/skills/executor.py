"""SkillExecutor — inline 指令注入 / fork 零工具子代理执行。

- inline：返回 skill 正文（render 后的方法论），主 agent 自己执行（不产生子代理）。
- fork：create_react_agent(llm, tools=[], prompt=agent_prompt) 生成独立零工具
  子代理，初始消息 = task，返回纯文本（不带 [n]）。零工具 = 防递归硬保证 + 不
  写共享 RequestContext.tool_contexts（design D7/D8/D9）。

可观测性（design D11）：fork 子代理复用主 agent 的 llm 实例（或 get_llm 新建实例）——
与主 agent **同级观测**（同一实例自带 callbacks；Langfuse 是否捕获取决于网关层，应用层
不新增 Langfuse 工作，见 design Risks「create_react_agent 观测缺口」）。本层不推 SSE
（由 delegate_task 工具 fork 分支负责）。

模型/思考（design D12）：skill 声明 model → get_llm(model=...) 新建；model 空 →
  继承主 agent 的 model_name。skill 声明 thinking → 新建实例 extra_body
  enable_thinking（显式开/关）；thinking 未声明 → 复用主 agent 实例（跟随主 agent，
  不覆盖其既有的 per-call thinking 行为）。

超时（design D13）：asyncio.wait_for(DELEGATE_TIMEOUT)，超时返回
DELEGATE_TIMEOUT_TEXT（ToolMessage 文本引导主 agent 基于现有上下文作答）。

截断（design D10）：fork 结果超 DELEGATE_RESULT_LIMIT → 截断为结构化摘要
（含总字数提示），防主 agent 上下文膨胀。
"""

import asyncio

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt import create_react_agent

from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import (
    DELEGATE_RESULT_LIMIT,
    DELEGATE_TIMEOUT,
    SSEInteractionTexts,
)
from src.models import get_llm  # 模块级 import：测试需 patch executor.get_llm


class SkillExecutor:
    """按 skill context 分发执行：inline 注入 / fork 子代理。"""

    def __init__(self, main_llm) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 且 skill 未声明 model 时按
                model_name 继承复用；声明 model/thinking 时经 get_llm 新建）
        """
        self._main_llm = main_llm
        # 主 agent llm 为 ChatOpenAI 族（get_llm 产物）时 model_name 为标准属性；
        # 测试替身/无该属性的模型对象按 None 处理（缺省 None 走 get_llm 默认模型）
        model_name = None
        if hasattr(main_llm, "model_name"):
            model_name = main_llm.model_name
        if not isinstance(model_name, str) or not model_name:
            model_name = None
        self._main_model_name = model_name

    async def execute(self, record: SkillRecord, task: str) -> str:
        """执行一个 skill，返回给主 agent 的文本。

        Args:
            record: 命中的 SkillRecord
            task: 主 agent 委托的任务描述（fork 时作子代理初始消息；inline 时填入
                inline_prompt 的 {task} 占位）

        Returns:
            inline：渲染后的方法论文本；fork：子代理纯文本（截断或超时文案）
        """
        if record.context == SkillContext.INLINE:
            return self._render_inline(record, task)
        return await self._run_fork(record, task)

    def _render_inline(self, record: SkillRecord, task: str) -> str:
        """渲染 inline_prompt：{task} 替换为任务文本（无占位则原样返回）。

        Args:
            record: inline SkillRecord
            task: 任务文本

        Returns:
            渲染后的方法论文本
        """
        prompt = record.inline_prompt or ""
        if "{task}" in prompt:
            return prompt.replace("{task}", task)
        return prompt

    async def _run_fork(self, record: SkillRecord, task: str) -> str:
        """fork 执行：零工具子代理深度分析。

        Args:
            record: fork SkillRecord
            task: 任务描述（子代理初始 HumanMessage）

        Returns:
            子代理纯文本；截断（>DELEGATE_RESULT_LIMIT）带总字数提示；超时返回
            DELEGATE_TIMEOUT_TEXT
        """
        llm = self._resolve_fork_llm(record)
        sub_agent = create_react_agent(
            llm,
            tools=[],  # 零工具硬保证（design D7）：防递归 + 不污染主 ctx
            prompt=record.agent_prompt,
        )
        # 隔离子代理回调传播：不 reset 会经 var_child_runnable_config 把外层
        # callback handler 传进 create_react_agent，子代理 LLM 事件（on_chat_model_*）
        # 泄漏到外层 graph.astream_events（SSE token 污染 + full_answer 累积子代理
        # 原文，见 final review Critical）；reset 后子代理事件只走其自身 handler
        token = var_child_runnable_config.set(None)
        try:
            result = await asyncio.wait_for(
                sub_agent.ainvoke({"messages": [HumanMessage(content=task)]}),
                timeout=DELEGATE_TIMEOUT,
            )
        except TimeoutError:
            return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
        finally:
            var_child_runnable_config.reset(token)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        text = self._last_message_text(messages)
        return self._truncate(text)

    def _resolve_fork_llm(self, record: SkillRecord):
        """解析 fork 子代理的 llm 实例（model/thinking 消费，design D12）。

        Args:
            record: fork SkillRecord

        Returns:
            llm 实例：
            - model 或 thinking 任一声明 → get_llm(model=record.model 或主 agent
              model_name, extra_body.enable_thinking=record.thinking) 新建
              （新建实例才能携带 enable_thinking；复用实例无法改构造期 extra_body）
            - 两者均未声明 → 复用主 agent llm 实例（跟随主 agent）
        """
        if record.model is None and record.thinking is None:
            return self._main_llm

        kwargs: dict = {}
        if record.model is not None:
            kwargs["model"] = record.model
        elif self._main_model_name is not None:
            kwargs["model"] = self._main_model_name
        if record.thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": record.thinking}
        return get_llm(**kwargs)

    def _last_message_text(self, messages: list) -> str:
        """取子代理结果最后一条消息的文本（AIMessage content str）。

        Args:
            messages: ainvoke 返回的消息列表

        Returns:
            content 的纯文本；无法提取时返回空串
        """
        if not messages:
            return ""
        last = messages[-1]
        content = last.content
        if not isinstance(content, str):
            content = str(content)
        return content

    def _truncate(self, text: str) -> str:
        """fork 结果截断（超阈值时带总字数提示）。

        Args:
            text: 子代理完整输出

        Returns:
            未超阈值原样返回；超阈值截断为 DELEGATE_TRUNCATED_PREFIX + 前 N 字
        """
        if len(text) <= DELEGATE_RESULT_LIMIT:
            return text
        truncated = text[:DELEGATE_RESULT_LIMIT]
        return SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX.format(
            total=len(text), truncated=truncated
        )
