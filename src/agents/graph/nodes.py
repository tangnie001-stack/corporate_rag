# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
"""

import re
from difflib import SequenceMatcher

from src.agents.graph.state import AgentState
from src.config.const import SSEInteractionTexts
from src.core import logging as core_logging
from src.core.log_events import Event, Signal

# 引用片段窗口字符数：过长截取内容不可读，过短丢失上下文
_SNIPPET_WINDOW = 200
# 与回答重叠低于该长度视为无意义，回退到内容开头窗口
_SNIPPET_MIN_MATCH = 15


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

    # 拒答检测（防御式）：命中拒答标记 且 不含 [n] 引用标记 才视为纯拒答。
    # web 兜底回答即使混入"未在文档中找到"措辞，只要带了引用标记就保留引用。
    has_abstention_marker = any(
        marker in answer for marker in SSEInteractionTexts.ABSTENTION_MARKERS
    )
    has_citation_marker = re.search(r"\[\d+\]", answer) is not None
    if has_abstention_marker and not has_citation_marker:
        core_logging.log_event(Event.FORMAT_DONE, citations=0, reason="abstention")
        return {"citations": []}

    # 提取回答中引用的编号 [n]，非法编号（超出 context 范围）不进 citations
    raw_numbers = [int(m) for m in re.findall(r"\[(\d+)\]", answer)]
    cited_numbers = set(raw_numbers)
    valid_numbers = {n for n in cited_numbers if 1 <= n <= len(contexts)}
    # 幻觉编号观测信号（design D5）：聚合一条（ids=去重升序，count=出现总次数），防日志噪音
    invalid_numbers = sorted(cited_numbers - valid_numbers)
    if invalid_numbers:
        invalid_count = sum(1 for n in raw_numbers if n in set(invalid_numbers))
        if hasattr(state, "query"):
            invalid_query = state.query
        else:
            invalid_query = ""
        core_logging.retrieval_signal(
            Signal.INVALID_CITATION,
            invalid_query,
            0,
            kb_id="",
            count=invalid_count,
            ids="|".join(str(n) for n in invalid_numbers),
        )
    if not valid_numbers:
        core_logging.log_event(Event.FORMAT_DONE, citations=0, reason="no_markers")
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
                "kind": ctx.kind,
                "tier": ctx.tier,
            }
        )
    core_logging.log_event(Event.FORMAT_DONE, citations=len(citations))
    # 对照基线信号：正常引用（kind 区分 kb/web），供检索质量诊断对照；
    # 态A/态B 只要走到正常引用即产出，故不按 kb_id 区分（保持空串）
    if hasattr(state, "query"):
        query_text = state.query
    else:
        query_text = ""
    kinds = {c.get("kind", "kb") for c in citations} or {"kb"}
    core_logging.retrieval_signal(
        Signal.CITED,
        query_text,
        0,
        kb_id="",
        citation_count=len(citations),
        kind="|".join(sorted(kinds)),
    )
    return {"citations": citations}
