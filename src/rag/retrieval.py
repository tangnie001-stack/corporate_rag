"""检索与查询改写 — 向量检索、Reranker 精排、查询分类与改写。"""

import asyncio

from src.config import (
    HYBRID_SEARCH_ENABLED,
    RETRY_BACKOFF_FACTOR,
    RETRY_INITIAL_INTERVAL,
    RETRY_MAX_ATTEMPTS,
    TOP_K_RERANK,
    TOP_K_RETRIEVAL,
    settings,
)
from src.config.const import (
    ENTITY_OPTIONAL_TYPES,
    ENTITY_TYPES,
    SSEInteractionTexts,
    resolve_source_tier,
)
from src.core.log_events import Event
from src.core.logging import log_event
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.chat_message import ChatMessage
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.models import with_retry
from src.rag.context import RAGContext

# 全部实体键（核心 + 可选），rerank 透传时从 chunk.metadata 读取
_ALL_ENTITY_KEYS: tuple[str, ...] = tuple(ENTITY_TYPES) + tuple(ENTITY_OPTIONAL_TYPES)


def _dedup_by_doc_id(
    results: list[ChunkResult], max_per_doc: int | None = None
) -> list[ChunkResult]:
    """按 doc_id 去重检索结果，每个文档最多保留 max_per_doc 条。

    默认（None）读 settings.RETRIEVAL_MAX_PER_DOC（=1 保持现状）；
    无 doc_id 的项按自身保留，不计入配额。

    Args:
        results: 检索结果列表（RRF 融合后）
        max_per_doc: 每文档保留条数上限，None 时读 settings

    Returns:
        去重后的结果列表
    """
    if max_per_doc is None:
        max_per_doc = settings.RETRIEVAL_MAX_PER_DOC
    seen_count: dict[str, int] = {}
    deduped: list[ChunkResult] = []
    for r in results:
        doc_id = r.metadata.get("doc_id")
        if doc_id is None:
            deduped.append(r)
            continue
        n = seen_count.get(doc_id, 0)
        if n >= max_per_doc:
            continue
        seen_count[doc_id] = n + 1
        deduped.append(r)
    return deduped


async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
    bm25: BM25Index | None = None,
) -> list[ChunkResult]:
    """执行语义检索（混合模式可选）。

    Args:
        query: 用户查询文本
        kb_id: 知识库 ID（调用方保证非空：`rag_tools.py` 在 kb_id 为空时直接返回空结果）
        vector_store: 向量数据库实例
        bm25: BM25 ���法检索引擎实例，启用混合检索时传入

    Returns:
        检索结果列表，按相关性降序排列；混合模式为 RRF 融合结果
    """
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        dense_t = asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
        )
        bm25_t = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_t, bm25_t)
        results = rrf_fusion(d or [], b or [])
        log_event(
            Event.HYBRID_DONE,
            kb_id=kb_id,
            query_len=len(query),
            result_count=len(results),
        )
        results = _dedup_by_doc_id(results)
        return results

    results = await asyncio.to_thread(
        vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
    )
    if results:
        result_count = len(results)
    else:
        result_count = 0
    log_event(
        Event.SEARCH_DONE,
        kb_id=kb_id,
        query_len=len(query),
        result_count=result_count,
    )
    results = _dedup_by_doc_id(results or [])
    return results


def rerank_results(
    query: str,
    results: list[ChunkResult],
    reranker,
) -> list[RAGContext]:
    """Reranker 精排，返回 top-N 的 RAGContext 列表。

    Args:
        query: 用户原始查询（用于 reranker 的相关性计算）
        results: 检索结果列表（已融合 Dense + BM25）
        reranker: Reranker 模型实例

    Returns:
        精排后的 RAGContext 列表，按相关性降序排列，长度不超过 TOP_K_RERANK；
        取前 TOP_K_RERANK 条相对结果，不应用绝对分数阈值过滤
    """
    if not results:
        log_event(Event.RERANK_SKIP, reason="empty_input")
        return []

    docs = [r.content for r in results]
    try:
        reranked = with_retry(
            reranker.rerank,
            max_attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )(docs, query)
    except Exception as e:  # noqa: BLE001
        log_event(
            Event.RERANK_FAILED,
            attempts=RETRY_MAX_ATTEMPTS,
            query=query,
            err=str(e),
        )
        reranked = []
        for i, r in enumerate(results):
            if r.distance is not None:
                fallback_score = 1 - r.distance
            else:
                fallback_score = 0
            reranked.append({"index": i, "relevance_score": fallback_score})

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        idx = item["index"]
        r = results[idx]
        score = item.get("relevance_score", 0)
        pc = r.metadata.get("parent_content")
        if pc:
            content = pc
        else:
            content = r.content
        contexts.append(
            RAGContext(
                content=content,
                source=r.metadata.get("source", ""),
                page=r.metadata.get("page", 0),
                doc_id=r.metadata.get("doc_id", ""),
                chunk_id=r.id,
                parent_content=pc,
                score=score,
                tier=resolve_source_tier(
                    r.metadata.get("source", ""),
                    SSEInteractionTexts.CITATION_KIND_KB,
                ),
                entities={
                    k: r.metadata.get(k) for k in _ALL_ENTITY_KEYS if r.metadata.get(k)
                },
            )
        )
    if contexts:
        log_event(
            Event.RERANK_DONE,
            doc_count=len(results),
            query_len=len(query),
        )
    return contexts


def expand_query(query: str, history: list[ChatMessage]) -> str:
    """对模糊短查询进行扩展。"""
    if not history:
        return query
    for msg in reversed(history):
        if msg.role == "user" and msg.content != query:
            return f"{msg.content} {query}"
    return query


def condense_query(query: str) -> str:
    """将口语化查询精简。"""
    condense_patterns = ["分析", "解释", "说明", "为什么"]
    cleaned = query
    for pat in condense_patterns:
        cleaned = cleaned.replace(pat, "").strip()
    return cleaned if cleaned else query


def decompose_query(query: str) -> list[str]:
    """将对比类查询分解为子查询。"""
    separators = ["对比", "比较", "差异", "versus", "vs", "和", "与"]
    parts = [query]
    for sep in separators:
        new_parts = []
        for p in parts:
            new_parts.extend(p.split(sep))
        parts = [p.strip() for p in new_parts if p.strip()]
    return [p for p in parts if p]


def rewrite_query(
    query: str,
    history: list[ChatMessage],
    intent_route: str = "medium",
) -> str | list[str]:
    """根据三级分类执行相应的改写策略。

    Args:
        query: 用户原始查询
        history: 对话历史
        intent_route: 意图路由，由上游 classify_node 提供

    Returns:
        str:      simple / medium 路径返回改写后的单条查询
        list[str]: complex 路径返回分解后的多条子查询
    """
    if intent_route == "simple":
        return query
    if intent_route == "complex":
        return decompose_query(query)
    # medium
    if len(query.strip()) < 10:
        return expand_query(query, history)
    if any(w in query for w in ["分析", "解释", "说明", "为什么"]):
        return condense_query(query)
    return query
