# fix-pdf-table-integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用双通道解析修复 Q4 RAGAS faithfulness 回归（0.8889 → ≥ 0.9333 基线）：fitz 主进程产出全文+表格（跨行单元格不拆列），pymupdf4llm 标题树走子进程隔离，标题定位去空白归一化匹配。

**Architecture:** 主进程只 `import pymupdf`（fitz 通道，内容权威源）；`import pymupdf4llm` 会永久破坏同进程内 `find_tables().extract()` 单元格顺序（实测 `63,134,713` → `63 134 713\n, ,` 且不可逆），因此标题树提取放独立子进程 `pdf_heading_extractor.py`（`python -m` 调用，完整复刻现有 pm 管道输出清洗后标题树，实测与当前树逐字节一致）。边距过滤拆为 `HEADER_MARGIN=45`/`FOOTER_MARGIN=80` 恢复页首标题。`_locate_heading_line` 增加去全部空白归一化退化匹配。

**Tech Stack:** Python 3.11+ / pymupdf 1.28.2 / pymupdf4llm 1.28.2 / pytest / ruff / pyright

## Global Constraints

- `pymupdf==1.28.2` + `pymupdf4llm==1.28.2`（版本锁定，不增删依赖）
- 主进程（`src/parsers/pymupdf_parser.py`）**不得 import pymupdf4llm**——其 import 副作用全局且不可逆
- 测试进程内**不得 import pymupdf4llm**（pytest 单进程共享全局，会污染 fitz 表格提取）
- 全仓仅 `pdf_heading_extractor.py` 的 `__main__` 块内可 import pymupdf4llm
- 代码风格：不用三元表达式；docstring 必写；`from src.config import *` 的常量都进 settings.py
- 注释、docstring、commit message 用中文
- 验证门禁：`pytest tests/ -v` 全绿、`ruff check .` 无错误、`pyright src/` 不新增 error

---

### Task 1: 配置常量拆分（HEADER_MARGIN / FOOTER_MARGIN / 子进程常量）

**Files:**
- Modify: `src/config/settings.py:148-150`（`MIN_TEXT_CHARS` 之后、`CROSS_PAGE_TABLE_MERGE_THRESHOLD` 之前）
- Test: `tests/config/test_settings.py`

**Interfaces:**
- Consumes: 无
- Produces: `HEADER_MARGIN: int = 45`、`FOOTER_MARGIN: int = 80`、`PDF_HEADING_SUBPROCESS_TIMEOUT: int = 180`、`MAX_CONCURRENT_HEADING_SUBPROCESS: int = 2`（均通过 `from src.config import ...` 导出）；`HEADER_FOOTER_MARGIN` 删除

- [ ] **Step 1: 写失败测试**

在 `tests/config/test_settings.py` 末尾追加：

```python
def test_margin_constants_split():
    """HEADER_MARGIN/FOOTER_MARGIN 替换 HEADER_FOOTER_MARGIN（默认 45/80）。"""
    from src.config import FOOTER_MARGIN, HEADER_MARGIN

    assert HEADER_MARGIN == 45
    assert FOOTER_MARGIN == 80
    with pytest.raises(ImportError):
        from src.config import HEADER_FOOTER_MARGIN  # noqa: F401


def test_pdf_heading_subprocess_constants():
    """pm 标题树子进程超时与并发上限默认值。"""
    from src.config import (
        MAX_CONCURRENT_HEADING_SUBPROCESS,
        PDF_HEADING_SUBPROCESS_TIMEOUT,
    )

    assert PDF_HEADING_SUBPROCESS_TIMEOUT == 180
    assert MAX_CONCURRENT_HEADING_SUBPROCESS == 2
```

在文件顶部确认 `import pytest` 存在（当前文件没有，需要加）：

```python
import pytest
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/config/test_settings.py -v`
Expected: `test_margin_constants_split` FAIL（`ImportError: cannot import name 'HEADER_MARGIN'`）；`test_pdf_heading_subprocess_constants` FAIL（导入错误）

- [ ] **Step 3: 修改 settings.py**

把 `src/config/settings.py:150` 的：

```python
# 页眉页脚排除阈值：距离页面顶部/底部 N px 内的文本块视为页眉页脚
HEADER_FOOTER_MARGIN: int = int(os.getenv("HEADER_FOOTER_MARGIN", "80"))
```

替换为：

