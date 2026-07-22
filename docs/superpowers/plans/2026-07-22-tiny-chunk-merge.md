# Tiny Chunk Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge document chunks with < 50 tokens into the preceding chunk during document processing, eliminating semantically incomplete tiny chunks.

**Architecture:** Single-function addition in `src/api/documents.py` as a post-processing step after `_enrich_chunk_pages` and before `validate_chunks`. Applies only to `parent_child` and `table_preserving` strategies; `qa` strategy is skipped. Pure data transformation — no changes to parser, chunker, or storage layers.

**Tech Stack:** Python 3.11+, existing `documents.py` module, `BaseChunker.count_tokens()`

## Global Constraints

- Merge threshold: 50 tokens (aligns with `validator.py` tiny chunk detection)
- Only applies to `parent_child` and `table_preserving` chunk strategies
- `qa` strategy chunks MUST pass through unchanged
- Merge direction: backward (tiny chunk appended to preceding chunk with `\n`)
- `_enrich_chunk_pages` MUST run before merge to preserve page metadata
- `metadata.tokens` MUST be recalculated after merge
- `ruff format . && ruff check . --fix` MUST pass clean
- `pytest tests/ -v` MUST pass (pre-existing failures excluded)

---

### Task 1: Add `_merge_tiny_chunks` function

**Files:**
- Modify: `src/api/documents.py` — add function and import
- Test: `tests/api/test_documents.py` — add test class

**Interfaces:**
- Consumes: `chunks: list[dict]` (chunker output), `strategy: str` (detected strategy name), `min_tokens: int = 50`
- Produces: `list[dict]` (merged chunks)

- [ ] **Step 1: Add import to documents.py**

Insert after line 38 (`from src.infra.errors import BusinessError, SystemError`):

```python
from src.infra.chunking.strategies.base import BaseChunker
```

- [ ] **Step 2: Add test file**

Test file already exists at `tests/api/test_documents.py`. Read it to find where to add:

```bash
grep -n "def test_" tests/api/test_documents.py | tail -5
```

Add at the end of the file:

```python
"""Tests for _merge_tiny_chunks — tiny chunk post-processing."""

import pytest
from src.api.documents import _merge_tiny_chunks


def test_merge_tiny_normal():
    """Normal merge: text chunk (256 tokens) + tiny (44 tokens) -> 1 chunk."""
    chunks = [
        {"content": "A" * 512, "metadata": {"tokens": 256, "block_type": "text"}},  # 512 chars ≈ 256 tokens
        {"content": "tiny tail", "metadata": {"tokens": 44, "block_type": "text"}},
    ]
    result = _merge_tiny_chunks(chunks)
    assert len(result) == 1
    assert result[0]["metadata"]["tokens"] == (512 + 9) // 2  # recalculated: 260


def test_merge_tiny_first_chunk():
    """First chunk is tiny: stays standalone."""
    chunks = [
        {"content": "tiny first", "metadata": {"tokens": 5, "block_type": "text"}},
        {"content": "B" * 600, "metadata": {"tokens": 300, "block_type": "text"}},
    ]
    result = _merge_tiny_chunks(chunks)
    assert len(result) == 2  # not merged


def test_merge_tiny_consecutive():
    """Multiple consecutive tiny chunks: all merged into predecessor."""
    chunks = [
        {"content": "C" * 500, "metadata": {"tokens": 250, "block_type": "text"}},
        {"content": "tiny1", "metadata": {"tokens": 10, "block_type": "text"}},
        {"content": "tiny2", "metadata": {"tokens": 8, "block_type": "text"}},
        {"content": "D" * 600, "metadata": {"tokens": 300, "block_type": "text"}},
    ]
    result = _merge_tiny_chunks(chunks)
    assert len(result) == 3
    assert "tiny1" in result[0]["content"]
    assert "tiny2" in result[0]["content"]


def test_merge_tiny_qa_skip():
    """QA strategy: passes through unchanged."""
    chunks = [
        {"content": "问：你好？答：我很好。", "metadata": {"tokens": 12, "block_type": "text"}},
    ]
    result = _merge_tiny_chunks(chunks, strategy="qa")
    assert len(result) == 1  # no merge


def test_merge_tiny_empty():
    """Empty list: returns empty."""
    result = _merge_tiny_chunks([])
    assert result == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/api/test_documents.py::test_merge_tiny_normal \
       tests/api/test_documents.py::test_merge_tiny_first_chunk \
       tests/api/test_documents.py::test_merge_tiny_consecutive \
       tests/api/test_documents.py::test_merge_tiny_qa_skip \
       tests/api/test_documents.py::test_merge_tiny_empty -v
```

