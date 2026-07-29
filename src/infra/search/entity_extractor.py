"""实体提取器 — 从查询中提取结构化实体信息。"""

from dataclasses import dataclass


@dataclass
class ExtractedEntity:
    """从查询中提取的实体。"""

    type: str  # 实体类型，如 person、org、date 等
    value: str | None  # 实体值
    confidence: float = 1.0  # 置信度，0~1
    source: str = "regex"  # 提取来源
