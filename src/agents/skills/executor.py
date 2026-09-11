"""SkillExecutor — inline 指令注入 / fork 子代理执行。

- inline：返回 skill 正文（render 后的方法论），主 agent 自己执行（不产生子代理）。
- fork：create_agent(model, tools=白名单筛选结果, system_prompt=执行者人设) 生成独立
  子代理；初始 user message = skill 正文（task 已注入），返回纯文本（不带 [n]）。
  未声明 allowed-tools 时零工具 = 防递归硬保证。执行期把 current_request_ctx 切到
  子上下文（run.ctx）：工具检索与引用编号落子池，不污染主 agent 引用池（design D7/D8/D9/R3）。

可观测性（design D11）：fork 子代理复用主 agent 的 llm 实例（或 get_llm 新建实例）——
与主 agent **同级观测**（同一实例自带 callbacks；Langfuse 是否捕获取决于网关层，应用层
不新增 Langfuse 工作，见 design Risks「create_agent 观测缺口」）。子代理事件消费
实现见 fork_stream.consume_fork_events（本模块只保留装配与控制流）。

模型/思考（spec delegate-execution-controls）：skill 声明 model → get_llm(model=...)
新建；model 空 → 继承主 agent 的 model_name。请求上下文可及 → 新建实例携带
extra_body.enable_thinking=ctx.deep_thinking（思考跟随请求档）；不可得（无 ctx）→
复用主 agent 实例（思考走模型默认）。

防失控（design D13 + spec delegate-execution-controls，三层）：事件级空闲超时
（DELEGATE_MAX_IDLE_S，流式增量不误杀）+ 总时长保险丝（asyncio.wait_for，deep_thinking
档 DELEGATE_TOTAL_TIMEOUT_THINKING_S / 默认档 DELEGATE_TOTAL_TIMEOUT_S）+ turn 上限
（DELEGATE_DEFAULT_MAX_TURNS）。idle/total/turn 中断统一返回
DELEGATE_TIMEOUT_TEXT 并写 ctx.fork_stop_reason；请求取消（abort_signal 置位）抛
CancelledError 由主任务按取消路径收尾。

截断（design D10）：fork 结果超 DELEGATE_RESULT_LIMIT → 截断为结构化摘要
（含总字数提示），防主 agent 上下文膨胀。
"""

import asyncio
from collections.abc import Callable

from langchain.agents import create_agent
from langchain_core.runnables.config import var_child_runnable_config

from src.agents.presets.models import AgentPreset
from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.fork_stream import consume_fork_events
from src.agents.skills.fork_tools import select_fork_tools
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.rendering import render_skill_body
from src.config import settings
from src.config.const import (
    DELEGATE_DEFAULT_MAX_TURNS,
    DELEGATE_RESULT_LIMIT,
    SKILL_TASK_PLACEHOLDERS,
    DelegateStopReason,
    SSEInteractionTexts,
)
from src.config.prompts import (
    FORK_DEFAULT_EXECUTOR_PROMPT,
    FORK_EXECUTION_CONTRACT,
    FORK_TASK_APPEND_TMPL,
)
from src.infra.llm.request_context import current_request_ctx
from src.models import get_llm  # 模块级 import：测试需 patch executor.get_llm