Expected: All 5 tests fail with `AttributeError: module 'src.api.documents' has no attribute '_merge_tiny_chunks'`

- [ ] **Step 4: Implement `_merge_tiny_chunks` function**

Add after `_enrich_chunk_pages` (after line 253 in documents.py):

```python
def _merge_tiny_chunks(
    chunks: list[dict],
    strategy: str = "",
    min_tokens: int = 50,
) -> list[dict]:
    """将 tokens < min_tokens 的 tiny chunk 合并到前一个 chunk。

    仅对 parent_child 和 table_preserving 策略生效。
    qa 策略的 chunk 是完整问答对，合并会破坏语义结构，跳过。

    Args:
        chunks: chunker.chunk() 输出的 chunk 列表
        strategy: 当前文档的分块策略
        min_tokens: tiny chunk 判定阈值

    Returns:
        合并后的 chunk 列表
    """
    if strategy == "qa":
        return chunks

    merged: list[dict] = []
    for c in chunks:
        tokens = c["metadata"].get("tokens", 0) or BaseChunker.count_tokens(
            c["content"]
        )
        if tokens < min_tokens and merged:
            merged[-1]["content"] += "\n" + c["content"]
            merged[-1]["metadata"]["tokens"] = BaseChunker.count_tokens(
                merged[-1]["content"]
            )
        else:
            merged.append(c)
    return merged
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/api/test_documents.py::test_merge_tiny_normal \
       tests/api/test_documents.py::test_merge_tiny_first_chunk \
       tests/api/test_documents.py::test_merge_tiny_consecutive \
       tests/api/test_documents.py::test_merge_tiny_qa_skip \
       tests/api/test_documents.py::test_merge_tiny_empty -v
```

Expected: All 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/api/documents.py tests/api/test_documents.py
git commit -m "feat: add _merge_tiny_chunks for merging sub-50-token chunks"
```

---

### Task 2: Wire `_merge_tiny_chunks` into `_process_document_task`

**Files:**
- Modify: `src/api/documents.py:329-330` — call the new function

**Interfaces:**
- Consumes: `_merge_tiny_chunks()` from Task 1, `strategy` variable already available in `_process_document_task`
- Produces: merged chunks used for validation and storage

- [ ] **Step 1: Read the current insertion point**

```bash
grep -n "_enrich_chunk_pages\|validate_chunks\|chunk_data_list" src/api/documents.py | head -10
```

Expected to show lines around 330-335.

- [ ] **Step 2: Insert the merge call**

Find the two lines:
```python
            _enrich_chunk_pages(chunks, parse_result.chunks, full_text)

            # 分块质量校验 — CPU，to_thread
```

Replace with:
```python
            _enrich_chunk_pages(chunks, parse_result.chunks, full_text)

            # 合并 tiny chunk — 将 < 50 tokens 的碎片合并到前一个 chunk
            chunks = _merge_tiny_chunks(chunks, strategy)

            # 分块质量校验 — CPU，to_thread
```

- [ ] **Step 3: Run existing tests to confirm no regressions**

```bash
pytest tests/api/test_documents.py -v --timeout=30 2>&1 | tail -20
```

Expected: All existing tests pass plus the 5 new tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/api/documents.py
git commit -m "feat: wire _merge_tiny_chunks into document processing pipeline"
```

---

### Task 3: Final verification

**Files:**
- All modified files

- [ ] **Step 1: Full lint and format check**

```bash
ruff format . && ruff check . --fix
```

Expected: All checks passed.

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v 2>&1 | tail -30
```

Expected: All tests pass (pre-existing failures are acceptable).

- [ ] **Step 3: Verify no unused imports**

```bash
ruff check . | grep "F401" || echo "No unused imports"
```

- [ ] **Step 4: Show final diff**

```bash
git diff --stat
```

Expected: Shows changes to `src/api/documents.py` and `tests/api/test_documents.py`.

- [ ] **Step 5: Commit any remaining cleanup**

```bash
git add -A
git commit -m "chore: format and lint fixes after tiny chunk merge"
```
