"""直出轮确认门：规则检测 + 复用澄清链路 + 与 verify 重跑互斥（D18）。"""

import pytest

from src.agents.graph.verify.confirm_gate import (
    ask_confirm_question,
    detect_confirm_request,
)
from src.config.const import FORK_CONFIRM_MARKER, SSEInteractionTexts


def test_detect_returns_question_when_marker_present():
    """命中 marker → 返回其后的提问文本。"""
    text = f"我判断需要确认。\n{FORK_CONFIRM_MARKER} 要按 2024 还是 2023 口径？"
    assert detect_confirm_request(text) == "要按 2024 还是 2023 口径？"


def test_detect_returns_empty_without_marker():
    """无 marker → 空串（直通 verify）。"""
    assert detect_confirm_request("这是正常结论。") == ""


def test_detect_ignores_marker_in_middle_only_of_a_line():
    """marker 必须出现在行首（防正文里偶然提到）。"""
    assert detect_confirm_request(f"前文 {FORK_CONFIRM_MARKER} 不是行首") == ""


@pytest.mark.asyncio
async def test_ask_returns_none_when_ctx_missing():
    """无请求上下文 → None（按未确认处理）。"""
    assert await ask_confirm_question("问题？", "sess_1") is None


class _FakeQueue:
    """最小 clarify_channel 替身：只记录 put 的载荷。"""

    def __init__(self):
        self.items = []

    async def put(self, item):
        self.items.append(item)


class _FakeSignal:
    """最小 abort_signal 替身。"""

    def is_set(self):
        return False


class _FakeCtx:
    """最小 RequestContext 替身（够 ask_confirm_question 走通）。"""

    def __init__(self):
        self.clarify_channel = _FakeQueue()
        self.abort_signal = _FakeSignal()


def _patch_ctx(monkeypatch, ctx) -> _FakeCtx:
    """把 confirm_gate 模块里的 current_request_ctx 换成固定返回 ctx 的替身。"""
    holder = type("V", (), {"get": staticmethod(lambda: ctx)})
    monkeypatch.setattr(
        "src.agents.graph.verify.confirm_gate.current_request_ctx", holder
    )
    return ctx


@pytest.mark.asyncio
async def test_ask_returns_none_when_wait_times_out(monkeypatch):
    """等待返回超时文案（既有 wait_with_abort_and_timeout 返回哨兵而非抛异常）→ None。"""
    ctx = _patch_ctx(monkeypatch, _FakeCtx())

    async def _timeout_result(*args, **kwargs):
        return SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT

    monkeypatch.setattr(
        "src.agents.graph.verify.confirm_gate.wait_with_abort_and_timeout",
        _timeout_result,
    )
    assert await ask_confirm_question("问题？", "sess_1") is None
    assert ctx.clarify_channel.items  # 问题确实经澄清通道投递给了前端


@pytest.mark.asyncio
async def test_ask_returns_reply_text(monkeypatch):
    """用户答复 `[{"selected": ["按 2024 口径"]}]`（clarify.py 的既有消费形状）→ 返回该文本。"""
    _patch_ctx(monkeypatch, _FakeCtx())

    async def _reply(*args, **kwargs):
        return [{"selected": ["按 2024 口径"]}]

    monkeypatch.setattr(
        "src.agents.graph.verify.confirm_gate.wait_with_abort_and_timeout", _reply
    )
    assert await ask_confirm_question("问题？", "sess_1") == "按 2024 口径"
