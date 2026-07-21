"""DocRouter 文档路由器的单元测试。

测试目标：
- 策略模式路由：根据文件后缀分发到对应解析器
- 支持的文件类型：.txt / .docx / .pdf
- 不支持的类型和无后缀文件应抛出 ValueError
"""

import pytest
from src.parsers.router import DocRouter
from src.parsers.base import ParseResult


class TestDocRouter:
    """文档路由器测试套件。"""

    def setup_method(self):
        """每个测试前初始化路由器。"""
        self.router = DocRouter()

    def test_route_txt(self):
        """路由 .txt 文件到 TxtParser。"""
        result = self.router.parse("data/test_docs/sample.txt")
        assert isinstance(result, ParseResult)
        assert result.file_type == "txt"
        assert len(result.chunks) > 0

    def test_route_docx(self):
        """路由 .docx 文件到 DocxParser。"""
        result = self.router.parse("data/test_docs/sample.docx")
        assert isinstance(result, ParseResult)
        assert result.file_type == "docx"
        assert len(result.chunks) > 0

    def test_route_pdf(self):
        """路由 .pdf 文件到 PyMuPDFParser。"""
        result = self.router.parse("data/test_docs/sample.pdf")
        assert isinstance(result, ParseResult)
        assert result.file_type == "pdf"
        assert len(result.chunks) > 0

    def test_route_unsupported_extension(self):
        """不支持的后缀（如 .xyz）应抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.router.parse("test.xyz")

    def test_route_no_extension(self):
        """无后缀文件（如 README）应抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.router.parse("README")
