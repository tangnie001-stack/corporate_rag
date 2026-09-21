"""system prompt 人设层语义与组装日志（结构断言见 test_assembly_sections.py）。"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import SystemMessage

from src.config.prompts import loader
from src.rag.prompt import build_prompt, build_system_prompt

_BASE_TOOLS = frozenset({"retrieve_kb", "ask_user"})


def _pm() -> MagicMock:
    """最小 PromptManager 替身：只实现 build_prompt 用到的用户模板取值。"""
    pm = MagicMock()
    pm.get_user_template.return_value = "用户模板"
    return pm


def _tools(*extra: str) -> frozenset[str]:
    """构造工具名集合。"""
    return _BASE_TOOLS | frozenset(extra)


def test_persona_replaces_base_segment() -> None:
    """persona 非空 → 人设在最前、通用 base 不再出现，环境约束段仍追加在其后。"""
    messages = build_system_prompt(
        persona="你是财务专家，只做财务分析。",
        kb_bound=True,
        has_skills=True,
        tool_names=_tools("delegate_task"),
        kb_domain="finance",
    )
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是财务专家，只做财务分析。")
    assert "你是一个企业知识库问答助手" not in content
    assert loader.get_content("output-citation").strip("\n") in content


def test_persona_without_delegate_tool_omits_delegate_section() -> None:
    """工具集里没有 delegate_task → 环境约束层不含委派引导段（判据是工具注册，不是 has_skills）。"""
    messages = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )
    content = messages[0].content
    assert loader.get_content("tools-delegate").strip("\n") not in content
    assert loader.get_content("output-delegate-citation").strip("\n") not in content


def test_persona_bound_keeps_retrieval_ladder() -> None:
    """选定 agent 且绑定 KB → 检索阶梯注入（人设被替换后仍强制叠加，P1 的核心修复）。"""
    messages = build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )
    assert loader.get_content("sources-kb-ladder").strip("\n") in messages[0].content


def test_no_persona_uses_general_base_when_domain_unknown() -> None:
    """未选 agent 且领域未知 → 回退通用 base，且不阻断、不降级成额外提示。

    与 T9 的 `test_base_three_way_replacement` 分工：那条只钉"正文以通用 base 开头"
    这一结构事实；本用例在此之外钉住 spec「领域标识缺失或无法识别时系统 SHALL 回退
    到内置通用 base，SHALL NOT 阻断请求」的两个可观测后果——领域 base 正文不参与组装，
    且绑库情形下仍只有一条 system 消息（"回退"不等于追加提示）。
    """
    messages = build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="hr",
    )
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是一个企业知识库问答助手")
    assert loader.get_content("base-financial").strip("\n") not in content
    assert len(messages) == 1


def test_build_prompt_passes_persona_through() -> None:
    """build_prompt 的带默认值形参让旧调用点零改动，且能把 persona 传下去。"""
    messages = build_prompt(
        "问题",
        "",
        [],
        _pm(),
        kb_bound=True,
        persona="你是财务专家。",
    )
    assert isinstance(messages[0], SystemMessage)
    content = messages[0].content
    assert isinstance(content, str)
    assert content.startswith("你是财务专家。")


def _capture_log_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把 `src.rag.prompt` 的 log_event 换成捕获器。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture

    Returns:
        捕获列表；元素为 {"event": Event, **kwargs}
    """
    calls: list[dict] = []

    def fake_log_event(event, **fields: object) -> None:
        """捕获一次日志调用。"""
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    return calls


def test_prompt_assembled_logged_with_preset_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """选定预设 → persona_source=preset，且记录 kb_domain 与工具数。"""
    from src.core.log_events import Event

    calls = _capture_log_events(monkeypatch)
    build_system_prompt(
        persona="你是财务专家。",
        kb_bound=True,
        has_skills=True,
        tool_names=_tools("search_web", "delegate_task"),
        kb_domain="finance",
    )

    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    assert payload["persona_source"] == "preset"
    assert payload["kb_bound"] is True
    assert payload["has_skills"] is True
    assert payload["kb_domain"] == "finance"
    assert payload["tool_count"] == 4
    assert payload["system_msgs"] == 1


def test_prompt_assembled_reports_domain_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未选预设且领域已知 → persona_source=domain；领域未知 → general。"""
    from src.core.log_events import Event

    calls = _capture_log_events(monkeypatch)
    build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="finance",
    )
    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    assert payload["persona_source"] == "domain"

    calls.clear()
    build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=_tools(),
        kb_domain="hr",
    )
    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    assert payload["persona_source"] == "general"
