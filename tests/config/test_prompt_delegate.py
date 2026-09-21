"""tools 段委派规则与 output 段委派引用规则的存在性与措辞约束。"""

from src.config.const import EXPERT_ANALYSIS_MARKER
from src.config.prompts import loader


def test_delegate_tool_rule_exists():
    """何时 delegate vs 自己答 + delegate_task 可用 skill 提示。"""
    text = loader.get_content("tools-delegate")
    assert "delegate_task" in text
    assert "不要为每个问题委派" in text
    assert "随 task 一并传入" in text


def test_delegate_citation_rule_uses_expert_analysis_marker():
    """陈述区隔规则（现居 output 段）：检索事实引 [n]；专家分析不配 [n]。"""
    text = loader.get_content("output-delegate-citation")
    assert "检索来源 [n]" in text
    assert "不配 [n]" in text
    assert EXPERT_ANALYSIS_MARKER in text
