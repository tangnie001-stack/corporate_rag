"""日志 helper 单测 — 注册表驱动 + 值类型编码 + round-trip。"""

import json
from unittest.mock import patch

from src.core.log_events import Event, Signal
from src.core.logging import encode_value, log_event, retrieval_signal


@patch("src.core.logging.logger")
def test_log_event_routes_level_from_spec(mock_logger):
    log_event(Event.SEARCH_DONE, kb_id="k1", query_len=12, result_count=8)
    mock_logger.log.assert_called_once()
    level, msg = mock_logger.log.call_args[0][0], mock_logger.log.call_args[0][1]
    # spec.level="info" 逻辑级别 → Loguru 级别名大写 INFO
    assert level == "INFO"
    assert msg.startswith("[retrieval] search done")
    assert "kb_id=k1" in msg
    assert "query_len=12" in msg


@patch("src.core.logging.logger")
def test_log_event_warning_level(mock_logger):
    log_event(Event.RERANK_TIMEOUT, timeout_s=3, query="腾讯2024年报")
    level, msg = mock_logger.log.call_args[0][0], mock_logger.log.call_args[0][1]
    assert level == "WARNING"
    assert 'query="腾讯2024年报"' in msg


@patch("src.core.logging.logger")
def test_log_event_coerces_raw_string(mock_logger):
    # 裸字符串合法值 → 规范化枚举；非法值 → ValueError（收口，无自由文本落盘）
    # pyright 抑制：签名收口为 Event 枚举，但本用例刻意测运行期裸字符串收口路径
    log_event("search done", kb_id="k1", query_len=1, result_count=1)  # pyright: ignore[reportArgumentType]
    assert mock_logger.log.call_args[0][0] == "INFO"
    import pytest

    with pytest.raises(ValueError):
        log_event("search don", kb_id="k1", query_len=1, result_count=1)  # pyright: ignore[reportArgumentType]


@patch("src.core.logging.logger")
def test_retrieval_signal_full_query_no_truncation(mock_logger):
    long_query = "长" * 100
    retrieval_signal(Signal.TO_WEB, long_query, 3, kb_id="k1", reason="query too vague")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("retrieval_signal: signal=to_web")
    assert "iteration=3" in msg
    # query 完整记录（不再截断 40）
    assert f'query="{long_query}"' in msg
    # 附加字段含空格 → 引号 + JSON 转义
    assert 'reason="query too vague"' in msg


def test_encode_value_charset_roundtrip():
    # round-trip：编码输出能被解析回原值（与 replay CLI 共用规则）
    raw = '腾讯 2024 年报 "Q&A"\\n第二行'
    encoded = encode_value(raw)
    assert json.loads(encoded) == raw
    assert encode_value("kb1") == "kb1"
    assert encode_value(8) == "8"
    assert encode_value(True) == "true"
    assert encode_value(["2023", "2025"]) == '["2023","2025"]'
