"""流式聊天 SSE 端点 — 支持分阶段状态推送和引用高亮。"""

import asyncio
import json
import os
from typing import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from loguru import logger

import jieba

from src.app_service import AppService

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


_service: AppService | None = None


def _get_service() -> AppService:
    """获取 AppService 单例实例。

    延迟初始化：首次调用时创建实例，后续复用。
    避免模块导入阶段产生网络或数据库连接。

    Returns:
        AppService 全局唯一实例
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service


# ── SSE event helpers ──────────────────────────────────────────────


def sse_status(stage: str, message: str, detail: str | None = None) -> str:
    """构建 SSE status 事件的文本行。

    在流式响应中推送流水线阶段状态，让前端展示进度指示。
    使用 ensure_ascii=False 保证中文字符不被转义。

    Args:
        stage: 阶段标识，如 retrieving / reranking / generating
        message: 向用户展示的阶段描述文本
        detail: 可选详细说明

    Returns:
        格式为 "event: status\ndata: {...}\n\n" 的 SSE 文本行
    """
    data: dict[str, str] = {"stage": stage, "message": message}
    if detail:
        data["detail"] = detail
    return f"event: status\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_token(token: str) -> str:
    """构建 SSE token 事件的文本行。

    每次推送一个回答片段，前端将其追加到对话气泡中。
    使用 ensure_ascii=False 保证中文字符不被转义。

    Args:
        token: LLM 生成的文本片段

    Returns:
        格式为 "event: token\ndata: {"token":"..."}\n\n" 的 SSE 文本行
    """
    return f"event: token\ndata: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"


def sse_citation(
    source: str,
    page: int,
    snippet: str,
    score: float = 0.0,
    highlighted_snippet: str | None = None,
) -> str:
    """构建 SSE citation 事件的文本行。

    推送引用来源信息，支持高亮片段展示。
    每个文档仅推送一次（由调用方去重）。

    Args:
        source: 文档来源名称（文件名）
        page: 引用页码
        snippet: 引用内容摘要（前 200 字）
        score: Reranker 相似度分数（0~1）
        highlighted_snippet: 含 <mark> 标记的高亮 HTML 片段

    Returns:
        格式为 "event: citation\ndata: {...}\n\n" 的 SSE 文本行
    """
    data = {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": score,
        "highlighted_snippet": highlighted_snippet,
    }
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    """构建 SSE done 事件的文本行。

    标记流式响应结束，前端收到后关闭连接并恢复 UI 交互。

    Returns:
        格式为 "event: done\ndata: {}\n\n" 的 SSE 文本行
    """
    return "event: done\ndata: {}\n\n"


def sse_error(error: str) -> str:
    """构建 SSE error 事件的文本行。

    在 RAG 流水线发生异常时推送错误信息。
    前端收到后展示错误提示并关闭流。

    Args:
        error: 错误描述文本

    Returns:
        格式为 "event: error\ndata: {"error":"..."}\n\n" 的 SSE 文本行
    """
    return f"event: error\ndata: {json.dumps({'error': error}, ensure_ascii=False)}\n\n"


async def _stream_rag_response(
    kb_id: str,
    session_id: str,
    query: str,
) -> AsyncGenerator[str, None]:
    """以 SSE 事件流推送 RAG 响应：status → token → citation → done。

    Args:
        kb_id: 知识库 UUID（空字符串表示跨库搜索）
        session_id: 会话 ID，用于对话历史上下文
        query: 用户查询文本

    Yields:
        str: SSE 格式的文本行，依次为 status（检索/精排/生成阶段）、
        token（回答片段）、citation（引用来源）、done（流结束标记）
    """
    try:
        svc = _get_service()

        # 启动 Langfuse trace
        tracer = svc.rag_chain._tracer
        trace_id = tracer.start_trace(
            "chat_stream",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )

        # Stage 1 — search
        yield sse_status("retrieving", "正在检索相关文档...")
        results = await svc.rag_chain.search(query, kb_id)

        # Stage 2 — rerank
        yield sse_status("reranking", f"已找到 {len(results)} 个候选，正在精排...")
        contexts = svc.rag_chain.rerank(query, results)

        # Stage 3 — generate
        yield sse_status("generating", "正在生成回答...")
        full_answer = ""
        for token in svc.rag_chain.stream_answer(
            query, contexts, [], trace_id=trace_id
        ):
            full_answer += token
            yield sse_token(token)
            await asyncio.sleep(0)

        # 结束 Langfuse trace（在 citations 和持久化之前记录输出）
        tracer.end_trace(trace_id, output=full_answer)

        # Citations (deduplicated by source+page)
        seen: set[tuple[str, int]] = set()
        for ctx in contexts:
            key = (ctx.source, ctx.page)
            if key in seen:
                continue
            seen.add(key)

            # Query-biased snippet with highlights
            qbs = get_query_biased_snippet(query, ctx.content)
            highlighted = _build_highlighted_snippet(qbs)

            snippet = getattr(ctx, "parent_content", None) or ctx.content
            yield sse_citation(
                ctx.source,
                ctx.page,
                snippet[:200],
                ctx.score,
                highlighted_snippet=highlighted,
            )
            await asyncio.sleep(0)

        # Save assistant response to chat history (deduplicated)
        seen_src: set[str] = set()
        sources = []
        for c in contexts:
            s = f"{c.source} (第{c.page}页)"
            if s in seen_src:
                continue
            seen_src.add(s)
            sources.append(s)
        tu = getattr(svc.rag_chain, "last_token_usage", {})
        model_name = os.getenv("LLM_MODEL", "qwen-max")
        await svc.rag_chain.chat_manager.add_message_async(
            session_id,
            "assistant",
            full_answer,
            sources=sources,
            prompt_tokens=tu.get("prompt_tokens", 0),
            completion_tokens=tu.get("completion_tokens", 0),
            total_tokens=tu.get("total_tokens", 0),
            model_name=model_name,
        )

        # 同步等待 MySQL 持久化完成，确保 done 事件发出时数据已落盘
        # 这样前端 loadSessions() / switchSession() 拿到的消息数一定正确
        await _persist_conversation(svc, session_id, kb_id, query, full_answer, sources)

    except Exception as e:
        logger.error("Chat stream error: {}", str(e))
        yield sse_error(str(e))

    # Signal completion
    yield sse_done()


async def _persist_conversation(
    svc: AppService,
    session_id: str,
    kb_id: str,
    query: str,
    answer: str,
    sources: list[str],
) -> None:
    """异步持久化对话到 MySQL，带重试。

    在 SSE 流结束后非阻塞执行。
    如果 MySQL 不可用，重试 3 次后放弃（只记日志）。
    绝不会抛异常冒泡到 SSE 响应。

    Args:
        svc: AppService 实例
        session_id: 会话 ID
        kb_id: 知识库 UUID
        query: 用户查询文本
        answer: LLM 生成的完整回答
        sources: 引用来源列表（去重后的 "文件名 (第x页)" 列表）
    """
    svc.rag_chain.chat_manager.set_mysql_db(svc.db)

    # 创建会话（如首次消息）。title = 首条消息前 20 字
    title = query[:20]

    async def retry(factory, max_retries=3):
        for i in range(max_retries):
            try:
                await factory()
                return
            except Exception as e:
                if i < max_retries - 1:
                    await asyncio.sleep(0.5 * (i + 1))
                else:
                    logger.warning(
                        "Persist failed after {} retries: {}", max_retries, e
                    )

    await retry(
        lambda: svc.rag_chain.chat_manager.save_session_async(session_id, title, kb_id)
    )
    await retry(
        lambda: svc.rag_chain.chat_manager.save_messages_async(
            session_id, kb_id, query, answer, sources
        )
    )


@router.get("/chat/stream")
async def chat_stream(
    session_id: str = Query(..., description="Session ID for conversation history"),
    kb_id: str = Query(
        ..., description="Knowledge base ID (or empty for cross-KB search)"
    ),
    query: str = Query(..., description="User question"),
):
    """流式 RAG 问答端点 — 返回 SSE 事件流。

    Args:
        session_id: 会话 ID，用于关联对话历史
        kb_id: 知识库 UUID（空字符串表示跨库搜索）
        query: 用户问题文本

    Returns:
        StreamingResponse: SSE 流式响应，包含
        status / token / citation / error / done 事件

    Raises:
        HTTPException 422: 参数校验失败（FastAPI 自动处理）
    """
    return StreamingResponse(
        _stream_rag_response(kb_id, session_id, query),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证 SSE 实时推送
        },
    )
