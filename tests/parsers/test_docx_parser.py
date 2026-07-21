"""DocxParser 的单元测试。

测试目标：
- DOCX 文件解析（段落 + 表格提取）
- 分块内容完整性
- 元数据包含 page 字段
- 异常场景：文件不存在
"""

import pytest
from unittest.mock import MagicMock
from src.parsers.base import ParseResult
from src.parsers.docx_parser import DocxParser


class TestDocxParser:
    """DOCX 解析器测试套件。"""

    def setup_method(self):
        """每个测试前初始化解析器和测试文件路径。"""
        self.parser = DocxParser()
        self.sample_path = "data/test_docs/sample.docx"

    def test_parse_docx_returns_parse_result(self):
        """基本解析：返回 ParseResult 且统计信息合理。"""
        result = self.parser.parse(self.sample_path)
        assert isinstance(result, ParseResult)
        assert result.file_type == "docx"  # 文件类型标识
        assert result.total_pages == 1  # DOCX 按单页处理
        assert result.total_chars > 0

    def test_parse_docx_has_chunks(self):
        """分块完整性：每个 chunk 必须有内容和来源元数据。"""
        result = self.parser.parse(self.sample_path)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert len(chunk.content) > 0
            assert chunk.metadata.get("source") == "sample.docx"

    def test_chunks_have_page_metadata(self):
        """元数据检查：DOCX chunk 必须包含 page 字段（用F于引用定位）。"""
        result = self.parser.parse(self.sample_path)
        for chunk in result.chunks:
            assert "page" in chunk.metadata  # 支持“第X页”引用

    def test_parse_nonexistent_file_raises(self):
        """文件不存在时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.docx")

    def test_docx_table_to_markdown(self):
        """_docx_table_to_markdown：应输出有效 Markdown 表格。"""
        table = MagicMock()
        cell1, cell2 = MagicMock(), MagicMock()
        cell1.text = "Name"
        cell2.text = "Age"
        cell3, cell4 = MagicMock(), MagicMock()
        cell3.text = "Alice"
        cell4.text = "30"

        row1 = MagicMock()
        row1.cells = [cell1, cell2]
        row2 = MagicMock()
        row2.cells = [cell3, cell4]
        table.rows = [row1, row2]

        md = self.parser._docx_table_to_markdown(table)
        assert "| Name | Age |" in md
        assert "| --- | --- |" in md
        assert "| Alice | 30 |" in md

    def test_docx_table_to_markdown_empty(self):
        """_docx_table_to_markdown：空表格返回空字符串。"""
        table = MagicMock()
        table.rows = []

        assert self.parser._docx_table_to_markdown(table) == ""
