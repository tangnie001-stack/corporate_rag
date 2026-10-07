"""单轮生成编排入口——站点与企微通道共用（design D12）。

从 api/chat.py 的 _stream_rag_response + _run_with_finalize 下沉而来：
产出结构化 SSEEvent 流，站点把它转成 SSE 帧，通道交给投影层。
编排前置（set_chat_repo / 原子闸门 / 落库）与收尾（终态落库 / 释放 / 注销）
内聚在本入口，避免调用方各写一半。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from loguru import logger

from src.chat.process_log import serialize_process
from src.chat.streaming import (
    StreamingRunManager,
    _subscribe_events,
    streaming_manager,
)
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.trace_context import (
    current_session_id,
    current_trace_id,
    current_user_id,
)
from src.services.agent_service import _run_generation
from src.services.app_service import AppService
from src.services.chat_lock import acquire_session_lock, release_session_lock
from src.utils.sse import SSEDoneEvent, SSEErrorEvent, SSEEvent


class TurnBusy(Exception):
    """同一会话已有进行中的生成（调用方译为用户可见结果）。"""


@dataclass
class TurnHandle:
    """一轮生成句柄。

    Attributes:
        session_id: 会话 ID（站点与通道共用）
        events: 结构化事件流（终止态由任务生命周期提供）
    """

    session_id: str
    events: AsyncIterator[SSEEvent]


async def _run_with_finalize(
    svc: AppService,
    session_id: str,
    kb_id: str,
    partial_holder: dict,
    answer_builder: Callable,
    manager: StreamingRunManager,
    abort_signal: asyncio.Event,
    release_lock: Callable[[], None],
    ctx: RequestContext,
    trace_id: str = "",
) -> None:
    """后台任务主体：跑生成，完成后按结果收尾落库，finally 释放锁并注销。

    后台任务与调用方处于不同 asyncio task，其 contextvars 是 create_task 在
    创建时复制的一份快照；本函数仍在入口显式 set
    current_request_ctx / current_trace_id / current_session_id（工具与节点
    经 contextvar 读取 clarify_channel / tool_contexts / 日志格式段等），
    是为不依赖该快照（create_task 之后写入的值到不了任务内，中间件顺序变化
    也会静默丢失），finally 中 reset。
    trace_id 由调用方在 create_task 前从 current_trace_id.get() 捕获并显式
    传入，任务内据此 set contextvar 并写 done 终态事件，保证与请求 trace_id
    一致（单一事实来源）。session_id 直接取 ctx.session_id，供日志 patcher
    注入固定格式段。

    收尾分三支：
    - 正常结束：完整回答落 complete（MySQL）+ 写 Redis 对话历史（供下一轮
      prompt 上下文），写 done 终态事件（含 trace_id）
    - 被取消（abort 触达 task.cancel）：已产出 token 落 interrupted（仅 MySQL），
      写 done(cancelled) 终态事件，随后 re-raise 保持取消语义
    - 异常：已产出 token 落 interrupted（仅 MySQL），写 error 终态事件

    Args:
        svc: AppService 实例（save_assistant_async 落 MySQL；
            chat_manager.add_message_async 写 Redis 对话历史）
        session_id: 会话 ID
        kb_id: 知识库 ID
        partial_holder: 生产者写入的 {"text": 已产出 token, "sources": 引用来源列表}
            共享 dict，取消/出错时据此写 interrupted 部分回答，收尾落库引用来源；
            _run_generation 另挂 events_log（过程事件列表引用）并于收尾写 model_name
        answer_builder: 可调用对象，执行生成并更新 partial_holder["text"]；
            终态落库内容为 process 分拣返回的净化正文（旁白已剔除）
        manager: StreamingRunManager（终态事件写入缓冲）
        abort_signal: 请求级中止信号（由 cancel 端点置位，任务内当前不消费）
        release_lock: per-session 并发锁释放回调（幂等，任务完成时调用）
        ctx: 请求上下文（含 clarify_channel / session_id），任务入口 set 到
            current_request_ctx / current_session_id
        trace_id: 请求级 trace_id（调用方启动任务前捕获），任务入口 set 到
            current_trace_id，done 终态事件据此写入
    """
    task = asyncio.current_task()
    assert task is not None, (
        "_run_with_finalize 须由 create_task 启动（注销需任务引用）"
    )
    ctx_token = current_request_ctx.set(ctx)
    trace_token = current_trace_id.set(trace_id or None)
    session_token = current_session_id.set(ctx.session_id)
    try:
        await answer_builder()
    except asyncio.CancelledError:
        # 净化正文：分拣剔除旁白后的末轮正文流，替代全量累积 token 落库，
        # 避免历史回放时旁白双重渲染；partial_holder["text"] 同步更新保持一致
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        if purified:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                purified,
                partial_holder.get("sources", []),
                "interrupted",
                process_json=process_json,
                model_name=partial_holder.get("model_name", ""),
            )
        manager.add_event(session_id, "done", {"cancelled": True})
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("generation failed: {}", e)
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        if purified:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                purified,
                partial_holder.get("sources", []),
                "interrupted",
                process_json=process_json,
                model_name=partial_holder.get("model_name", ""),
            )
        manager.add_event(session_id, "error", {"error": str(e)})
    else:
        process_json, purified = serialize_process(
            partial_holder.get("events_log") or []
        )
        partial_holder["text"] = purified
        await svc.save_assistant_async(
            session_id,
            kb_id,
            purified,
            partial_holder.get("sources", []),
            "complete",
            process_json=process_json,
            model_name=partial_holder.get("model_name", ""),
        )
        # 净化正文写 Redis 对话历史（get_history_async 供下一轮 prompt 上下文，
        # 与 MySQL 落库内容一致）；取消/异常的部分回答保持仅 MySQL，不写 Redis
        await svc.chat_manager.add_message_async(session_id, "assistant", purified)
        manager.add_event(session_id, "done", {"trace_id": trace_id or ""})
    finally:
        current_request_ctx.reset(ctx_token)
        current_trace_id.reset(trace_token)
        current_session_id.reset(session_token)
        release_lock()
        manager.unregister_if_current(session_id, task)


async def start_turn(
    svc: AppService,
    *,
    session_id: str,
    kb_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
    agent: str = "",
    title: str | None = None,
    abort_signal: asyncio.Event | None = None,
) -> TurnHandle:
    """启动一轮生成，返回事件流句柄（站点与通道共用）。

    编排步骤（design D12）：
    1. 注入 chat_repo（漏则 save_* 静默跳过）
    2. 原子闸门：进程内注册表 is_running → Redis SETNX 锁（Redis 不可用则跳过）
    3. 落库前置：session + user 消息（失败先释放已取的锁再抛）
    4. stream_chat 取订阅与启动上下文；调用本身失败不抛，改为 error + done 终止态
    5. 起后台任务跑生成 + 收尾落库 + 终态事件 + 释放锁 + 注销
    6. 返回 TurnHandle，events 为结构化 SSEEvent 流

    Args:
        svc: AppService
        session_id: 会话 ID
        kb_id: 知识库 ID（空串表示不检索）
        query: 用户文本
        user_id: 用户 ID（企微侧为派生 UUID，站点为登录用户）
        deep_thinking: 深度思考开关
        agent: 智能体预设名
        title: 会话标题（None → query[:20]；仅首次落库生效）
        abort_signal: 取消信号（None 时内部新建）

    Returns:
        TurnHandle：其 events 为结构化 SSEEvent 流

    Raises:
        TurnBusy: 同一会话已有进行中的生成（闸门未获得）
        Exception: 落库前置失败等编排前置错误（与站点现值一致）
    """
    await svc.set_chat_repo()

    # 并发防护顺序：先查进程内注册表（is_running），再取 Redis 锁。
    # 注册表是权威状态——Redis 锁 TTL（SESSION_LOCK_TTL）可能短于
    # 含 ask_user 的一轮生成，锁过期不代表生成结束。
    if streaming_manager.is_running(session_id):
        raise TurnBusy("当前会话正在处理中")

    # per-session 并发锁：Redis 可用时加锁，冲突直接抛 TurnBusy
    lock_held = False
    redis = svc.chat_manager._redis
    if redis is not None:
        try:
            lock_held = await acquire_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            # Redis 不可用：跳过锁，不阻塞请求（与 ChatManager 降级策略一致）
            logger.warning("Session lock skipped (Redis unavailable): {}", e)
        else:
            # 仅当 SETNX 明确返回 False（已有锁）才视为冲突
            if not lock_held:
                raise TurnBusy("当前会话正在处理中")

    # M1：请求开始同步落 user（session 创建幂等，写入成功后才启动生成）
    try:
        await svc.save_session_async(
            session_id,
            title if title is not None else query[:20],
            kb_id,
            user_id,
        )
        await svc.save_user_async(session_id, kb_id, query)
    except Exception:
        # 落库失败（编程错误等非吞掉路径）：先释放锁，避免 session 锁挂到 TTL
        if lock_held:
            await release_session_lock(redis, session_id)
        raise
    logger.info("user message persisted at request start: session_id={}", session_id)

    async def _release_lock_async() -> None:
        """异步释放 per-session 并发锁（异常只记日志，锁有 TTL 兜底）。"""
        try:
            await release_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Session lock release failed: {}", e)

    def release_lock_cb() -> None:
        """同步释放回调：调度异步释放 Redis 锁（幂等）。

        锁由后台任务持有到完成——SSE 断连不提前释放（进程内注册表
        is_running 才是并发防护的权威状态）。_run_with_finalize finally 与
        task done_callback 双路径调用，靠 lock_held 标志保证只释放一次。
        """
        nonlocal lock_held
        if not lock_held:
            return
        lock_held = False
        asyncio.create_task(_release_lock_async())

    try:
        _subscription, launch_ctx = await svc.agent_service.stream_chat(
            kb_id, session_id, query, deep_thinking, agent=agent
        )
    except Exception as e:  # noqa: BLE001
        # 任务未启动，锁无后台任务可释放，本路径直接释放避免挂到 TTL；
        # 不抛出——整体包裹会让站点从 200+SSE 变 500，改为 error + done 终止态
        logger.exception("Chat stream setup failed: {}", str(e))
        release_lock_cb()
        # 提前绑定错误文本：except 退出后 `e` 被删除，延迟执行的生成器读不到它
        error_text = str(e)

        async def _error_events() -> AsyncIterator[SSEEvent]:
            yield SSEErrorEvent(error_text)
            yield SSEDoneEvent(trace_id=current_trace_id.get() or "")

        return TurnHandle(session_id=session_id, events=_error_events())

    partial_holder: dict = {"text": "", "sources": []}
    signal = abort_signal if abort_signal is not None else asyncio.Event()
    ctx = launch_ctx["ctx"]
    # 将 cancel 端点置位的 abort_signal 接到请求上下文：ask_user 的
    # wait_with_abort_and_timeout 等待的是 ctx.abort_signal，不接线则取消
    # 唤不醒澄清等待，会干等 ASK_USER_TIMEOUT 超时
    ctx.abort_signal = signal

    # trace_id 与 user_id 均在请求作用域内捕获后显式传入生成调用，不依赖后台任务
    # 通过 contextvar 继承读取：任务的上下文是 create_task 创建时的拷贝，该时点之后写入
    # 的值到不了任务内，依赖继承只会静默取空。
    user_id = current_user_id.get()

    async def answer_builder() -> str:
        # 根 observation 的 id 即 trace id；任务入口已 set 过 current_trace_id，
        # 此处按调用时读取并显式传入，不依赖任务上下文的那份拷贝。
        return await _run_generation(
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            launch_ctx["query"],
            launch_ctx["history"],
            launch_ctx["deep_thinking"],
            ctx,
            streaming_manager,
            graph=launch_ctx["graph"],
            partial_holder=partial_holder,
            abort_signal=signal,
            direct_skill=launch_ctx["direct_skill"],
            user_id=user_id,
            # @observe 包装器在调用前取走 langfuse_observation_id（静态签名看不到），
            # 类型检查无法感知该 kwarg
            langfuse_observation_id=current_trace_id.get() or "",  # type: ignore[reportCallIssue]
        )

    task = asyncio.create_task(
        _run_with_finalize(
            svc,
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            partial_holder,
            answer_builder,
            streaming_manager,
            signal,
            release_lock_cb,
            ctx,
            trace_id=current_trace_id.get() or "",
        )
    )
    streaming_manager.register(launch_ctx["session_id"], task, signal)
    task.add_done_callback(lambda _t: release_lock_cb())
    task.add_done_callback(
        lambda _t: streaming_manager.unregister_if_current(
            launch_ctx["session_id"], task
        )
    )

    events = _subscribe_events(
        launch_ctx["session_id"], streaming_manager, max_idle=None
    )
    return TurnHandle(session_id=launch_ctx["session_id"], events=events)
