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


def test_extract_heading_tree_forwards_stderr_on_success():
    """成功路径：子进程 stderr 逐行转发到主进程 logger（带 trace_id 串联）。"""
    stderr = (
        "pm heading subprocess: start /tmp/x.pdf\n"
        "pm heading subprocess: extracted 2 headings"
    )
    with (
        patch(
            "src.parsers.pdf_heading_extractor.subprocess.run",
            return_value=_fake_proc("[]", stderr=stderr),
        ),
        patch("src.parsers.pdf_heading_extractor.logger.info") as mock_info,
    ):
        tree = extract_heading_tree("/tmp/x.pdf")
    assert tree == []
    assert mock_info.call_count == 2
    assert mock_info.call_args_list[0][0] == (
        "pm subprocess: {}",
        "pm heading subprocess: start /tmp/x.pdf",
    )
    assert (
        mock_info.call_args_list[1][0][1]
        == "pm heading subprocess: extracted 2 headings"
    )


def test_extract_heading_tree_forwards_truncated_stderr():
    """成功路径：stderr 超过 20 行时只转发尾部 20 行。"""
    stderr = "\n".join(f"line {i}" for i in range(25))
    with (
        patch(
            "src.parsers.pdf_heading_extractor.subprocess.run",
            return_value=_fake_proc("[]", stderr=stderr),
        ),
        patch("src.parsers.pdf_heading_extractor.logger.info") as mock_info,
    ):
        extract_heading_tree("/tmp/x.pdf")
    assert mock_info.call_count == 20
    # 尾部 20 行 = line 5 .. line 24
    assert mock_info.call_args_list[0][0][1] == "line 5"
    assert mock_info.call_args_list[-1][0][1] == "line 24"


def test_extract_heading_tree_empty_stderr_no_forward():
    """成功路径：stderr 为空时不转发。"""
    with (
        patch(
            "src.parsers.pdf_heading_extractor.subprocess.run",
            return_value=_fake_proc("[]"),
        ),
        patch("src.parsers.pdf_heading_extractor.logger.info") as mock_info,
    ):
        extract_heading_tree("/tmp/x.pdf")
    assert mock_info.call_count == 0
