"""fork 子代理事件消费 —— astream_events 迭代、防失控、delegate 增量转发。

从 executor 拆出的模块级函数：SkillExecutor 负责 fork 的装配与控制流，
本模块负责把子代理事件流消费成聚合正文，并经 ctx.clarify_channel 投 delegate
delta 增量（thinking/content）。三层防失控（idle/total/turn）与取消语义的
逐条说明随实现迁移。
"""

import asyncio
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.skills.delegate_run import DelegateRun
from src.config import settings
from src.config.const import DelegateStopReason, SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.rag.stream import estimate_usage

# idle 到点哨兵：区别于 None（事件源正常收尾），供 consume_fork_events 分流
_IDLE = object()


@dataclass
class _ForkStreamState:
    """fork 事件消费的可变累加状态（模块级 helper 间传递，取代内联局部变量）。

    Attributes:
        idle_timeout: 事件级空闲阈值（来源 settings.DELEGATE_MAX_IDLE_S；用途 idle 判定）
        max_turns: turn 上限（来源 DELEGATE_DEFAULT_MAX_TURNS；用途 turn 判定）
        model_starts: on_chat_model_start 计数（用途 turn 超限判定）
        model_turn_started: 当前模型调用起始 monotonic 时间（用途 turn 日志 latency）
        last_activity: 最近一次收到事件的时间（用途 idle 判定）
        last_flush: 最近一次 flush 增量的时间（用途聚合节流）
        text_parts: content 全量累计片段（用途最终正文拼接）
        pending_think: 待 flush 的思考增量缓冲
        pending_content: 待 flush 的正文增量缓冲
        get_next: 飞行中的 __anext__ 任务（用途 finally 收敛避免悬空 asyncgen）
    """

    idle_timeout: float  # 事件级空闲阈值
    max_turns: int  # turn 上限
    model_starts: int = 0  # 模型调用计数
    model_turn_started: float | None = None  # 当前模型调用起始时间
    last_activity: float = 0.0  # 最近收到事件时间
    last_flush: float = 0.0  # 最近 flush 时间
    text_parts: list[str] = field(default_factory=list)  # content 全量累计
    pending_think: str = ""  # 待 flush 思考缓冲
    pending_content: str = ""  # 待 flush 正文缓冲
    get_next: asyncio.Future | None = None  # 飞行中的 __anext__


def _resolve_stream_ctx(run: DelegateRun | None) -> tuple[RequestContext | None, str]:
    """解析本次 fork 事件消费所用的请求上下文与委派 id。

    显式 if/else 表达（项目禁止三元）；ctx 仍可能为 None（run 与当前请求
    上下文均无），由调用方按既有分支处理。

    Args:
        run: 本次委派运行态；None 时读当前请求上下文（生产 delegate_task 的
            两参路径），非 None 时读 run.ctx 子上下文

    Returns:
        (ctx, delegate_id)：ctx 可能为 None；ctx 为 None 时 delegate_id 为空串
    """
    if run is not None:
        ctx = run.ctx
    else:
        ctx = current_request_ctx.get()
    if ctx is not None:
        delegate_id = ctx.delegate_id
    else:
        delegate_id = ""
    return ctx, delegate_id


async def consume_fork_events(
    sub_agent, run: DelegateRun | None, task: str, skill_name: str, max_turns: int
) -> str:
    """迭代子代理 astream_events(v2)：聚合正文/思考、防失控、转发 delegate delta。

    Args:
        sub_agent: create_react_agent 返回的子代理（astream_events 事件源）
        run: 本次委派运行态；None 时读当前请求上下文（生产 delegate_task 的两参
            路径），非 None 时读 run.ctx 子上下文
        task: 子代理初始任务文本
        skill_name: 投递 delegate 增量时附带的 skill 标签。用形参而非
            run.skill_name：生产 delegate_task 仍走 run=None 路径，从 run 取会丢
            标签；同时避免本模块与 SkillRecord 结构耦合
        max_turns: turn 上限（DELEGATE_DEFAULT_MAX_TURNS）

    Returns:
        聚合后的子代理最终正文纯文本（不含 reasoning）

    Raises:
        asyncio.CancelledError: ctx.abort_signal 置位（reason=cancelled）
    """
    ctx, delegate_id = _resolve_stream_ctx(run)
    state = _ForkStreamState(
        idle_timeout=settings.DELEGATE_MAX_IDLE_S,
        max_turns=max_turns,
    )
    state.last_activity = time.monotonic()
    state.last_flush = state.last_activity

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
    try:
        while True:
            ev = await _next_fork_event(agen, abort_task, ctx, state)
            if ev is _IDLE:
                return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
            if ev is None:
                break  # 事件源正常收尾
            stop_text = await _handle_fork_event(
                ev, ctx, delegate_id, skill_name, state
            )
            if stop_text is not None:
                return stop_text
        # 结束：flush 残留
        await _flush_delegate(
            ctx, delegate_id, skill_name, state.pending_think, state.pending_content
        )
        return "".join(state.text_parts)
    finally:
        if abort_task is not None:
            abort_task.cancel()
        # 收敛可能仍在飞行中的 __anext__：total 被外层 wait_for 取消或 idle 到点
        # cancel 悬挂的 get_next 时，CancelledError 需经事件循环投递进生成器后才能
        # aclose（否则 aclose 抛 RuntimeError: already running，见 Task E 探测）。
        # gather(return_exceptions=True) 等待其结束但不吞外层 CancelledError——
        # 吞掉会破坏 wait_for 的 TimeoutError 收敛与 delegate_task 的取消语义。
        get_next = state.get_next
        if get_next is not None and not get_next.done():
            get_next.cancel()
            await asyncio.gather(get_next, return_exceptions=True)
        # 显式关闭子代理事件流：防悬空 asyncgen 依赖 GC/loop-shutdown
        # （正常 break / idle / turn 返回 / total 被外层 wait_for 取消 / abort raise 均达此）
        await agen.aclose()


