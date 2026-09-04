"""会话注入单测 — current_session_id ContextVar + 日志行第 4 段。"""

from loguru import logger

from src.infra.llm.trace_context import current_session_id


def test_session_contextvar_default_empty():
    assert current_session_id.get() == ""


def test_log_line_contains_session_segment():
    from src.core import logging as core_logging

    # 装 patcher（configure 覆盖，幂等），使 sink 按 _LOG_FORMAT 渲染 extra
    core_logging._setup_trace_id_patcher()
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(m), format=core_logging._LOG_FORMAT)
    try:
        token = current_session_id.set("sess_123")
        try:
            logger.info("hello")
        finally:
            current_session_id.reset(token)
    finally:
        logger.remove(sink_id)
    assert captured, "sink 未捕获任何输出"
    # session_id 落在第 4 段（time | level | trace_id | session_id | ...）
    assert any("sess_123" in line for line in captured), captured
