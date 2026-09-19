"""RRF 融合：纯函数，无需数据库，两路等权。"""

from src.infra.db.vector_store.types import ChunkResult


def _r(chunk_id: str, *, dense_rank=None, sparse_rank=None) -> ChunkResult:
    return ChunkResult(
        id=chunk_id,
        content=f"内容 {chunk_id}",
        metadata={},
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )


def test_fusion_empty_inputs():
    from src.rag.fusion import rrf_fusion

    assert rrf_fusion([], [], k=60, top_n=50) == []


def test_fusion_dense_only_keeps_dense_rank():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [], k=60, top_n=10)
    assert [r.id for r in result] == ["a", "b"]
    assert [r.dense_rank for r in result] == [0, 1]
    assert [r.sparse_rank for r in result] == [None, None]


def test_fusion_sparse_only_keeps_sparse_rank():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([], [_r("a"), _r("b")], k=60, top_n=10)
    assert [r.sparse_rank for r in result] == [0, 1]
    assert [r.dense_rank for r in result] == [None, None]


def test_fusion_both_paths_preserve_both_ranks():
    """同一结果出现在两路时，两侧排名都要保留（来源可辨）。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [_r("b"), _r("a")], k=60, top_n=10)
    by_id = {r.id: r for r in result}
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].sparse_rank == 0


def test_fusion_two_paths_are_equal_weight():
    """两路等权：同一 id 在任一路排第 1 的得分贡献相同。"""
    from src.rag.fusion import rrf_fusion

    dense_first = rrf_fusion([_r("x")], [_r("y"), _r("z")], k=60, top_n=10)
    sparse_first = rrf_fusion([_r("y"), _r("z")], [_r("x")], k=60, top_n=10)
    assert {r.id for r in dense_first} == {r.id for r in sparse_first}


def test_fusion_respects_top_n():
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r(f"d{i}") for i in range(10)], [], k=60, top_n=3)
    assert len(result) == 3


def test_fusion_multi_three_way():
    from src.rag.fusion import rrf_fusion_multi

    merged = rrf_fusion_multi(
        [[_r("a"), _r("b")], [_r("b"), _r("c")], [_r("c"), _r("a")]], k=60, top_n=5
    )
    assert {r.id for r in merged} == {"a", "b", "c"}


def test_fusion_has_no_database_dependency():
    """融合必须能仅凭两路结果列表验证（spec 的 scenario）。"""
    import inspect

    from src.rag import fusion

    source = inspect.getsource(fusion)
    for forbidden in ("sqlalchemy", "session_factory", "asyncpg", "asyncio"):
        assert forbidden not in source
