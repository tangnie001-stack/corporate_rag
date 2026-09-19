"""metadata 回填契约的单测 —— 不碰数据库、不碰 Chroma。"""

import pytest

from src.chunking.validator import ChunkData
from src.infra.db.vector_store.mapping import (
    CONTRACT_KEYS,
    ChunkRow,
    build_rows,
    row_to_chunk_result,
    row_to_chunk_row,
    split_metadata,
)


def test_contract_keys_are_the_five_agreed_names():
    assert CONTRACT_KEYS == ("doc_id", "chunk_index", "chunk_total", "source", "page")


def test_split_metadata_keeps_custom_keys_verbatim():
    """jsonb 必须原样承载 chunker 的自定义键（含 parent_content）。"""
    split = split_metadata(
        {
            "source": "茅台2024.pdf",
            "page": 7,
            "parent_content": "母公司报表",
            "block_type": "table",
            "heading_path": ["一、经营情况"],
            "doc_id": "should-be-dropped",
        }
    )
    assert split.source == "茅台2024.pdf"
    assert split.page == 7
    assert split.extra == {
        "parent_content": "母公司报表",
        "block_type": "table",
        "heading_path": ["一、经营情况"],
    }


def test_split_metadata_defaults_and_coercion():
    """缺 source/page 时给契约默认值；page 强制成 int（Chroma 侧可能是 float）。"""
    split = split_metadata({})
    assert split.source == ""
    assert split.page == 0
    assert split.extra == {}

    split2 = split_metadata({"page": 3.0})
    assert split2.page == 3
    assert isinstance(split2.page, int)


def test_build_rows_sets_columns_and_placeholder_segment():
    """行形状：id 格式、chunk_total、content_seg 占位（P2 写原文）。"""
    chunks = [
        ChunkData(
            content="第一段", metadata={"source": "a.pdf", "page": 1}, chunk_id="a:0"
        ),
        ChunkData(
            content="第二段",
            metadata={"source": "a.pdf", "page": 2, "parent_content": "P"},
            chunk_id="a:1",
        ),
    ]
    rows = build_rows("kb1", "doc1", chunks, [[0.1] * 1024, [0.2] * 1024])
    assert [r.id for r in rows] == ["doc1:0", "doc1:1"]
    assert [r.chunk_index for r in rows] == [0, 1]
    assert all(r.chunk_total == 2 for r in rows)
    assert [r.content_seg for r in rows] == ["第一段", "第二段"]
    assert rows[1].extra == {"parent_content": "P"}
    assert rows[1].source == "a.pdf"
    assert rows[1].page == 2


def test_row_to_chunk_result_backfills_columns_and_jsonb():
    """回填：列值 + jsonb 平铺；5 个契约键必须可读（去重/引用/实体透传依赖它们）。"""
    row = ChunkRow(
        id="doc1:3",
        kb_id="kb1",
        doc_id="doc1",
        chunk_index=3,
        chunk_total=9,
        content="正文",
        content_seg="正文",
        embedding=None,
        source="茅台2024.pdf",
        page=12,
        extra={"parent_content": "母公司报表", "block_type": "table"},
    )
    result = row_to_chunk_result(row, distance=0.25, dense_rank=2)
    assert result.id == "doc1:3"
    assert result.content == "正文"
    assert result.distance == 0.25
    assert result.dense_rank == 2
    assert result.lexical_score is None
    assert result.sparse_rank is None
    for key in CONTRACT_KEYS:
        assert key in result.metadata
    assert result.metadata["doc_id"] == "doc1"
    assert result.metadata["chunk_index"] == 3
    assert result.metadata["chunk_total"] == 9
    assert result.metadata["source"] == "茅台2024.pdf"
    assert result.metadata["page"] == 12
    assert result.metadata["parent_content"] == "母公司报表"
    assert result.metadata["block_type"] == "table"