```python
# 页眉页脚边距过滤（fitz 通道按 y 坐标剔除重复页眉/页脚）
# 顶部 45：保留页首内容标题（如 tencent 的"简明综合财务状况表"，y≈52-66）
# 底部 80：剔除页码页脚（如 neusoft 的 "1 / 10 东软集团..."，y≈768-791）
HEADER_MARGIN: int = int(os.getenv("HEADER_MARGIN", "45"))
FOOTER_MARGIN: int = int(os.getenv("FOOTER_MARGIN", "80"))
# pm 标题树子进程：超时秒数与主进程并发上限（每个子进程 import pymupdf4llm 约 200MB）
PDF_HEADING_SUBPROCESS_TIMEOUT: int = int(os.getenv("PDF_HEADING_SUBPROCESS_TIMEOUT", "180"))
MAX_CONCURRENT_HEADING_SUBPROCESS: int = int(os.getenv("MAX_CONCURRENT_HEADING_SUBPROCESS", "2"))
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/config/test_settings.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/config/settings.py tests/config/test_settings.py
git commit -m "feat(config): 拆分 HEADER_MARGIN/FOOTER_MARGIN，新增 pm 子进程常量"
```

---

### Task 2: 标题定位去空白归一化匹配

**Files:**
- Modify: `src/rag/heading_locator.py`（新增 `import re`；`_locate_heading_line` 退化路径改归一化比较）
- Test: `tests/rag/test_heading_locator.py`

**Interfaces:**
- Consumes: 现有 `_locate_heading_line(full_text: str, title: str) -> int`
- Produces: 新增模块级函数 `_normalize_ws(text: str) -> str`（去全部空白）；`_locate_heading_line` 语义扩展（精确 find + 行校验不命中后，按去空白归一化逐行比较）；`build_heading_segments`/`locate_heading_path` 签名不变

- [ ] **Step 1: 写失败测试**

在 `tests/rag/test_heading_locator.py` 顶部加 import 并追加测试：

```python
from src.rag.heading_locator import _locate_heading_line, _normalize_ws
```

```python
def test_normalize_ws_removes_all_whitespace():
    """_normalize_ws 去掉全部空白（含单空格与标点邻接空格）。"""
    assert _normalize_ws("收入高质量增长 运营效率持续提升") == "收入高质量增长运营效率持续提升"
    assert _normalize_ws("约 1 , 120 亿港元") == "约1,120亿港元"
    assert _normalize_ws("同比 8% ，毛利") == "同比8%，毛利"


def test_locate_heading_line_whitespace_diff():
    """单空格差异：pm 标题无空格，fitz 正文有空格，仍能定位。"""
    full_text = "收入高质量增长 运营效率持续提升\n内容A"
    assert _locate_heading_line(full_text, "收入高质量增长运营效率持续提升") == 0


def test_locate_heading_line_emphasis_punct_diff():
    """强调标点邻接空格：'约 1 , 120 亿港元' vs '约 1,120 亿港元' 仍能定位。"""
    full_text = "回购增长逾倍至约 1,120 亿港元\n内容A"
    assert _locate_heading_line(full_text, "回购增长逾倍至约 1 , 120 亿港元") == 0


def test_locate_heading_line_table_row_not_matched():
    """表格内 |...| 包裹的标题不作为独立标题行定位（接受缺口）。"""
    full_text = "| 其他财务资料 | 100 | 200 |\n内容A"
    assert _locate_heading_line(full_text, "其他财务资料") == -1


def test_locate_heading_line_truncated_not_matched():
    """截断标题（标题是正文子串但整行不相等）不匹配。"""
    full_text = "总收入：同比增长 8% 至 5,835 亿港元\n内容A"
    assert _locate_heading_line(full_text, "总收入：同比增长 8%") == -1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/rag/test_heading_locator.py -v`
Expected: `test_normalize_ws_removes_all_whitespace` FAIL（`ImportError: cannot import name '_normalize_ws'`）；其余新测试 FAIL（`_normalize_ws` 未定义 / 匹配失败）

- [ ] **Step 3: 修改 heading_locator.py**

文件顶部加 `import re`（当前文件无 import），并新增：

```python
def _normalize_ws(text: str) -> str:
    """去掉全部空白，用于标题匹配的格式归一化。

    中文无词间空格，pm 标题与 fitz 正文的空白差异（合并/保留、标点邻接空格）
    只需去掉全部空白即可对齐。仅用于比较，不影响原文。

    Args:
        text: 待归一化的标题或行文本

    Returns:
        去空白后的文本
    """
    return re.sub(r"\s+", "", text)
```

把 `_locate_heading_line` 的退化分支（第 71-77 行）替换为：

```python
    # 退化：去全部空白归一化逐行匹配（覆盖 pm 标题 vs fitz 正文的空白格式差异）
    norm_title = _normalize_ws(title)
    line_start = 0
    for line in full_text.split("\n"):
        stripped = line.strip()
        if _normalize_ws(stripped.lstrip("#").strip()) == norm_title:
            return line_start
        line_start += len(line) + 1
    return -1
```

