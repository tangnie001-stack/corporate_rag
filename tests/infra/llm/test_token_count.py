"""统一 token 计数入口的单测。

确定性用例注入假 encoder，不触碰真 tiktoken（不联网）；仅中文口径用例依赖
真 encoder 以钉住"真分词器口径 ≠ len//2"，encoder 不可用时跳过。
"""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm import token_count
from src.infra.llm.token_count import count_messages_tokens, count_tokens


class _FakeEncoder:
    """替身 encoder：encode 逐字符返回（等价于按字符计数），不联网。"""

    def encode(self, text: str) -> list[str]:
        return list(text)


def _raise_encoder() -> None:
    """替身 _encoder：模拟分词器不可用（获取即抛，与真实 _encoder 失败语义一致）。"""
    raise RuntimeError("encoder unavailable")


@pytest.fixture
def fake_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """用假 encoder 替换 _encoder，令确定性用例不发起网络调用。"""
    monkeypatch.setattr(token_count, "_encoder", lambda: _FakeEncoder())


def test_count_tokens_handles_empty():
    """空串为 0，不抛异常。"""
    assert count_tokens("") == 0


def test_count_tokens_counts_text(fake_encoder):
    """非空文本计数为正整数。"""
    assert count_tokens("hello world") > 0


def test_count_tokens_exceeds_char_heuristic_on_chinese():
    """中文语料下分词器计数显著高于旧口径 len//2（钉住"确实换了口径"）。

    encoder 不可用时（无网且无本地词表）计数会降级为 len//2，该差值不成立，
    故显式跳过——本用例证明的是"分词器路径生效"，不是降级路径。
    """
    try:
        token_count._encoder()
    except Exception:  # noqa: BLE001
        pytest.skip("tiktoken encoder 不可用，本用例不适用降级口径")
    text = "腾讯控股二零二四年年报营业收入" * 10
    assert count_tokens(text) > len(text) // 2


def test_count_messages_tokens_joins_str_contents(fake_encoder):
    """各条 str 型 content 以空格连接后统一计数。"""
    messages = [
        SystemMessage(content="SYS-ONE"),
        SystemMessage(content="SYS-TWO"),
        HumanMessage(content="hi"),
    ]
    assert count_messages_tokens(messages) == count_tokens("SYS-ONE SYS-TWO hi")


def test_count_messages_tokens_skips_non_message(fake_encoder):
    """非 BaseMessage 元素被跳过，不抛异常。"""
    assert count_messages_tokens(
        [object(), HumanMessage(content="hi")]
    ) == count_tokens("hi")


def test_count_tokens_degrades_when_encoder_unavailable(monkeypatch):
    """encoder 获取抛异常时降级为 len(text)//2，且不抛异常。"""
    monkeypatch.setattr(token_count, "_encoder", _raise_encoder)
    assert count_tokens("abcdefghij") == 5


def test_count_tokens_warns_through_registered_event_once(monkeypatch):
    """降级走注册事件通道告警，且进程内只记一次（_WARNED_UNAVAILABLE 幂等）。"""
    events: list[object] = []
    monkeypatch.setattr(
        token_count.core_logging,
        "log_event",
        lambda event, **fields: events.append(event),
    )
    monkeypatch.setattr(token_count, "_encoder", _raise_encoder)
    monkeypatch.setattr(token_count, "_WARNED_UNAVAILABLE", False)
    # 冷却负缓存须复位：否则跨用例残留的失败时刻会让本用例根本不尝试 encoder
    monkeypatch.setattr(token_count, "_encoder_failed_at", None)
    assert count_tokens("abcdefghij") == 5
    assert count_tokens("abcdefghij") == 5
    assert events == [token_count.Event.TOKEN_ENCODER_UNAVAILABLE]


def test_encoder_failure_is_cooldown_gated(monkeypatch):
    """失败后冷却窗口内不再重试：连续两次 count_tokens 只尝试一次 encoder。

    判别性：若去掉冷却负缓存，两次计数会各触发一次 `_encoder()`，attempts==2。
    """
    attempts: list[int] = []

    def _counting_raise() -> None:
        attempts.append(1)
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(token_count, "_encoder", _counting_raise)
    monkeypatch.setattr(token_count, "_WARNED_UNAVAILABLE", True)  # 告警与本用例无关
    monkeypatch.setattr(token_count, "_encoder_failed_at", None)
    assert count_tokens("abcdefghij") == 5
    assert count_tokens("abcdefghij") == 5
    assert len(attempts) == 1, "冷却窗口内不得重复尝试 encoder"


def test_warm_token_encoder_reports_availability(monkeypatch):
    """预热返回可用性布尔值；encoder 获取抛异常时为 False。"""
    monkeypatch.setattr(token_count, "_encoder", _raise_encoder)
    assert token_count.warm_token_encoder() is False
