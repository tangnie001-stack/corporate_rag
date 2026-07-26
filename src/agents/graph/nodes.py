# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

from typing import Callable
from loguru import logger
from src.infra.search.query_router import QueryRouter
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.stream import stream_answer, estimate_usage
from src.rag.prompt import build_prompt, build_simple_prompt, format_context
from src.agents.grader import RetrievalGrader
from src.agents.graph.state import AgentState, RAGQueryIntent


def _tid(state: AgentState) -> str:
    return state.trace_id


def classify_node(state: AgentState) -> dict:
    """查询分类节点：基于 QueryRouter 输出三级路由。"""
    tid = _tid(state)
    logger.info("[{}] classify_node start: query={}", tid, state.query[:50])

    router = QueryRouter()
    raw_route = router.route(state.query)

    # 映射 vague → medium
    route_map = {
        "simple": "simple",
        "vague": "medium",
        "medium": "medium",
        "complex": "complex",
    }
    route = route_map.get(raw_route, "medium")

    logger.info("[{}] classify_node done: raw={} mapped={}", tid, raw_route, route)
    return {"intent": RAGQueryIntent(route=route, rewritten=False)}


def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    tid = _tid(state)
    query = state.query
    rewritten = rewrite_query(query, state._history or [])

    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)

    result = {"rewritten_query": rewritten}
    if rewritten != query:
        result["intent"] = RAGQueryIntent(
            route=state.intent.route or "medium",
            rewritten=True,
        )
        logger.info("[{}] rewrite_node: {} -> {}", tid, query[:30], rewritten[:30])
    else:
        logger.info("[{}] rewrite_node: no rewrite", tid)
    return result


def make_retrieve_node(vector_store, bm25) -> Callable:
    """创建检索节点工厂函数。"""

    async def retrieve_node(state: AgentState) -> dict:
        tid = _tid(state)
        q = state.rewritten_query or state.query
        kb_id = state.kb_id
        logger.info("[{}] retrieve_node start: query={} kb_id={}", tid, q[:50], kb_id)
        results = await search(q, kb_id, vector_store, bm25)
        if results is None:
            results = []
            logger.warning(
                "[{}] retrieve_node: search returned None, using empty list", tid
            )
        logger.info("[{}] retrieve_node done: results={}", tid, len(results))
        return {"retrieval_results": results}

    return retrieve_node


def grader_node(state: AgentState) -> dict:
    """质量评分节点：关键字覆盖度评分 + 重试计数管理。"""
    tid = _tid(state)
    query = state.rewritten_query or state.query
    results = state.retrieval_results or []
    grader = RetrievalGrader()
    score = grader.grade(query, results, results)
    retries = state.retrieval_retries
    logger.info("[{}] grader_node: score={:.2f} retries={}", tid, score, retries)

    retries = state.retrieval_retries  # noqa: PLW2901 — 从 state 刷新，后续逻辑用
    if score is not None and score >= 0.5:
        return {"grader_score": score, "retrieval_retries": 0}
    if retries < 2:
        return {"grader_score": score, "retrieval_retries": retries + 1}
    # 重试用尽，降级到 Enhanced RAG
    return {
        "grader_score": score,
        "retrieval_retries": retries + 1,
        "downgraded": True,
        "downgrade_reason": "grader_retries_exhausted",
    }


def make_rerank_node(reranker) -> Callable:
    """创建精排节点工厂函数。"""

    def rerank_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.rewritten_query or state.query
        results = state.retrieval_results or []
        if not results:
            return {"contexts": []}
        contexts = rerank_results(query, results, reranker)
        # 直接存 RAGContext 列表，不做 dict 转换
        logger.info("[{}] rerank_node: contexts={}", tid, len(contexts))
        return {"contexts": contexts}

    return rerank_node


def make_generate_node(llm, prompt_manager, tracer) -> Callable:
    """创建生成节点工厂函数。"""

    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.rewritten_query or state.query
        contexts = state.contexts or []

        if not contexts:
            # 降级到 Naive RAG
            logger.info("[{}] generate_node: empty contexts, Naive RAG fallback", tid)
            prompt = build_simple_prompt(query, state._history or [], prompt_manager)
        else:
            # contexts 已经是 list[RAGContext]，不需要转换
            context_str = format_context(contexts)
            prompt = build_prompt(
                query, context_str, state._history or [], prompt_manager
            )

        # 收集所有 token，组装完整文本
        full_text = ""
        for token in stream_answer(prompt, llm, tracer, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result = {"answer": full_text, "_token_usage": usage}
        if not contexts:
            result["downgraded"] = True
            result["downgrade_reason"] = "rerank_empty"
        logger.info(
            "[{}] generate_node done: answer_len={} tokens={}",
            tid,
            len(full_text),
            usage.total_tokens,
        )
        return result

    return generate_node


def format_node(state: AgentState) -> dict:
    """格式化节点：去重后的引用列表。"""
    tid = _tid(state)
    contexts = state.contexts or []
    seen = set()
    citations = []
    for ctx in contexts:
        key = (ctx.source, ctx.page)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "source": ctx.source,
                "page": ctx.page,
                "snippet": ctx.content[:200],
                "score": ctx.score,
            }
        )
    logger.info("[{}] format_node: citations={}", tid, len(citations))
    return {"citations": citations}
