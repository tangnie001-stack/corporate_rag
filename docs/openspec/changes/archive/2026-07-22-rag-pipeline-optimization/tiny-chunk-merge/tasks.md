## 1. Core Implementation

- [x] 1.1 Add `BaseChunker` import to `src/api/documents.py`
- [x] 1.2 Add `_merge_tiny_chunks()` function in `src/api/documents.py`
  - Skip merge when strategy == "qa"
  - Iterate chunks; merge tokens < 50 into previous chunk
  - Recalculate `tokens` metadata after merge
- [x] 1.3 Call `_merge_tiny_chunks(chunks, strategy)` in `_process_document_task`
  - Insert after `_enrich_chunk_pages()` and before `validate_chunks()`
  - Use merged result for all downstream processing

## 2. Testing

- [x] 2.1 Write unit tests for `_merge_tiny_chunks`:
  - Normal merge: text chunk (256 tokens) + tiny chunk (44 tokens) → merged to 1 chunk
  - First chunk tiny: stays standalone
  - Consecutive tiny: all merged into same predecessor
  - QA strategy skip: returns unchanged
  - Empty list: returns empty
- [x] 2.2 Run `pytest tests/ -v` to confirm no regressions
- [x] 2.3 Manual verification: upload a document with previously-known tiny chunk issue and confirm tiny chunks are eliminated

## 3. Cleanup

- [x] 3.1 `ruff format . && ruff check . --fix`
- [x] 3.2 Commit with message: "feat: merge tiny chunks (<50 tokens) before storage"
