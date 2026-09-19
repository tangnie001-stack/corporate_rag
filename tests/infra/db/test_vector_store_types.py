"""ChunkResult 的字段契约：分路得分与分路排名。"""

from src.infra.db.vector_store.types import ChunkResult


def test_chunk_result_has_split_fields():
    """分路字段必须存在且默认缺席（None）。"""
    r = ChunkResult(id="d:0", content="正文")
    assert r.lexical_score is None
    assert r.dense_rank is None
    assert r.sparse_rank is None


def test_chunk_result_carries_path_specific_values():
    """两路的得分与排名各自独立。"""
    dense = ChunkResult(id="d:0", content="正文", distance=0.12, dense_rank=0)
    lexical = ChunkResult(id="d:1", content="正文2", lexical_score=3.7, sparse_rank=1)
    assert dense.dense_rank == 0
    assert dense.dense_rank != lexical.sparse_rank
    assert lexical.sparse_rank == 1


def test_chunk_result_has_no_bm25_named_field():
    """旧名必须消失：它会把与引擎无关的词法得分误导成 BM25。"""
    r = ChunkResult(id="d:0", content="正文")
    assert not hasattr(r, "bm25_score")
