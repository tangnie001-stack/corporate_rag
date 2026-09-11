"""AgentPresetLoader 解析测试（agents/<name>.md，驼峰 frontmatter）。"""

import pytest

from src.agents.presets.loader import AgentPresetLoader


def test_parse_preset_with_all_fields(tmp_path):
    """解析完整 frontmatter：display_name/description/tools/skills/maxTurns + 正文人设。"""
    (tmp_path / "finance-expert.md").write_text(
        "---\n"
        "name: finance-expert\n"
        "display_name: 财务专家\n"
        "description: 财报分析、估值与财务风险研判\n"
        "tools: retrieve_kb\n"
        "skills: finance-qa\n"
        "maxTurns: 8\n"
        "---\n"
        "你是一名资深财务分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert len(presets) == 1
    preset = presets[0]
    assert preset.name == "finance-expert"
    assert preset.display_name == "财务专家"
    assert preset.tools == ["retrieve_kb"]
    assert preset.skills == ["finance-qa"]
    assert preset.max_turns == 8
    assert preset.system_prompt == "你是一名资深财务分析师。"


def test_display_name_defaults_to_name(tmp_path):
    """缺省 display_name 用 name。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 数据分析\n---\n你是数据分析师。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].display_name == "analyst"


def test_description_falls_back_to_first_paragraph(tmp_path):
    """缺省 description 用正文首段。"""
    (tmp_path / "legal.md").write_text(
        "---\nname: legal\n---\n你是法务顾问，负责合同审阅。\n\n更多说明。\n",
        encoding="utf-8",
    )

    presets = AgentPresetLoader(tmp_path).load_all()

    assert presets[0].description == "你是法务顾问，负责合同审阅。"


def test_non_ascii_name_is_skipped(tmp_path):
    """非 ASCII slug 名称记 warning 并跳过。"""
    (tmp_path / "财务.md").write_text(
        "---\nname: 财务专家\n---\n正文\n", encoding="utf-8"
    )

    with pytest.warns(UserWarning, match="非法"):
        presets = AgentPresetLoader(tmp_path).load_all()

    assert presets == []


def test_missing_dir_returns_empty(tmp_path):
    """目录不存在时返回空列表，不抛异常。"""
    assert AgentPresetLoader(tmp_path / "nope").load_all() == []
