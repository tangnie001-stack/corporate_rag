# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

import asyncio
from typing import Callable
from loguru import logger
from src.infra.search.query_router import QueryRouter
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.stream import stream_answer, estimate_usage
from src.rag.prompt import build_prompt, build_simple_prompt, format_context
from src.agents.grader import RetrievalGrader
from src.agents.graph.state import AgentState, RAGQueryIntent
from src.config.const import LangGraphNode
from src.config import TOP_K_RETRIEVAL, LLM_MODEL


def _tid(state: AgentState) -> str:
    return state.trace_id


def make_kb_router_node(embed_fn, llm) -> Callable:
    """创建 KB 路由节点工厂函数。

    当 kb_id 为空（"所有知识库"）时，使用 KBRouter 智能匹配 KB。
    当 kb_id 非空时直接穿透。
    """
    from src.rag.kb_router import KBRouter

    router = KBRouter(embed_fn, llm)

    async def kb_router_node(state: AgentState) -> dict:
        # kb_id 非空 → 穿透
        if state.kb_id:
            return {"_resolved_kb_ids": [state.kb_id]}

        # kb_id 为空 → 路由
        from src.infra.llm.trace_context import current_user_id
        from src.infra.db.mysql_db import KbRepo
        from src.infra.db.engine import session_factory

        uid = current_user_id.get()
        if not uid:
            logger.info("kb_router_node: no user_id, fallback to all")
            return {"_resolved_kb_ids": None}

        kbs = await KbRepo(session_factory).get_all_kb(uid)
        kb_ids = router.route(state.query, kbs)
        logger.info(
            "kb_router_node: query={} kb_count={} routed={}",
            state.query[:40],
            len(kbs),
            kb_ids,
        )
        return {"_resolved_kb_ids": kb_ids if kb_ids else None}

    return kb_router_node


def make_classify_node(llm) -> Callable:
    """创建 classify_node 工厂函数。"""

    async def classify_node(state: AgentState) -> dict:
        """查询分类节点：基于 QueryRouter 输出三级路由 + 缺失实体。"""
        logger.info("classify_node start: query={}", state.query[:50])
        router = QueryRouter(llm=llm)
        result = router.route(state.query, state._history)
        logger.info("classify_node done: route={}", result["intent"].route)
        return {
            "intent": result["intent"],
            "extracted_entities": result["extracted_entities"],
            "missing_entities": result["missing_entities"],
            "classification_confidence": result["classification_confidence"],
        }

    return classify_node


def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    query = state.query
    intent_route = state.intent.route or "medium"
    rewritten = rewrite_query(query, state._history or [], intent_route=intent_route)

    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)

    result = {"rewritten_query": rewritten}
    if rewritten != query:
        result["intent"] = RAGQueryIntent(
            route=state.intent.route or "medium",
            rewritten=True,
        )
        logger.info("rewrite_node: {} -> {}", query[:30], rewritten[:30])
    else:
        logger.info("rewrite_node: no rewrite")
    return result


def make_retrieve_node(vector_store, bm25) -> Callable:
    """创建检索节点工厂函数。"""

    async def retrieve_node(state: AgentState) -> dict:
        q = state.rewritten_query or state.query
        resolved_ids = state._resolved_kb_ids or state.kb_id
        # resolved_ids 可以是 str | list[str] | None
        # None → retrieval.py 中 get_all_kb 全量搜索
        logger.info("retrieve_node start: query={} kb_ids={}", q[:50], resolved_ids)

        # 多 KB 路由时走多库并行检索
        if isinstance(resolved_ids, list) and len(resolved_ids) > 1:
            results = await asyncio.to_thread(
                vector_store.similarity_search_multi,
                resolved_ids,
                q,
                TOP_K_RETRIEVAL,
            )
        else:
            results = await search(q, resolved_ids, vector_store, bm25)

        if results is None:
            results = []
        logger.info("retrieve_node done: results={}", len(results))
        return {"retrieval_results": results}

    return retrieve_node


def grader_node(state: AgentState) -> dict:
    """质量评分节点：关键字覆盖度评分 + 重试计数管理。"""
    query = state.rewritten_query or state.query
    results = state.retrieval_results or []
    grader = RetrievalGrader()
    score = grader.grade(query, results, results)
    retries = state.retrieval_retries
    logger.info("grader_node: score={:.2f} retries={}", score, retries)

    retries = state.retrieval_retries  # noqa: PLW2901 — 从 state 刷新，后续逻辑用
    if score is not None and score >= 0.5:
        return {
            LangGraphNode.Grader.SCORE: score,
            LangGraphNode.Grader.RETRIEVAL_RETRIES: 0,
        }
    if retries < 2:
        return {
            LangGraphNode.Grader.SCORE: score,
            LangGraphNode.Grader.RETRIEVAL_RETRIES: retries + 1,
        }
    # 重试用尽，降级到 Enhanced RAG
    return {
        LangGraphNode.Grader.SCORE: score,
        LangGraphNode.Grader.RETRIEVAL_RETRIES: retries + 1,
        LangGraphNode.Grader.DOWNGRADED: True,
        LangGraphNode.Grader.DOWNGRADE_REASON: "grader_retries_exhausted",
    }


def make_rerank_node(reranker) -> Callable:
    """创建精排节点工厂函数。"""

    def rerank_node(state: AgentState) -> dict:
        query = state.rewritten_query or state.query
        results = state.retrieval_results or []
        if not results:
            return {LangGraphNode.Rerank.CONTEXTS: []}
        contexts = rerank_results(query, results, reranker)
        # 直接存 RAGContext 列表，不做 dict 转换
        logger.info("rerank_node: contexts={}", len(contexts))
        return {LangGraphNode.Rerank.CONTEXTS: contexts}

    return rerank_node


def make_generate_node(llm, prompt_manager) -> Callable:
    """创建生成节点工厂函数。"""

    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.rewritten_query or state.query
        contexts = state.contexts or []

        if not contexts:
            # 降级到 Naive RAG
            logger.info("generate_node: empty contexts, Naive RAG fallback")
            prompt = build_simple_prompt(query, state._history or [], prompt_manager)
        else:
            # contexts 已经是 list[RAGContext]，不需要转换
            context_str = format_context(contexts)
            prompt = build_prompt(
                query, context_str, state._history or [], prompt_manager
            )

        # 收集所有 token，组装完整文本
        full_text = ""
        for token in stream_answer(prompt, llm, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result: dict = {"answer": full_text, "_token_usage": usage}
        # 记录使用的模型名（从 llm 配置取，LiteLLM fallback 在代理层透明处理）
        model_name = getattr(llm, "model", LLM_MODEL) or ""
        if model_name:
            result["model_used"] = model_name
        if not contexts:
            result["downgraded"] = True
            result["downgrade_reason"] = "rerank_empty"
        logger.info(
            "generate_node done: answer_len={} tokens={}",
            len(full_text),
            usage.total_tokens,
        )
        return result

    return generate_node


def format_node(state: AgentState) -> dict:
    """格式化节点：去重后的引用列表。"""
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
    logger.info("format_node: citations={}", len(citations))
    return {"citations": citations}
