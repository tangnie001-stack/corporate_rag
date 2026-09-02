"""完整性校验 — 年份提取与要求覆盖年份比对（原 verify_node.py 迁移）。"""

import re

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
        required: 问题要求覆盖年份（RequestContext.temporal_years）
        answer: 答案文本

    Returns:
        缺失年份列表（升序）
    """
    covered = extract_years(answer)
    missing = [y for y in required if y not in covered]
    return sorted(missing)
