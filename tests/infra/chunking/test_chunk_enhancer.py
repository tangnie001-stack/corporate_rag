"""ParentChildChunker 和 count_tokens 的单元测试。

测试范围:
  - count_tokens: 空文本 / ASCII / CJK 文本的 token 估算
  - ParentChildChunker.chunk: 父-子分块结构和 metadata 正确性
"""

from src.infra.chunking.enhancer import ParentChildChunker, count_tokens


def test_count_tokens_empty():
    assert count_tokens("") == 1


def test_count_tokens_ascii():
    assert count_tokens("hello") == 2  # 5 chars // 2


def test_count_tokens_chinese():
    assert count_tokens("净利润862亿元") == 4  # 8 chars // 2


def test_parent_child_structure():
    chunker = ParentChildChunker()
    text = "财务数据 " * 500
    chunks = chunker.chunk(text, {"source": "test.pdf", "page": 1, "doc_id": "doc1"})
    assert len(chunks) > 0
    for c in chunks:
        assert "parent_content" in c["metadata"]
        assert "parent_chunk_id" in c["metadata"]
    child_tokens = [len(c["content"]) // 2 for c in chunks]
    parent_tokens = [len(c["metadata"]["parent_content"]) // 2 for c in chunks]
    assert all(ct <= pt for ct, pt in zip(child_tokens, parent_tokens))
