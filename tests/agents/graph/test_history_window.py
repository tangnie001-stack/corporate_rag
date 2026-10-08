"""测试历史窗口截断 — _truncate_history 的轮数粗筛 + 绝对 token 预算。

预算口径为集中的绝对值 `HISTORY_TOKEN_BUDGET`（与模型窗口解耦），
计数走 `src.infra.llm.token_count`（tiktoken）。直接构造 ChatMessage 调用
模块函数，不发真实网络调用。

"最近 1 轮保留例外"路径会记 `history budget exceeded`，故用 autouse fixture
拦截 `log_event`：既收集该事件供断言，也避免日志污染测试输出。
"""

import pytest

from src.agents.graph.agent_node import _truncate_history
from src.agents.graph.history_window import exceeds_budget, split_history_window
from src.config.const import HISTORY_MAX_TURNS, HISTORY_TOKEN_BUDGET
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens

_MSG_X = "x" * 2000
_MSG_Y = "y" * 2000
# 一轮 = user(x*2000) + assistant(y*2000)；动态算出，避免把分词器数字写死在断言里
_TURN_TOKENS = count_tokens(_MSG_X) + count_tokens(_MSG_Y)


@pytest.fixture(autouse=True)
def _capture_events(monkeypatch) -> list[dict]:
    """拦截 log_event（日志走 loguru，caplog 抓不到）。"""
    calls: list[dict] = []

    def fake_log_event(event, **fields):
        calls.append({"event": event, **fields})

    monkeypatch.setattr("src.core.logging.log_event", fake_log_event)
    return calls


def test_truncate_keeps_recent_turns(_capture_events):
    """15 轮历史 → 输出 ≤ 20 条（最近 10 轮），最后一条是最近的 assistant 消息。"""
    history = []
    for i in range(15):
        history.append(ChatMessage(role="user", content=f"q{i}"))
        history.append(ChatMessage(role="assistant", content=f"a{i}"))

    out = _truncate_history(history, max_turns=HISTORY_MAX_TURNS)

    assert len(out) <= 20  # 10 轮 * 2 条
    assert out[-1].content == "a14"  # 最近一条保留
    # 短历史走默认预算，总量必然 ≤ HISTORY_TOKEN_BUDGET（钉住默认口径生效）
    assert sum(count_tokens(m.content) for m in out) <= HISTORY_TOKEN_BUDGET
    assert _capture_events == []  # 未超预算：不记例外


def test_token_budget_truncates_oldest(_capture_events):
    """总 token 超预算时从最旧逐条弹出，条数下降但不少于最近 1 轮。"""
    history = []
    for _ in range(5):
        history.append(ChatMessage(role="user", content=_MSG_X))
        history.append(ChatMessage(role="assistant", content=_MSG_Y))

    # 预算恰好容得下 2 轮（+1 token 余量），故第 3 轮起被裁
    out = _truncate_history(history, token_budget=_TURN_TOKENS * 2 + 1)

    assert len(out) == 4
    assert out[-1].content == _MSG_Y
    assert _capture_events == []  # 裁到预算内：不记例外


def test_recent_round_always_kept_and_logged(_capture_events):
    """极端小预算下最后 1 轮（2 条）仍完整保留，并显式记录该例外。"""
    history = []
    for i in range(3):
        history.append(ChatMessage(role="user", content=f"q{i}" * 2000))
        history.append(ChatMessage(role="assistant", content=f"a{i}" * 2000))

    out = _truncate_history(history, token_budget=100)

    assert len(out) == 2  # 最近 1 轮（2 条）不被截
    assert out[0].content == "q2" * 2000
    assert out[-1].content == "a2" * 2000
    exceptions = [
        e for e in _capture_events if e["event"] == Event.HISTORY_BUDGET_EXCEEDED
    ]
    assert len(exceptions) == 1
    assert exceptions[0]["budget"] == 100
    assert exceptions[0]["used"] > 100
    assert exceptions[0]["kept"] == 2


# `_MSG_X` / `_MSG_Y` / `_TURN_TOKENS` 已在本文件上方（Phase A 引入）定义，
# 直接复用，**不要重复定义**（会构成逐字重复）。


def _three_turns():
    history = []
    for _ in range(3):
        history.append(ChatMessage(role="user", content=_MSG_X))
        history.append(ChatMessage(role="assistant", content=_MSG_Y))
    return history


def test_split_returns_kept_and_discarded():
    """切分返回 (kept, discarded)，两段拼起来等于原历史。"""
    history = _three_turns()
    kept, discarded = split_history_window(history, max_turns=1, token_budget=10**6)
    assert kept[-1] is history[-1]
    assert len(discarded) == len(history) - len(kept)
    assert discarded + kept == history


def test_split_kept_matches_truncate_history():
    """切分产出的 kept 与 _truncate_history 的返回值逐一相等（同口径）。"""
    history = _three_turns()
    budget = _TURN_TOKENS + 1
    kept, _ = split_history_window(history, max_turns=10, token_budget=budget)
    assert kept == _truncate_history(history, max_turns=10, token_budget=budget)


def test_split_discarded_empty_when_tail_covers_all():
    """保留尾部覆盖全量时 discarded 为空——生成侧据此跳过（防每轮空转）。"""
    history = _three_turns()
    kept, discarded = split_history_window(history, max_turns=10, token_budget=10**6)
    assert discarded == []
    assert kept == history


def test_exceeds_budget():
    """预算判定：等于预算不算超。"""
    history = _three_turns()
    kept, _ = split_history_window(history, max_turns=10, token_budget=10**6)
    total = sum(count_tokens(m.content) for m in kept)
    assert exceeds_budget(kept, total) is False
    assert exceeds_budget(kept, total - 1) is True
