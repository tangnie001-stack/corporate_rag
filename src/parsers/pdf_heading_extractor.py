"""PDF 标题树子进程助手 — 主进程解析器的 pm 通道。

为什么是子进程：
  import pymupdf4llm 会设置全局 mupdf 状态（quad corrections / layout 分析器），
  永久破坏同进程内 find_tables().extract() 的单元格顺序（63,134,713 → 63 134 713\n, ,）。
  因此本模块的 pymupdf4llm 只在 `python -m src.parsers.pdf_heading_extractor <file>`
  子进程入口的 __main__ 内 import；主进程调用 extract_heading_tree() 时绝不 import。

主进程使用：
  from src.parsers.pdf_heading_extractor import extract_heading_tree
  tree = extract_heading_tree(file_path)  # [(level, heading), ...]
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import cast

from loguru import logger

from src.config import (
    MAX_CONCURRENT_HEADING_SUBPROCESS,
    PDF_HEADING_SUBPROCESS_TIMEOUT,
)

# 限制主进程并发 pm 子进程数（每个子进程 import pymupdf4llm 约 200MB 内存，
# 批量入库并发子进程可能击穿容器 mem_limit）
_SUBPROCESS_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_HEADING_SUBPROCESS)


def extract_heading_tree(file_path: str) -> list[tuple[int, str]]:
    """在子进程中跑 pymupdf4llm 标题树管道，返回清洗后的标题树。

    子进程自身的日志（pm 管道 start/标题数等）打到 stderr，本函数在成功
    路径逐行转发到主进程 loguru（主进程 loguru patcher 注入当前请求
    trace_id），使子进程活动在主日志流中与请求串联。

    Args:
        file_path: PDF 文件路径

    Returns:
        标题层级列表 [(level, heading), ...]；失败/超时返回空列表（不抛异常）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    cmd = [sys.executable, "-m", "src.parsers.pdf_heading_extractor", file_path]
    try:
        with _SUBPROCESS_SEMAPHORE:
            proc = subprocess.run(
                cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=PDF_HEADING_SUBPROCESS_TIMEOUT,
                check=False,
            )
    except subprocess.TimeoutExpired:
        logger.warning(
            "PDF heading subprocess timed out (>{}) for '{}'",
            PDF_HEADING_SUBPROCESS_TIMEOUT,
            file_path,
        )
        return []
    except OSError as exc:
        logger.warning(
            "PDF heading subprocess failed to start for '{}': {}",
            file_path,
            exc,
        )
        return []
    if proc.returncode != 0:
        logger.warning(
            "PDF heading subprocess exited {} for '{}': {}",
            proc.returncode,
            file_path,
            proc.stderr[-500:],
        )
        return []
    # 成功路径：转发子进程 stderr（pm 管道自身的日志/警告）到主进程日志，
    # 由主进程 loguru patcher 注入当前请求 trace_id；截断尾部防刷屏
    if proc.stderr.strip():
        for line in proc.stderr.strip().splitlines()[-20:]:
            logger.info("pm subprocess: {}", line)
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "PDF heading subprocess returned invalid JSON for '{}'",
            file_path,
        )
        return []
    if not isinstance(data, list):
        logger.warning(
            "PDF heading subprocess returned unexpected shape for '{}'",
            file_path,
        )
        return []
    return [(int(level), str(title)) for level, title in data]


if __name__ == "__main__":
    # 仅在子进程入口 import pymupdf4llm（避免全局污染主进程 fitz 表格提取）
    import pymupdf
    import pymupdf4llm

    from src.parsers.pymupdf_parser import PyMuPDFParser

    # 子进程活动打到 stderr，由主进程 extract_heading_tree 成功路径转发到
    # 主进程日志（带 trace_id）；trace_id 不在此处处理
    print(f"pm heading subprocess: start {sys.argv[1]}", file=sys.stderr)

    parser = PyMuPDFParser()
    doc = pymupdf.open(sys.argv[1])
    try:
        md_pages = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            show_progress=False,
            write_images=False,
            header=False,
            footer=False,
        )
    finally:
        doc.close()
    # page_chunks=True 时始终返回 list[dict]，显式声明类型便于静态检查
    pages = cast(list[dict], md_pages)
    # 复刻现有 pm 管道：逐页 _clean_markdown_noise → \n 拼接 → _extract_heading_tree
    # （顺序必须一致，否则标题树与当前 parse() 漂移，如 "# #" 伪标题）
    cleaned_pages = [parser._clean_markdown_noise(p["text"]) for p in pages]
    full_text = "\n".join(cleaned_pages)
    tree = parser._extract_heading_tree(full_text)
    print(f"pm heading subprocess: extracted {len(tree)} headings", file=sys.stderr)
    json.dump([[level, title] for level, title in tree], sys.stdout)
