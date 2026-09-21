"""system prompt 三层组装：人设层 + 环境约束层；未选 agent 时逐字不变。"""

from unittest.mock import MagicMock

from langchain_core.messages import SystemMessage

from src.config.prompts import loader
from src.rag.prompt import build_prompt, build_system_prompt


def _pm(base: str = "基础段正文") -> MagicMock:
    """最小 PromptManager 替身：只实现本任务用到的取值方法。"""
    pm = MagicMock()
    pm.get_base_system_prompt.return_value = base
    pm.get_system_prompt.return_value = base + "环境约束"
    pm.get_user_template.return_value = "用户模板"
    return pm


def test_no_persona_bound_system_message_includes_discipline():
    """persona='' 且绑库 → 第一条 system 消息 = 基础段 + 检索纪律 + 引用指令 + 委派引导 + 日期。

    base 已瘦身、不再自含检索阶梯，故 persona 为空时检索纪律照常注入。
    """
    from src.infra.llm.prompt_manager import _with_current_date

    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    expected = _with_current_date(
        "基础段正文"
        + loader.get_content("sources-kb-ladder")
        + loader.get_content("output-citation")
        + loader.get_content("tools-delegate")
    )
    assert len(messages) == 1
    assert messages[0].content == expected


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
    assert loader.get_content("output-citation") in content


def test_persona_without_skills_omits_delegate_section():
    """has_skills=False 且 persona 非空 → 环境约束层不含委派引导段（P3-R6）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家。", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert loader.get_content("tools-delegate") not in messages[0].content


def test_persona_bound_keeps_retrieval_discipline():
    """选定 agent 且绑定 KB → 环境约束层注入检索纪律（人设被替换后仍强制叠加）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="你是财务专家。", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    discipline = loader.get_content("sources-kb-ladder")
    assert discipline in messages[0].content


def test_no_persona_always_keeps_delegate_section():
    """persona='' 时即使 has_skills=False 也保留委派引导段（逐字不变的前提）。"""
    pm = _pm(base="基础段正文")
    messages = build_system_prompt(
        persona="", kb_bound=True, has_skills=False, prompt_manager=pm
    )
    assert loader.get_content("tools-delegate") in messages[0].content


def test_build_prompt_passes_persona_through():
    """build_prompt 的带默认值形参让旧调用点零改动，且能把 persona 传下去。"""
    pm = _pm()
    messages = build_prompt("问题", "", [], pm, kb_bound=True, persona="你是财务专家。")
    assert isinstance(messages[0], SystemMessage)
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是财务专家。")


def test_get_base_system_prompt_excludes_env_appends(monkeypatch):
    """真实 get_base_system_prompt 原样返回取数缝隙的文本，不加日期后缀。

    取数缝隙 `PromptManager._get` 被替换为返回哨兵串，消除网络依赖；
    日期追加只发生在 get_system_prompt 层，故本方法返回值应逐字等于哨兵串。

    Args:
        monkeypatch: pytest 内置夹具，用于替换取数缝隙
    """
    from src.infra.llm.prompt_manager import PromptManager

    sentinel = "基础段哨兵正文"
    pm = PromptManager()
    monkeypatch.setattr(pm, "_get", lambda name, fallback: sentinel)
    base = pm.get_base_system_prompt()
    assert base == sentinel
    assert "今天是" not in base


def test_prompt_assembled_logged_with_injection_facts(monkeypatch):
    """build_system_prompt 记录人设来源与条件注入事实（design D11 #3/D15）。"""
    from src.core.log_events import Event

    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    pm = _pm(base="基础段正文")

    build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=True,
        prompt_manager=pm,
    )

    assembled = [c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED]
    assert len(assembled) == 1
    payload = assembled[0]
    assert payload["persona_source"] == "preset"
    assert payload["kb_bound"] is True
    assert payload["has_skills"] is True
    assert payload["discipline_injected"] is True
    assert payload["delegate_injected"] is True
    assert payload["system_msgs"] == 1


def test_prompt_assembled_reports_base_persona_and_injects_discipline(monkeypatch):
    """未选 agent（persona 为空）→ 人设来源 base；绑库时检索纪律照常注入。

    用最小替身（基础段不含委派引导）验证"persona 为空时委派段恒追加（缺则补）"：
    真实 PromptManager 基础段已内嵌委派引导，守卫命中 → delegate_injected 为 False。
    """
    from src.core.log_events import Event

    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    pm = _pm(base="基础段正文")

    build_system_prompt(persona="", kb_bound=True, has_skills=False, prompt_manager=pm)

    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    assert payload["persona_source"] == "base"
    assert payload["discipline_injected"] is True
    assert payload["delegate_injected"] is True
