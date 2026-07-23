"""检索与查询改写 — 向量检索、Reranker 精排、查询分类与改写。"""

import asyncio
import re
from typing import Optional

from loguru import logger

from src.config import TOP_K_RETRIEVAL, TOP_K_RERANK, HYBRID_SEARCH_ENABLED
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.infra.db.vector_store import VectorStore
from src.models import with_retry
from src.config import (
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_INTERVAL,
    RETRY_BACKOFF_FACTOR,
)
from src.rag.context import RAGContext


async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
    bm25: Optional[BM25Index] = None,
) -> list[dict]:
    """执行语义检索（混合模式可选）。"""
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        dense_t = asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
        )
        bm25_t = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_t, bm25_t)
        results = rrf_fusion(d, b)
        logger.info(
            "RAG search: kb_id={} query_len={} results={} mode=hybrid",
            kb_id,
            len(query),
            len(results),
        )
        return results

    if not kb_id:
        results = await asyncio.to_thread(
            vector_store.similarity_search_all, query, k=TOP_K_RETRIEVAL
        )
    else:
        results = await asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
        )
    logger.info(
        "RAG search: kb_id={} query_len={} results={} mode=dense",
        kb_id,
        len(query),
        len(results),
    )
    return results


def rerank_results(
    query: str,
    results: list[dict],
    reranker,
) -> list[RAGContext]:
    """Reranker 精排，返回 top-N 的 RAGContext 列表。"""
    if not results:
        return []

    docs = [r["content"] for r in results]
    try:
        reranked = with_retry(
            reranker.rerank,
            max_attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )(query, docs)
    except Exception as e:
        logger.warning(
            "Rerank failed after {} attempts (using raw order): {}",
            RETRY_MAX_ATTEMPTS,
            e,
        )
        reranked = [
            {"index": i, "relevance_score": r.get("distance", 0)}
            for i, r in enumerate(results)
        ]

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        idx = item["index"]
        r = results[idx]
        metadata = r.get("metadata", {})
        pc = metadata.get("parent_content")
        score = item.get("relevance_score", 0)
        contexts.append(
            RAGContext(
                content=pc if pc else r["content"],
                source=metadata.get("source", ""),
                page=metadata.get("page", 0),
                doc_id=metadata.get("doc_id", ""),
                chunk_id=r["id"],
                parent_content=pc,
                score=score,
            )
        )
    if contexts:
        logger.info(
            "Rerank completed: {} -> {} contexts, top_score={:.4f}",
            len(results),
            len(contexts),
            contexts[0].score,
        )
    return contexts


# ═══════════════════ 查询改写 ═══════════════════


def classify_query(query: str) -> str:
    """对用户查询进行三级分类。

    Returns:
        "simple":  单事实检索（如数字、年份+指标），走 Naive RAG
        "medium":  需要 2-3 个事实关联或分析，走 Enhanced RAG
        "complex": 多跳推理、跨文档对比，走 Agentic RAG
    """
    cleaned = query.strip()
    if not cleaned:
        return "simple"

    # 对比/比较类 → complex
    if any(w in cleaned for w in ["对比", "比较", "差异", "versus", "vs"]):
        return "complex"

    # 分析/推理类 → medium (need retrieval but not complex enough for grader)
    if any(w in cleaned for w in ["分析", "解释", "说明", "为什么", "原因"]):
        return "medium"

    # 模糊短查询 → medium (needs context expansion)
    if len(cleaned) < 10:
        return "medium"

    # 单事实数字查询 → simple
    if re.search(r"\d{4}年", cleaned) or re.search(r"(营收|利润|收入|成本|资产|负债|现金流|净利润)", cleaned):
        return "simple"

    return "medium"  # default fallback


def expand_query(query: str, history: list[dict]) -> str:
    """对模糊短查询进行扩展。"""
    if not history:
        return query
    for msg in reversed(history):
        if msg.get("role") == "user" and msg["content"] != query:
            return f"{msg['content']} {query}"
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


def rewrite_query(query: str, history: list[dict]) -> str | list[str]:
    """根据三级分类执行相应的改写策略。

    Returns:
        str:      simple / medium 路径返回改写后的单条查询
        list[str]: complex 路径返回分解后的多条子查询
    """
    t = classify_query(query)
    if t == "simple":
        return query
    if t == "medium":
        # 模糊短查询 → 用历史上下文扩展；口语化查询 → 精简
        if len(query.strip()) < 10:
            return expand_query(query, history)
        if any(w in query for w in ["分析", "解释", "说明", "为什么"]):
            return condense_query(query)
        return query
    if t == "complex":
        return decompose_query(query)
    return query
