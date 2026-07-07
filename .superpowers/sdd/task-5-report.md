## Implementer Report: Task 5 — RAG 链日志 + 后台任务

**Status:** DONE

**Changes:**
- `src/rag_chain.py` — 3 changes:
  - A) `search()`: 3 return paths refactored to capture results → log → return
  - B) `rerank()`: capture contexts → log before/after counts → return
  - C) `_stream_answer()`: added first-token latency timer + log
- `src/api/routes/documents.py` — added `logger.info("process_task start: ...")` at top of `_process_document_task`

**ruff check:** All checks passed!
**Concerns:** none
