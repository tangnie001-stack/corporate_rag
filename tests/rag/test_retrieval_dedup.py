"""检索结果 doc_id 去重测试。"""

from src.infra.db.vector_store.types import ChunkResult
from src.rag.retrieval import _dedup_by_doc_id


def _chunk(cid: str, doc_id: str) -> ChunkResult:
    return ChunkResult(
        id=cid,
        content=f"内容{cid}",
        metadata={"doc_id": doc_id, "source": f"{doc_id}.pdf", "page": 1},
    )


def test_dedup_keeps_first_per_doc():
    """同一 doc_id 只保留最先出现的结果，不同 doc_id 全部保留。"""
    results = [
        _chunk("a1", "d1"),
        _chunk("a2", "d1"),
        _chunk("b1", "d2"),
        _chunk("a3", "d1"),
    ]
    out = _dedup_by_doc_id(results)
    assert [c.id for c in out] == ["a1", "b1"]


def test_dedup_keeps_items_without_doc_id():
    """无 doc_id 的项按自身保留（不误删）。"""
    results = [
        _chunk("a1", "d1"),
        ChunkResult(id="x1", content="x", metadata={}),
        _chunk("a2", "d1"),
    ]
    out = _dedup_by_doc_id(results)
    assert [c.id for c in out] == ["a1", "x1"]
