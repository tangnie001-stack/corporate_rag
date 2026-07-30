"""复杂度加权评分器 — 为查询计算复杂度分数，作为 LLM 的 hint。"""

import re
from enum import IntEnum

from src.infra.search.entity_extractor import ExtractedEntity


class ComplexityLevel(IntEnum):
    """复杂度级别（用于规则分类）。"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    VERY_HIGH = 4


COMPLEXITY_RULES: dict[ComplexityLevel, list[tuple[str, int]]] = {
    ComplexityLevel.LOW: [
        (r"(你好|您好|hi|hello)", 1),
        (r"(什么是|什么叫|定义)", 1),
    ],
    ComplexityLevel.MEDIUM: [
        (r"(计算|查询|找出|列出)", 2),
        (r"(如何|怎么|怎样)", 2),
    ],
    ComplexityLevel.HIGH: [
        (r"(比较|对比|差异|versus|vs)", 3),
        (r"(分析|解释|说明|为什么|原因)", 3),
    ],
    ComplexityLevel.VERY_HIGH: [
        (r"(报告|报表|生成)", 4),
        (r"多个|多.*?个|各种|所有", 4),
    ],
}


def score_complexity(query: str, entities: list[ExtractedEntity]) -> float:
    """计算查询复杂度评分。

    根据查询内容中的关键词和实体数量，计算一个综合复杂度分数，
    用于指导后续处理流程（如选择不同的检索或生成策略）。

    Args:
        query: 用户查询文本。
        entities: 从查询中提取的实体列表。

    Returns:
        float: 复杂度评分，分数越高表示查询越复杂。
    """
    if not query.strip():
        return 0.0

    score = 0.0
    for level, patterns in COMPLEXITY_RULES.items():
        for pattern, weight in patterns:
            if re.search(pattern, query):
                score += weight

    score += len(entities) * 0.5

    if re.search(r"[和或与]", query):
        score += 2

    return score
