"""日志 helper 单测 — 格式与 query 截断。"""

from unittest.mock import patch

from src.core.logging import log_event, retrieval_signal


@patch("src.core.logging.logger")
def test_log_event_emits_prefixed_kv(mock_logger):
    log_event("retrieval", "search start", query="q1", kb_id="k1")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("[retrieval] search start")
    assert "query=q1" in msg
    assert "kb_id=k1" in msg


@patch("src.core.logging.logger")
def test_retrieval_signal_truncates_query(mock_logger):
    long_query = "长" * 100
    retrieval_signal("to_web", long_query, 3, kb_id="k1")
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert msg.startswith("retrieval_signal: signal=to_web")
    assert "iteration=3" in msg
    # query 按字符截断为 40 字符 + 省略号（保留引号格式，截断值位于引号内）
    truncated = "长" * 40 + "…"
    assert f'query="{truncated}"' in msg


@patch("src.core.logging.logger")
def test_retrieval_signal_extra_fields(mock_logger):
    retrieval_signal("cited", "腾讯营收", 1, kb_id="k1", citation_count=3)
    mock_logger.info.assert_called_once()
    msg = mock_logger.info.call_args[0][0]
    assert "citation_count=3" in msg
