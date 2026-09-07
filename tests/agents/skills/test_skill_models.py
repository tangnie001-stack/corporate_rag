"""测试 SkillRecord 内容模型与 context 常量。"""

from pathlib import Path

from src.agents.skills.models import SkillContext, SkillRecord


def test_context_constants():
    assert SkillContext.INLINE == "inline"
    assert SkillContext.FORK == "fork"


def test_skill_record_defaults():
    rec = SkillRecord(
        name="finance-qa",
        description="财务问答规则",
        source_path=Path("skills/finance-qa/SKILL.md"),
    )
    assert rec.context == SkillContext.INLINE
    assert rec.inline_prompt is None
    assert rec.agent_prompt is None
    assert rec.model is None
    assert rec.thinking is None
    assert rec.allowed_tools == []
    assert rec.max_iterations is None


def test_skill_record_fork_fields():
    rec = SkillRecord(
        name="finance-analyst",
        description="财务建模专家",
        context=SkillContext.FORK,
        agent_prompt="你是财务建模专家",
        model="qwen3.8-max",
        thinking=True,
        allowed_tools=[],
        max_iterations=0,
        source_path=Path("skills/finance-analyst/SKILL.md"),
    )
    assert rec.context == SkillContext.FORK
    assert rec.agent_prompt == "你是财务建模专家"
    assert rec.inline_prompt is None