async def _next_fork_event(agen, abort_task, ctx, state: _ForkStreamState):
    """等待下一个子代理事件，处理 idle/abort 竞速。

    Args:
        agen: 子代理 astream_events 异步迭代器
        abort_task: 请求取消观察任务（None = 无 ctx，不观察）
        ctx: 当前请求上下文（可能为 None）
        state: fork 消费累加状态（读取 idle_timeout/last_activity，写 get_next）

    Returns:
        事件 dict（正常）；None（事件源收尾）；_IDLE 哨兵（idle 到点，调用方返回超时文案）

    Raises:
        asyncio.CancelledError: ctx.abort_signal 置位（reason=cancelled）
    """
    if ctx is not None and ctx.abort_signal.is_set():
        ctx.fork_stop_reason = DelegateStopReason.CANCELLED
        raise asyncio.CancelledError
    remaining = state.idle_timeout - (time.monotonic() - state.last_activity)
    if remaining <= 0:
        # 事件级流空闲 watchdog：完全静默超时（正常长思考为流式增量不误杀）
        if ctx is not None:
            ctx.fork_stop_reason = DelegateStopReason.IDLE
        return _IDLE
    nxt = asyncio.ensure_future(agen.__anext__())
    state.get_next = nxt  # 供 finally 收敛仍在飞行中的 __anext__
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
        return _IDLE
    if abort_task is not None and abort_task in done:
        # 请求取消优先于 idle/turn：原因=cancelled，抛 CancelledError 走主任务取消路径
        if nxt in pending:
            nxt.cancel()
        # abort_task 存在即 ctx 非 None（仅 ctx 存在时创建），显式 guard 便于类型收敛
        if ctx is not None:
            ctx.fork_stop_reason = DelegateStopReason.CANCELLED
        raise asyncio.CancelledError
    try:
        return nxt.result()
    except StopAsyncIteration:
        return None  # 事件源正常收尾
    except asyncio.CancelledError:
        raise


async def _handle_fork_event(
    ev, ctx, delegate_id: str, skill: str, state: _ForkStreamState
) -> str | None:
    """处理单个事件：刷新活跃时间、turn 计数、model turn 日志、增量聚合与节流 flush。

    Args:
        ev: 事件 dict（langgraph v2）
        ctx: 当前请求上下文（可能为 None）
        delegate_id: 本次委派 id（事件标签）
        skill: skill 标签（delegate delta 用）
        state: fork 消费累加状态（读写计数与增量缓冲）

    Returns:
        turn 超限时的 DELEGATE_TIMEOUT_TEXT；其余返回 None（继续消费）
    """
    state.last_activity = time.monotonic()
    kind = ev.get("event", "")
    data = ev.get("data") or {}
    if kind == "on_chat_model_start":
        state.model_starts += 1
        state.model_turn_started = time.monotonic()
        if state.model_starts > state.max_turns:
            if ctx is not None:
                ctx.fork_stop_reason = DelegateStopReason.TURN
            return SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
        return None
    if kind == "on_chat_model_end":
        await _record_delegate_model_turn(data, delegate_id, state.model_turn_started)
        return None
    if kind != "on_chat_model_stream":
        return None
    chunk = data.get("chunk")
    if chunk is None:
        return None
    content = getattr(chunk, "content", "") or ""
    reasoning = (chunk.additional_kwargs or {}).get("reasoning_content", "")
    if reasoning:
        state.pending_think += reasoning
    if content:
        state.text_parts.append(content)
        state.pending_content += content
    # 聚合节流（约 80ms 或单方向累积超 600 字一次 flush），防高频事件刷屏
    now = time.monotonic()
    if (now - state.last_flush) >= 0.08:
        await _flush_delegate(
            ctx, delegate_id, skill, state.pending_think, state.pending_content
        )
        state.pending_think, state.pending_content = "", ""
        state.last_flush = now
    return None


async def _flush_delegate(
    ctx, delegate_id: str, skill: str, think: str, content: str
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
    data: dict, delegate_id: str, turn_started: float | None
) -> None:
    """记录 delegate model turn 日志（usage 缺失走 estimate_usage 兜底并标注）。

    Args:
        data: on_chat_model_end 事件的 data（含 output）
        delegate_id: 本次委派 id（来自 ctx.delegate_id）
        turn_started: 本次模型调用起始 monotonic 时间（None = 无 on_chat_model_start，
            忽略 latency）
    """
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
            est = estimate_usage([output], _output_text(output))
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


def _output_text(output) -> str:
    """从 model end output（AIMessage）取文本 content 供 usage 估算。"""
    if hasattr(output, "content"):
        c = output.content
        if isinstance(c, str):
            return c
        return str(c)
    return ""
