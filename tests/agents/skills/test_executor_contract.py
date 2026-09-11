"""执行契约必须随执行者人设一起下发给子代理（否则确认门收不到 marker）。"""

from src.agents.skills.executor import SkillExecutor
from src.config.prompts import FORK_EXECUTION_CONTRACT


def test_system_prompt_always_carries_execution_contract():
    """无 preset 时：默认人设 + 执行契约。"""
    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(None)
    assert FORK_EXECUTION_CONTRACT in prompt


def test_preset_persona_also_carries_execution_contract():
    """有 preset 时：preset 人设 + 执行契约（契约是执行约束，不由内容作者决定）。"""

    class _Preset:
        system_prompt = "你是财务专家。"

    exe = SkillExecutor(main_llm=object())
    prompt = exe._executor_system_prompt(_Preset())
    assert prompt.startswith("你是财务专家。")
    assert FORK_EXECUTION_CONTRACT in prompt