注意：原退化分支是 `stripped.lstrip("#").strip() == title`（精确相等），归一化比较是严格超集（精确相等 ⇒ 归一化相等），不会漏掉原能匹配的标题。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/rag/test_heading_locator.py -v`
Expected: PASS（原有 6 个 + 新增 5 个，共 11 个）

- [ ] **Step 5: 提交**

```bash
git add src/rag/heading_locator.py tests/rag/test_heading_locator.py
git commit -m "feat(rag): heading 定位去空白归一化退化匹配"
```

---

### Task 3: pm 标题树子进程助手

**Files:**
- Create: `src/parsers/pdf_heading_extractor.py`
- Test: `tests/parsers/test_pdf_heading_extractor.py`（新建）

**Interfaces:**
- Consumes: `PDF_HEADING_SUBPROCESS_TIMEOUT`、`MAX_CONCURRENT_HEADING_SUBPROCESS`（Task 1）；`PyMuPDFParser._clean_markdown_noise` / `_extract_heading_tree`（Task 4 实现，但本 Task 只 import，调用发生在 `__main__` 子进程运行时，Task 4 前子进程不跑——集成测试在 Task 5 验证）
- Produces: `extract_heading_tree(file_path: str) -> list[tuple[int, str]]`（主进程调用：信号量保护下 `subprocess.run`，返回清洗后标题树；失败/超时返回空列表不抛异常；内部读 `current_trace_id` ContextVar 作为 argv[2] 传给子进程，子进程 `logger.patch` 注入 trace_id 到自己的日志）；`python -m src.parsers.pdf_heading_extractor <file> [trace_id]` 子进程入口

- [ ] **Step 1: 写失败测试**

创建 `tests/parsers/test_pdf_heading_extractor.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/parsers/test_pdf_heading_extractor.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'src.parsers.pdf_heading_extractor'`）

- [ ] **Step 3: 创建 pdf_heading_extractor.py**

创建 `src/parsers/pdf_heading_extractor.py`：

```python
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

from loguru import logger

from src.config import (
    MAX_CONCURRENT_HEADING_SUBPROCESS,
    PDF_HEADING_SUBPROCESS_TIMEOUT,
)
from src.infra.llm.trace_context import current_trace_id

# 限制主进程并发 pm 子进程数（每个子进程 import pymupdf4llm 约 200MB 内存，
# 批量入库并发子进程可能击穿容器 mem_limit）
_SUBPROCESS_SEMAPHORE = threading.Semaphore(MAX_CONCURRENT_HEADING_SUBPROCESS)


