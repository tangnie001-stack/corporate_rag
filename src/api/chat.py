"""流式聊天 SSE 端点 — 支持分阶段状态推送和引用高亮。"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import jieba
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from src.api.dependencies import get_app_service
from src.chat.streaming import StreamingRunManager, streaming_manager
from src.config.const import SESSION_LOCK_TTL
from src.infra.llm.request_context import RequestContext, current_request_ctx
from src.infra.llm.trace_context import current_trace_id
from src.services.agent_service import _run_generation
from src.services.app_service import AppService
from src.utils.sse import (
    SSEDoneEvent,
    SSEErrorEvent,
    to_sse,
)

router = APIRouter()

# ── Query-Biased Snippet helpers ───────────────────────────────────

STOP_WORDS = {
    "的",
    "了",
    "是",
    "在",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "那",
    "什么",
    "怎么",
    "吗",
    "吧",
    "啊",
    "呢",
}


def get_query_biased_snippet(query: str, chunk_text: str, window: int = 100) -> dict:
    """基于查询关键词提取摘要片段及高亮位置。

    用 jieba 分词从查询中提取关键词，在分块文本中定位匹配位置，
    返回首个关键词周围的上下文窗口和高亮区域。

    Args:
        query: 用户原始查询文本
        chunk_text: 分块完整文本
        window: 关键词前后上下文窗口大小（字符数，默认 100）

    Returns:
        dict: 包含 snippet（摘要文本）、highlights（高亮位置列表，
        每项含 start/end/keyword）、fallback（是否无匹配的标记）
    """
    words = jieba.lcut(query)
    keywords = [w for w in words if len(w) > 1 and w not in STOP_WORDS]
    if not keywords:
        return {"snippet": chunk_text[:200], "highlights": [], "fallback": True}
    matches: list[tuple[int, int, str]] = []
    for kw in keywords:
        idx = chunk_text.find(kw)
        while idx != -1:
            matches.append((idx, idx + len(kw), kw))
            idx = chunk_text.find(kw, idx + 1)
    if not matches:
        return {"snippet": chunk_text[:200], "highlights": [], "fallback": True}
    first = min(m[0] for m in matches)
    start = max(0, first - window)
    end = min(len(chunk_text), first + window)
    snippet = chunk_text[start:end]
    highlights = []
    for hs, he, kw in matches:
        if hs >= start and he <= end:
            highlights.append({"start": hs - start, "end": he - start, "keyword": kw})
    if highlights:
        highlights.sort(key=lambda h: h["start"])
        merged = [highlights[0]]
        for h in highlights[1:]:
            if h["start"] <= merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], h["end"])
            else:
                merged.append(h)
        highlights = merged
    return {"snippet": snippet, "highlights": highlights, "fallback": False}


def _build_highlighted_snippet(qbs: dict) -> str:
    """将 query-biased snippet 转为含 <mark> 高亮的 HTML 片段。

    若为 fallback（无关键词匹配），仅做 HTML 转义后返回原文，
    保证前端可安全渲染。否则按 highlights 区间逐段包裹 <mark> 标签。

    Args:
        qbs: get_query_biased_snippet() 返回的摘要字典，含
        snippet、highlights、fallback 三个键

    Returns:
        str: 含 <mark> 高亮标签的 HTML 字符串
    """
    from html import escape

    snippet = qbs["snippet"]
    if qbs.get("fallback"):
        return escape(snippet)

    highlights = qbs.get("highlights", [])
    if not highlights:
        return escape(snippet)

    # 遍历高亮区间逐段拼接 HTML，重叠区间已由调用方合并
    parts = []
    pos = 0
    for h in highlights:
        start = h["start"]
        end = h["end"]
        if start > pos:
            parts.append(escape(snippet[pos:start]))
        parts.append(f"<mark>{escape(snippet[start:end])}</mark>")
        pos = end
    if pos < len(snippet):
        parts.append(escape(snippet[pos:]))
    return "".join(parts)


async def _run_with_finalize(
    svc: AppService,
    session_id: str,
    kb_id: str,
    partial_holder: dict,
    answer_builder,
    manager: StreamingRunManager,
    abort_signal: asyncio.Event,
    release_lock: Callable[[], None],
    ctx: RequestContext,
) -> None:
    """后台任务主体：跑生成，完成后按结果收尾落库，finally 释放锁并注销。

    后台任务与调用方处于不同 asyncio task，contextvars 不会自动传播，因此本
    函数在入口显式 set current_request_ctx / current_trace_id（工具与节点经
    contextvar 读取 clarify_channel / tool_contexts 等），finally 中 reset。

    收尾分三支：
    - 正常结束：完整回答落 complete，写 done 终态事件（含 trace_id）
    - 被取消（abort 触达 task.cancel）：已产出 token 落 interrupted，
      写 done(cancelled) 终态事件，随后 re-raise 保持取消语义
    - 异常：已产出 token 落 interrupted，写 error 终态事件

    Args:
        svc: AppService 实例（save_assistant_async 落库）
        session_id: 会话 ID
        kb_id: 知识库 ID
        partial_holder: 生产者写入的 {"text": 已产出 token, "sources": 引用来源列表}
            共享 dict，取消/出错时据此写 interrupted 部分回答，收尾落库引用来源
        answer_builder: 可调用对象，执行生成并更新 partial_holder["text"]，
            返回完整回答
        manager: StreamingRunManager（终态事件写入缓冲）
        abort_signal: 请求级中止信号（由 cancel 端点置位，任务内当前不消费）
        release_lock: per-session 并发锁释放回调（幂等，任务完成时调用）
        ctx: 请求上下文（含 clarify_channel），任务入口 set 到 current_request_ctx
    """
    task = asyncio.current_task()
    assert task is not None, (
        "_run_with_finalize 须由 create_task 启动（注销需任务引用）"
    )
    trace_id = current_trace_id.get()
    ctx_token = current_request_ctx.set(ctx)
    trace_token = current_trace_id.set(trace_id)
    try:
        full_answer = await answer_builder()
    except asyncio.CancelledError:
        partial = partial_holder["text"]
        if partial:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                partial,
                partial_holder.get("sources", []),
                "interrupted",
            )
        manager.add_event(session_id, "done", {"cancelled": True})
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("generation failed: {}", e)
        partial = partial_holder["text"]
        if partial:
            await svc.save_assistant_async(
                session_id,
                kb_id,
                partial,
                partial_holder.get("sources", []),
                "interrupted",
            )
        manager.add_event(session_id, "error", {"error": str(e)})
    else:
        await svc.save_assistant_async(
            session_id,
            kb_id,
            full_answer,
            partial_holder.get("sources", []),
            "complete",
        )
        manager.add_event(session_id, "done", {"trace_id": trace_id or ""})
    finally:
        current_request_ctx.reset(ctx_token)
        current_trace_id.reset(trace_token)
        release_lock()
        manager.unregister_if_current(session_id, task)


async def _stream_rag_response(
    svc: AppService,
    kb_id: str,
    session_id: str,
    query: str,
    user_id: str = "",
    deep_thinking: bool = False,
    release_lock: Callable[[], None] | None = None,
) -> AsyncGenerator[str, None]:
    """以 SSE 事件流推送 RAG 响应 — 委托给 agent_service。

    由 stream_chat 取订阅生成器与启动上下文后，启动 _run_with_finalize
    后台任务（跑 _run_generation 并负责 assistant 落库 / 终态事件 /
    锁释放 / 任务注销），随后订阅事件缓冲转换为 SSE 帧产出。

    Args:
        svc: AppService 实例
        kb_id: 知识库 UUID（空字符串表示跨库搜索）
        session_id: 会话 ID
        query: 用户查询文本
        user_id: 当前用户 ID（保留签名供契约对齐，收尾已并入后台任务）
        deep_thinking: 深度思考开关（透传给 agent_service，最终控制
            agent LLM 的 enable_thinking 参数，默认 False）
        release_lock: 后台任务完成时释放 per-session 并发锁的同步回调
            （由 chat_stream 注入，幂等）；无锁场景（测试直调）传 None
    """
    if release_lock is None:
        release_lock = lambda: None

    try:
        subscription, launch_ctx = await svc.agent_service.stream_chat(
            kb_id, session_id, query, deep_thinking
        )
    except Exception as e:  # noqa: BLE001
        # 任务未启动，锁无后台任务可释放，本路径直接释放避免挂到 TTL
        logger.exception("Chat stream setup failed: {}", str(e))
        release_lock()
        yield to_sse(SSEErrorEvent(str(e)))
        yield to_sse(SSEDoneEvent(trace_id=current_trace_id.get() or ""))
        return

    partial_holder: dict = {"text": "", "sources": []}
    abort_signal = asyncio.Event()
    ctx = launch_ctx["ctx"]

    async def answer_builder() -> str:
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
        )

    task = asyncio.create_task(
        _run_with_finalize(
            svc,
            launch_ctx["session_id"],
            launch_ctx["kb_id"],
            partial_holder,
            answer_builder,
            streaming_manager,
            abort_signal,
            release_lock,
            ctx,
        )
    )
    streaming_manager.register(launch_ctx["session_id"], task, abort_signal)
    task.add_done_callback(lambda _t: release_lock())
    task.add_done_callback(
        lambda _t: streaming_manager.unregister_if_current(
            launch_ctx["session_id"], task
        )
    )

    try:
        async for event in subscription:
            yield to_sse(event)
    except Exception as e:  # noqa: BLE001
        logger.exception("Chat stream unhandled error: {}", str(e))
        yield to_sse(SSEErrorEvent(str(e)))
        yield to_sse(SSEDoneEvent(trace_id=current_trace_id.get() or ""))


async def _acquire_session_lock(redis, session_id: str) -> bool:
    """SETNX 获取 per-session 并发锁，返回是否获取成功。

    锁 key 为 chat_lock:{session_id}，带 TTL（SESSION_LOCK_TTL）兜底过期，
    防止流中断（如客户端断连）后锁永不释放。

    Args:
        redis: redis.asyncio 客户端（Redis 不可用时为 None，由调用方跳过加锁）
        session_id: 会话 ID

    Returns:
        bool: 获取成功返回 True；已有锁（并发冲突）返回 False
    """
    key = f"chat_lock:{session_id}"
    return bool(await redis.set(key, "1", nx=True, ex=SESSION_LOCK_TTL))


async def _release_session_lock(redis, session_id: str) -> None:
    """释放 per-session 并发锁（删除对应 Redis key）。

    Args:
        redis: redis.asyncio 客户端
        session_id: 会话 ID
    """
    await redis.delete(f"chat_lock:{session_id}")


@router.get("/chat/stream")
async def chat_stream(
    request: Request,
    session_id: str = Query(..., description="Session ID for conversation history"),
    kb_id: str = Query(
        ..., description="Knowledge base ID (or empty for cross-KB search)"
    ),
    query: str = Query(..., description="User question"),
    deep_thinking: bool = Query(False, description="深度思考开关（enable_thinking）"),
    svc: AppService = Depends(get_app_service),
):
    """流式 RAG 问答端点 — 返回 SSE 事件流。

    Args:
        session_id: 会话 ID，用于关联对话历史
        kb_id: 知识库 UUID（空字符串表示跨库搜索）
        query: 用户问题文本
        deep_thinking: 深度思考开关（默认 False，true 时开启 agent LLM
            enable_thinking）
        svc: AppService 实例（通过 FastAPI Depends 注入）
        request: FastAPI 请求对象（从中提取 user_id 用于会话归属）

    Returns:
        StreamingResponse: SSE 流式响应，包含
        status / token / citation / error / done 事件

    Raises:
        HTTPException 422: 参数校验失败（FastAPI 自动处理）
        HTTPException 409: 同一会话已有进行中的请求（进程内注册表或 Redis 并发锁冲突）
    """
    user_id = getattr(request.state, "user_id", "") if request else ""

    # 并发防护顺序：先查进程内注册表（is_running），再取 Redis 锁。
    # 注册表是权威状态——Redis 锁 TTL（SESSION_LOCK_TTL）可能短于
    # 含 ask_user 的一轮生成，锁过期不代表生成结束。
    if streaming_manager.is_running(session_id):
        raise HTTPException(409, "当前会话正在处理中")

    # per-session 并发锁：Redis 可用时加锁，冲突直接返回 409
    lock_held = False
    redis = svc.chat_manager._redis
    if redis is not None:
        try:
            lock_held = await _acquire_session_lock(redis, session_id)
        except Exception as e:  # noqa: BLE001
            # Redis 不可用：跳过锁，不阻塞请求（与 ChatManager 降级策略一致）
            logger.warning("Session lock skipped (Redis unavailable): {}", e)
        else:
            # 仅当 SETNX 明确返回 False（已有锁）才视为冲突
            if not lock_held:
                raise HTTPException(409, "当前会话正在处理中")

    # M1：请求开始同步落 user（session 创建幂等，写入成功后才启动生成）
    try:
        await svc.set_chat_repo()
        await svc.save_session_async(session_id, query[:20], kb_id, user_id)
        await svc.save_user_async(session_id, kb_id, query)
    except Exception:
        # 落库失败（编程错误等非吞掉路径）：先释放锁，避免 session 锁挂到 TTL
        if lock_held:
            await _release_session_lock(redis, session_id)
        raise
    logger.info("user message persisted at request start: session_id={}", session_id)

    async def _release_lock_async() -> None:
        """异步释放 per-session 并发锁（异常只记日志，锁有 TTL 兜底）。"""
        try:
            await _release_session_lock(redis, session_id)
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

    async def _stream_with_lock() -> AsyncGenerator[str, None]:
        """持有并发锁流式推送 RAG 响应（锁由后台任务完成时释放，SSE 断连不提前释放）。"""
        async for event in _stream_rag_response(
            svc, kb_id, session_id, query, user_id, deep_thinking, release_lock_cb
        ):
            yield event

    return StreamingResponse(
        _stream_with_lock(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证 SSE 实时推送
        },
    )
