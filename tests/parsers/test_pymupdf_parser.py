"""PyMuPDFParser 的单元测试。

测试目标：
- PDF 文件解析（逐页提取文本）
- 分块元数据包含 source 和 page
- 扫描件检测（文本字符数低于阈值）
- 异常场景：文件不存在
"""

import os
import re
from unittest.mock import patch

import pytest

from src.parsers.base import ParseResult
from src.parsers.pymupdf_parser import PyMuPDFParser


class TestPyMuPDFParser:
    """PyMuPDF PDF 解析器测试套件。"""

    def setup_method(self):
        """每个测试前初始化解析器和测试文件路径。"""
        self.parser = PyMuPDFParser()
        # 单测 mock 子进程：标题树用固定值，避免真实 subprocess 依赖
        self._tree_patcher = patch(
            "src.parsers.pymupdf_parser.extract_heading_tree",
            return_value=[(1, "一、主要财务数据"), (2, "（一）会计数据")],
        )
        self._tree_patcher.start()
        # sample.pdf 仅为占位文件（无实际文字层），内容测试用真实年报
        self.sample_pdf = "data/test_docs/neusoft_2025_q1.pdf"

    def teardown_method(self):
        """每个测试后停止子进程 mock。"""
        self._tree_patcher.stop()

    def test_parse_pdf_returns_parse_result(self):
        """基本解析：返回 ParseResult 且页数 / 字符数 > 0。"""
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        assert isinstance(result, ParseResult)
        assert result.file_type == "pdf"
        assert result.total_pages > 0
        assert result.total_chars > 0

    def test_parse_pdf_has_chunks(self):
        """分块完整性：每个 chunk 必须有内容、source 和 page 元数据。"""
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert len(chunk.content) > 0  # 不允许空块
            assert "source" in chunk.metadata  # 来源文件名
            assert "page" in chunk.metadata  # 页码（用F于引用定位）

    def test_chunks_have_page_numbers(self):
        """页码检查：page 必须为正整数（从 1 开始）。"""
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        for chunk in result.chunks:
            assert isinstance(chunk.metadata["page"], int)
            assert chunk.metadata["page"] >= 1  # 页码从 1 开始

    def test_parse_nonexistent_file_raises(self):
        """文件不存在时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.pdf")

    def test_scanned_document_detection(self):
        """扫描件检测：几乎无文本的 PDF 应被标记为 is_scanned=True。

        原理：PyMuPDFParser 内部用 MIN_TEXT_CHARS 阈值判断，
        如果平均每页文本字符数低于阈值，则认为是扫描件。
        """
        import pymupdf

        # 构造一个几乎无文本的 PDF（仅 1 个字符，低于阈值）
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "x")  # 只插入 1 个字符
        path = "/tmp/scanned_test.pdf"
        doc.save(path)
        doc.close()

        result = self.parser.parse(path)
        os.remove(path)
        # 验证扫描件检测逻辑触发
        assert result.is_scanned is True

    def test_footer_removed(self):
        """页眉页脚过滤：chunk 中不应出现页脚页码行（如 "1 / 10"）。"""
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        # 页脚页码行是单独的 "N / M" 行（to_markdown 的 footer=False 应已剔除）
        for chunk in result.chunks:
            assert not re.search(r"^\d+\s*/\s*\d+\s*$", chunk.content, re.MULTILINE)

    def test_body_markdown_noise_cleaned(self):
        """正文噪音清洗：chunk 中不应残留强调标记或 HTML 标签。

        覆盖 Imp-3 修复：**X**、**_X_**、<br>、<sup>、<mark>、<u> 等
        pymupdf4llm 标记在进 chunk 前应被去除。
        """
        path = "data/test_docs/tencent_2024_annual.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert "**" not in chunk.content
            assert not re.search(r"<br\s*/?>|</?sup>|</?mark>|</?u>", chunk.content)

    def test_clean_markdown_noise(self):
        """_clean_markdown_noise：去除强调标记与 HTML 标签，保留文本与表格结构。"""
        clean = PyMuPDFParser._clean_markdown_noise
        # 强调标记（**_X_** / **X**）
        assert clean("同比 **_8%_** 毛利") == "同比 8% 毛利"
        assert clean("|**1,385**|1,343|") == "|1,385|1,343|"
        # HTML 标签（保留标签内文本）
        assert clean("按非国际财务报告准则<sup>1</sup>") == "按非国际财务报告准则1"
        assert clean("<mark>、监事、</mark>") == "、监事、"
        assert clean("<u>单位：万元</u>") == "单位：万元"
        # <br> 替换为空格，不破坏表格 | 行结构
        assert (
            clean("经营业务<br>密切相关|8,981,032|") == "经营业务 密切相关|8,981,032|"
        )
        # 邮箱等含下划线的真实内容不被误伤（裸 _X_ 规则已移除）
        assert clean("mm_sun@tkl.tsannkuen.com") == "mm_sun@tkl.tsannkuen.com"
        # 页脚页码行（如 "6 / 12"）被兜底剔除
        assert clean("页脚残留\n6 / 12") == "页脚残留"

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


class TestHeadingTree:
    """标题树提取与页码保留测试。"""

    def setup_method(self):
        """每个测试前初始化解析器。"""
        self.parser = PyMuPDFParser()

    def test_pdf_heading_tree_extracted(self):
        """标题树提取：能从 PDF 中解析出章节标题（如"主要财务数据"）。"""
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        assert isinstance(result.heading_tree, list)
        assert len(result.heading_tree) > 0
        # 至少有一个章节标题（如"主要财务数据"）
        titles = [h for _, h in result.heading_tree]
        assert any("财务" in t for t in titles)

    def test_pdf_page_preserved(self):
        """页码保留：每个 chunk 的 page 元数据必须为正整数（从 1 开始）。"""
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        pages = {c.metadata.get("page") for c in result.chunks}
        # isinstance 过滤空值并缩小类型，保证 page 为正整数（从 1 开始）
        assert pages and all(isinstance(p, int) and p >= 1 for p in pages)

    def test_pseudo_headings_filtered(self):
        """伪标题过滤：复选框行（√/□）与"编制单位"标注行不应进入标题树。"""
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        titles = [h for _, h in result.heading_tree]
        # 复选框行（√适用□不适用 等）与编制单位标注行应被过滤
        assert not any("√" in t or "□" in t for t in titles)
        assert not any(t.startswith("编制单位") for t in titles)
