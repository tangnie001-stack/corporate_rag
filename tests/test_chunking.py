# tests/test_chunking.py
import pytest
from src.infra.chunking.strategies.parent_child import ParentChildChunker
from src.infra.chunking.strategies.qa import QAChunker
from src.infra.chunking.strategies.table_preserving import TablePreservingChunker
from src.infra.chunking.strategies.base import BaseChunker
from src.infra.chunking.router import ChunkRouter


def test_heading_injection():
    r = BaseChunker.inject_heading_prefix("营收100亿", "2024年 > 利润表 > 主要项目")
    assert r == "【利润表 > 主要项目】营收100亿"


def test_parent_child_has_parent_content():
    chunker = ParentChildChunker()
    text = ("这是第一段内容。" * 50) + ("这是第二段内容。" * 50)
    result = chunker.chunk(text, {"source": "t.txt", "doc_id": "d1"})
    assert len(result) > 0
    assert result[0]["metadata"]["chunk_strategy"] == "parent_child"
    assert "parent_content" in result[0]["metadata"]


def test_qa_no_parent():
    chunker = QAChunker()
    text = "问：营收多少？\n答：100亿\n问：利润多少？\n答：20亿"
    result = chunker.chunk(text, {"source": "q.txt", "doc_id": "d2"})
    for r in result:
        assert r["metadata"]["parent_content"] is None
        assert r["metadata"]["chunk_strategy"] == "qa"


def test_table_preserving_keeps_table():
    chunker = TablePreservingChunker()
    text = "开头\n| 项目 | 金额 |\n|--- |--- |\n| 营收 | 100亿 |\n| 利润 | 20亿 |\n结尾"
    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d3"})
    table_chunks = [r for r in result if "| 营收" in r["content"]]
    assert len(table_chunks) >= 1
    for tc in table_chunks:
        assert "| 营收 | 100亿 |" in tc["content"]
        assert "| 利润 | 20亿 |" in tc["content"]


def test_chunk_router_qa():
    from src.infra.chunking.validator import ChunkData
    text = "问：你好吗？\n答：我很好。\n问：吃了吗？\n答：吃了。"
    chunks = [ChunkData("a", {"block_type": "text"}, "0"), ChunkData("b", {"block_type": "text"}, "1")]
    assert ChunkRouter.detect_strategy(text, chunks) == "qa"


def test_chunk_router_table():
    from src.infra.chunking.validator import ChunkData
    text = "普通文本。\n| 项目 |\n|--- |\n| 数据 |"
    chunks = [ChunkData("txt", {"block_type": "text"}, "0"), ChunkData("| 项目 |", {"block_type": "table"}, "1")]
    assert ChunkRouter.detect_strategy(text, chunks) == "table_preserving"


def test_chunk_router_default():
    from src.infra.chunking.validator import ChunkData
    text = ("这是一段普通的说明文字。" * 10)
    chunks = [ChunkData(text, {"block_type": "text"}, "0")]
    assert ChunkRouter.detect_strategy(text, chunks) == "parent_child"