def extract_heading_tree(file_path: str) -> list[tuple[int, str]]:
    """在子进程中跑 pymupdf4llm 标题树管道，返回清洗后的标题树。

    从 current_trace_id ContextVar 读取当前请求 trace_id，作为 argv[2]
    传给子进程，使子进程自己的 loguru 日志也能带上 trace_id（ContextVar
    不跨进程，需显式传递）；主进程侧的失败警告由 loguru patcher 自动注入
    trace_id（asyncio.to_thread 会传播 contextvar）。

    Args:
        file_path: PDF 文件路径

    Returns:
        标题层级列表 [(level, heading), ...]；失败/超时返回空列表（不抛异常）。
    """
    repo_root = Path(__file__).resolve().parents[2]
    trace_id = current_trace_id.get()
    cmd = [sys.executable, "-m", "src.parsers.pdf_heading_extractor", file_path]
    if trace_id:
        cmd.append(trace_id)
    try:
        with _SUBPROCESS_SEMAPHORE:
            proc = subprocess.run(
                cmd,
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=PDF_HEADING_SUBPROCESS_TIMEOUT,
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
    import pymupdf  # noqa: PLC0415
    import pymupdf4llm  # noqa: PLC0415
    from src.parsers.pymupdf_parser import PyMuPDFParser  # noqa: PLC0415

    # trace_id 作为可选 argv[2] 传入；子进程日志用 loguru patcher 注入，
    # 与主进程请求日志串联（ContextVar 不跨进程，需显式传递）
    _trace_id = sys.argv[2] if len(sys.argv) > 2 else None
    if _trace_id:
        logger.configure(
            extra={"trace_id": ""},
            patcher=lambda record: record["extra"].__setitem__("trace_id", _trace_id),
        )

    parser = PyMuPDFParser()
    doc = pymupdf.open(sys.argv[1])
    try:
        pages = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            show_progress=False,
            write_images=False,
            header=False,
            footer=False,
        )
    finally:
        doc.close()
    # 复刻现有 pm 管道：逐页 _clean_markdown_noise → \n 拼接 → _extract_heading_tree
    # （顺序必须一致，否则标题树与当前 parse() 漂移，如 "# #" 伪标题）
    cleaned_pages = [parser._clean_markdown_noise(p["text"]) for p in pages]
    full_text = "\n".join(cleaned_pages)
    tree = parser._extract_heading_tree(full_text)
    json.dump([[level, title] for level, title in tree], sys.stdout)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/parsers/test_pdf_heading_extractor.py -v`
Expected: PASS（5 个测试）

- [ ] **Step 5: 提交**

```bash
git add src/parsers/pdf_heading_extractor.py tests/parsers/test_pdf_heading_extractor.py
git commit -m "feat(parser): pm 标题树子进程助手（隔离 pymupdf4llm 全局污染）"
```

---

### Task 4: parser 双通道重写（fitz 全文+表格主进程）

**Files:**
- Modify: `src/parsers/pymupdf_parser.py`（全文件重写；保留 `_split_chunks`/`_extract_heading_tree`/`_clean_heading`/`_clean_markdown_noise`/正则常量；删除 `import pymupdf4llm`、`_extract_text_by_page`；新增 `_extract_text_by_page_fitz`/`_extract_tables_from_page`/`_table_to_markdown`）
- Test: `tests/parsers/test_pymupdf_parser.py`

**Interfaces:**
- Consumes: `extract_heading_tree`（Task 3）、`HEADER_MARGIN`/`FOOTER_MARGIN`/`MIN_TEXT_CHARS`/`CHUNK_SIZE`/`CHUNK_OVERLAP`（Task 1）；`BaseParser.sanitize_cell`（src/parsers/base.py:72）
- Produces: `PyMuPDFParser.parse(file_path) -> ParseResult`（契约不变：chunks/total_pages/total_chars/is_scanned/file_type/heading_tree）；`_extract_heading_tree(full_text)` 保持（供子进程 `__main__` 使用）

- [ ] **Step 1: 更新失败测试**

在 `tests/parsers/test_pymupdf_parser.py` 顶部加 import：

```python
from unittest.mock import patch
```

在 `TestPyMuPDFParser.setup_method` 中追加 mock（单测不跑真实子进程）：

```python
    def setup_method(self):
        """每个测试前初始化解析器和测试文件路径。"""
        self.parser = PyMuPDFParser()
        # 单测 mock 子进程：标题树用固定值，避免真实 subprocess 依赖
        self._tree_patcher = patch(
            "src.parsers.pymupdf_parser.extract_heading_tree",
            return_value=[(1, "一、主要财务数据"), (2, "（一）会计数据")],
        )
        self._tree_patcher.start()

    def teardown_method(self):
        """每个测试后停止子进程 mock。"""
        self._tree_patcher.stop()
```

在 `TestPyMuPDFParser` 类内追加（放在 `test_clean_markdown_noise` 之后）：

```python
    def test_table_cell_newline_flattened(self):
        """跨行单元格拍平：标签与数值保持同行（Q4 修复核心）。"""
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        # 同一个 chunk 内同时含标签与数值 → 跨行单元格未被拆成两行
        assert any(
            "购建固定资产" in c.content and "63,134,713" in c.content
            for c in result.chunks
        )

    def test_header_margin_keeps_page_top_content(self):
        """顶部边距 45：页首标题/内容段保留（tencent 回归）。"""
        path = "data/test_docs/tencent_2024_annual.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        joined = "\n".join(c.content for c in result.chunks)
        assert "二零二四年第四季业绩摘要" in joined
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/parsers/test_pymupdf_parser.py -v`
Expected: `test_table_cell_newline_flattened` FAIL（`AttributeError: 'PyMuPDFParser' object has no attribute '_extract_text_by_page_fitz'`）；`test_header_margin_keeps_page_top_content` FAIL

- [ ] **Step 3: 重写 pymupdf_parser.py**

完整替换 `src/parsers/pymupdf_parser.py`：

```python
"""PDF 文档解析器 — 双通道：fitz 全文+表格（主进程）+ pymupdf4llm 标题树（子进程）。

处理流程：
  1. fitz 通道（主进程）：逐页 find_tables() + blocks 文本提取，sanitize_cell 拍平跨行单元格
  2. 检测扫描件（每页可提取文字少于 MIN_TEXT_CHARS 视为扫描页）
  3. pm 通道（子进程）：pdf_heading_extractor 完整复刻 pymupdf4llm 标题树管道，输出清洗后标题树
  4. 按页分块（每页独立分块，保留页码元数据）

为什么 pm 走子进程：
  import pymupdf4llm 会设置全局 mupdf 状态，永久破坏同进程内
  find_tables().extract() 的单元格顺序（63,134,713 → 63 134 713\\n, ,）。
  因此主进程不 import pymupdf4llm，标题树提取放子进程隔离全局污染。
"""

import os
import re

