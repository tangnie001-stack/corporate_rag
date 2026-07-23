# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

import time
import asyncio
from typing import Any, Callable
from loguru import logger
from src.config import TOP_K_RERANK
from src.infra.search.query_router import QueryRouter
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.stream import stream_answer, estimate_usage
from src.rag.prompt import build_prompt, build_simple_prompt, format_context
from src.agents.grader import RetrievalGrader
from src.agents.graph.state import AgentState


def _tid(state: AgentState) -> str:
    return state.get("trace_id", "unknown")


def classify_node(state: AgentState) -> dict:
    """查询分类节点：基于 QueryRouter 输出三级路由。"""
    tid = _tid(state)
    logger.info("[{}] classify_node start: query={}", tid, state.get("query", "")[:50])

    router = QueryRouter()
    raw_route = router.route(state.get("query", ""))

    # 映射 vague → medium
    route_map = {"simple": "simple", "vague": "medium", "medium": "medium", "complex": "complex"}
    route = route_map.get(raw_route, "medium")

    logger.info("[{}] classify_node done: raw={} mapped={}", tid, raw_route, route)
    return {"intent": {"route": route, "rewritten": False}}


def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    tid = _tid(state)
    query = state.get("query", "")
    rewritten = rewrite_query(query, state.get("_history", []))

    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)

    result = {"rewritten_query": rewritten}
    if rewritten != query:
        result["intent"] = {"route": state.get("intent", {}).get("route", "medium"), "rewritten": True}
        logger.info("[{}] rewrite_node: {} -> {}", tid, query[:30], rewritten[:30])
    else:
        logger.info("[{}] rewrite_node: no rewrite", tid)
    return result


def make_retrieve_node(vector_store, bm25) -> Callable:
    """创建检索节点工厂函数。"""
    async def _search(query, kb_id):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(search(query, kb_id, vector_store, bm25))
        finally:
            loop.close()

    def retrieve_node(state: AgentState) -> dict:
        tid = _tid(state)
        q = state.get("rewritten_query") or state.get("query", "")
        kb_id = state.get("kb_id", "")
        logger.info("[{}] retrieve_node start: query={} kb_id={}", tid, q[:50], kb_id)
        results = _search(q, kb_id)
        logger.info("[{}] retrieve_node done: results={}", tid, len(results))
        return {"retrieval_results": results}
    return retrieve_node


def grader_node(state: AgentState) -> dict:
    """质量评分节点：关键字覆盖度评分。"""
    tid = _tid(state)
    query = state.get("rewritten_query") or state.get("query", "")
    results = state.get("retrieval_results", [])
    grader = RetrievalGrader()
    score = grader.grade(query, results, results)
    logger.info("[{}] grader_node: score={:.2f}", tid, score)
    return {"grader_score": score}


def make_rerank_node(reranker) -> Callable:
    """创建精排节点工厂函数。"""
    def rerank_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.get("rewritten_query") or state.get("query", "")
        results = state.get("retrieval_results", [])
        if not results:
            return {"contexts": []}
        contexts = rerank_results(query, results, reranker)
        ctx_list = [
            {"content": c.content, "source": c.source, "page": c.page,
             "doc_id": c.doc_id, "chunk_id": c.chunk_id, "score": c.score}
            for c in contexts
        ]
        logger.info("[{}] rerank_node: contexts={}", tid, len(ctx_list))
        return {"contexts": ctx_list}
    return rerank_node


def make_generate_node(llm, prompt_manager, tracer) -> Callable:
    """创建生成节点工厂函数。"""
    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.get("rewritten_query") or state.get("query", "")
        contexts = state.get("contexts", [])
        downgraded = state.get("downgraded", False)

        if not contexts:
            # 降级到 Naive RAG
            logger.info("[{}] generate_node: empty contexts, Naive RAG fallback", tid)
            prompt = build_simple_prompt(query, state.get("_history", []), prompt_manager)
        else:
            # TypedDict → RAGContext 转换
            from src.rag.context import RAGContext
            rag_ctx_list = [RAGContext(**c) for c in contexts]
            context_str = format_context(rag_ctx_list)
            prompt = build_prompt(query, context_str, state.get("_history", []), prompt_manager)

        # 收集所有 token，组装完整文本
        full_text = ""
        for token in stream_answer(prompt, llm, tracer, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result = {"answer": full_text, "_token_usage": usage}
        if not contexts:
            result["downgraded"] = True
            result["downgrade_reason"] = "rerank_empty"
        logger.info("[{}] generate_node done: answer_len={} tokens={}",
                    tid, len(full_text), usage.get("total", 0))
        return result
    return generate_node


def format_node(state: AgentState) -> dict:
    """格式化节点：去重后的引用列表。"""
    tid = _tid(state)
    contexts = state.get("contexts", [])
    seen = set()
    citations = []
    for ctx in contexts:
        key = (ctx.get("source", ""), ctx.get("page", 0))
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "source": ctx.get("source", ""),
            "page": ctx.get("page", 0),
            "snippet": ctx.get("content", "")[:200],
            "score": ctx.get("score", 0),
        })
    logger.info("[{}] format_node: citations={}", tid, len(citations))
    return {"citations": citations}
