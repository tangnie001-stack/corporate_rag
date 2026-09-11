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
    assert rec.fork_body is None
    assert rec.agent is None
    assert rec.model is None
    assert rec.allowed_tools == []
    assert rec.user_invocable is True
    assert rec.disable_model_invocation is False


def test_skill_record_fork_fields():
    rec = SkillRecord(
        name="finance-analyst",
        description="财务建模专家",
        context=SkillContext.FORK,
        fork_body="你是财务建模专家",
        agent="finance-analyst",
        model="qwen3.8-max",
        allowed_tools=["retrieve_kb"],
        user_invocable=False,
        disable_model_invocation=True,
        source_path=Path("skills/finance-analyst/SKILL.md"),
    )
    assert rec.context == SkillContext.FORK
    assert rec.fork_body == "你是财务建模专家"
    assert rec.agent == "finance-analyst"
    assert rec.inline_prompt is None
    assert rec.user_invocable is False
    assert rec.disable_model_invocation is True


def test_skill_record_has_dual_axis_and_agent_fields():
    """SkillRecord 含双轴与 agent 字段，且不再有 thinking/max_iterations/agent_prompt。"""
    import dataclasses

    from src.agents.skills.models import SkillRecord

    field_names = {f.name for f in dataclasses.fields(SkillRecord)}
    assert "user_invocable" in field_names
    assert "disable_model_invocation" in field_names
    assert "agent" in field_names
    assert "fork_body" in field_names
    assert "thinking" not in field_names
    assert "max_iterations" not in field_names
    assert "agent_prompt" not in field_names


def test_skill_record_dual_axis_defaults_are_open():
    """未显式赋值时双轴默认开放（推导在 loader 层覆写）。"""
    from src.agents.skills.models import SkillRecord

    record = SkillRecord(name="finance-qa", description="财务问答")
    assert record.user_invocable is True
    assert record.disable_model_invocation is False
    assert record.fork_body is None
    assert record.agent is None
