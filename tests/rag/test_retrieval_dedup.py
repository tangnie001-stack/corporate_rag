"""检索结果内容级（父块）去重测试。"""

from src.rag.context import RAGContext
from src.rag.retrieval import _dedup_by_parent


def _ctx(cid: str, doc_id: str, parent: str | None, score: float) -> RAGContext:
    return RAGContext(
        content=f"子内容{cid}",
        source=f"{doc_id}.pdf",
        page=1,
        doc_id=doc_id,
        chunk_id=cid,
        parent_content=parent,
        score=score,
    )


def test_same_parent_keeps_highest_score():
    """同一父块的多个 chunk 只留 .score 最高的那条（不是最先出现的）。"""
    parent = "同一段父块正文"
    ctxs = [
        _ctx("c1", "d1", parent, 0.20),
        _ctx("c2", "d1", parent, 0.90),
        _ctx("c3", "d1", parent, 0.50),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c2"]


def test_different_parents_in_same_doc_all_kept():
    """同一文档的多个不同父块全部保留（取消每文档配额）。"""
    ctxs = [
        _ctx("c1", "d1", "父块A", 0.9),
        _ctx("c2", "d1", "父块B", 0.8),
        _ctx("c3", "d1", "父块C", 0.7),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2", "c3"]


def test_cross_document_identical_parent_not_folded():
    """跨文档逐字相同的父块不折叠 —— 键必须含 doc_id。"""
    same = "年报的重要提示（样板文本）"
    ctxs = [_ctx("c1", "d1", same, 0.9), _ctx("c2", "d2", same, 0.8)]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2"]


def test_chunk_without_parent_kept_as_is():
    """无 parent_content 的 chunk 按自身保留，不参与折叠。"""
    ctxs = [
        _ctx("c1", "d1", None, 0.9),
        _ctx("c2", "d1", None, 0.8),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2"]


def test_kept_order_is_score_desc():
    """输出按 .score 降序（供后续按 TOP_K_RERANK 截断）。"""
    ctxs = [
        _ctx("c1", "d1", "父块A", 0.1),
        _ctx("c2", "d2", "父块B", 0.9),
        _ctx("c3", "d3", "父块C", 0.5),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c2", "c3", "c1"]