class SkillExecutor:
    """按 skill context 分发执行：inline 注入 / fork 子代理。"""

    def __init__(
        self,
        main_llm,
        tool_provider: Callable[[], list] | None = None,
        preset_registry: AgentPresetRegistry | None = None,
    ) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 且 skill 未声明 model 时按
                model_name 继承复用；声明 model 或请求上下文可及时经 get_llm 新建）
            tool_provider: 延迟 provider，返回当前启用工具列表（供 fork 按
                allowed-tools 筛选）。None = 无可用工具（子代理零工具）
            preset_registry: 智能体预设注册表（fork 执行者选择来源）。None =
                不做执行者选择，恒落系统默认人设
        """
        self._main_llm = main_llm
        self._tool_provider = tool_provider
        self._preset_registry = preset_registry
        # 主 agent llm 为 ChatOpenAI 族（get_llm 产物）时 model_name 为标准属性；
        # 测试替身/无该属性的模型对象按 None 处理（缺省 None 走 get_llm 默认模型）
        model_name = None
        if hasattr(main_llm, "model_name"):
            model_name = main_llm.model_name
        if not isinstance(model_name, str) or not model_name:
            model_name = None
        self._main_model_name = model_name

    async def execute(
        self, record: SkillRecord, task: str, run: DelegateRun | None = None
    ) -> str:
        """执行一个 skill，返回给主 agent 的文本。

        Args:
            record: 命中的 SkillRecord
            task: 主 agent 委托的任务描述（fork 时同时作子代理初始消息与正文
                $ARGUMENTS/{task} 占位替换值；inline 时填入 inline_prompt 占位）
            run: 本次 fork 委派运行态；None 时用当前主 ctx、不隔离（既有 inline /
                无 ctx 调用路径）；非 None 时切到 run.ctx 子上下文执行

        Returns:
            inline：渲染后的方法论文本；fork：子代理纯文本（截断/防失控超时文案）
        """
        if record.context == SkillContext.INLINE:
            return self._render_inline(record, task)
        return await self._run_fork(record, task, run)

    def _render_inline(self, record: SkillRecord, task: str) -> str:
        """渲染 inline_prompt 的任务占位符（$ARGUMENTS 或 {task}；无占位则原样）。

        Args:
            record: inline SkillRecord
            task: 任务文本

        Returns:
            渲染后的方法论文本
        """
        return render_skill_body(record.inline_prompt or "", task)

    def _render_fork_task(self, record: SkillRecord, task: str) -> str:
        """渲染 fork 子代理的初始 user message（skill 正文，任务已注入）。

        fork 正文声明了任务占位符（SKILL_TASK_PLACEHOLDERS 任一成员）时直接渲染；
        未声明时在正文末尾追加默认任务段，保证子代理始终拿到任务文本。

        Args:
            record: fork SkillRecord（读 fork_body）
            task: 主 agent 委托的任务文本

        Returns:
            渲染后的正文，作为子代理初始 HumanMessage 内容
        """
        body = record.fork_body or ""
        declared = False
        for placeholder in SKILL_TASK_PLACEHOLDERS:
            if placeholder in body:
                declared = True
                break
        if not declared:
            body = body + "\n" + FORK_TASK_APPEND_TMPL.format(task=task)
        return render_skill_body(body, task)

    async def _run_fork(
        self, record: SkillRecord, task: str, run: DelegateRun | None = None
    ) -> str:
        """fork 执行：子上下文隔离 + astream 级消费 + 三层防失控 + 思考跟随请求档。

        Args:
            record: fork SkillRecord
            task: 任务描述（渲染进 skill 正文，作子代理初始 HumanMessage）
            run: 本次委派运行态；None 时用当前主 ctx（不隔离），非 None 时切到
                run.ctx 子上下文执行，工具检索写入子引用池

        Returns:
            子代理聚合纯文本（截断对齐原实现）；因 idle/total/turn 中断统一返回
            DELEGATE_TIMEOUT_TEXT 并把 reason 写入子 ctx 的 fork_stop_reason；
            请求取消（abort_signal 置位）抛 asyncio.CancelledError（reason=cancelled），
            由调用方（delegate_task → 主任务）按取消路径收尾。

        Raises:
            asyncio.CancelledError: abort_signal 置位（请求取消）
        """
        # 会话智能体名必须在切子 ctx 之前读主 ctx（child() 不复制 agent，
        # 切后 current_request_ctx 已指向子 ctx）
        ctx = current_request_ctx.get()
        if ctx is None:
            session_agent = ""
        else:
            session_agent = ctx.agent
        if run is not None:
            # 子 ctx 为本次委派独占：注入本次 id 并复位停止原因，供 consume_fork_events
            # 读取与中断写入（无并发串号）
            run.ctx.delegate_id = run.delegate_id
            run.ctx.fork_stop_reason = None
            child_ctx = run.ctx
        else:
            child_ctx = ctx
        user_content = self._render_fork_task(record, task)
        preset = self._resolve_executor(record, session_agent)
        sub_agent = self._build_sub_agent(record, preset)
        max_turns = self._fork_max_turns(preset)
        total_timeout = self._fork_total_timeout(child_ctx)
        # 隔离子代理回调传播：不 reset 会经 var_child_runnable_config 把外层
        # callback handler 传进 create_agent，子代理 LLM 事件泄漏到外层
        # graph.astream_events（SSE token 污染 + full_answer 累积子代理原文）。
        # reset 后子代理事件只走其自身 handler，由本方法显式接入（scope=delegate）。
        token = var_child_runnable_config.set(None)
        # 切到子 ctx：工具经 ContextVar 读 ctx，检索与引用编号落子池，
        # 主 agent 引用池不受影响（design D7/R3）
        token_ctx = current_request_ctx.set(child_ctx)
        try:
            try:
                text = await asyncio.wait_for(
                    consume_fork_events(
                        sub_agent, run, user_content, record.name, max_turns
                    ),
                    timeout=total_timeout,
                )
            except TimeoutError:
                # 总时长保险丝（total）：wait_for 取消内层后在此收敛。
                # 注意：abort 引发的 CancelledError 不会被 wait_for 吞成 TimeoutError——
                # 非超时路径的任务自身 CancelledError 原样透传（Python 3.11+ wait_for 语义），
                # 因此 cancel 会继续上抛到 delegate_task 的 except asyncio.CancelledError
                # （该 CancelledError 已在 consume_fork_events 置 fork_stop_reason=cancelled）。
                # 端到端验证见 Task J；勿在此加 except CancelledError 防吞（会破坏取消语义）
                if child_ctx is not None:
                    child_ctx.fork_stop_reason = DelegateStopReason.TOTAL
                result = SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
            else:
                result = self._truncate(text)
            # 回写本次委派最终返回给调用方的文本（含 idle/total/turn 中断文案；
            # 取消路径抛异常不写）。当前无读取方，消费方在 Plan 3（确认门/直出），
            # 勿当死代码清理
            if run is not None:
                run.result_text = result
            return result
        finally:
            # 回写本次委派停止原因；当前无读取方，消费方在 Plan 3（确认门/直出），
            # 勿当死代码清理
            if run is not None:
                run.stop_reason = run.ctx.fork_stop_reason
            current_request_ctx.reset(token_ctx)
            var_child_runnable_config.reset(token)

    def _resolve_executor(
        self, record: SkillRecord, session_agent: str
    ) -> AgentPreset | None:
        """按优先级选执行者预设：skill.agent > 会话智能体 > None（系统默认）。

        Args:
            record: fork SkillRecord（其 agent 字段为最高优先级）
            session_agent: 会话绑定智能体名（空串=未绑定）

        Returns:
            命中的 AgentPreset；两处都查不到（或未装配 registry）返回 None
        """
        if self._preset_registry is None:
            return None
        if record.agent:
            preset = self._preset_registry.get(record.agent)
            if preset is not None:
                return preset
        if session_agent:
            preset = self._preset_registry.get(session_agent)
            if preset is not None:
                return preset
        return None

    def _fork_max_turns(self, preset: AgentPreset | None) -> int:
        """取 fork 子代理的 turn 上限：preset.max_turns 优先，缺省回落系统默认。"""
        if preset is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        if preset.max_turns is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        return preset.max_turns

    def _build_sub_agent(self, record: SkillRecord, preset):
        """构建 fork 子代理：system=执行者人设、user=skill 正文、tools=交集。

        初始 user message（skill 正文 + 任务）由 _run_fork 渲染后经
        consume_fork_events 传入；本方法只负责装配人设、工具交集与 middleware。

        Args:
            record: fork SkillRecord
            preset: 执行者 AgentPreset（None → 系统默认人设）

        Returns:
            create_agent 编译产物（astream_events 事件源）
        """
        return create_agent(
            self._resolve_fork_llm(record),
            tools=self._fork_tools(record, preset),
            system_prompt=self._executor_system_prompt(preset),
            middleware=[],
        )

    def _executor_system_prompt(self, preset) -> str:
        """解析 fork 子代理的 system prompt（执行者人设 + 执行契约）。

        Args:
            preset: 执行者 AgentPreset；None 表示未选执行者预设

        Returns:
            preset 非空且 system_prompt 非空时返回其人设，否则返回系统默认
            FORK_DEFAULT_EXECUTOR_PROMPT；两种情况下均在末尾追加执行契约
            FORK_EXECUTION_CONTRACT（契约是执行约束，不由内容作者决定）。
            本层不构造 PromptManager、不拉 Langfuse。
        """
        if preset is not None:
            prompt = preset.system_prompt
            if prompt:
                return prompt + FORK_EXECUTION_CONTRACT
        return FORK_DEFAULT_EXECUTOR_PROMPT + FORK_EXECUTION_CONTRACT

    def _fork_tools(self, record: SkillRecord, preset):
        """按 allowed-tools ∩ 执行者 tools 选子代理工具（无 provider 时为零工具）。"""
        if self._tool_provider is not None:
            available = self._tool_provider()
        else:
            available = []
        if preset is not None:
            executor_tools = preset.tools
        else:
            executor_tools = None
        return select_fork_tools(record.allowed_tools, available, executor_tools)

    @staticmethod
    def _fork_total_timeout(ctx) -> float:
        """按请求思考档选 fork 总时长保险丝。

        Args:
            ctx: 当前请求上下文（可能为 None）

        Returns:
            deep_thinking 档取 DELEGATE_TOTAL_TIMEOUT_THINKING_S，否则默认档
            DELEGATE_TOTAL_TIMEOUT_S（无 ctx 亦走默认档）
        """
        if ctx is not None and ctx.deep_thinking:
            return settings.DELEGATE_TOTAL_TIMEOUT_THINKING_S
        return settings.DELEGATE_TOTAL_TIMEOUT_S

    def _resolve_fork_llm(self, record: SkillRecord):
        """解析 fork 子代理的 llm 实例（model / deep_thinking 消费）。

        Args:
            record: fork SkillRecord

        Returns:
            llm 实例：
            - 声明 model → get_llm(model=record.model) 新建（请求上下文可及则
              附 extra_body.enable_thinking=ctx.deep_thinking）
            - 未声明 model 但请求上下文可及 → get_llm(model=主 agent model_name,
              extra_body.enable_thinking=ctx.deep_thinking) 新建
            - 两者均不可得（无 ctx 且未声明 model）→ 复用主 agent llm 实例
            enable_thinking 取值：ctx.deep_thinking（请求级档位）
        """
        ctx = current_request_ctx.get()
        if ctx is None:
            thinking = None
        else:
            thinking = ctx.deep_thinking
        if record.model is None and thinking is None:
            return self._main_llm

        kwargs: dict = {}
        if record.model is not None:
            kwargs["model"] = record.model
        elif self._main_model_name is not None:
            kwargs["model"] = self._main_model_name
        if thinking is not None:
            kwargs["extra_body"] = {"enable_thinking": thinking}
        return get_llm(**kwargs)

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
