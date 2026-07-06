"""基础解析器数据模型与抽象类的单元测试。

测试目标：
- ChunkData：不可变数据块（content + metadata + chunk_id）
- ParseResult：解析结果聚合（chunks 列表 + 统计信息）
- BaseParser：抽象基类，确保子类必须实现 parse() 方法
"""

import pytest
from src.parsers.base import ChunkData, ParseResult, BaseParser


# ==================== ChunkData 数据块测试 ====================
class TestChunkData:
    """测试 ChunkData 不可变数据类的构造与字段访问。"""

    def test_create_chunk(self):
        """验证 ChunkData 三要素：文本内容、元数据字典、唯一 ID。"""
        chunk = ChunkData(
            content="test content",
            metadata={"source": "test.txt", "page": 1},
            chunk_id="doc1:0",  # 格式：{doc_id}:{chunk_index}
        )
        assert chunk.content == "test content"
        assert chunk.metadata["source"] == "test.txt"
        assert chunk.chunk_id == "doc1:0"


# ==================== ParseResult 解析结果测试 ====================
class TestParseResult:
    """测试 ParseResult 聚合对象的默认值与自动计算逻辑。"""

    def test_empty_result(self):
        """空结果：所有统计字段应有合理默认值。"""
        result = ParseResult()
        assert result.chunks == []
        assert result.total_pages == 0
        assert result.total_chars == 0
        assert result.is_scanned is False  # 默认非扫描件

    def test_result_with_chunks(self):
        """带分块的结果：手动指定统计信息。"""
        chunks = [
            ChunkData(content="a", metadata={}, chunk_id="d:0"),
            ChunkData(content="b", metadata={}, chunk_id="d:1"),
        ]
        result = ParseResult(
            chunks=chunks,
            total_pages=3,
            total_chars=100,
            file_type="pdf",
        )
        assert len(result.chunks) == 2
        assert result.total_pages == 3

    def test_total_chars_auto_calc(self):
        """total_chars 自动计算：未手动传入时，累加所有 chunk 的 len(content)。"""
        chunks = [
            ChunkData(content="hello", metadata={}, chunk_id="d:0"),
            ChunkData(content="world", metadata={}, chunk_id="d:1"),
        ]
        result = ParseResult(chunks=chunks, total_pages=1, file_type="txt")
        # "hello"(5) + "world"(5) = 10，验证 __post_init__ 中的自动求和逻辑
        assert result.total_chars == 10


# ==================== BaseParser 抽象基类测试 ====================
class TestBaseParser:
    """测试 BaseParser 抽象基类的约束行为。"""

    def test_abstract_cannot_instantiate(self):
        """抽象基类不可直接实例化，必须子类实现 parse()。"""
        with pytest.raises(TypeError):
            BaseParser()

    def test_concrete_implementation(self):
        """子类实现 parse() 后可正常实例化并调用。"""

        class TestParser(BaseParser):
            def parse(self, file_path):
                return ParseResult(chunks=[], total_pages=0, file_type="test")

        parser = TestParser()
        result = parser.parse("dummy.txt")
        assert result.file_type == "test"
