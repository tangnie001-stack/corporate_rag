"""正则实体提取器 — 从查询中提取财务关键实体，0 LLM 成本。"""

import re
from dataclasses import dataclass


@dataclass
class ExtractedEntity:
    """从查询中提取的实体。"""

    type: str  # 实体类型，如 person、org、date 等
    value: str | None  # 实体值
    confidence: float = 1.0  # 置信度，0~1
    source: str = "regex"  # 提取来源


class EntityExtractor:
    """正则实体提取器 — 从查询中提取预定义模式的实体。"""

    PATTERNS: dict[str, str] = {
        "year": r"20\d{2}",
        "quarter": r"第?[1-4一二三四]季[度]?|Q[1-4]",
        "month": r"\d{1,2}月",
        "metric": r"(营收|利润|收入|成本|资产|负债|现金流|毛利率|净利率|周转率|ROE|ROA)",
        "money": r"[¥$]?\d+(?:,\d{3})*(?:\.\d{2})?[亿万元]?",
        "percentage": r"\d+(?:\.\d+)?%",
        "company": r"(?:[A-Z]\w{1,10}(?:公司|集团|有限[公司])?|[\u4e00-\u9fa5]{2,8}(?:公司|集团|有限[公司])|[\u4e00-\u9fa5]{3,8})",
    }

    def extract(self, query: str) -> list[ExtractedEntity]:
        """正则提取所有匹配实体。

        Args:
            query: 用户查询文本

        Returns:
            提取到的实体列表，按 PATTERNS 顺序逐个匹配
        """
        entities: list[ExtractedEntity] = []
        for etype, pattern in self.PATTERNS.items():
            match = re.search(pattern, query)
            if match:
                entities.append(
                    ExtractedEntity(
                        type=etype,
                        value=match.group(0),
                        confidence=1.0,
                        source="regex",
                    )
                )
        return entities
