"""文本脱敏工具 — 替换可能触发内容安全审核的敏感内容。

替换规则（仅高风险阶段）：
  - 违规/处罚/违法 → [合规事项]
  - 诉讼/纠纷    → [合规事项]
  - 监管/整改    → [监管事项]
保留：金额、日期、百分比、公司名、人名、地名（供 NERExtractor 使用）
"""
import re


# 规则顺序：先替换长模式，后替换短模式，避免被短模式截胡
_DESENSITIZE_RULES: list[tuple[str, str]] = [
    # ---- 高风险：违规/处罚/诉讼类 ----
    (r'违规\S{0,10}', '[合规事项]'),
    (r'违法\S{0,10}', '[合规事项]'),
    (r'处罚\S{0,10}', '[合规事项]'),
    (r'诉讼\S{0,10}', '[合规事项]'),
    (r'纠纷\S{0,10}', '[合规事项]'),
    (r'监管\S{0,5}', '[监管事项]'),
    (r'整改\S{0,5}', '[监管事项]'),
]


def desensitize(text: str) -> str:
    """对文本进行脱敏处理，替换可能触发内容安全审核的内容。

    Args:
        text: 原始文本

    Returns:
        脱敏后的文本
    """
    for pattern, replacement in _DESENSITIZE_RULES:
        text = re.sub(pattern, replacement, text)
    return text
