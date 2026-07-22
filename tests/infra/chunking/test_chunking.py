# tests/test_chunking.py
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
    chunks = [
        ChunkData("a", {"block_type": "text"}, "0"),
        ChunkData("b", {"block_type": "text"}, "1"),
    ]
    assert ChunkRouter.detect_strategy(text, chunks) == "qa"


def test_chunk_router_table():
    from src.infra.chunking.validator import ChunkData

    text = "普通文本。\n| 项目 |\n|--- |\n| 数据 |"
    chunks = [
        ChunkData("txt", {"block_type": "text"}, "0"),
        ChunkData("| 项目 |", {"block_type": "table"}, "1"),
    ]
    assert ChunkRouter.detect_strategy(text, chunks) == "table_preserving"


def test_chunk_router_default():
    from src.infra.chunking.validator import ChunkData

    text = "这是一段普通的说明文字。" * 10
    chunks = [ChunkData(text, {"block_type": "text"}, "0")]
    assert ChunkRouter.detect_strategy(text, chunks) == "parent_child"


def test_table_preserving_orphan_text_merge():
    """短文本（<200 chars）在表格前或后应合并到表格上."""
    chunker = TablePreservingChunker()
    text = (
        "| 项目 | 金额 |\n|--- |--- |\n| 营收 | 100亿 |\n"
        "注：以上数据来自审计报告\n"
        "| 项目 | 数量 |\n|--- |--- |\n| 订单 | 500 |"
    )
    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d4"})
    # 短文本应被合并到相邻表格上，不应作为独立 text chunk
    text_chunks = [r for r in result if r["metadata"]["block_type"] != "table"]
    orphan_texts = [r for r in text_chunks if "审计报告" in r["content"]]
    assert len(orphan_texts) == 0, "短文本应被合并到表格，不应独立成块"

    # 第一个表格应包含 "注：以上数据来自审计报告"
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    merged = any("审计报告" in c["content"] for c in table_chunks)
    assert merged, "表格 chunk 应包含被合并的短文本"


def test_table_preserving_split_large_table():
    """大表格（>2000 chars）应按行边界切分，每块保留表头."""
    chunker = TablePreservingChunker()
    # 构建一个约 3000 字符的表格（80 行，每行 ~38 字符）
    header = "| 项目 | 金额（万元） | 占比（%） | 同比增长（%） |\n|---|---|---|---|\n"
    rows = [
        f"| 项目{i} | {i}00万 | {i}.{i % 10}% | +{i * 10}.{i % 10}% |"
        for i in range(80)
    ]
    text = "开头\n" + header + "\n".join(rows) + "\n结尾"

    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d5"})

    # 应产生多个 table chunk
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    assert len(table_chunks) >= 2, (
        f"大表格应被切分为多个子表，实际: {len(table_chunks)}"
    )

    # 每个子表都应包含表头行
    for tc in table_chunks:
        assert "| 项目 | 金额（万元） | 占比（%） | 同比增长（%） |" in tc["content"], (
            "每个子表块应包含表头"
        )

    # 所有数据行应完整保留
    all_content = "".join(tc["content"] for tc in table_chunks)
    for i in range(80):
        assert f"| 项目{i}" in all_content, f"数据行 项目{i} 应被保留"


def test_table_preserving_split_no_separator():
    """无分隔行(|---|)的表格也能正常切分."""
    chunker = TablePreservingChunker()
    # 表格没有分隔行
    table = "\n".join([f"| col{i} | value{i} |" for i in range(25)])
    text = table  # >2000 chars with 25 rows

    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d6"})
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    assert len(table_chunks) >= 1

    # 首个数据行的内容应保持一致
    if len(table_chunks) >= 2:
        assert "| col0 | value0 |" in table_chunks[0]["content"]
