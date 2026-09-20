"""测试主 agent delegate 引导 prompt 存在且含陈述区隔规则。"""

from src.config.const import EXPERT_ANALYSIS_MARKER
from src.config.prompts import loader


def test_delegate_guidance_section_exists():
    """引导段含：何时 delegate vs 自己答 + delegate_task 可用 skill 提示。"""
    text = loader.get_content("tools-delegate-guidance")
    assert "delegate_task" in text
    assert "何时" in text or "不要" in text


def test_fallback_system_prompt_includes_delegate_guidance():
    """兜底系统 prompt 拼接了 delegate 引导段（等价于原基础段的拼装）。"""
    from src.infra.llm.prompt_manager import _FALLBACK_SYSTEM_PROMPT

    assert loader.get_content("tools-delegate-guidance") in _FALLBACK_SYSTEM_PROMPT


def test_delegate_guidance_contains_statement_distinction():
    """陈述区隔规则：检索事实引 [n]；专家分析不配 [n]（可标注经验分析）。"""
    text = loader.get_content("tools-delegate-guidance")
    assert "检索" in text and "[n]" in text
    assert "不配 [n]" in text or "不标注" in text


def test_delegate_guidance_uses_expert_analysis_marker():
    """引导措辞必须含 EXPERT_ANALYSIS_MARKER 原文（kb_citation_guardrail 靠它豁免，M7）。"""
    assert EXPERT_ANALYSIS_MARKER in loader.get_content("tools-delegate-guidance")
