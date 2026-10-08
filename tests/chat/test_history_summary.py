"""跨轮历史摘要核心逻辑的单测（不发起真实 LLM 调用）。"""

from unittest.mock import AsyncMock

import pytest

from src.chat import history_summary
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens


def test_strip_citation_numbers_removes_only_bracket_digits():
    """只剥离 [数字]，不误伤年份与 Markdown 链接。"""
    raw = "结论见 [1]，2024 年数据见 [12]，链接 [文档](http://x)。"
    out = history_summary.strip_citation_numbers(raw)
    assert "[1]" not in out
    assert "[12]" not in out
    assert "2024" in out
    assert "[文档](http://x)" in out


def test_validate_summary_rejects_not_smaller():
    """摘要不比被丢弃段小 → 不采用。"""
    ok, reason = history_summary.validate_summary(
        text="短", discarded_tokens=1, previous_tokens=None
    )
    assert ok is False
    assert reason == "not_smaller"


def test_validate_summary_rejects_growth():
    """就地更新时新摘要不得比上一版更长 → 违反即不采用。"""
    text = "长" * 200
    ok, reason = history_summary.validate_summary(
        text=text,
        discarded_tokens=10**6,
        previous_tokens=max(1, count_tokens(text) - 1),
    )
    assert ok is False
    assert reason == "grew"


def test_validate_summary_accepts_normal():
    """同时满足两条时不拒绝。"""
    text = "摘要正文"
    ok, reason = history_summary.validate_summary(
        text=text, discarded_tokens=10**6, previous_tokens=None
    )
    assert ok is True
    assert reason == ""


@pytest.mark.asyncio
async def test_summarize_history_degrades_on_llm_failure(monkeypatch):
    """LLM 抛异常 → degraded 结果，不抛出去。"""
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("boom")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    discarded = [
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="a"),
    ]
    result = await history_summary.summarize_history("", 0, discarded)
    assert result.degraded is True
    assert result.text == ""
    assert "boom" in result.reason


@pytest.mark.asyncio
async def test_summarize_history_rejects_truncated(monkeypatch):
    """结束原因为截断 → 不采用（四条不变量之「拒绝截断摘要」）。"""
    llm = AsyncMock()
    llm.ainvoke.return_value = _fake_response("半截摘要", finish_reason="length")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    discarded = [ChatMessage(role="user", content="q" * 500)]
    result = await history_summary.summarize_history("", 0, discarded)
    assert result.degraded is True
    assert result.reason == "truncated"


@pytest.mark.asyncio
async def test_summarize_history_returns_text_and_covered(monkeypatch):
    """成功路径：返回摘要正文与覆盖条数（= 之前覆盖数 + 本次丢弃段条数）。"""
    llm = AsyncMock()
    llm.ainvoke.return_value = _fake_response("## 用户目标\n看年报")
    monkeypatch.setattr(history_summary, "get_summary_llm", lambda: llm)
    # 被丢弃段须显著大于摘要正文，否则会命中「更小」不变量被判 not_smaller
    # （该不变量由 test_validate_summary_* 单独覆盖，本用例只验成功路径）。
    discarded = [
        ChatMessage(role="user", content="q1" * 200),
        ChatMessage(role="assistant", content="a1" * 200),
    ]
    result = await history_summary.summarize_history("", 4, discarded)
    assert result.degraded is False
    assert result.text == "## 用户目标\n看年报"
    assert result.covered == 6


def _fake_response(content: str, finish_reason: str = "stop"):
    """构造带 response_metadata 的假模型响应。"""

    class _Resp:
        def __init__(self) -> None:
            self.content = content
            self.response_metadata = {"finish_reason": finish_reason}

    return _Resp()
