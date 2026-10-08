"""摘要调度的单测：触发判据、锁 best-effort、失败降级（不发起真实 LLM/Redis）。"""

import pytest

from src.config.const import (
    HISTORY_MAX_TURNS,
    HISTORY_SUMMARY_TRIGGER_TOKENS,
    HISTORY_TOKEN_BUDGET,
)
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens
from src.services import summary_scheduler


class _FakeManager:
    def __init__(self):
        self.saved = []
        self.summary = ("", 0)

    async def get_summary_async(self, session_id):
        return self.summary

    async def save_summary_async(self, session_id, text, covered):
        self.saved.append((session_id, text, covered))
        self.summary = (text, covered)

    async def add_message_async(self, *a, **k):
        return None


class _FakeLock:
    def __init__(self, ok=True):
        self.ok = ok
        self.released = 0

    async def acquire(self, redis, session_id):
        return self.ok

    async def release(self, redis, session_id):
        self.released += 1


def _big_history(turns: int = 20):
    """构造超过触阈的历史：每轮 user/assistant 各 1800 字。

    数据规模对**两种计数口径**都留足余量——真分词器约 1 token/字、encoder 不可用时
    降级口径 `len//2` = 0.5 token/字——使"被丢弃段 > HISTORY_SUMMARY_TRIGGER_TOKENS"
    在两种口径下都成立，本用例不依赖同进程其他测试留下的计数环境。
    """
    history = []
    for i in range(turns):
        history.append(ChatMessage(role="user", content="问" * 1800))
        history.append(ChatMessage(role="assistant", content="答" * 1800))
    return history


def test_discarded_empty_skips_without_scheduling():
    """保留尾部覆盖全量（被丢弃段为空）⇒ 不调度（防每轮空转）。"""
    scheduler = summary_scheduler.SummaryScheduler(
        manager=_FakeManager(), redis=object()
    )
    short = [
        ChatMessage(role="user", content="短"),
        ChatMessage(role="assistant", content="短"),
    ]
    assert scheduler.should_schedule(short) is False


def test_schedules_when_discarded_exceeds_trigger():
    """被丢弃段超触阈 ⇒ 需要调度。"""
    from src.agents.graph.history_window import split_history_window

    scheduler = summary_scheduler.SummaryScheduler(
        manager=_FakeManager(), redis=object()
    )
    history = _big_history()
    _kept, discarded = split_history_window(
        history, HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
    )
    assert discarded, "构造数据应产生非空被丢弃段"
    assert (
        sum(count_tokens(m.content) for m in discarded) > HISTORY_SUMMARY_TRIGGER_TOKENS
    )
    assert scheduler.should_schedule(history) is True


@pytest.mark.asyncio
async def test_generate_writes_summary_and_emits_done(monkeypatch):
    """生成成功：写回摘要并释放锁。"""
    monkeypatch.setattr(
        summary_scheduler,
        "summarize_history",
        lambda prev_text, prev_covered, discarded: _ok_result(prev_covered, discarded),
    )
    manager = _FakeManager()
    lock = _FakeLock(ok=True)
    scheduler = summary_scheduler.SummaryScheduler(
        manager=manager, redis=object(), lock=lock
    )
    history = _big_history()
    await scheduler.generate("s1", history)
    assert manager.saved and manager.saved[0][0] == "s1"
    assert lock.released == 1


@pytest.mark.asyncio
async def test_generate_skips_when_lock_unavailable(monkeypatch):
    """抢不到锁 ⇒ 直接跳过，不写、不调 LLM。"""
    called = []
    monkeypatch.setattr(
        summary_scheduler, "summarize_history", lambda *a, **k: called.append(1)
    )
    manager = _FakeManager()
    lock = _FakeLock(ok=False)
    scheduler = summary_scheduler.SummaryScheduler(
        manager=manager, redis=object(), lock=lock
    )
    await scheduler.generate("s1", _big_history())
    assert manager.saved == []
    assert called == []
    assert lock.released == 0


async def _ok_result(prev_covered, discarded):
    from src.chat.history_summary import SummaryResult

    return SummaryResult("## 用户目标\n摘要", prev_covered + len(discarded), False, "")
