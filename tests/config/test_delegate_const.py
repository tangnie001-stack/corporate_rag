"""测试 delegate 相关常量与 SSE 文案（agent-delegation-skills change）。"""

from src.config.const import (
    DELEGATE_RESULT_LIMIT,
    DELEGATE_TIMEOUT,
    INLINE_PROMPT_MAX_CHARS,
    MAX_DELEGATE_BONUS,
    SSEInteractionTexts,
)


def test_delegate_constants():
    assert DELEGATE_TIMEOUT > 0
    assert DELEGATE_RESULT_LIMIT == 1000
    assert MAX_DELEGATE_BONUS == 2
    assert INLINE_PROMPT_MAX_CHARS == 500
    assert SSEInteractionTexts.STAGE_DELEGATE == "delegate"
    assert SSEInteractionTexts.DELEGATE_STATUS_START.startswith("正在调用")
    assert SSEInteractionTexts.DELEGATE_STATUS_END.startswith("领域专家分析完成")


def test_delegate_unknown_skill_template():
    msg = SSEInteractionTexts.DELEGATE_UNKNOWN_SKILL.format(
        skill="finance-analyst", available="finance-qa"
    )
    assert "finance-analyst" in msg and "finance-qa" in msg


def test_delegate_truncated_prefix_template():
    msg = SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX.format(
        total=3000, truncated="..."
    )
    assert "3000" in msg and "..." in msg


def test_expert_analysis_marker():
    """专家分析标记短语存在（kb_citation_guardrail 豁免依据，M7）。"""
    from src.config.const import EXPERT_ANALYSIS_MARKER

    assert EXPERT_ANALYSIS_MARKER == "基于领域经验的分析"