import pymupdf
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    FOOTER_MARGIN,
    HEADER_MARGIN,
    MIN_TEXT_CHARS,
)
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.parsers.pdf_heading_extractor import extract_heading_tree

# 匹配 Markdown 表格（以 | 开头和结尾的行组成的多行表格）
TABLE_PATTERN = re.compile(r"^\|.+\|[\s\S]*?^\|.+\|", re.MULTILINE)

# 匹配 pymupdf4llm 强调残留（**_X_** 加粗+斜体、**X** 加粗），保留文本内容。
# 注意：不含裸 _X_ 斜体规则——年报表格里邮箱等含下划线的真实内容会被误伤。
EMPHASIS_PATTERN = re.compile(r"\*{1,2}_([^*_]+)_\*{1,2}|\*\*([^*]+)\*\*")
# 匹配 <br> 换行残留（表格行内的换行会破坏 | 结构，统一替换为空格）
BR_PATTERN = re.compile(r"<br\s*/?>")
# 匹配其余内联 HTML 标签残留（<sup>、<mark>、<u> 等，保留标签内文本）
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
# 匹配页脚页码行（如 "6 / 12"），子进程清洗标题时兜底剔除
FOOTER_PAGE_NUM_PATTERN = re.compile(r"^\d+\s*/\s*\d+\s*$", re.MULTILINE)
# 伪标题过滤：复选框行（√/□ 适用/不适用 等）、编制单位标注行
PSEUDO_HEADING_PATTERN = re.compile(r"[√□]|^编制单位")


