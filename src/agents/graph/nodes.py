# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

import asyncio
import re
from typing import Any, Callable

from loguru import logger

from src.agents.grader import RetrievalGrader
from src.agents.graph.state import (
    DOWNGRADE_REASON_REWRITE_NO_INCREMENT,
    AgentState,
    LangGraphNode,
    RAGQueryIntent,
)
from src.config import LLM_MODEL, TOP_K_RETRIEVAL
from src.config.prompts import ABSTENTION_MARKERS, ABSTENTION_TEXT
from src.infra.db.vector_store.types import ChunkResult
from src.infra.search.query_router import QueryRouter
from src.rag.prompt import build_prompt, build_simple_prompt, format_context
from src.rag.retrieval import rerank_results, rewrite_query, search
from src.rag.stream import estimate_usage, stream_answer


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
        from src.infra.db.engine import session_factory
        from src.infra.db.mysql_db import KbRepo
        from src.infra.llm.trace_context import current_user_id

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
        logger.info(
            "classify_node done: route={}", result[LangGraphNode.Classify.INTENT].route
        )
        return {
            LangGraphNode.Classify.INTENT: result[LangGraphNode.Classify.INTENT],
            LangGraphNode.Classify.EXTRACTED_ENTITIES: result[
                LangGraphNode.Classify.EXTRACTED_ENTITIES
            ],
            LangGraphNode.Classify.MISSING_ENTITIES: result[
                LangGraphNode.Classify.MISSING_ENTITIES
            ],
            LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE: result[
                LangGraphNode.Classify.CLASSIFICATION_CONFIDENCE
            ],
            LangGraphNode.Classify.SKIP_RETRIEVAL: result.get(
                LangGraphNode.Classify.SKIP_RETRIEVAL, False
            ),
        }

    return classify_node


def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    query = state.query
    intent_route = state.intent.route or "medium"
    rewritten = rewrite_query(query, state._history or [], intent_route=intent_route)

    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)

    result: dict[str, Any] = {"rewritten_query": rewritten}
    if rewritten != query:
        result[LangGraphNode.Classify.INTENT] = RAGQueryIntent(
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
        # resolved_ids 是非空 list（单/多库）或空值；空值时跳过搜索直接返回空结果
        logger.info("retrieve_node start: query={} kb_ids={}", q[:50], resolved_ids)
        results: list[ChunkResult] = []
        # 多 KB 路由时走多库并行检索
        if isinstance(resolved_ids, list) and len(resolved_ids) > 1:
            results = await asyncio.to_thread(
                vector_store.similarity_search_multi,
                resolved_ids,
                q,
                TOP_K_RETRIEVAL,
            )
        elif isinstance(resolved_ids, list) and len(resolved_ids) == 1:
            # 单元素 list 需取出字符串，search() 的 kb_id 参数只接受 str | None
            kb_id = resolved_ids[0]
            results = await search(q, kb_id, vector_store, bm25)
        # 无 else：resolved_ids 为空（未解析出知识库）时不发起搜索，保持空结果

        logger.info("retrieve_node done: results={}", len(results))
        return {"retrieval_results": results}

    return retrieve_node


def grader_node(state: AgentState) -> dict:
    """质量评分节点：关键字覆盖度评分 + 重试计数管理。

    短路逻辑：rewrite 是纯规则函数，输入不变则改写结果必不变；
    若本轮改写查询与上一轮相同，重试必然复现上一轮检索结果，
    直接降级跳过无意义重试。
    """
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
    # 短路：本轮改写查询与上一轮相同 → rewrite 无信息增量，重试必复现失败
    if state._prev_rewritten_query and query == state._prev_rewritten_query:
        logger.info("grader_node: rewrite no-increment (query unchanged), downgrade")
        return {
            LangGraphNode.Grader.SCORE: score,
            LangGraphNode.Grader.RETRIEVAL_RETRIES: retries + 1,
            LangGraphNode.Grader.DOWNGRADED: True,
            LangGraphNode.Grader.DOWNGRADE_REASON: DOWNGRADE_REASON_REWRITE_NO_INCREMENT,
        }
    if retries < 2:
        return {
            LangGraphNode.Grader.SCORE: score,
            LangGraphNode.Grader.RETRIEVAL_RETRIES: retries + 1,
            LangGraphNode.Grader.PREV_REWRITTEN_QUERY: query,
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

        # ① 问候/闲聊：免检索直接回答（无视 contexts）
        if state.skip_retrieval:
            logger.info("generate_node: skip_retrieval, simple prompt")
            prompt = build_simple_prompt(query, state._history or [], prompt_manager)
            full_text = ""
            for token in stream_answer(prompt, llm, tid):
                full_text += token
            usage = estimate_usage(prompt, full_text)
            result: dict = {"answer": full_text, "_token_usage": usage}
            model_name = getattr(llm, "model", LLM_MODEL) or ""
            if model_name:
                result["model_used"] = model_name
            logger.info(
                "generate_node done (skip_retrieval): answer_len={} tokens={}",
                len(full_text),
                usage.total_tokens,
            )
            return result

        # ② 检索无达标 context：abstention 静态文案，不回 LLM
        if not contexts:
            logger.info("generate_node: empty contexts, abstention")
            usage = estimate_usage([], ABSTENTION_TEXT)
            logger.info(
                "generate_node done (abstention): answer_len={} tokens={}",
                len(ABSTENTION_TEXT),
                usage.total_tokens,
            )
            # abstention 未调用生成 LLM，但流程已用该模型实例，仍带出模型名供前端展示
            model_name = getattr(llm, "model", LLM_MODEL)
            if not isinstance(model_name, str):
                model_name = ""
            return {
                "answer": ABSTENTION_TEXT,
                "_token_usage": usage,
                "model_used": model_name,
                "is_fallback": False,
            }

        # ③ 正常 RAG 生成
        context_str = format_context(contexts)
        prompt = build_prompt(query, context_str, state._history or [], prompt_manager)
        full_text = ""
        for token in stream_answer(prompt, llm, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result = {"answer": full_text, "_token_usage": usage}
        model_name = getattr(llm, "model", LLM_MODEL) or ""
        if model_name:
            result["model_used"] = model_name
        logger.info(
            "generate_node done: answer_len={} tokens={}",
            len(full_text),
            usage.total_tokens,
        )
        return result

    return generate_node


def format_node(state: AgentState) -> dict:
    """格式化节点：只保留回答中实际引用的来源，去重并带原始编号。"""
    answer = state.answer or ""
    contexts = state.contexts or []

    # 拒答检测：回答明确表示未找到数据时，不输出引用
    if any(marker in answer for marker in ABSTENTION_MARKERS):
        logger.info("format_node: answer is abstention, citations=[]")
        return {"citations": []}

    # 提取回答中引用的编号 [n]，非法编号（超出 context 范围）忽略
    cited_numbers = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    valid_numbers = {n for n in cited_numbers if 1 <= n <= len(contexts)}
    if not valid_numbers:
        logger.info("format_node: no valid citation markers, citations=[]")
        return {"citations": []}

    # 按编号升序取对应 context，按 (source, page) 去重
    seen = set()
    citations = []
    for n in sorted(valid_numbers):
        ctx = contexts[n - 1]
        key = (ctx.source, ctx.page)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "index": n,
                "source": ctx.source,
                "page": ctx.page,
                "snippet": ctx.content[:200],
                "score": ctx.score,
            }
        )
    logger.info("format_node: citations={}", len(citations))
    return {"citations": citations}
