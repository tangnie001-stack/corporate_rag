"""执行契约必须随执行者人设一起下发给子代理（否则确认门收不到 marker）。"""

from src.agents.skills.executor import SkillExecutor
from src.config.const import DELEGATE_VIA_DELEGATE, DELEGATE_VIA_DIRECT
from src.config.prompts import (
    FORK_DEFAULT_EXECUTOR_PROMPT,
    FORK_DELEGATE_CITATION_INSTRUCTION,
    FORK_DIRECT_CITATION_INSTRUCTION,
    FORK_EXECUTION_CONTRACT,
)


def test_system_prompt_always_carries_execution_contract():
    """无 preset 时：默认人设 + 执行契约。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None, DELEGATE_VIA_DELEGATE)
    assert FORK_EXECUTION_CONTRACT in prompt


def test_preset_persona_also_carries_execution_contract():
    """有 preset 时：preset 人设 + 执行契约（契约是执行约束，不由内容作者决定）。"""

    class _Preset:
        system_prompt = "你是财务专家。"

    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(_Preset(), DELEGATE_VIA_DELEGATE)
    assert prompt.startswith("你是财务专家。")
    assert FORK_EXECUTION_CONTRACT in prompt


def test_direct_path_asks_for_citations():
    """直出路径：子代理须自检索并自标 [n]（没有主 agent 补标）。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None, DELEGATE_VIA_DIRECT)
    assert "自行检索" in prompt
    assert "[n]" in prompt


def test_delegate_path_forbids_citations():
    """委派路径：子代理不得自标 [n]（编号由主 agent 统一补标）。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None, DELEGATE_VIA_DELEGATE)
    assert "不要标注引用编号 [n]" in prompt


def test_preset_persona_also_gets_path_citation_instruction():
    """预设人设同样受按路径的引用指示约束（指示统一追加在最末）。"""

    class _Preset:
        system_prompt = "你是财务专家。"

    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(_Preset(), DELEGATE_VIA_DIRECT)
    assert prompt.startswith("你是财务专家。")
    assert "自行检索" in prompt


def test_default_executor_prompt_has_no_unconditional_citation_rule():
    """默认人设里不得留任何引用编号约束（那会与直出路径矛盾，且应由按路径指示承载）。"""
    assert "引用编号" not in FORK_DEFAULT_EXECUTOR_PROMPT


def test_two_path_citation_instructions_differ():
    """两条按路径的引用指示文案不同且有意为之，不得被"统一"。"""
    assert FORK_DIRECT_CITATION_INSTRUCTION != FORK_DELEGATE_CITATION_INSTRUCTION
