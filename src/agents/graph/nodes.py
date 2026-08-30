# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
"""

import re
from collections.abc import Callable
from difflib import SequenceMatcher

from loguru import logger

from src.agents.graph.state import AgentState
from src.config.const import SSEInteractionTexts

# 引用片段窗口字符数：过长截取内容不可读，过短丢失上下文
_SNIPPET_WINDOW = 200
# 与回答重叠低于该长度视为无意义，回退到内容开头窗口
_SNIPPET_MIN_MATCH = 15


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


def _relevant_snippet(content: str, answer: str) -> str:
    """截取 chunk 内容中与回答最相关的片段作为引用预览。

    固定取前 N 字符的问题：parent-child chunk 的相关内容可能位于深处，
    预览会误导用户以为引用不支撑回答。这里用最长公共子串定位回答
    与 chunk 内容的重叠区间，以它为中心取窗口；无有效重叠时回退到
    内容开头窗口（保持原行为）。

    Args:
        content: chunk 全文
        answer: 模型回答（含 [n] 引用标记）

    Returns:
        截取的片段文本，开头不在 chunk 起点时带省略号前缀
    """
    clean_answer = re.sub(r"\[\d+\]", "", answer)
    if not content or not clean_answer:
        return content[:_SNIPPET_WINDOW]

    # 去全部空白归一化用于匹配，同时保留原内容非空白字符位置用于回映
    pos_map = [i for i, ch in enumerate(content) if not ch.isspace()]
    norm_content = "".join(content[i] for i in pos_map)
    norm_answer = "".join(ch for ch in clean_answer if not ch.isspace())
    if not norm_content or not norm_answer:
        return content[:_SNIPPET_WINDOW]

    match = SequenceMatcher(None, norm_content, norm_answer).find_longest_match(
        0, len(norm_content), 0, len(norm_answer)
    )
    if match.size < _SNIPPET_MIN_MATCH:
        return content[:_SNIPPET_WINDOW]

    start = pos_map[match.a]
    start = max(0, start - 60)  # 匹配起点前补少量上下文，便于用户定位
    end = min(len(content), start + _SNIPPET_WINDOW)
    if start > 0:
        return "…" + content[start:end]
    return content[start:end]


def format_node(state: AgentState) -> dict:
    """格式化节点：只保留回答中实际引用的来源，去重并带原始编号。"""
    answer = state.answer or ""
    contexts = state.tool_contexts or []

    # 拒答检测：回答明确表示未找到数据时，不输出引用
    if any(marker in answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS):
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
                "snippet": _relevant_snippet(ctx.content, answer),
                "score": ctx.score,
            }
        )
    logger.info("format_node: citations={}", len(citations))
    return {"citations": citations}