class PyMuPDFParser(BaseParser):
    """PDF 文档解析器 — fitz 全文+表格（主进程）+ pymupdf4llm 标题树（子进程）。"""

    def parse(self, file_path: str) -> ParseResult:
        """解析 PDF 文件，按页提取文字并分块，同时提取标题树。

        Args:
            file_path: PDF 文件路径

        Returns:
            ParseResult，file_type="pdf"，total_pages=实际页数，
            heading_tree=标题层级列表

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        doc = pymupdf.open(file_path)
        # 用 try-finally 保证异常时也关闭文件句柄，防止资源泄漏
        try:
            text_by_page, total_chars, scanned_pages = self._extract_text_by_page_fitz(
                doc
            )
        finally:
            doc.close()

        # 所有页都是扫描页 → 标记为扫描件
        total_pages = len(text_by_page)
        is_scanned = scanned_pages == total_pages
        source = os.path.basename(file_path)

        # pm 标题树（子进程）；扫描件无文字层，跳过子进程
        heading_tree: list[tuple[int, str]] = []
        if not is_scanned:
            heading_tree = extract_heading_tree(file_path)

        chunks = self._split_chunks(text_by_page, source)

        return ParseResult(
            chunks=chunks,
            total_pages=total_pages,
            total_chars=total_chars,
            is_scanned=is_scanned,
            file_type="pdf",
            heading_tree=heading_tree,
        )

    def _extract_text_by_page_fitz(
        self, doc
    ) -> tuple[list[tuple[str, int]], int, int]:
        """fitz 通道：逐页提取文本块与表格，组装页面文字，统计字符数与扫描页数。

        表格用 find_tables().extract() + sanitize_cell 拍平跨行单元格
        （单元格内 \\n → 空格），标签与数值保持同行，避免表格分块丢列。

        Args:
            doc: pymupdf 打开的文档对象

        Returns:
            (text_by_page, total_chars, scanned_pages)：
              - text_by_page: [(页面文字, 页码), ...]，页码从 1 开始
              - total_chars: 全文总字符数
              - scanned_pages: 文字量低于 MIN_TEXT_CHARS 的页数
        """
        total_pages = len(doc)
        text_by_page: list[tuple[str, int]] = []
        total_chars = 0
        scanned_pages = 0

        for page_num in range(total_pages):
            page = doc[page_num]
            page_height = page.rect.height

            # 先提取表格，按视觉顺序排序，过滤误检（不足 2 行的视为误检）
            table_finder = page.find_tables()
            if table_finder is None:
                tables = []
            else:
                tables = sorted(
                    list(table_finder), key=lambda t: (t.bbox[1], t.bbox[0])
                )
            tables = [t for t in tables if len(t.extract()) >= 2]
            table_mds = self._extract_tables_from_page(page, tables)
            table_bboxes = [t.bbox for t in tables]

            if table_bboxes:
                # 有表格时：只提取表格区域外的文字，避免文本层与表格内容重复
                blocks = page.get_text("blocks")
                items: list[tuple[float, str, bool]] = []
                for b in blocks:
                    x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                    if y1 < HEADER_MARGIN or y0 > page_height - FOOTER_MARGIN:
                        continue
                    if not b[4].strip():
                        continue
                    bbox = pymupdf.Rect(x0, y0, x1, y1)
                    block_area = (x1 - x0) * (y1 - y0)
                    in_table = False
                    for tb in table_bboxes:
                        tr = pymupdf.Rect(tb)
                        if bbox.intersects(tr):
                            inter = bbox.intersect(tr)
                            inter_area = (inter.x1 - inter.x0) * (
                                inter.y1 - inter.y0
                            )
                            if inter_area / block_area > 0.5:
                                in_table = True
                                break
                    if not in_table:
                        items.append(((y0 + y1) / 2, b[4], False))
                # 收集表格 markdown（取表格的 Y 中心位置）
                for table, md in zip(tables, table_mds):
                    tb = table.bbox
                    items.append(((tb[1] + tb[3]) / 2, md, True))
                # 按 Y 位置排序后组装文本
                items.sort(key=lambda x: x[0])
                text_parts: list[str] = []
                for _, content, is_table in items:
                    if text_parts and is_table:
                        text_parts.append("\n\n" + content)
                    elif text_parts:
                        text_parts.append("\n" + content)
                    else:
                        text_parts.append(content)
                text = "".join(text_parts)
            else:
                # 无表格时：按块提取并排除页眉页脚
                blocks = page.get_text("blocks")
                content_blocks = []
                for b in blocks:
                    y0 = float(b[1])
                    y1 = float(b[3])
                    if y1 < HEADER_MARGIN or y0 > page_height - FOOTER_MARGIN:
                        continue
                    if not b[4].strip():
                        continue
                    content_blocks.append(b[4])
                text = "\n".join(content_blocks)

            char_count = len(text.strip())
            total_chars += char_count
            # 扫描页检测：文字极少说明该页可能是图片扫描件
            if char_count < MIN_TEXT_CHARS:
                scanned_pages += 1
            text_by_page.append((text, page_num + 1))
        return text_by_page, total_chars, scanned_pages

    def _split_chunks(
        self, text_by_page: list[tuple[str, int]], source: str
    ) -> list[ChunkData]:
        """按页分块，每页独立分块并保留页码元数据。

        Args:
            text_by_page: [(页面文字, 页码), ...]
            source: 原始文件名

        Returns:
            分块列表，block_type 按 TABLE_PATTERN 标记为 table/text
        """
        # 分块器（中文友好分隔符）
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )

        chunks = []
        for page_text, page_num in text_by_page:
            if not page_text.strip():
                continue  # 跳过空白页
            texts = splitter.split_text(page_text)
            for i, t in enumerate(texts):
                block_type = "table" if TABLE_PATTERN.search(t) else "text"
                chunks.append(
                    ChunkData(
                        content=t,
                        metadata={
                            "source": source,
                            "page": page_num,
                            "block_type": block_type,
                        },
                        # chunk_id 包含页码，便于定位和去重
                        chunk_id=f"{source}:p{page_num}:{i}",
                    )
                )
        return chunks

    def _extract_tables_from_page(self, page, tables=None) -> list[str]:
        """从单页 PDF 提取所有表格，返回 Markdown 格式的表格字符串列表。

        Args:
            page: PyMuPDF 页面对象
            tables: 预排序的表格列表（为 None 时自动获取并按视觉顺序排序）

        Returns:
            Markdown 表格字符串列表（每个元素是一个完整的 Markdown 表格）
        """
        if tables is None:
            tables = list(page.find_tables())
            # 按视觉顺序（Y 从上到下，X 从左到右）排序
            tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
        result = []
        for table in tables:
            md = self._table_to_markdown(table)
            if md:
                result.append(md)
        return result

    def _table_to_markdown(self, table) -> str:
        """将 PyMuPDF 表格对象转换为 Markdown 格式字符串。

        Args:
            table: PyMuPDF 表格对象（find_tables() 返回的条目）

        Returns:
            Markdown 格式的表格字符串，空表格返回空字符串

        Note:
            extract() 在畸形表格结构下可能返回 None，
            空单元格为 None，用 sanitize_cell 保证输出空字符串。
        """
        rows = table.extract()
        if not rows or len(rows) < 1:
            return ""
        lines = ["| " + " | ".join(self.sanitize_cell(c) for c in rows[0]) + " |"]
        lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(self.sanitize_cell(c) for c in row) + " |")
        return "\n".join(lines)

    def _extract_heading_tree(self, full_text: str) -> list[tuple[int, str]]:
        """从拼接后的 pm Markdown 全文提取标题树。

        供子进程 pdf_heading_extractor 使用（主进程 parse 不再直接调用）。
        逐行扫描 # 前缀，清洗标记残留、过滤伪标题。

        Args:
            full_text: 拼接后的 pm Markdown 文本

        Returns:
            标题层级列表 [(level, heading), ...]
        """
        heading_tree: list[tuple[int, str]] = []
        for line in full_text.split("\n"):
            stripped = line.rstrip()
            if not stripped.startswith("#"):
                continue
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = self._clean_heading(stripped.lstrip("#").strip())
            if heading:
                heading_tree.append((level, heading))
        return heading_tree

    @staticmethod
    def _clean_heading(text: str) -> str:
        """清洗标题文本并过滤伪标题。

        在 _clean_markdown_noise 基础上，过滤复选框行（√/□ 适用/不适用）、
        编制单位标注行等非真实标题，返回空字符串表示应丢弃。

        Args:
            text: 含 Markdown 标记的原始标题文本

        Returns:
            清洗后的标题文本；伪标题返回空字符串
        """
        cleaned = PyMuPDFParser._clean_markdown_noise(text)
        if PSEUDO_HEADING_PATTERN.search(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _clean_markdown_noise(text: str) -> str:
        """清洗 pymupdf4llm 输出的 Markdown/HTML 标记残留（标题与正文通用）。

        只做轻量清洗，保留文本内容与表格结构：
          - 去掉强调标记 **_X_** / **X**，保留文本内容
          - <br> 替换为空格（表格行内的换行会破坏 | 结构）
          - 去掉其余内联 HTML 标签（<sup>、<mark>、<u> 等），保留标签内文本
          - 去掉页脚页码行（如 "6 / 12"，footer=False 偶有漏网的兜底）

        Args:
            text: 含 Markdown/HTML 标记的原始文本

        Returns:
            清洗后的文本
        """
        # 先替换 <br>（避免表格行内换行破坏 | 结构）
        cleaned = BR_PATTERN.sub(" ", text)
        # 去强调标记
        cleaned = EMPHASIS_PATTERN.sub(r"\1\2", cleaned)
        # 去其余内联 HTML 标签，保留标签内文本
        cleaned = HTML_TAG_PATTERN.sub("", cleaned)
        # 去页脚页码行
        cleaned = FOOTER_PAGE_NUM_PATTERN.sub("", cleaned)
        return cleaned.strip()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/parsers/test_pymupdf_parser.py -v`
Expected: PASS（含新增的 2 个 fitz 通道测试；原有测试在 mock 下全部保持通过）；`test_scanned_document_detection` 用临时 PDF 验证 is_scanned 且不触发子进程（mock 已覆盖，且扫描件跳过逻辑不调用）

- [ ] **Step 5: 提交**

```bash
git add src/parsers/pymupdf_parser.py tests/parsers/test_pymupdf_parser.py
git commit -m "feat(parser): 双通道解析 — fitz 全文+表格主进程 + pm 标题树子进程"
```

---

### Task 5: 端到端集成测试（真子进程 + 表格完整性）

**Files:**
- Modify: `tests/parsers/test_pymupdf_parser.py`（追加独立测试类，不走 mock）

**Interfaces:**
- Consumes: `PyMuPDFParser.parse`（Task 4 完成）；`pdf_heading_extractor.py` 子进程入口（Task 3 完成）
- Produces: 无新接口；验证真实链路（fitz 主进程干净 + 子进程标题树 + 无互相污染）

- [ ] **Step 1: 写集成测试**

在 `tests/parsers/test_pymupdf_parser.py` 末尾追加（独立类，不继承 TestPyMuPDFParser 的 mock）：

```python
class TestDualChannelIntegration:
    """端到端集成：真子进程 pm 标题树 + fitz 表格完整性（不 mock）。"""

    def setup_method(self):
        """每个测试前初始化解析器。"""
        self.parser = PyMuPDFParser()

    def test_parse_neusoft_dual_channel_integration(self):
        """真实链路：标题树含"财务" + Q4 表格数值同行（Q4 修复验收）。"""
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        # 子进程标题树（真 subprocess，import pymupdf4llm 只在子进程）
        titles = [h for _, h in result.heading_tree]
        assert len(result.heading_tree) > 0
        assert any("财务" in t for t in titles)
        # fitz 表格完整性：同一 chunk 含标签与数值
        assert any(
            "购建固定资产" in c.content and "63,134,713" in c.content
            for c in result.chunks
        )
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/parsers/test_pymupdf_parser.py::TestDualChannelIntegration -v`
Expected: PASS。此测试会真拉起一个子进程（约 2-4s）；跑完后进程内 fitz 表格提取仍干净（同 pytest 进程后续测试若测表格不回归，即证明隔离有效）

- [ ] **Step 3: 验证主进程无 pymupdf4llm import**

Run: `grep -rn "import pymupdf4llm\|from pymupdf4llm" src/ | grep -v __pycache__`
Expected: 仅 `src/parsers/pdf_heading_extractor.py:__main__` 块内出现（其余 src 文件无）

- [ ] **Step 4: 提交**

```bash
git add tests/parsers/test_pymupdf_parser.py
git commit -m "test(parser): 双通道端到端集成测试（真子进程 + Q4 表格完整性）"
```

---

### Task 6: 全量回归 + 质量门禁

**Files:**
- Modify: 视回归结果而定（如其他测试因解析换源失联）
- Test: 全量 `tests/`

**Interfaces:**
- Consumes: 前 5 个任务的产物
- Produces: 全绿测试套件；ruff/pyright 干净

- [ ] **Step 1: 全量跑测试**

Run: `pytest tests/ -v`
Expected: 全绿。若出现失联断言（如 RAGContext 构造处、chunk 内容断言），按 claude.md「契约同步」修复对应测试。

- [ ] **Step 2: ruff 格式化 + lint**

Run: `ruff format src/parsers tests/parsers && ruff check . --fix`
Expected: 无错误

- [ ] **Step 3: pyright 检查**

Run: `pyright src/parsers/pymupdf_parser.py src/parsers/pdf_heading_extractor.py src/rag/heading_locator.py`
Expected: 无新增 error（存量第三方库误报不计）

- [ ] **Step 4: 提交（如有修复）**

```bash
git add -A
git commit -m "test: 全量回归修复解析换源后的断言失联"
```

---

### Task 7: 存量重建 + Q4 RAGAS 回归评估

**Files:**
- 运行: `scripts/rebuild_kb_data.py`（复用，不修改）
- 运行: `src/cli/eval_ragas.py`（复用）

**Interfaces:**
- Consumes: 前 6 个任务的产物（代码已合入）
- Produces: 存量数据重建完成；Q4 faithfulness ≥ 基线 0.9333 的评估结果

- [ ] **Step 1: 重建存量文档**

Run: `.venv/bin/python scripts/rebuild_kb_data.py`
Expected: 保留 doc 记录、清 ChromaDB chunks、重置状态、重新 process_document 全部完成，日志无异常（注意：主进程此时不 import pymupdf4llm，fitz 表格干净）

- [ ] **Step 2: Q4 单问回归**

用 Q4 原始问题检索测试集文档，确认回答不再拒答且引用表格数值 `63,134,713` 相关行。
Expected: 回答含完整数值列上下文，不触发 abstention

- [ ] **Step 3: Q4 RAGAS 评估**

先列知识库定位 KB：

```bash
.venv/bin/python -m src.cli.eval_ragas --list-kbs
```

对目标 KB（Q4 文档所在库）跑 v2 测试集评估：

```bash
.venv/bin/python -m src.cli.eval_ragas \
  --kb-id <上一步列出的 KB UUID> \
  --testset-version 2 \
  --output data/ragas/reports/ragas_eval_fix_pdf_table_$(date +%Y%m%d_%H%M%S).csv \
  --gate
```

Expected: Q4 faithfulness ≥ 0.9333 基线；`--gate` 下全量指标与基线对比无倒退

- [ ] **Step 4: 提交（如有评估脚本或文档微调）**

```bash
git add -A
git commit -m "eval: Q4 表格完整性修复后回归评估达标"
```

---

### Task 8: 文档同步

**Files:**
- Modify: `docs/agents/chunking-issues.md`（Q4 根因 + F5 全局污染 + 双通道子进程方案补记）
- Modify: `docs/api_contract.md`（本 change 不改公共 API/响应结构；如有发现变化补记）
- Modify: `docs/openspec/changes/fix-pdf-table-integrity/`（task 完成后勾选 tasks.md）

**Interfaces:**
- Consumes: 前 7 个任务结论
- Produces: 文档与实现一致

- [ ] **Step 1: 补记 chunking-issues.md**

在 `docs/agents/chunking-issues.md` 追加一节，记录：
- Q4 回归根因（pymupdf4llm Layout 拆跨行单元格 → table_preserving 丢数值列）
- F5：`import pymupdf4llm` 全局破坏 fitz 表格提取（不可逆，`63,134,713` → `63 134 713\n, ,`）
- 双通道子进程方案与标题定位去空白归一化

- [ ] **Step 2: 更新 api_contract.md（如适用）**

检查本 change 是否改了公共方法签名/响应结构。无则跳过；有则补记。

- [ ] **Step 3: 勾选 tasks.md**

`docs/openspec/changes/fix-pdf-table-integrity/tasks.md` 全部勾选为 `[x]`。

- [ ] **Step 4: 提交**

```bash
git add docs/agents/chunking-issues.md docs/api_contract.md docs/openspec/changes/fix-pdf-table-integrity/tasks.md
git commit -m "docs: 记录 Q4 根因、F5 全局污染与双通道子进程方案"
```
