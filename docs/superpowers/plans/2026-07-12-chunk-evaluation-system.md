# Chunk Evaluation System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated chunk quality evaluation (3 lightweight metrics + RAGAS persistence) to the document upload pipeline.

**Architecture:** A new `ChunkQualityScorer` runs after chunking in `_process_document_task` (gated by `CHUNK_EVAL_ENABLED` toggle). Results write to `document.meta_info` JSON column. A new `eval_report` table stores RAGAS evaluation results. Frontend displays scores per file and KB-level RAGAS summary.

**Tech Stack:** Python 3.11+, FastAPI, MySQL 8.0 (async via aiomysql), DashScopeEmbeddings (text-embedding-v1), RAGAS 0.3.1

## Global Constraints

- Existing `LANGFUSE_ENABLE` pattern for toggle: `os.getenv("CHUNK_EVAL_ENABLED", "false").lower() == "true"`
- All sync operations in `_process_document_task` MUST use `asyncio.to_thread()`
- Follow existing patterns in `src/api/documents.py`, `src/infra/db/mysql_db.py`, `src/config/queries.py`
- SBR embedding MUST batch via `embed_documents()`, NOT individual calls
- `meta_info` JSON column stores eval data; existing column, zero schema change
- `meta_info.eval` structure MUST follow the JSON schema from spec/chunk-quality-scorer
- All scores normalized to 0-1 where 1.0 = best, pass threshold >= 0.70
- Run `ruff check . && pytest tests/ -v` before final commit

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/config/settings.py` | + `CHUNK_EVAL_ENABLED` toggle |
| `src/eval/__init__.py` | Package init |
| `src/eval/chunk_scorer.py` | **New** — `ChunkQualityScorer` class with 3 metrics |
| `src/config/queries.py` | + `eval_report` DDL + `UPDATE_DOCUMENT_META_INFO` |
| `src/infra/db/mysql_db.py` | + CRUD for eval_report + meta_info update |
| `src/api/documents.py` | Call scorer in `_process_document_task` |
| `src/api/model/response.py` | Extend `DocumentListResponse` + new KB eval endpoint |
| `src/cli/eval_ragas.py` | Write results to `eval_report` table |
| `src/api/kb_eval.py` | **New** — endpoint to get latest KB-level RAGAS eval |
| `tests/unit/test_chunk_scorer.py` | **New** — unit tests for ChunkQualityScorer |
| `tests/integration/test_eval_pipeline.py` | **New** — integration tests for full pipeline |

---

### Task 1: Settings toggle

**Files:**
- Modify: `src/config/settings.py` (after line 141)

**Interfaces:**
- Consumes: nothing
- Produces: `settings.CHUNK_EVAL_ENABLED: bool`

- [ ] **Step 1: Add the toggle to settings.py**

Insert after the `LANGFUSE_ENABLE` block (line ~141):

```python
# 分块质量评估开关：true 时上传文件后自动跑 3 个质量指标
# 默认关闭，不影响现有流程
CHUNK_EVAL_ENABLED: bool = os.getenv("CHUNK_EVAL_ENABLED", "false").lower() == "true"
```

- [ ] **Step 2: Verify no import breaks**

Run: `python -c "from src.config import settings; print(settings.CHUNK_EVAL_ENABLED)"`
Expected: `False`

- [ ] **Step 3: Commit**

```bash
git add src/config/settings.py
git commit -m "feat: add CHUNK_EVAL_ENABLED toggle for chunk quality evaluation"
```

---

### Task 2: ChunkQualityScorer — structure_integrity

**Files:**
- Create: `src/eval/__init__.py`
- Create: `src/eval/chunk_scorer.py`
- Test: `tests/unit/test_chunk_scorer.py`

**Interfaces:**
- Consumes: `chunks: list[dict]` (each dict has `content: str`, `metadata: dict`)
- Produces: `_check_structure_integrity(chunks) -> dict` with format:
  ```python
  {
    "score": 0.97,
    "table": {"score": 1.0, "total": 3, "broken": []},
    "heading": {"score": 0.92, "total": 12, "broken": [{"index": 7, "text": "3. 主营业务分析"}]},
    "clause": {"score": 0.90, "total": 10, "broken": [{"index": 1, "text": "1、公司董事会..."}]}
  }
  ```

- [ ] **Step 1: Create `src/eval/__init__.py`**

```python
"""Chunk quality evaluation module."""
```

- [ ] **Step 2: Write failing unit tests**

Create `tests/unit/test_chunk_scorer.py`:

```python
"""Tests for ChunkQualityScorer structure integrity check."""

import pytest
from src.eval.chunk_scorer import _check_structure_integrity