def test_row_to_chunk_result_column_wins_on_conflict():
    """冲突以列为准：jsonb 里若混进契约键，列值覆盖它。"""
    row = ChunkRow(
        id="doc1:0",
        kb_id="kb1",
        doc_id="doc1",
        chunk_index=0,
        chunk_total=1,
        content="正文",
        content_seg="正文",
        embedding=None,
        source="列里的.pdf",
        page=1,
        extra={"source": "jsonb里的.pdf", "page": 999, "doc_id": "伪造"},
    )
    result = row_to_chunk_result(row)
    assert result.metadata["source"] == "列里的.pdf"
    assert result.metadata["page"] == 1
    assert result.metadata["doc_id"] == "doc1"


class _FakeChunkModel:
    """鸭子类型的 ChunkModel 替身：只需具备同名属性。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "doc1:0")
        self.kb_id = kwargs.get("kb_id", "kb1")
        self.doc_id = kwargs.get("doc_id", "doc1")
        self.chunk_index = kwargs.get("chunk_index", 0)
        self.chunk_total = kwargs.get("chunk_total", 1)
        self.content = kwargs.get("content", "正文")
        self.content_seg = kwargs.get("content_seg", "正文")
        self.embedding = kwargs.get("embedding", [0.0] * 1024)
        self.source = kwargs.get("source", "a.pdf")
        self.page = kwargs.get("page", 1)
        self.extra = kwargs.get("extra", {"parent_content": "P"})


def test_row_to_chunk_row_maps_all_fields():
    """ORM 模型 → ChunkRow：11 个字段逐个对上（鸭子类型，属性名即契约）。"""
    row = row_to_chunk_row(_FakeChunkModel())
    assert isinstance(row, ChunkRow)
    assert (row.id, row.kb_id, row.doc_id) == ("doc1:0", "kb1", "doc1")
    assert (row.chunk_index, row.chunk_total) == (0, 1)
    assert (row.content, row.content_seg) == ("正文", "正文")
    assert (row.source, row.page) == ("a.pdf", 1)
    assert row.extra == {"parent_content": "P"}
    assert row.embedding is not None and len(row.embedding) == 1024


def test_row_to_chunk_row_handles_none_embedding_and_none_extra():
    """两个 None 分支：embedding 为 None 时保持 None；extra 为 None 时给空 dict。"""
    row = row_to_chunk_row(_FakeChunkModel(embedding=None, extra=None))
    assert row.embedding is None
    assert row.extra == {}


def test_build_rows_rejects_length_mismatch():
    """chunks 与 embeddings 数量不一致必须抛 ValueError（docstring 已声明的 Raises）。"""
    chunks = [ChunkData(content="只有一个", metadata={}, chunk_id="a:0")]
    with pytest.raises(ValueError):
        build_rows("kb1", "doc1", chunks, [])


def test_split_metadata_drops_all_five_contract_keys():
    """5 个契约键全部从 extra 剔除（doc_id/chunk_index/chunk_total 以参数为准）。"""
    split = split_metadata(
        {
            "doc_id": "x",
            "chunk_index": 1,
            "chunk_total": 2,
            "source": "a.pdf",
            "page": 3,
            "keep": "me",
        }
    )
    assert split.extra == {"keep": "me"}


def test_split_metadata_page_none_and_bool_become_zero():
    """page 为 None / bool 时归零（bool 是 int 子类，必须先判）。"""
    assert split_metadata({"page": None}).page == 0
    assert split_metadata({"page": True}).page == 0
    assert split_metadata({"page": False}).page == 0


def test_content_seg_is_tokenized_not_raw():
    """写入检索文本必须是分词输出，不得是正文原值（P2 的占位已过期）。"""
    from src.chunking.validator import ChunkData
    from src.infra.db.vector_store.mapping import build_rows
    from src.infra.search.tokenizer import to_lexical_text

    content = "公司资产负债率上升，研发费用 5 月增加"
    rows = build_rows(
        "kb1", "doc1", [ChunkData(content=content, metadata={})], [[0.0] * 1024]
    )
    assert rows[0].content == content
    assert rows[0].content_seg == to_lexical_text(content)
    assert rows[0].content_seg != content
