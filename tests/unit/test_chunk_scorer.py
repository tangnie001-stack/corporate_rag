"""Tests for ChunkQualityScorer structure integrity check."""

from src.eval.chunk_scorer import _check_structure_integrity


def test_table_fully_contained():
    """A complete markdown table in one chunk should score 1.0."""
    chunks = [
        {
            "content": "| A | B |\n|---| ---|\n| 1 | 2 |\n| 3 | 4 |",
            "metadata": {"page": 1},
        },
    ]
    result = _check_structure_integrity(chunks)
    assert result["table"]["score"] == 1.0
    assert result["table"]["total"] == 1
    assert result["table"]["broken"] == []


def test_table_split_across_chunks():
    """A table split across two chunks should be marked broken."""
    chunks = [
        {"content": "| A | B |\n|---| ---|\n| 1 | 2 |", "metadata": {}},
        {"content": "| 3 | 4 |", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert result["table"]["score"] == 0.0
    assert result["table"]["total"] == 1
    assert len(result["table"]["broken"]) == 1


def test_multiple_tables_some_broken():
    """Only broken tables should be in the broken list."""
    chunks = [
        {"content": "| X | Y |\n|---|---|\n| a | b |", "metadata": {}},
        {"content": "some text", "metadata": {}},
        {"content": "| M | N |\n|---|---|\n| c | d |", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert result["table"]["total"] == 2
    assert result["table"]["score"] == 1.0
    assert result["table"]["broken"] == []


def test_heading_detected_and_intact():
    """A heading and its body in the same chunk should be intact."""
    chunks = [
        {"content": "3. 主营业务分析\n公司主要经营业务包括...", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert result["heading"]["score"] == 1.0
    assert result["heading"]["broken"] == []


def test_heading_separated_from_body():
    """A heading at end of chunk N with body in chunk N+1 should be broken."""
    chunks = [
        {"content": "3. 主营业务分析", "metadata": {}},
        {"content": "公司主要经营业务包括...", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert len(result["heading"]["broken"]) == 1
    assert result["heading"]["broken"][0]["text"].startswith("3.")


def test_chinese_numbered_heading():
    """Chinese numbered headings like （一） should be detected."""
    chunks = [
        {"content": "（一）主要会计数据和财务指标\n总资产 1.7亿", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert result["heading"]["score"] == 1.0


def test_clause_continuity_intact():
    """Clauses in the same chunk should not be broken."""
    chunks = [
        {"content": "1、公司董事会\n2、监事会\n3、高管", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert result["clause"]["score"] == 1.0


def test_clause_split():
    """A clause split across chunks should be marked broken."""
    chunks = [
        {"content": "1、公司董事会、监事会及董事、", "metadata": {}},
        {"content": "监事、高级管理人员保证...", "metadata": {}},
    ]
    result = _check_structure_integrity(chunks)
    assert len(result["clause"]["broken"]) >= 1


def test_no_table_in_document():
    """Document with no tables should skip table sub-dimension gracefully."""
    chunks = [{"content": "纯文本段落", "metadata": {}}]
    result = _check_structure_integrity(chunks)
    assert result["table"]["total"] == 0
    assert result["table"]["score"] is None  # skipped


def test_no_headings_detected():
    """Document with no headings should skip heading gracefully."""
    chunks = [{"content": "纯文本内容，没有标题", "metadata": {}}]
    result = _check_structure_integrity(chunks)
    assert result["heading"]["total"] == 0
    assert result["heading"]["score"] is None


def test_no_clauses():
    """Document with no clauses should skip clause gracefully."""
    chunks = [{"content": "纯文本", "metadata": {}}]
    result = _check_structure_integrity(chunks)
    assert result["clause"]["total"] == 0
    assert result["clause"]["score"] is None
