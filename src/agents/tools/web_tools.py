"""Agent 工具 — search_web（Tavily 联网搜索兜底）。

独立模块承载 search_web，避免 rag_tools.py 超过 400 行红线。工具无闭包依赖
（不需 vector_store/bm25/reranker），直接读 config 与 current_request_ctx；
结果与 retrieve_kb 共用 RequestContext.tool_contexts（全局递增编号），
format_node 统一产出引用，kind=web 区分来源。
"""

import time

from langchain_core.tools import tool
from loguru import logger
from pydantic import BaseModel, Field

from src.config import settings
from src.config.const import (
    WEB_BODY_LIMIT,
    SSEInteractionTexts,
)
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.tavily_client import tavily_extract, tavily_search
from src.rag.context import RAGContext


class SearchWebArgs(BaseModel):
    """search_web 工具参数（LLM 可见的入参契约）。"""

    query: str = Field(description="搜索查询文本")
    top_k: int = Field(default=5, ge=1, le=10, description="返回结果条数上限")


@tool("search_web", args_schema=SearchWebArgs)
async def search_web(query: str, top_k: int = 5) -> str:
    """在互联网上搜索实时信息，返回带来源链接的网页摘要/正文。

    何时调用：retrieve_kb 检索结果为空或全部明显不相关，已确认问题不在
    当前知识库范围内时调用，用于补充知识库外的事实性信息。
    知识库能回答的问题不要调用本工具。

    Args:
        query: 搜索查询文本（简洁、含关键实体）
        top_k: 返回结果条数上限（默认 5，最多 10）

    Returns:
        带全局编号的网页块文本 "[n] 来源: url\\n内容: ..."；达限次/失败时返回提示或空串
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    if ctx.web_count >= settings.WEB_SEARCH_PER_TURN_LIMIT:
        logger.info(
            "tool=search_web limit reached session_id={} query={}",
            ctx.session_id,
            query[:40],
        )
        return SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT
    ctx.web_count += 1

    start = time.monotonic()
    results = await tavily_search(query, top_k=top_k, timeout=settings.TAVILY_TIMEOUT)
    if not results:
        logger.info(
            "tool=search_web query={} result_count=0 latency_ms={:.0f}",
            query[:40],
            (time.monotonic() - start) * 1000,
        )
        return ""

    # extract 拉取 top-1~2 正文，失败不影响已拿到的摘要
    bodies: dict[str, str] = {}
    extracted = await tavily_extract(
        [r["url"] for r in results[:2]], timeout=settings.TAVILY_TIMEOUT
    )
    for item in extracted:
        bodies[item["url"]] = item.get("content", "")[:WEB_BODY_LIMIT]

    collector = ctx.tool_contexts
    offset = len(collector)
    blocks = []
    for r in results:
        snippet = bodies.get(r["url"])
        if snippet is None:
            snippet = r.get("content", "")
        content = snippet[:WEB_BODY_LIMIT]
        if not content:
            continue
        # kind 字段（CITATION_KIND_WEB）由后续任务补入 RAGContext
        collector.append(
            RAGContext(
                content=content,
                source=r["url"],
                page=0,
                doc_id=r["url"],
                chunk_id=r["url"],
            )
        )
        blocks.append(f"[{offset + len(blocks) + 1}] 来源: {r['url']}\n内容: {content}")
    logger.info(
        "judge: query={} stage=web_confirm count={} result_count={} latency_ms={:.0f}",
        query[:40],
        ctx.web_count,
        len(blocks),
        (time.monotonic() - start) * 1000,
    )
    return "\n\n".join(blocks)
