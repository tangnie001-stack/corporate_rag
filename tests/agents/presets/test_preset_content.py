"""仓库内真实内容文件的契约测试（防内容与契约脱节）。"""

from pathlib import Path

from src.agents.presets.loader import AgentPresetLoader
from src.agents.skills.loader import SkillLoader

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_finance_expert_preset_loads():
    """agents/finance-expert.md 能被加载且带中文展示名。"""
    presets = AgentPresetLoader(REPO_ROOT / "agents").load_all()

    names = [p.name for p in presets]
    assert "finance-expert" in names
    preset = next(p for p in presets if p.name == "finance-expert")
    assert preset.display_name == "财务专家"
    assert preset.system_prompt


def test_repo_skills_all_load_and_are_named_ascii():
    """仓库内全部 skill 均通过名称校验且被解析（无跳过）。"""
    records = SkillLoader(REPO_ROOT / "skills").load_all()

    assert len(records) == len(list((REPO_ROOT / "skills").glob("*/SKILL.md")))
    for record in records:
        assert record.name.isascii()


def test_finance_analyst_is_methodology_not_persona():
    """finance-analyst 已改写为方法论：正文不含"你是一名"式人设。"""
    records = SkillLoader(REPO_ROOT / "skills").load_all()

    record = next(r for r in records if r.name == "finance-analyst")
    body = record.fork_body if record.fork_body is not None else record.inline_prompt
    assert body is not None
    assert "你是一名" not in body
