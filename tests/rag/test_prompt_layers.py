"""system prompt 三层组装：人设层 + 环境约束层；未选 agent 时逐字不变。"""

from unittest.mock import MagicMock

from langchain_core.messages import SystemMessage

from src.config.prompts import DELEGATE_GUIDANCE_SECTION, INLINE_CITATION_INSTRUCTION
from src.rag.prompt import build_prompt, build_system_prompt


def _pm(base: str = "基础段正文") -> MagicMock:
    """最小 PromptManager 替身：只实现本任务用到的取值方法。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = base
    pm.get_system_prompt.return_value = base + "环境约束"
    pm.get_user_template.return_value = "用户模板"
    return pm


def test_no_persona_keeps_system_messages_byte_identical():
    """persona='' 时第一条 system 消息与 get_system_prompt() 逐字相同。"""
    from src.infra.llm.prompt_manager import PromptManager

    pm = PromptManager()
    messages = build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert len(messages) == 1
    assert messages[0].content == pm.get_system_prompt()


def test_no_persona_unbound_adds_second_system_message():
    """未绑定 KB 仍是独立的第二条 SystemMessage（结构不变）。"""
    pm = _pm()
    messages = build_system_prompt(
        persona="", kb_bound=False, has_skills=False, prompt_manager=pm
    )
    assert len(messages) == 2
    assert isinstance(messages[1], SystemMessage)
    assert messages[1].content != ""


def test_persona_replaces_base_segment():
    """persona 非空 → 人设在最前、基础段不再出现，环境约束段仍追加在其后。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家，只做财务分析。",
        kb_bound=True,
        has_skills=True,
        prompt_manager=pm,
    )
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是财务专家，只做财务分析。")
    assert "基础段正文" not in content
    assert INLINE_CITATION_INSTRUCTION in content


def test_persona_without_skills_omits_delegate_section():
    """has_skills=False 且 persona 非空 → 环境约束层不含委派引导段（P3-R6）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家。", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert DELEGATE_GUIDANCE_SECTION not in messages[0].content


def test_no_persona_always_keeps_delegate_section():
    """persona='' 时即使 has_skills=False 也保留委派引导段（逐字不变的前提）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert DELEGATE_GUIDANCE_SECTION in messages[0].content


def test_build_prompt_passes_persona_through():
    """build_prompt 的带默认值形参让旧调用点零改动，且能把 persona 传下去。"""
    pm = _pm()
    messages = build_prompt("问题", "", [], pm, kb_bound=True, persona="你是财务专家。")
    assert isinstance(messages[0], SystemMessage)
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是财务专家。")


def test_get_base_system_prompt_excludes_env_appends():
    """基础段不含日期追加（get_system_prompt 才追加）。"""
    from src.infra.llm.prompt_manager import PromptManager

    pm = PromptManager()
    base = pm.get_base_system_prompt()
    assert base
    assert "今天是" not in base
