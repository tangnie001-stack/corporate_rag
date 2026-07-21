"""PyMuPDFParser 的单元测试。

测试目标：
- PDF 文件解析（逐页提取文本）
- 分块元数据包含 source 和 page
- 扫描件检测（文本字符数低于阈值）
- 异常场景：文件不存在
"""

import os
import pytest
from src.parsers.base import ParseResult
from src.parsers.pymupdf_parser import PyMuPDFParser


class TestPyMuPDFParser:
    """PyMuPDF PDF 解析器测试套件。"""

    def setup_method(self):
        """每个测试前初始化解析器和测试文件路径。"""
        self.parser = PyMuPDFParser()
        self.sample_pdf = "data/test_docs/sample.pdf"

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
        import fitz

        # 构造一个几乎无文本的 PDF（仅 1 个字符，低于阈值）
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "x")  # 只插入 1 个字符
        path = "/tmp/scanned_test.pdf"
        doc.save(path)
        doc.close()

        result = self.parser.parse(path)
        os.remove(path)
        # 验证扫描件检测逻辑触发
        assert result.is_scanned is True

    def test_table_to_markdown(self):
        """_table_to_markdown：应输出有效 Markdown 表格。"""

        # Mock a table-like object with extract() method
        class MockTable:
            def extract(self):
                return [
                    ["Name", "Age", "City"],
                    ["Alice", "30", "NY"],
                    ["Bob", "25", "LA"],
                ]

        md = self.parser._table_to_markdown(MockTable())
        assert "| Name | Age | City |" in md
        assert "| --- | --- | --- |" in md
        assert "| Alice | 30 | NY |" in md

    def test_table_to_markdown_empty(self):
        """_table_to_markdown：空表格返回空字符串。"""

        class MockTable:
            def extract(self):
                return []

        assert self.parser._table_to_markdown(MockTable()) == ""
