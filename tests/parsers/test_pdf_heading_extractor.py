"""pdf_heading_extractor.extract_heading_tree 的单元测试（mock subprocess.run）。"""

import subprocess
from unittest.mock import Mock, patch

from src.parsers.pdf_heading_extractor import extract_heading_tree


def _fake_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> Mock:
    """构造假的 CompletedProcess 对象。"""
    proc = Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_extract_heading_tree_parses_json():
    """正常路径：解析子进程 stdout JSON 为标题树。"""
    with patch(
        "src.parsers.pdf_heading_extractor.subprocess.run",
        return_value=_fake_proc('[[1, "一、主要财务数据"]]'),
    ) as mock_run:
        tree = extract_heading_tree("/tmp/x.pdf")
    assert tree == [(1, "一、主要财务数据")]
    assert mock_run.call_count == 1


def test_extract_heading_tree_nonzero_exit_returns_empty():
    """非零退出码：返回空列表，不抛异常。"""
    with patch(
        "src.parsers.pdf_heading_extractor.subprocess.run",
        return_value=_fake_proc(returncode=1, stderr="boom"),
    ):
        assert extract_heading_tree("/tmp/x.pdf") == []


def test_extract_heading_tree_invalid_json_returns_empty():
    """非法 JSON：返回空列表，不抛异常。"""
    with patch(
        "src.parsers.pdf_heading_extractor.subprocess.run",
        return_value=_fake_proc("not json"),
    ):
        assert extract_heading_tree("/tmp/x.pdf") == []


def test_extract_heading_tree_timeout_returns_empty():
    """超时：返回空列表，不抛异常。"""
    with patch(
        "src.parsers.pdf_heading_extractor.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=1),
    ):
        assert extract_heading_tree("/tmp/x.pdf") == []


def test_extract_heading_tree_passes_trace_id():
    """trace_id 作为 argv[2] 传给子进程（日志串联）。"""
    from src.infra.llm.trace_context import current_trace_id

    token = current_trace_id.set("trace_test_123")
    try:
        with patch(
            "src.parsers.pdf_heading_extractor.subprocess.run",
            return_value=_fake_proc("[]"),
        ) as mock_run:
            extract_heading_tree("/tmp/x.pdf")
    finally:
        current_trace_id.reset(token)
    cmd = mock_run.call_args.args[0]
    assert cmd[-1] == "trace_test_123"
    assert cmd[-2] == "/tmp/x.pdf"
