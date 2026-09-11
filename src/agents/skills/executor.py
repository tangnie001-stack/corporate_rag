"""SkillExecutor — inline 指令注入 / fork 零工具子代理执行。

- inline：返回 skill 正文（render 后的方法论），主 agent 自己执行（不产生子代理）。
- fork：create_react_agent(llm, tools=[], prompt=fork_body) 生成独立零工具
  子代理，初始消息 = task，返回纯文本（不带 [n]）。零工具 = 防递归硬保证。
  执行期把 current_request_ctx 切到子上下文（run.ctx）：工具检索与引用编号落子
  池，不污染主 agent 引用池（design D7/D8/D9/R3）。

可观测性（design D11）：fork 子代理复用主 agent 的 llm 实例（或 get_llm 新建实例）——
与主 agent **同级观测**（同一实例自带 callbacks；Langfuse 是否捕获取决于网关层，应用层
不新增 Langfuse 工作，见 design Risks「create_react_agent 观测缺口」）。子代理执行以
astream_events(v2) 显式消费：LLM 事件只走本消费循环（var_child_runnable_config 隔离 +
scope=delegate 配置），聚合正文经 ctx.clarify_channel 投 delegate delta 增量（thinking/
content），delegate end 终态由 delegate_task finally 统一记录（本层不重复记）。

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
import dataclasses
import time

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.prebuilt import create_react_agent

from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.rendering import render_skill_body
from src.config import settings
from src.config.const import (
    DELEGATE_DEFAULT_MAX_TURNS,
    DELEGATE_RESULT_LIMIT,
    DelegateStopReason,
    SSEInteractionTexts,
)
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import current_request_ctx
from src.models import get_llm  # 模块级 import：测试需 patch executor.get_llm
from src.rag.stream import estimate_usage


class SkillExecutor:
    """按 skill context 分发执行：inline 注入 / fork 子代理。"""

    def __init__(self, main_llm) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 且 skill 未声明 model 时按
                model_name 继承复用；声明 model 或请求上下文可及时经 get_llm 新建）
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

    async def _run_fork(
        self, record: SkillRecord, task: str, run: DelegateRun | None = None
    ) -> str:
        """fork 执行：子上下文隔离 + astream 级消费 + 三层防失控 + 思考跟随请求档。

        Args:
            record: fork SkillRecord
            task: 任务描述（子代理初始 HumanMessage）
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
        if run is not None:
            # 子 ctx 为本次委派独占：注入本次 id 并复位停止原因，供 _consume_fork_events
            # 读取与中断写入（无并发串号）
            run.ctx.delegate_id = run.delegate_id
            run.ctx.fork_stop_reason = None
            child_ctx = run.ctx
        else:
            child_ctx = current_request_ctx.get()
        fork_body = record.fork_body
        if fork_body is not None:
            # fork_body 作子代理 prompt：渲染任务占位符（$ARGUMENTS/{task}）。用副本
            # 承载，避免改写 registry 缓存的 SkillRecord（并发委派共享该对象）
            record = dataclasses.replace(
                record, fork_body=render_skill_body(fork_body, task)
            )
        sub_agent = self._build_sub_agent(record, preset=None)
        max_turns = DELEGATE_DEFAULT_MAX_TURNS
        total_timeout = self._fork_total_timeout(child_ctx)
        # 隔离子代理回调传播：不 reset 会经 var_child_runnable_config 把外层
        # callback handler 传进 create_react_agent，子代理 LLM 事件泄漏到外层
        # graph.astream_events（SSE token 污染 + full_answer 累积子代理原文）。
        # reset 后子代理事件只走其自身 handler，由本方法显式接入（scope=delegate）。
        token = var_child_runnable_config.set(None)
        # 切到子 ctx：工具经 ContextVar 读 ctx，检索与引用编号落子池，
        # 主 agent 引用池不受影响（design D7/R3）
        token_ctx = current_request_ctx.set(child_ctx)
        try:
            try:
                text = await asyncio.wait_for(
                    self._consume_fork_events(
                        sub_agent, record, task, child_ctx, max_turns
                    ),
                    timeout=total_timeout,
                )
            except TimeoutError:
                # 总时长保险丝（total）：wait_for 取消内层后在此收敛。
                # 注意：abort 引发的 CancelledError 不会被 wait_for 吞成 TimeoutError——
                # 非超时路径的任务自身 CancelledError 原样透传（Python 3.11+ wait_for 语义），
                # 因此 cancel 会继续上抛到 delegate_task 的 except asyncio.CancelledError
                # （该 CancelledError 已在 _consume_fork_events 置 fork_stop_reason=cancelled）。
                # 端到端验证见 Task J；勿在此加 except CancelledError 防吞（会破坏取消语义）
                if child_ctx is not None:
                    child_ctx.fork_stop_reason = DelegateStopReason.TOTAL
                result = SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
            else:
                result = self._truncate(text)
            # 回写本次委派最终返回给调用方的文本（含 idle/total/turn 中断文案；
            # 取消路径抛异常不写）
            if run is not None:
                run.result_text = result
            return result
        finally:
            if run is not None:
                run.stop_reason = run.ctx.fork_stop_reason
            current_request_ctx.reset(token_ctx)
            var_child_runnable_config.reset(token)

    def _build_sub_agent(self, record: SkillRecord, preset):
        """构建 fork 子代理（本任务零工具 + fork_body 作 prompt）。

        Args:
            record: fork SkillRecord（fork_body 已渲染任务占位符）
            preset: 执行者预设（Task 5 接入人设；本任务恒为 None）

        Returns:
            create_react_agent 返回的子代理
        """
        return create_react_agent(
            self._resolve_fork_llm(record),
            tools=self._fork_tools(record, preset),
            prompt=record.fork_body,
        )

    def _fork_tools(self, record: SkillRecord, preset):
        """按 allowed-tools ∩ 执行者 tools 选子代理工具（本任务为占位，Task 3 接入筛选）。"""
        return []

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

    async def _consume_fork_events(
        self, sub_agent, record: SkillRecord, task: str, ctx, max_turns: int
    ) -> str:
        """迭代子代理 astream_events(v2)：聚合正文/思考、防失控、转发 delegate delta。

        Args:
            sub_agent: create_react_agent 返回的子代理（astream_events 事件源）
            record: fork SkillRecord
            task: 子代理初始任务文本
            ctx: 当前请求上下文（可能为 None；None 时仅聚合不推送/不防失控）
            max_turns: turn 上限（DELEGATE_DEFAULT_MAX_TURNS）

        Returns:
            聚合后的子代理最终正文纯文本（不含 reasoning）

        Raises:
            asyncio.CancelledError: ctx.abort_signal 置位（reason=cancelled）
        """
        idle_timeout = settings.DELEGATE_MAX_IDLE_S
        model_starts = 0
        model_turn_started: float | None = None
        last_activity = time.monotonic()
        text_parts: list[str] = []  # content 全量累计（含中途 assistant 文本）
        last_flush = time.monotonic()
        pending_think = ""
        pending_content = ""
        delegate_id = ctx.delegate_id if ctx is not None else ""
        skill = record.name

        config = {"tags": ["delegate"], "metadata": {"scope": "delegate"}}
        agen = sub_agent.astream_events(
            {"messages": [HumanMessage(content=task)]},
            config=config,
            version="v2",
        )
        # 请求级取消观察任务：与 __anext__ 竞速（FIRST_COMPLETED）。静默等待期间
        # abort 置位也能即时中断（若只靠每事件轮询，需等下一事件或 idle 60s 才感知，
        # 违背"置位即中断、原因=cancelled"语义，spec delegate-execution-controls）
        abort_task: asyncio.Task | None = None
        if ctx is not None:
            abort_task = asyncio.create_task(ctx.abort_signal.wait())
        get_next: asyncio.Future | None = None
        try:
            while True:
                if ctx is not None and ctx.abort_signal.is_set():
                    ctx.fork_stop_reason = DelegateStopReason.CANCELLED
                    raise asyncio.CancelledError
                remaining = idle_timeout - (time.monotonic() - last_activity)
                if remaining <= 0:
                    # 事件级流空闲 watchdog：完全静默超时（正常长思考为流式增量不误杀）
                    if ctx is not None:
                        ctx.fork_stop_reason = DelegateStopReason.IDLE
                    return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                nxt = asyncio.ensure_future(agen.__anext__())
                get_next = nxt  # 供 finally 收敛仍在飞行中的 __anext__
                wait_set: list = [nxt]
                if abort_task is not None:
                    wait_set.append(abort_task)
                done, pending = await asyncio.wait(
                    wait_set,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    # idle 到点（无事件且无 abort）：cancel 悬挂的 next/abort 后收场
                    for t in pending:
                        t.cancel()
                    if ctx is not None:
                        ctx.fork_stop_reason = DelegateStopReason.IDLE
                    return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                if abort_task is not None and abort_task in done:
                    # 请求取消优先于 idle/turn：原因=cancelled，抛 CancelledError 走主任务取消路径
                    if nxt in pending:
                        nxt.cancel()
                    # abort_task 存在即 ctx 非 None（仅 ctx 存在时创建），显式 guard 便于类型收敛
                    if ctx is not None:
                        ctx.fork_stop_reason = DelegateStopReason.CANCELLED
                    raise asyncio.CancelledError
                try:
                    ev = nxt.result()
                except StopAsyncIteration:
                    break  # 事件源正常收尾
                except asyncio.CancelledError:
                    raise

                last_activity = time.monotonic()
                kind = ev.get("event", "")
                data = ev.get("data") or {}
                if kind == "on_chat_model_start":
                    model_starts += 1
                    model_turn_started = time.monotonic()
                    if model_starts > max_turns:
                        if ctx is not None:
                            ctx.fork_stop_reason = DelegateStopReason.TURN
                        return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
                    continue
                if kind == "on_chat_model_end":
                    await self._record_delegate_model_turn(
                        data, delegate_id, model_turn_started
                    )
                    continue
                if kind != "on_chat_model_stream":
                    continue
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                content = getattr(chunk, "content", "") or ""
                reasoning = (chunk.additional_kwargs or {}).get("reasoning_content", "")
                if reasoning:
                    pending_think += reasoning
                if content:
                    text_parts.append(content)
                    pending_content += content
                # 聚合节流（约 80ms 或单方向累积超 600 字一次 flush），防高频事件刷屏
                now = time.monotonic()
                if (now - last_flush) >= 0.08:
                    await self._flush_delegate(
                        ctx, delegate_id, skill, pending_think, pending_content
                    )
                    pending_think, pending_content = "", ""
                    last_flush = now
            # 结束：flush 残留
            await self._flush_delegate(
                ctx, delegate_id, skill, pending_think, pending_content
            )
            text = "".join(text_parts)
            return text
        finally:
            if abort_task is not None:
                abort_task.cancel()
            # 收敛可能仍在飞行中的 __anext__：total 被外层 wait_for 取消或 idle 到点
            # cancel 悬挂的 get_next 时，CancelledError 需经事件循环投递进生成器后才能
            # aclose（否则 aclose 抛 RuntimeError: already running，见 Task E 探测）。
            # gather(return_exceptions=True) 等待其结束但不吞外层 CancelledError——
            # 吞掉会破坏 wait_for 的 TimeoutError 收敛与 delegate_task 的取消语义。
            if get_next is not None and not get_next.done():
                get_next.cancel()
                await asyncio.gather(get_next, return_exceptions=True)
            # 显式关闭子代理事件流：防悬空 asyncgen 依赖 GC/loop-shutdown
            # （正常 break / idle / turn 返回 / total 被外层 wait_for 取消 / abort raise 均达此）
            await agen.aclose()

    async def _flush_delegate(
        self, ctx, delegate_id: str, skill: str, think: str, content: str
    ) -> None:
        """把累积的思考/正文增量投递为 delegate delta 事件（空则跳过）。"""
        if ctx is None:
            return
        if think:
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "delta",
                    "delegate_id": delegate_id,
                    "skill": skill,
                    "kind": "thinking",
                    "delta": think,
                    "ok": True,
                    "reason": "",
                }
            )
        if content:
            await ctx.clarify_channel.put(
                {
                    "type": "delegate",
                    "action": "delta",
                    "delegate_id": delegate_id,
                    "skill": skill,
                    "kind": "content",
                    "delta": content,
                    "ok": True,
                    "reason": "",
                }
            )

    async def _record_delegate_model_turn(
        self, data: dict, delegate_id: str, turn_started: float | None
    ) -> None:
        """记录 delegate model turn 日志（usage 缺失走 estimate_usage 兜底并标注）。

        Args:
            data: on_chat_model_end 事件的 data（含 output）
            delegate_id: 本次委派 id（来自 ctx.delegate_id）
            turn_started: 本次模型调用起始 monotonic 时间（None = 无 on_chat_model_start，
                忽略 latency）
        """
        from langchain_core.messages import AIMessage

        output = data.get("output")
        usage_in = 0
        usage_out = 0
        usage_estimated = False
        model = ""
        # on_chat_model_end 的 output 恒为 AIMessage（含 usage_metadata/response_metadata）
        if isinstance(output, AIMessage):
            meta = output.usage_metadata
            if isinstance(meta, dict) and (
                meta.get("input_tokens") or meta.get("output_tokens")
            ):
                usage_in = int(meta.get("input_tokens") or 0)
                usage_out = int(meta.get("output_tokens") or 0)
            else:
                est = estimate_usage([output], SkillExecutor._output_text(output))
                usage_in = est.prompt_tokens
                usage_out = est.completion_tokens
                usage_estimated = True
            resp_meta = output.response_metadata
            if isinstance(resp_meta, dict):
                model = resp_meta.get("model_name", "")
                if not isinstance(model, str) or not model:
                    model = resp_meta.get("model", "")
            if not isinstance(model, str):
                model = ""
        latency_ms = 0
        if turn_started is not None:
            latency_ms = int((time.monotonic() - turn_started) * 1000)
        core_logging.log_event(
            Event.DELEGATE_MODEL_TURN,
            delegate_id=delegate_id,
            model=model,
            usage_in=usage_in,
            usage_out=usage_out,
            usage_estimated="true" if usage_estimated else "false",
            latency_ms=latency_ms,
        )

    @staticmethod
    def _output_text(output) -> str:
        """从 model end output（AIMessage）取文本 content 供 usage 估算。"""
        if hasattr(output, "content"):
            c = output.content
            if isinstance(c, str):
                return c
            return str(c)
        return ""

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