def test_table_fully_contained():
    """A complete markdown table in one chunk should score 1.0."""
    chunks = [
        {"content": "| A | B |\n|---| ---|\n| 1 | 2 |\n| 3 | 4 |", "metadata": {"page": 1}},
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
```

- [ ] **Step 3: Run tests to verify failures**

```bash
pytest tests/unit/test_chunk_scorer.py -v
```
Expected: All tests FAIL (module not found)

- [ ] **Step 4: Implement `_check_structure_integrity`**

Create `src/eval/chunk_scorer.py`:

```python
"""Chunk quality scorer — 3 lightweight metrics for evaluating chunk quality.

This module provides the ChunkQualityScorer class that computes:
  1. Structure integrity: table/heading/clause continuity across chunks
  2. Semantic Breakage Rate (SBR): cosine similarity between adjacent chunks
  3. Granularity CV: coefficient of variation of chunk token counts

All metrics normalize to 0-1 where 1.0 = best quality.
"""

import re
import statistics
from typing import Any

from src.models import get_embeddings

# Heading detection patterns (financial document focused)
HEADING_PATTERNS = [
    re.compile(r"^[一二三四五六七八九十]+、"),            # 一、二、三、
    re.compile(r"^（[一二三四五六七八九十]+）"),          # （一）（二）（三）
    re.compile(r"^\d+[\.、]"),                           # 1. 2. 3、
    re.compile(r"^第[一二三四五六七八九十]+条"),           # 第一条、第二条
]

# Clause/List item patterns
CLAUSE_PATTERNS = [
    re.compile(r"^\d+[\.、]"),
    re.compile(r"^[（(]\d+[）)]"),                       # (1) （1）
    re.compile(r"^[•·\-]\s"),                            # bullet points
    re.compile(r"^[①②③④⑤⑥⑦⑧⑨⑩]"),                  # circled numbers
]

# Table pattern: lines starting and ending with |
TABLE_LINE = re.compile(r"^\|.+\|$")
TABLE_SEPARATOR = re.compile(r"^\|[\s\-|]+\|$")


def _detect_headings(lines: list[str]) -> list[int]:
    """Return indices of lines that match heading patterns."""
    indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in HEADING_PATTERNS:
            if pat.match(stripped):
                indices.append(i)
                break
    return indices


def _detect_clauses(lines: list[str]) -> list[int]:
    """Return indices of lines that match clause/list patterns."""
    indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in CLAUSE_PATTERNS:
            if pat.match(stripped):
                indices.append(i)
                break
    return indices


def _detect_tables(chunks: list[dict]) -> list[list[dict]]:
    """Group consecutive |...| lines across chunks into logical tables.
    
    Returns list of tables, where each table is a list of dicts:
      {"chunk_index": int, "line_index": int, "text": str}
    """
    tables = []
    current_table = []
    in_table = False

    for ci, chunk in enumerate(chunks):
        lines = chunk["content"].split("\n")
        for li, line in enumerate(lines):
            stripped = line.strip()
            if TABLE_LINE.match(stripped) and not TABLE_SEPARATOR.match(stripped):
                current_table.append({"chunk_index": ci, "line_index": li, "text": stripped})
                in_table = True
            else:
                if in_table and current_table:
                    tables.append(current_table)
                    current_table = []
                in_table = False

    if in_table and current_table:
        tables.append(current_table)
    return tables


def _check_structure_integrity(chunks: list[dict]) -> dict:
    """Check structural integrity of chunks: table, heading, clause continuity.
    
    Args:
        chunks: List of dicts with "content" and "metadata" keys
        
    Returns:
        dict with score and per-sub-dimension results
    """
    # ---- Table integrity ----
    tables = _detect_tables(chunks)
    broken_tables = []
    for table in tables:
        # A table is broken if its rows span more than 1 chunk
        chunk_indices = set(row["chunk_index"] for row in table)
        if len(chunk_indices) > 1:
            broken_tables.append({
                "index": len(broken_tables),
                "chunks": sorted(chunk_indices),
                "preview": table[0]["text"][:50],
            })

    table_score = None
    if tables:
        table_total = len(tables)
        table_score = 1.0 - (len(broken_tables) / table_total) if table_total > 0 else 1.0

    # ---- Heading integrity ----
    all_lines = []
    line_to_chunk = []
    for ci, chunk in enumerate(chunks):
        for line in chunk["content"].split("\n"):
            all_lines.append(line)
            line_to_chunk.append(ci)

    heading_indices = _detect_headings(all_lines)
    broken_headings = []
    for idx in heading_indices:
        # A heading is broken if it's the last content line of its chunk
        # and the next line is in a different chunk
        ci = line_to_chunk[idx]
        if idx + 1 < len(all_lines):
            next_ci = line_to_chunk[idx + 1]
            if next_ci != ci:
                broken_headings.append({
                    "index": len(broken_headings),
                    "text": all_lines[idx][:50],
                })

    heading_score = None
    if heading_indices:
        heading_total = len(heading_indices)
        heading_score = 1.0 - (len(broken_headings) / heading_total) if heading_total > 0 else 1.0

    # ---- Clause integrity ----
    clause_indices = _detect_clauses(all_lines)
    broken_clauses = []
    for idx in clause_indices:
        ci = line_to_chunk[idx]
        if idx + 1 < len(all_lines):
            next_ci = line_to_chunk[idx + 1]
            if next_ci != ci:
                broken_clauses.append({
                    "index": len(broken_clauses),
                    "text": all_lines[idx][:50],
                })

    clause_score = None
    if clause_indices:
        clause_total = len(clause_indices)
        clause_score = 1.0 - (len(broken_clauses) / clause_total) if clause_total > 0 else 1.0

    # ---- Aggregate score (skip sub-dimensions with total=0) ----
    available = [s for s in [table_score, heading_score, clause_score] if s is not None]
    overall = sum(available) / len(available) if available else 0.0

    return {
        "score": round(overall, 4),
        "table": {"score": table_score, "total": len(tables), "broken": broken_tables},
        "heading": {"score": heading_score, "total": len(heading_indices), "broken": broken_headings},
        "clause": {"score": clause_score, "total": len(clause_indices), "broken": broken_clauses},
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_chunk_scorer.py -v
```
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/eval/ tests/unit/test_chunk_scorer.py
git commit -m "feat: add structure integrity check (table/heading/clause)"
```

---

### Task 3: ChunkQualityScorer — SBR + granularity CV + orchestrator

**Files:**
- Modify: `src/eval/chunk_scorer.py`
- Modify: `tests/unit/test_chunk_scorer.py`

**Interfaces:**
- Consumes: `_check_structure_integrity(chunks)` from Task 2, `get_embeddings()` from src.models
- Produces: `ChunkQualityScorer.evaluate(chunks: list[dict], source: str) -> dict` returning full eval JSON

- [ ] **Step 1: Add tests for SBR and granularity CV**

Append to `tests/unit/test_chunk_scorer.py`:

```python
def test_sbr_no_breakage():
    """Adjacent chunks with same content should have similarity >= 0.35."""
    from src.eval.chunk_scorer import _calc_sbr
    embeddings = [[0.1, 0.2, 0.3], [0.1, 0.21, 0.29]]
    result = _calc_sbr(embeddings)
    assert result["score"] == 1.0  # no broken boundaries
    assert result["total_boundaries"] == 1
    assert result["broken_boundaries"] == []


def test_sbr_with_breakage():
    """Very different embeddings should be flagged as broken."""
    from src.eval.chunk_scorer import _calc_sbr
    import numpy as np
    # Orthogonal vectors -> cosine similarity ~ 0
    embeddings = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
    result = _calc_sbr(embeddings)
    assert result["score"] < 1.0
    assert len(result["broken_boundaries"]) >= 1


def test_granularity_cv_uniform():
    """Equal-length chunks should have low CV."""
    from src.eval.chunk_scorer import _calc_granularity_cv
    chunks = [
        {"content": "A" * 200, "metadata": {}},
        {"content": "B" * 200, "metadata": {}},
        {"content": "C" * 200, "metadata": {}},
    ]
    result = _calc_granularity_cv(chunks)
    assert result["cv"] == 0.0
    assert result["score"] == 1.0


def test_granularity_cv_with_extremes():
    """A very tiny chunk should be flagged as extreme."""
    from src.eval.chunk_scorer import _calc_granularity_cv
    chunks = [
        {"content": "A" * 200, "metadata": {}},
        {"content": "tiny", "metadata": {}},  # < 50 tokens
        {"content": "C" * 200, "metadata": {}},
    ]
    result = _calc_granularity_cv(chunks)
    assert len(result["extreme_chunks"]) >= 1
    assert result["extreme_chunks"][0]["type"] == "tiny"


def test_evaluate_full_pipeline():
    """Full evaluate() should return the complete eval JSON."""
    from src.eval.chunk_scorer import ChunkQualityScorer
    scorer = ChunkQualityScorer()
    chunks = [
        {"content": "| A | B |\n|---|---|\n| 1 | 2 |", "metadata": {"page": 1}},
        {"content": "（一）主要会计数据\n总资产 1.7亿元", "metadata": {"page": 1}},
    ]
    result = scorer.evaluate(chunks, "test.pdf")
    assert "overall_score" in result
    assert "structure_integrity" in result
    assert "sbr" in result
    assert "granularity_cv" in result
    assert "version" in result
    assert 0.0 <= result["overall_score"] <= 1.0


def test_evaluate_graceful_degradation():
    """If a metric fails, overall_score should use remaining metrics."""
    from src.eval.chunk_scorer import ChunkQualityScorer
    
    # Simulate SBR failure by making embed_documents raise
    class FailingScorer(ChunkQualityScorer):
        def _calc_sbr(self, chunks):
            raise RuntimeError("Embedding API timeout")
    
    scorer = FailingScorer()
    chunks = [
        {"content": "test content", "metadata": {}},
    ]
    result = scorer.evaluate(chunks, "test.pdf")
    assert result["sbr"]["error"] is not None
    assert result["overall_score"] is not None  # computed from remaining metrics


def test_empty_chunks():
    """Empty chunks list should return null scores, not crash."""
    from src.eval.chunk_scorer import ChunkQualityScorer
    scorer = ChunkQualityScorer()
    result = scorer.evaluate([], "empty.pdf")
    assert result["overall_score"] is None
    assert not result["passed"]
```

- [ ] **Step 2: Run tests to verify failures**

```bash
pytest tests/unit/test_chunk_scorer.py -v
```
Expected: New tests FAIL (functions not defined)

- [ ] **Step 3: Implement SBR**

Append to `src/eval/chunk_scorer.py`:

```python
import numpy as np


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_np = np.array(a)
    b_np = np.array(b)
    norm_a = np.linalg.norm(a_np)
    norm_b = np.linalg.norm(b_np)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_np, b_np) / (norm_a * norm_b))


def _calc_sbr(embeddings: list[list[float]], threshold: float = 0.35) -> dict:
    """Compute Semantic Breakage Rate from embedding vectors.
    
    Args:
        embeddings: List of embedding vectors, one per chunk
        threshold: Cosine similarity below this is a break
        
    Returns:
        dict with score, total_boundaries, broken_boundaries
    """
    if len(embeddings) < 2:
        return {"score": 1.0, "total_boundaries": 0, "broken_boundaries": []}

    similarities = []
    for i in range(len(embeddings) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        similarities.append(sim)

    broken = []
    for i, sim in enumerate(similarities):
        if sim < threshold:
            broken.append({"index": i, "similarity": round(sim, 4)})

    total = len(similarities)
    score = 1.0 - (len(broken) / total) if total > 0 else 1.0
    return {
        "score": round(score, 4),
        "total_boundaries": total,
        "broken_boundaries": broken,
    }
```

- [ ] **Step 4: Implement granularity CV**

Append to `src/eval/chunk_scorer.py`:

```python
def _count_tokens(text: str) -> int:
    """Rough token count (Chinese ~2 chars/token)."""
    return max(1, len(text) // 2)


def _calc_granularity_cv(chunks: list[dict]) -> dict:
    """Compute granularity consistency (CV) and detect extreme chunks.
    
    Args:
        chunks: List of dicts with "content" key
        
    Returns:
        dict with score, cv, min_tokens, max_tokens, extreme_chunks
    """
    if not chunks:
        return {
            "score": None,
            "cv": None,
            "min_tokens": 0,
            "max_tokens": 0,
            "extreme_chunks": [],
        }

    token_counts = [_count_tokens(c["content"]) for c in chunks]
    mean = statistics.mean(token_counts)
    cv = statistics.stdev(token_counts) / mean if mean > 0 else 0.0

    extreme = []
    for i, t in enumerate(token_counts):
        if t < 50:
            extreme.append({"index": i, "tokens": t, "type": "tiny"})
        elif t > 2 * mean:
            extreme.append({"index": i, "tokens": t, "type": "oversized"})

    score = 1.0 - min(cv, 1.0)
    return {
        "score": round(score, 4),
        "cv": round(cv, 4),
        "min_tokens": min(token_counts),
        "max_tokens": max(token_counts),
        "extreme_chunks": extreme,
    }
```

- [ ] **Step 5: Implement ChunkQualityScorer class**

Append to `src/eval/chunk_scorer.py`:

```python
class ChunkQualityScorer:
    """Evaluates chunk quality using 3 lightweight metrics.
    
    Usage:
        scorer = ChunkQualityScorer()
        result = scorer.evaluate(chunks, "filename.pdf")
        # result is a dict matching the meta_info.eval JSON schema
    """
    
    SBR_THRESHOLD = 0.35
    
    def evaluate(self, chunks: list[dict], source: str) -> dict:
        """Run all 3 metrics and return the full eval JSON.
        
        Args:
            chunks: List of chunk dicts with "content" and "metadata" keys
            source: Source filename for logging
            
        Returns:
            dict matching the meta_info.eval JSON schema
        """
        if not chunks:
            return {
                "version": 1,
                "enabled": True,
                "overall_score": None,
                "passed": False,
                "structure_integrity": self._safe_call("structure_integrity", _check_structure_integrity, chunks),
                "sbr": self._safe_call("sbr", self._calc_sbr, chunks),
                "granularity_cv": self._safe_call("granularity_cv", _calc_granularity_cv, chunks),
            }

        structure = self._safe_call("structure_integrity", _check_structure_integrity, chunks)
        sbr_result = self._safe_call("sbr", self._calc_sbr, chunks)
        cv_result = self._safe_call("granularity_cv", _calc_granularity_cv, chunks)

        # Compute overall score from metrics that succeeded
        scores = []
        weights = []
        metric_weights = {
            "structure_integrity": 0.40,
            "sbr": 0.30,
            "granularity_cv": 0.30,
        }

        for key, result in [("structure_integrity", structure), ("sbr", sbr_result), ("granularity_cv", cv_result)]:
            if result.get("score") is not None:
                scores.append(result["score"])
                weights.append(metric_weights[key])

        overall = None
        passed = False
        if scores and sum(weights) > 0:
            normalized_weights = [w / sum(weights) for w in weights]
            overall = sum(s * w for s, w in zip(scores, normalized_weights))
            passed = overall >= 0.70

        return {
            "version": 1,
            "enabled": True,
            "overall_score": round(overall, 4) if overall is not None else None,
            "passed": passed,
            "structure_integrity": structure,
            "sbr": sbr_result,
            "granularity_cv": cv_result,
        }

    def _calc_sbr(self, chunks: list[dict]) -> dict:
        """Compute SBR by embedding all chunks and checking adjacent similarity."""
        if len(chunks) < 2:
            return {"score": 1.0, "total_boundaries": 0, "broken_boundaries": []}

        texts = [c["content"] for c in chunks]
        embedder = get_embeddings()
        embeddings = embedder.embed_documents(texts)  # batch call

        result = _calc_sbr(embeddings, self.SBR_THRESHOLD)
        # Add 50-char previews to broken boundaries
        for b in result["broken_boundaries"]:
            idx = b["index"]
            b["preview_before"] = texts[idx][:50]
            b["preview_after"] = texts[idx + 1][:50]

        return result

    @staticmethod
    def _safe_call(name: str, func, *args, **kwargs) -> dict:
        """Call a metric function, returning error dict on failure."""
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            return {"score": None, "error": f"{name} failed: {e}"}
```

- [ ] **Step 6: Add imports to chunk_scorer.py**

Add at the top of `src/eval/chunk_scorer.py`:

```python
import statistics
import re
from typing import Any

import numpy as np

from src.models import get_embeddings
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/unit/test_chunk_scorer.py -v
```
Expected: All tests PASS (note: integration tests with real embedding API will be mocked/marked)

- [ ] **Step 8: Commit**

```bash
git add src/eval/chunk_scorer.py tests/unit/test_chunk_scorer.py
git commit -m "feat: add SBR and granularity CV metrics to ChunkQualityScorer"
```

---

### Task 4: MySQL queries — eval_report DDL + UPDATE_DOCUMENT_META_INFO

**Files:**
- Modify: `src/config/queries.py`
- Modify: `src/infra/db/mysql_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `INSERT_EVAL_REPORT`, `SELECT_LATEST_EVAL_REPORT`, `UPDATE_DOCUMENT_META_INFO` SQL constants + corresponding MySQLDB methods

- [ ] **Step 1: Add SQL constants to queries.py**

Append after `SOFT_DELETE_DOCUMENT_BY_ID` block (line ~185):

```python
# ====== 分块评估结果 ======

# eval_report 表 DDL（在第一次写入时自动创建）
CREATE_EVAL_REPORT_TABLE: str = """\
CREATE TABLE IF NOT EXISTS eval_report (
    id                  VARCHAR(36)  PRIMARY KEY,
    kb_id               VARCHAR(36)  NOT NULL,
    run_type            VARCHAR(20)  NOT NULL,
    qa_count            INT          NOT NULL,
    faithfulness        DECIMAL(5,4),
    answer_relevancy    DECIMAL(5,4),
    context_precision   DECIMAL(5,4),
    context_recall      DECIMAL(5,4),
    overall_score       DECIMAL(5,4),
    passed              TINYINT(1)   DEFAULT 0,
    report_path         VARCHAR(512),
    triggered_by        VARCHAR(36),
    detail_json         JSON,
    eval_date           DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE,
    INDEX idx_kb_date (kb_id, eval_date DESC)
)
"""

# 插入评估报告。参数：[id, kb_id, run_type, qa_count, faithfulness, answer_relevancy,
#                        context_precision, context_recall, overall_score, passed,
#                        report_path, triggered_by, detail_json]
INSERT_EVAL_REPORT: str = """\
INSERT INTO eval_report
    (id, kb_id, run_type, qa_count, faithfulness, answer_relevancy,
     context_precision, context_recall, overall_score, passed,
     report_path, triggered_by, detail_json)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# 查询知识库最新评估报告。参数：[kb_id]
SELECT_LATEST_EVAL_REPORT: str = """\
SELECT id, kb_id, run_type, qa_count, faithfulness, answer_relevancy,
       context_precision, context_recall, overall_score, passed,
       report_path, triggered_by, detail_json, eval_date
FROM eval_report
WHERE kb_id = %s
ORDER BY eval_date DESC
LIMIT 1
"""

# 更新文档 meta_info（JSON 列）。参数：[meta_info, doc_id]
UPDATE_DOCUMENT_META_INFO: str = """\
UPDATE document SET meta_info = %s WHERE id = %s
"""
```

- [ ] **Step 2: Add DB methods to mysql_db.py**

Add after `update_document_status` method (line ~455):

```python
async def update_document_meta_info(self, doc_id: str, meta_info: dict) -> None:
    """更新文档的 meta_info JSON 列（用于存储分块评估结果）。
    
    Args:
        doc_id: 文档 UUID
        meta_info: 要写入的 JSON 可序列化字典
    """
    from src.config.queries import UPDATE_DOCUMENT_META_INFO
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            json_str = json.dumps(meta_info, ensure_ascii=False)
            await cursor.execute(UPDATE_DOCUMENT_META_INFO, (json_str, doc_id))
        await conn.commit()
    logger.info("SQL: UPDATE document meta_info | doc_id={}", doc_id)
```

After `get_documents` method (line ~618), add eval_report methods:

```python
async def ensure_eval_report_table(self) -> None:
    """确保 eval_report 表存在（幂等）。"""
    from src.config.queries import CREATE_EVAL_REPORT_TABLE
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(CREATE_EVAL_REPORT_TABLE)
        await conn.commit()
    logger.info("SQL: CREATE TABLE IF NOT EXISTS eval_report")

async def insert_eval_report(self, report: dict) -> None:
    """插入一条 RAGAS 评估报告。
    
    Args:
        report: 包含 kb_id, run_type, qa_count, faithfulness, answer_relevancy,
                context_precision, context_recall, overall_score, passed,
                report_path, triggered_by, detail_json 的字典
    """
    from src.config.queries import INSERT_EVAL_REPORT
    import uuid
    
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            detail_str = json.dumps(report.get("detail_json", []), ensure_ascii=False) if report.get("detail_json") else None
            await cursor.execute(INSERT_EVAL_REPORT, (
                str(uuid.uuid4()),
                report["kb_id"],
                report.get("run_type", "manual"),
                report["qa_count"],
                report.get("faithfulness"),
                report.get("answer_relevancy"),
                report.get("context_precision"),
                report.get("context_recall"),
                report.get("overall_score"),
                1 if report.get("passed") else 0,
                report.get("report_path"),
                report.get("triggered_by"),
                detail_str,
            ))
        await conn.commit()
    logger.info("SQL: INSERT eval_report | kb_id={} run_type={}", report["kb_id"], report.get("run_type"))

async def get_latest_eval_report(self, kb_id: str) -> dict | None:
    """获取知识库最新的 RAGAS 评估报告。
    
    Args:
        kb_id: 知识库 UUID
        
    Returns:
        dict 或 None（无评估记录时）
    """
    from src.config.queries import SELECT_LATEST_EVAL_REPORT
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(SELECT_LATEST_EVAL_REPORT, (kb_id,))
            row = await cursor.fetchone()
    if row:
        return {
            "id": row[0], "kb_id": row[1], "run_type": row[2],
            "qa_count": row[3], "faithfulness": row[4],
            "answer_relevancy": row[5], "context_precision": row[6],
            "context_recall": row[7], "overall_score": row[8],
            "passed": bool(row[9]), "report_path": row[10],
            "triggered_by": row[11],
            "detail_json": json.loads(row[12]) if row[12] else None,
            "eval_date": row[13],
        }
    return None
```

Add import for `json` at the top of `mysql_db.py` if not already present:

```python
import json
```

- [ ] **Step 3: Ensure table migration on startup**

In `src/main.py` or `src/infra/db/mysql_db.py` init, add a call to `ensure_eval_report_table()`. The best place is in `MySQLDB.__init__` or its pool initialization. Add after the pool create section:

Reference the existing pattern in `mysql_db.py` — search for where `_get_pool` is first called, and add `await self.ensure_eval_report_table()` after table creation. If there's no dedicated init area, add a `lazy_init` flag or call it from `insert_eval_report` before insert.

Simplest approach: call `ensure_eval_report_table()` at the start of `insert_eval_report`:

```python
async def insert_eval_report(self, report: dict) -> None:
    await self.ensure_eval_report_table()
    # ... rest of insert ...
```

- [ ] **Step 4: Run existing tests to verify no breakage**

```bash
pytest tests/ -v -x
```
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/queries.py src/infra/db/mysql_db.py
git commit -m "feat: add eval_report table and meta_info update queries"
```

---

### Task 5: Integrate ChunkQualityScorer into upload pipeline

**Files:**
- Modify: `src/api/documents.py`

**Interfaces:**
- Consumes: `ChunkQualityScorer` from Task 3, `svc.db.update_document_meta_info()` from Task 4
- Produces: `meta_info.eval` written to each uploaded document's MySQL record

- [ ] **Step 1: Modify `_process_document_task` to run evaluation**

In `src/api/documents.py`, add import at top:

```python
from src.config import CHUNK_EVAL_ENABLED
from src.eval.chunk_scorer import ChunkQualityScorer
```

After the `validate_chunks` block (after line ~286), insert:

```python
            # 分块质量评估 — 开关控制，只记录不拦截
            if CHUNK_EVAL_ENABLED:
                try:
                    scorer = ChunkQualityScorer()
                    eval_result = await asyncio.to_thread(
                        scorer.evaluate, chunks, filename
                    )
                    await svc.db.update_document_meta_info(doc_id, {"eval": eval_result})
                    logger.info(
                        "Chunk eval for '{}': score={} passed={}",
                        filename,
                        eval_result.get("overall_score"),
                        eval_result.get("passed"),
                    )
                except Exception as eval_err:
                    logger.warning("Chunk eval failed for '{}': {}", filename, eval_err)
```

- [ ] **Step 2: Handle dedup case**

In the dedup code section (around line 155-162 in `upload_document`), add copy of `meta_info.eval`:

Find the dedup return block and modify it to also copy the eval data. The current code returns early before `_process_document_task` runs. You need to also copy `meta_info.eval`:

```python
            # 去重时保留评估数据
            if d.get("meta_info") and isinstance(d["meta_info"], str):
                import json
                try:
                    meta = json.loads(d["meta_info"])
                    if "eval" in meta:
                        await svc.db.update_document_meta_info(d["id"], {"eval": meta["eval"]})
                except (json.JSONDecodeError, Exception):
                    pass
```

This code should go after the dedup check but before the return. Look for where `dedup=True` is returned and add the copy logic there.

- [ ] **Step 3: Run existing tests to verify no breakage**

```bash
pytest tests/ -v -x
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/api/documents.py
git commit -m "feat: integrate ChunkQualityScorer into document upload pipeline"
```

---

### Task 6: Frontend API — extend DocumentListResponse

**Files:**
- Modify: `src/api/model/response.py`
- Modify: `src/api/documents.py` (list endpoint)
- Create: `src/api/kb_eval.py`

**Interfaces:**
- Consumes: `svc.db.get_latest_eval_report()` from Task 4
- Produces: Extended `DocumentListResponse` with eval fields, new KB eval endpoint

- [ ] **Step 1: Extend DocumentListResponse**

In `src/api/model/response.py`, add fields to `DocumentListResponse`:

```python
class DocumentListResponse(BaseModel):
    """文档列表响应（含分块评估数据）。"""
    id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    created_at: str
    chunk_count: int = 0
    eval_score: float | None = None
    eval_passed: bool | None = None
    eval_detail: dict | None = None
```

- [ ] **Step 2: Parse meta_info.eval in list endpoint**

In `src/api/documents.py`, modify the list endpoint to parse `meta_info.eval`:

```python
import json

# Inside the list comprehension after d["chunk_count"]:
eval_score = None
eval_passed = None
eval_detail = None
meta_raw = d.get("meta_info")
if meta_raw:
    try:
        if isinstance(meta_raw, str):
            meta = json.loads(meta_raw)
        else:
            meta = meta_raw
        eval_data = meta.get("eval", {}) if isinstance(meta, dict) else {}
        if eval_data:
            eval_score = eval_data.get("overall_score")
            eval_passed = eval_data.get("passed")
            eval_detail = eval_data
    except (json.JSONDecodeError, AttributeError):
        pass
```

Update the `DocumentListResponse` construction to include these fields:

```python
return [
    DocumentListResponse(
        id=d["id"],
        filename=d["filename"],
        file_type=d["file_type"],
        file_size=d["file_size"],
        status=d["status"],
        created_at=d["created_at"].isoformat()
        if hasattr(d["created_at"], "isoformat")
        else d["created_at"],
        chunk_count=d.get("chunk_count", 0),
        eval_score=eval_score,
        eval_passed=eval_passed,
        eval_detail=eval_detail,
    )
    for d in docs
]
```

- [ ] **Step 3: Create KB eval endpoint**

Create `src/api/kb_eval.py`:

```python
"""知识库级 RAGAS 评估结果查询 API。"""

from fastapi import APIRouter, Request
from loguru import logger

from src.api.model.response import BaseResponse
from src.app_service import AppService

router = APIRouter()


def _get_service() -> AppService:
    return AppService()


@router.post("/kbs/eval/latest")
async def get_latest_kb_eval(kb_id: str, request: Request = None) -> BaseResponse:
    """获取知识库最新的 RAGAS 评估结果。
    
    Args:
        kb_id: 知识库 UUID
        
    Returns:
        BaseResponse with eval data or null
    """
    svc = _get_service()
    report = await svc.db.get_latest_eval_report(kb_id)
    if report:
        return BaseResponse(data={
            "eval_date": report["eval_date"].isoformat() if hasattr(report["eval_date"], "isoformat") else str(report["eval_date"]),
            "faithfulness": float(report["faithfulness"]) if report["faithfulness"] else None,
            "answer_relevancy": float(report["answer_relevancy"]) if report["answer_relevancy"] else None,
            "context_precision": float(report["context_precision"]) if report["context_precision"] else None,
            "context_recall": float(report["context_recall"]) if report["context_recall"] else None,
            "overall_score": float(report["overall_score"]) if report["overall_score"] else None,
            "passed": report["passed"],
            "qa_count": report["qa_count"],
            "run_type": report["run_type"],
        })
    return BaseResponse(data=None)
```

In `src/api/__init__.py` or `src/main.py`, register the router:

```python
from src.api.kb_eval import router as kb_eval_router
app.include_router(kb_eval_router, prefix="/api")
```

- [ ] **Step 4: Run existing tests to verify no breakage**

```bash
pytest tests/ -v -x
```
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/model/response.py src/api/documents.py src/api/kb_eval.py
git commit -m "feat: extend document list API with eval data, add KB eval endpoint"
```

---

### Task 7: RAGAS CLI — write to eval_report table

**Files:**
- Modify: `src/cli/eval_ragas.py`

**Interfaces:**
- Consumes: `svc.db.insert_eval_report()`, `svc.db.get_kb_by_name()` from Task 4
- Produces: eval_report rows on each RAGAS CLI run

- [ ] **Step 1: Add eval_report persistence**

In `src/cli/eval_ragas.py`, after `save_markdown_report()` call (around line 520), add:

```python
            # ---- 写入 eval_report 表 ----
            try:
                from src.app_service import AppService
                from src.config.qa_pairs import QUESTIONS, GROUND_TRUTH

                eval_svc = AppService()
                kb_id = eval_svc.db.get_kb_by_name(kb_name)
                if kb_id:
                    df = result.to_pandas()
                    metric_cols = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
                    detail = []
                    for i, q in enumerate(QUESTIONS):
                        entry = {"q_index": i, "question": q[:100]}
                        for col in metric_cols:
                            if col in df.columns:
                                entry[col] = float(df[col].iloc[i])
                        detail.append(entry)

                    avg = {}
                    for col in metric_cols:
                        if col in df.columns:
                            avg[col] = float(df[col].mean())

                    faithfulness = avg.get("faithfulness")
                    context_recall = avg.get("context_recall")
                    context_precision = avg.get("context_precision")
                    answer_relevancy = avg.get("answer_relevancy")

                    # Compute weighted overall score
                    weights = {"faithfulness": 0.3, "context_recall": 0.3,
                               "context_precision": 0.2, "answer_relevancy": 0.2}
                    weighted_sum = 0.0
                    total_w = 0.0
                    for k, w in weights.items():
                        v = avg.get(k)
                        if v is not None:
                            weighted_sum += v * w
                            total_w += w
                    overall = weighted_sum / total_w if total_w > 0 else None

                    await eval_svc.db.insert_eval_report({
                        "kb_id": kb_id,
                        "run_type": "manual",
                        "qa_count": len(QUESTIONS),
                        "faithfulness": faithfulness,
                        "answer_relevancy": answer_relevancy,
                        "context_precision": context_precision,
                        "context_recall": context_recall,
                        "overall_score": overall,
                        "passed": overall >= 0.70 if overall is not None else False,
                        "report_path": output_path,
                        "triggered_by": None,
                        "detail_json": detail,
                    })
                    logger.info("Eval report written to eval_report table for KB '{}'", kb_name)
            except Exception as db_err:
                logger.warning("Failed to write eval report to database: {}", db_err)
```

Note: `insert_eval_report` is async, so this code must be inside an async context. Looking at the existing `main()`, it already has `async` in its flow (though it's called from `if __name__`). Ensure `insert_eval_report` gets awaited.

- [ ] **Step 2: Run a quick smoke test**

```bash
python -m src.cli.eval_ragas --check
```
Expected: QA pair count OK, exits 0

- [ ] **Step 3: Commit**

```bash
git add src/cli/eval_ragas.py
git commit -m "feat: persist RAGAS eval results to eval_report table"
```

---

### Task 8: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Ruff check**

```bash
ruff check . --fix
```
Expected: No errors

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 3: Manual integration check**

Verify the module imports work:
```bash
python -c "from src.eval.chunk_scorer import ChunkQualityScorer; print('OK')"
python -c "from src.config import CHUNK_EVAL_ENABLED; print('OK')"
```

- [ ] **Step 4: Final commit if needed**

```bash
git add -A
git commit -m "chore: final cleanup for chunk evaluation system"
```
