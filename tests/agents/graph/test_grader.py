"""Tests for RetrievalGrader."""

from src.agents.grader import RetrievalGrader
from src.infra.db.vector_store.types import ChunkResult


def _cr(content: str) -> ChunkResult:
    """Helper to create a ChunkResult with just content."""
    return ChunkResult(id="", content=content, metadata={})


def test_grader_high_coverage():
    """所有关键词在 Top-N 结果中 → score >= 0.5。"""
    grader = RetrievalGrader()
    query = "2024年营业收入净利润"
    results = [_cr("...")]
    reranked = [
        _cr("2024年公司营业收入达到100亿元"),
        _cr("净利润同比增长20%"),
    ]
    score = grader.grade(query, results, reranked)
    assert score >= 0.5, f"Expected >= 0.5, got {score}"


def test_grader_low_coverage():
    """关键词不在 Top-N 结果中 → score < 0.5。"""
    grader = RetrievalGrader()
    query = "2024年营业收入净利润"
    results = [_cr("...")]
    reranked = [
        _cr("公司总部位于北京"),
        _cr("员工人数5000人"),
    ]
    score = grader.grade(query, results, reranked)
    assert score < 0.5, f"Expected < 0.5, got {score}"


def test_grader_no_keywords():
    """无可提取关键词 → 默认通过 0.8。"""
    grader = RetrievalGrader()
    query = "是的"
    score = grader.grade(query, [], [_cr("test")])
    assert score == 0.8


def test_grader_empty_reranked():
    """精排结果为空 → 返回 0.0。"""
    grader = RetrievalGrader()
    score = grader.grade("test", [], [])
    assert score == 0.0
