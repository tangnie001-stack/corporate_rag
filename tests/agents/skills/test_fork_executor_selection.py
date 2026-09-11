"""fork 执行者选择：skill.agent > 会话智能体 > 系统默认；maxTurns 回落。"""

from pathlib import Path

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.executor import SkillExecutor
from src.agents.skills.models import SkillContext, SkillRecord
from src.config.const import DELEGATE_DEFAULT_MAX_TURNS

_AGENT_MD = """---
name: {name}
description: {name} 预设
system_prompt: 你是{name}
maxTurns: {max_turns}
---

人设正文。
"""


def _registry(tmp_path: Path, specs: list[tuple[str, int]]) -> AgentPresetRegistry:
    """按 (name, maxTurns) 写临时预设文件并建立已加载的注册表。"""
    for name, max_turns in specs:
        (tmp_path / f"{name}.md").write_text(
            _AGENT_MD.format(name=name, max_turns=max_turns), encoding="utf-8"
        )
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    return registry


def _fork_record(**overrides) -> SkillRecord:
    """构造 fork SkillRecord，默认不声明 agent。"""
    defaults = {
        "name": "finance-analyst",
        "description": "d",
        "context": SkillContext.FORK,
        "fork_body": "任务：$ARGUMENTS",
        "allowed_tools": [],
        "agent": "",
        "source_path": Path("/tmp/finance-analyst/SKILL.md"),
    }
    defaults.update(overrides)
    return SkillRecord(**defaults)


def test_skill_agent_wins_over_session_agent(tmp_path):
    """skill.agent 命中时优先于会话选定智能体。"""
    exe = SkillExecutor(
        main_llm=object(),
        preset_registry=_registry(
            tmp_path, [("legal-expert", 3), ("finance-expert", 7)]
        ),
    )
    preset = exe._resolve_executor(_fork_record(agent="legal-expert"), "finance-expert")
    assert preset is not None
    assert preset.name == "legal-expert"


def test_session_agent_used_when_skill_declares_none(tmp_path):
    """skill 未声明 agent → 用会话选定智能体。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    preset = exe._resolve_executor(_fork_record(), "finance-expert")
    assert preset is not None
    assert preset.name == "finance-expert"


def test_unknown_names_fall_back_to_system_default(tmp_path):
    """skill.agent 与会话智能体都查不到 → None（系统默认人设）。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    assert exe._resolve_executor(_fork_record(agent="ghost"), "ghost") is None


def test_no_registry_falls_back_to_system_default():
    """未装配 registry → 恒 None，不抛。"""
    exe = SkillExecutor(main_llm=object())
    assert (
        exe._resolve_executor(_fork_record(agent="finance-expert"), "finance-expert")
        is None
    )


def test_fork_max_turns_uses_preset_then_default(tmp_path):
    """maxTurns：preset 声明值优先；未声明或 preset 为 None 时用 DELEGATE_DEFAULT_MAX_TURNS。"""
    exe = SkillExecutor(
        main_llm=object(), preset_registry=_registry(tmp_path, [("finance-expert", 7)])
    )
    preset = exe._resolve_executor(_fork_record(), "finance-expert")

    assert exe._fork_max_turns(preset) == 7
    assert exe._fork_max_turns(None) == DELEGATE_DEFAULT_MAX_TURNS


def test_fork_max_turns_defaults_when_preset_omits_max_turns(tmp_path):
    """preset 未写 maxTurns（None）→ 回落 DELEGATE_DEFAULT_MAX_TURNS。"""
    (tmp_path / "bare.md").write_text(
        "---\nname: bare\ndescription: bare\nsystem_prompt: 你是 bare\n---\n\n正文。\n",
        encoding="utf-8",
    )
    registry = AgentPresetRegistry(AgentPresetLoader(tmp_path))
    registry.reload_if_changed()
    exe = SkillExecutor(main_llm=object(), preset_registry=registry)
    preset = exe._resolve_executor(_fork_record(), "bare")

    assert preset is not None
    assert preset.max_turns is None
    assert exe._fork_max_turns(preset) == DELEGATE_DEFAULT_MAX_TURNS
