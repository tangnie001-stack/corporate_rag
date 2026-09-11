"""完整性校验 — 年份提取与要求覆盖年份比对（原 verify_node.py 迁移）。"""

import re

from langchain_core.messages import AIMessage

_YEAR_PATTERN = re.compile(r"20\d{2}")


def extract_years(answer: str) -> set[int]:
    """正则提取答案文本中的 4 位年份。

    Args:
        answer: 答案文本（AgentState.answer）

    Returns:
        年份集合（可能为空）
    """
    return {int(m) for m in _YEAR_PATTERN.findall(answer)}


def completeness_check(required: list[int], answer: str) -> list[int]:
    """比对要求覆盖年份与答案实际覆盖年份，返回缺失。

    Args:
        required: 问题要求覆盖年份（AgentState.verify_temporal_years）
        answer: 答案文本

    Returns:
        缺失年份列表（升序）
    """
    covered = extract_years(answer)
    missing = [y for y in required if y not in covered]
    return sorted(missing)


def last_search_web_queries(messages) -> list[str] | None:
    """返回 messages 中最近一次 search_web 工具调用的 queries 参数。

    Args:
        messages: state.messages（langchain BaseMessage 列表）

    Returns:
        最近一次 search_web 的 queries 列表；从未调过 search_web 返回 None
    """
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                if tc.get("name") == "search_web":
                    args = tc.get("args") or {}
                    qs = args.get("queries")
                    if isinstance(qs, list):
                        return [str(q) for q in qs]
    return None


def queries_cover_missing(queries: list[str] | None, missing: list[int]) -> bool:
    """判断 search_web queries 是否覆盖全部缺失年份。

    Args:
        queries: 上一轮 search_web 的 queries（None 表示未调过）
        missing: 缺失年份列表

    Returns:
        True queries 非空且每个缺失年份至少出现在一个 query 的文本中
    """
    if not queries:
        return False
    for y in missing:
        if not any(str(y) in q for q in queries):
            return False
    return True
