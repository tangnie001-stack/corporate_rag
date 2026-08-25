# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
"""

import re
from collections.abc import Callable

from loguru import logger

from src.agents.graph.state import AgentState
from src.config.prompts import ABSTENTION_MARKERS


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


def format_node(state: AgentState) -> dict:
    """格式化节点：只保留回答中实际引用的来源，去重并带原始编号。"""
    answer = state.answer or ""
    contexts = state.tool_contexts or []

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
