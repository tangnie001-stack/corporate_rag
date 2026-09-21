"""PROMPT_ASSEMBLED 带 section_chars（各非空段字符数）与占比软告警。

- `section_chars` 以 **dict** 交给日志层编码（紧凑 JSON 由 `encode_value` 负责，
  本测试只断言字段形状，不重复测编码器）。
- 占比告警是 **阈值驱动** 的：正常阈值下不触发，降低阈值后触发（见两个阈值用例）。
"""

import pytest

from src.config import settings
from src.core.log_events import Event
from src.rag.prompt import build_system_prompt

# 被断言的「正常阈值」显式镜像 settings 的发货默认值：本用例只回答
# 「在 0.05 这个阈值下是否静默」，不隐式依赖 settings 的当前默认值。
_NORMAL_THRESHOLD = 0.05


def _capture_log_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把 `src.rag.prompt` 的 log_event 换成捕获器。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture

    Returns:
        捕获列表；元素为 {"event": Event, **kwargs}
    """
    calls: list[dict] = []

    def fake_log_event(event: Event, **fields: object) -> None:
        """捕获一次日志调用。"""
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.rag.prompt.core_logging.log_event", fake_log_event)
    return calls


def _build() -> None:
    """以固定入参组装一次 system prompt（触发 PROMPT_ASSEMBLED）。"""
    build_system_prompt(
        persona="",
        kb_bound=True,
        has_skills=False,
        tool_names=frozenset({"retrieve_kb", "ask_user"}),
        kb_domain="general",
    )


def test_prompt_assembled_carries_section_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """组装日志的 section_chars 是正整数字典（编码由日志层负责，此处不测编码）。"""
    calls = _capture_log_events(monkeypatch)
    _build()

    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    section_chars = payload["section_chars"]
    assert isinstance(section_chars, dict)
    assert section_chars, "至少应含 base 段"
    for name, count in section_chars.items():
        assert isinstance(name, str)
        assert isinstance(count, int) and count > 0


def test_section_chars_key_order_matches_assembly_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """section_chars 的键序 = 段组装顺序（不是文件名顺序）。"""
    from src.rag.prompt import SECTION_ORDER

    calls = _capture_log_events(monkeypatch)
    _build()

    payload = next(c for c in calls if c["event"] is Event.PROMPT_ASSEMBLED)
    keys = list(payload["section_chars"])
    assert keys == [name for name in SECTION_ORDER if name in keys]


def test_section_share_warning_fires_when_threshold_lowered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """阈值降到 0 时触发 warning（证明显式阈值驱动，可被触发）。"""
    calls = _capture_log_events(monkeypatch)
    monkeypatch.setattr(settings, "PROMPT_CONTEXT_SHARE_WARN", 0.0)
    _build()

    assert any(c["event"] is Event.PROMPT_SECTION_SHARE_HIGH for c in calls)


def test_section_share_warning_silent_at_normal_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在显式固定的正常阈值下不触发 warning（证明告警不是恒开）。

    阈值在用例内显式固定（镜像发货默认值 0.05），不与 settings 的当前默认值
    或模板体积隐式耦合：本用例失败即意味着当前段字符数已越过该阈值触发点，
    失败原因与用例名一致。
    """
    calls = _capture_log_events(monkeypatch)
    monkeypatch.setattr(settings, "PROMPT_CONTEXT_SHARE_WARN", _NORMAL_THRESHOLD)
    _build()

    assert not any(c["event"] is Event.PROMPT_SECTION_SHARE_HIGH for c in calls)
