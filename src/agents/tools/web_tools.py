"""Agent 工具 — search_web（Tavily 联网搜索兜底）。

独立模块承载 search_web，避免 rag_tools.py 超过 400 行红线。工具无闭包依赖
（不需 vector_store/bm25/reranker），直接读 config 与 current_request_ctx；
结果与 retrieve_kb 共用 RequestContext.tool_contexts（全局递增编号），
format_node 统一产出引用，kind=web 区分来源。
"""

import asyncio
import time

from langchain_core.tools import tool
from loguru import logger
from pydantic import BaseModel, Field

from src.config import settings
from src.config.const import (
    WEB_BODY_LIMIT,
    SSEInteractionTexts,
)
from src.core.logging import log_event
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.tavily_client import tavily_extract, tavily_search
from src.rag.context import RAGContext


class SearchWebArgs(BaseModel):
    """search_web 工具参数（多查询：一次调用覆盖多个搜索目标）。"""

    queries: list[str] = Field(
        description="搜索查询列表（最多 4 个），一次调用并行搜索并合并结果"
    )
    top_k: int = Field(default=5, ge=1, le=10, description="每个查询返回结果条数上限")


@tool("search_web", args_schema=SearchWebArgs)
async def search_web(queries: list[str], top_k: int = 5) -> str:
    """在互联网上并行搜索实时信息，返回带来源链接的网页摘要/正文。

    何时调用：retrieve_kb 检索结果为空或全部明显不相关，已确认问题不在
    当前知识库范围内时调用，用于补充知识库外的事实性信息。
    知识库能回答的问题不要调用本工具。
    一次调用可传多个查询（queries，最多 4 个）覆盖多个独立搜索目标，
    各查询并行搜索后按查询顺序合并统一编号，仅占用 1 次联网搜索额度。

    Args:
        queries: 搜索查询列表（最多 4 个），每个查询简洁、含关键实体
        top_k: 每个查询返回结果条数上限（默认 5，最多 10）

    Returns:
        带全局编号的网页块文本 "[n] 来源: url\\n内容: ..."；达限次/失败时返回提示或空串
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    # 过滤空串查询（LLM 可能输出空字符串），只保留有效 query
    queries = [q for q in queries[:4] if q and q.strip()]
    if not queries:
        return ""
    if ctx.web_count >= settings.WEB_SEARCH_PER_TURN_LIMIT:
        logger.debug(
            "[retrieval] search_web limit reached session_id={} queries={}",
            ctx.session_id,
            queries,
        )
        return SSEInteractionTexts.WEB_SEARCH_LIMIT_TEXT
    ctx.web_count += 1  # 一次多查询调用只占 1 次额度

    start = time.monotonic()
    results_list = await asyncio.gather(
        *[
            tavily_search(q, top_k=top_k, timeout=settings.TAVILY_TIMEOUT)
            for q in queries
        ],
        return_exceptions=True,
    )
    # 单 query 失败不连坐全部：过滤异常项，保留成功项继续合并
    successful_results: list[list[dict]] = []
    failed_count = 0
    for r in results_list:
        # gather(return_exceptions=True) 捕获 BaseException（含 KeyboardInterrupt 等），
        # 按 BaseException 判断才能完整排除异常项
        if isinstance(r, BaseException):
            failed_count += 1
        else:
            successful_results.append(r)
    if failed_count:
        logger.warning(
            "[retrieval] search_web partial_failed session_id={} failed={}/{}",
            ctx.session_id,
            failed_count,
            len(results_list),
        )
    if not any(successful_results):
        logger.warning(
            "[retrieval] search_web all_failed session_id={} queries={}",
            ctx.session_id,
            queries[:3],
        )
        return ""

    # 每个 query 取其 top-1~2 拉正文（保持单查询语义、保证各查询覆盖），
    # 合并为一次 extract 调用，失败降级跳过正文抽取（保留已拿到的摘要）
    extract_urls = [r["url"] for q_results in successful_results for r in q_results[:2]]
    bodies: dict[str, str] = {}
    try:
        extracted = await tavily_extract(extract_urls, timeout=settings.TAVILY_TIMEOUT)
        for item in extracted:
            bodies[item["url"]] = item.get("content", "")[:WEB_BODY_LIMIT]
    except Exception as exc:  # noqa: BLE001  # extract 失败不阻断合并结果，降级用搜索摘要兜底
        logger.warning(
            "tool=search_web extract failed urls={} session_id={} err={}",
            extract_urls,
            ctx.session_id,
            exc,
        )

    results = [r for q_results in successful_results for r in q_results]
    collector = ctx.tool_contexts
    offset = len(collector)
    blocks = []
    for r in results:
        snippet = bodies.get(r["url"])
        if not snippet:
            snippet = r.get("content", "")
        content = snippet[:WEB_BODY_LIMIT]
        if not content:
            continue
        # 带 kind=web 标记来源类型，format_node 据此保留 web 兜底引用
        collector.append(
            RAGContext(
                content=content,
                source=r["url"],
                page=0,
                doc_id=r["url"],
                chunk_id=r["url"],
                kind=SSEInteractionTexts.CITATION_KIND_WEB,
            )
        )
        blocks.append(f"[{offset + len(blocks) + 1}] 来源: {r['url']}\n内容: {content}")
    log_event(
        "retrieval",
        "search_web done",
        session_id=ctx.session_id,
        query_count=len(queries),
        result_count=len(blocks),
        latency_ms=f"{(time.monotonic() - start) * 1000:.0f}",
    )
    return "\n\n".join(blocks)
