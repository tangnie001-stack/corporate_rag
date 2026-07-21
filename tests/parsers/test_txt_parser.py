"""TxtParser 的单元测试。

测试目标：
- UTF-8 / GBK 编码自动检测与解码
- 分块结果完整性（content / metadata / chunk_id）
- 异常场景：文件不存在、空文件
"""

import pytest
from src.parsers.base import ParseResult
from src.parsers.txt_parser import TxtParser


class TestTxtParser:
    """TXT 解析器测试套件。"""

    def setup_method(self):
        """每个测试前初始化解析器和测试文件路径。"""
        self.parser = TxtParser()
        self.sample_path = "data/test_docs/sample.txt"  # UTF-8 编码样本
        self.gbk_path = "data/test_docs/sample_gbk.txt"  # GBK 编码样本（含中文金融数据）

    def test_parse_txt_returns_parse_result(self):
        """基本解析：返回 ParseResult 且统计信息合理。"""
        result = self.parser.parse(self.sample_path)
        assert isinstance(result, ParseResult)
        assert result.file_type == "txt"  # TXT 文件固定为单页
        assert result.total_pages == 1
        assert result.total_chars > 0

    def test_parse_txt_has_chunks(self):
        """分块完整性：每个 chunk 必须有内容、来源元数据、唯一 ID。"""
        result = self.parser.parse(self.sample_path)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert len(chunk.content) > 0  # 不允许空块
            assert "source" in chunk.metadata  # 必须有来源文件名
            assert chunk.chunk_id  # 必须有唯一标识

    def test_parse_gbk_txt(self):
        """GBK 编码自动检测：chardet 检测后映射为 gbk，中文内容正确解码。"""
        result = self.parser.parse(self.gbk_path)
        assert len(result.chunks) > 0
        # chardet 可能返回 gb2312/gb18030，解析器内部统一映射为 gbk
        assert result.encoding == "gbk"
        # 验证中文金融数据未被乱码
        all_text = " ".join(c.content for c in result.chunks)
        assert "营业总收入" in all_text  # 关键财务字段
        assert "贵州茅台" in all_text  # 公司名称

    def test_chunks_have_source_metadata(self):
        """元数据检查：所有 chunk 的 source 必须为文件名（不含路径）。"""
        result = self.parser.parse(self.sample_path)
        for chunk in result.chunks:
            assert chunk.metadata["source"] == "sample.txt"

    def test_parse_nonexistent_file_raises(self):
        """文件不存在时抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.txt")

    def test_parse_empty_file(self, tmp_path):
        """空文件应返回零个 chunk，不抛异常。"""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        result = self.parser.parse(str(empty_file))
        assert len(result.chunks) == 0
