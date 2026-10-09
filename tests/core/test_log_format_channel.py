"""日志行渠道段（第 5 段）契约 — 段位、取值域、空值占位与下游解析兼容。

段位契约（见 src/core/logging.py 的 _LOG_FORMAT 注释）：
第 3 段 trace_id、第 4 段 session_id、第 5 段 channel。渠道标识用于区分
站点（web）/ 企业微信（wecom）/ 预留飞书（feishu）；未设置时以 CHANNEL_DEFAULT 占位。
"""

from loguru import logger

from src.config.const import CHANNEL_DEFAULT, Channel
from src.infra.llm.trace_context import (
    current_channel,
    current_session_id,
    current_trace_id,
)


def _capture(emit) -> list[str]:
    """装 patcher 后用 _LOG_FORMAT 建 sink，捕获 emit() 期间产出的日志行。"""
    from src.core import logging as core_logging

    core_logging._setup_trace_id_patcher()
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(m), format=core_logging._LOG_FORMAT)
    try:
        emit()
    finally:
        logger.remove(sink_id)
    return captured


def test_channel_segment_position_and_value():
    """trace 第 3 段、session 第 4 段、channel 第 5 段；channel 取 contextvar 的值。"""

    def emit():
        trace_tok = current_trace_id.set("trace_abc")
        sess_tok = current_session_id.set("sess_123")
        chan_tok = current_channel.set(Channel.WECOM)
        try:
            logger.info("hello")
        finally:
            current_channel.reset(chan_tok)
            current_session_id.reset(sess_tok)
            current_trace_id.reset(trace_tok)

    captured = _capture(emit)
    assert captured, "sink 未捕获任何输出"
    parts = captured[0].rstrip("\n").split("|")
    assert len(parts) >= 5, parts
    assert parts[2].strip() == "trace_abc", parts
    assert parts[3].strip() == "sess_123", parts
    assert parts[4].strip() == Channel.WECOM, parts


def test_channel_segment_defaults_to_placeholder():
    """未设置 channel 时以 CHANNEL_DEFAULT 占位，不留空段。"""

    def emit():
        tok = current_channel.set("")
        try:
            logger.info("hello")
        finally:
            current_channel.reset(tok)

    captured = _capture(emit)
    parts = captured[0].rstrip("\n").split("|")
    assert parts[4].strip() == CHANNEL_DEFAULT, parts
    assert parts[4].strip() != "", "不得留空段（下游按段位解析）"


def test_channel_segment_keeps_symptom_metrics_compatible():
    """新增第 5 段后，symptom_metrics 仍能取到 trace 与干净 message。"""
    from src.cli.symptom_metrics import parse_line

    def emit():
        trace_tok = current_trace_id.set("trace_zzz")
        try:
            logger.info("hello world")
        finally:
            current_trace_id.reset(trace_tok)

    line = _capture(emit)[0]
    parsed = parse_line(line)
    assert parsed is not None, line
    assert parsed[0] == "trace_zzz"
    assert parsed[1] == "hello world"
