"""统一 token 计数入口的单测（不联网：encoder 已在环境缓存）。"""

from langchain_core.messages import HumanMessage, SystemMessage

from src.infra.llm import token_count
from src.infra.llm.token_count import count_messages_tokens, count_tokens


def test_count_tokens_handles_empty():
    """空串为 0，不抛异常。"""
    assert count_tokens("") == 0


def test_count_tokens_counts_text():
    """非空文本计数为正整数。"""
    assert count_tokens("hello world") > 0


def test_count_tokens_exceeds_char_heuristic_on_chinese():
    """中文语料下分词器计数显著高于旧口径 len//2（钉住"确实换了口径"）。"""
    text = "腾讯控股二零二四年年报营业收入" * 10
    assert count_tokens(text) > len(text) // 2


def test_count_messages_tokens_joins_str_contents():
    """各条 str 型 content 以空格连接后统一计数。"""
    messages = [
        SystemMessage(content="SYS-ONE"),
        SystemMessage(content="SYS-TWO"),
        HumanMessage(content="hi"),
    ]
    assert count_messages_tokens(messages) == count_tokens("SYS-ONE SYS-TWO hi")


def test_count_messages_tokens_skips_non_message():
    """非 BaseMessage 元素被跳过，不抛异常。"""
    assert count_messages_tokens(
        [object(), HumanMessage(content="hi")]
    ) == count_tokens("hi")


def test_count_tokens_degrades_when_encoder_unavailable(monkeypatch):
    """encoder 不可用时降级为 len(text)//2，且不抛异常。"""
    monkeypatch.setattr(token_count, "_encoder", lambda: None)
    assert count_tokens("abcdefghij") == 5


def test_warm_token_encoder_reports_availability(monkeypatch):
    """预热返回可用性布尔值；encoder 缺失时为 False。"""
    monkeypatch.setattr(token_count, "_encoder", lambda: None)
    assert token_count.warm_token_encoder() is False
