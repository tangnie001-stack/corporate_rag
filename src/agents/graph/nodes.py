# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

import asyncio
import re
from collections.abc import Callable
from typing import Any

from loguru import logger

from src.agents.graph.state import (
    AgentState,
    LangGraphNode,
    RAGQueryIntent,
)
from src.config import LLM_MODEL, TOP_K_RETRIEVAL
from src.config.prompts import ABSTENTION_MARKERS, ABSTENTION_TEXT
from src.infra.db.vector_store.types import ChunkResult
from src.infra.search.query_router import (
    KbEntityAggregate,
    QueryRouter,
    aggregate_kb_entities,
    needs_kb_entities,
)
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
        """查询分类节点：基于 QueryRouter 输出三级路由 + 缺失实体。

        仅当查询可能走 LLM classify / clarify 时聚合 KB 候选实体
        （kb_router 节点已填充 _resolved_kb_ids），注入 classifier prompt，
        并把聚合结果透传进 state 供 clarify 建议使用。
        """
        logger.info("classify_node start: query={}", state.query[:50])
        router = QueryRouter(llm=llm)
        if needs_kb_entities(state.query):
            kb_aggregate = await aggregate_kb_entities(state._resolved_kb_ids)
        else:
            # 问候/空/超短查询会在 route() 中短路，用不到 KB 候选，跳过聚合扫库
            kb_aggregate = KbEntityAggregate.empty()
        result = router.route(
            state.query, state._history, kb_entities=kb_aggregate.text
        )
        logger.info(
            "classify_node done: route={}",
            result[LangGraphNode.Classify.INTENT].route,
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
            LangGraphNode.Classify.KB_ENTITIES: kb_aggregate.text,
            LangGraphNode.Classify.KB_SUGGESTIONS: kb_aggregate.to_suggestions(),
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


def make_rerank_node(reranker) -> Callable:
    """创建精排节点工厂函数。"""

    def rerank_node(state: AgentState) -> dict:
        results = state.retrieval_results or []
        if not results:
            return {LangGraphNode.Rerank.CONTEXTS: []}
        contexts = []
        route = state.intent.route or "medium"
        if route == "complex" and state.rewritten_queries:
            # complex：逐子查询打分，各取 top 后按 chunk_id 去重合并
            merged = []
            seen = set()
            for sub in state.rewritten_queries:
                for ctx in rerank_results(sub, results, reranker):
                    if ctx.chunk_id in seen:
                        continue
                    seen.add(ctx.chunk_id)
                    merged.append(ctx)
            contexts = merged
        else:
            # medium：统一用原始 query 对合并候选池打一次分
            contexts = rerank_results(state.query, results, reranker)
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

        # ② 检索无结果：abstention 静态文案（检索空才静态，非空由 LLM 判断）
        if not (state.retrieval_results or []):
            logger.info("generate_node: empty retrieval, abstention")
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
