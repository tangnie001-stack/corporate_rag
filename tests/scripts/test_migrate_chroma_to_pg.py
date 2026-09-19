"""搬迁脚本的纯函数部分：Chroma 记录 → ChunkRow。"""

from scripts.migrate_chroma_to_pg import (
    ChromaRecord,
    chroma_record_to_row,
    collection_name_to_kb_id,
)
from src.infra.search.tokenizer import to_lexical_text


def test_collection_name_to_kb_id_strips_prefix():
    assert collection_name_to_kb_id("kb_53890512f25245bf948525b4253cb4f1") == (
        "53890512f25245bf948525b4253cb4f1"
    )


def test_record_to_row_splits_contract_keys_and_keeps_custom_keys():
    """契约键升列、自定义键整包进 jsonb（parent_content 必须活下来）。"""
    document = "贵州茅台2024年营业收入1741亿元"
    record = ChromaRecord(
        id="docabc:7",
        document=document,
        metadata={
            "doc_id": "docabc",
            "chunk_index": 7,
            "chunk_total": 12,
            "source": "茅台2024.pdf",
            "page": 3,
            "parent_content": "母公司报表",
            "block_type": "table",
        },
        embedding=[0.1] * 1024,
    )
    row = chroma_record_to_row(record)
    assert row.id == "docabc:7"
    assert row.doc_id == "docabc"
    assert row.chunk_index == 7
    assert row.chunk_total == 12
    assert row.source == "茅台2024.pdf"
    assert row.page == 3
    assert row.extra == {"parent_content": "母公司报表", "block_type": "table"}
    assert row.content == document
    assert row.content_seg == to_lexical_text(document)
    assert row.content_seg != document
    assert row.embedding is not None
    assert len(row.embedding) == 1024


def test_record_to_row_falls_back_to_id_suffix_for_missing_chunk_index():
    """Chroma 侧 metadata 缺 chunk_index 时，从 id 的 {doc_id}:{i} 后缀还原。"""
    record = ChromaRecord(
        id="docabc:4",
        document="正文",
        metadata={"doc_id": "docabc", "chunk_total": 9},
        embedding=[0.0] * 1024,
    )
    row = chroma_record_to_row(record)
    assert row.chunk_index == 4
