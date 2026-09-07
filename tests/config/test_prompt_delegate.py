"""测试主 agent delegate 引导 prompt 存在且含陈述区隔规则。"""

from src.config.const import EXPERT_ANALYSIS_MARKER
from src.config.prompts import DELEGATE_GUIDANCE_SECTION, FINANCIAL_SYSTEM_PROMPT


def test_delegate_guidance_section_exists():
    """引导段含：何时 delegate vs 自己答 + delegate_task 可用 skill 提示。"""
    assert "delegate_task" in DELEGATE_GUIDANCE_SECTION
    assert "何时" in DELEGATE_GUIDANCE_SECTION or "不要" in DELEGATE_GUIDANCE_SECTION


def test_financial_system_prompt_includes_delegate_guidance():
    """主系统 prompt 拼接了 delegate 引导段。"""
    assert DELEGATE_GUIDANCE_SECTION in FINANCIAL_SYSTEM_PROMPT


def test_delegate_guidance_contains_statement_distinction():
    """陈述区隔规则：检索事实引 [n]；专家分析不配 [n]（可标注经验分析）。"""
    text = DELEGATE_GUIDANCE_SECTION
    assert "检索" in text and "[n]" in text
    assert "不配 [n]" in text or "不标注" in text


def test_delegate_guidance_uses_expert_analysis_marker():
    """引导措辞必须含 EXPERT_ANALYSIS_MARKER 原文（kb_citation_guardrail 靠它豁免，M7）。"""
    assert EXPERT_ANALYSIS_MARKER in DELEGATE_GUIDANCE_SECTION
