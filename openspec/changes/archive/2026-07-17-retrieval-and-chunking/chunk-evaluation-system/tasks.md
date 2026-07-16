## 1. Settings & Config

- [ ] 1.1 Add `CHUNK_EVAL_ENABLED` setting to `src/config/settings.py` (default false, ref existing `LANGFUSE_ENABLE` pattern)

## 2. Chunk Evaluation Scorer

- [ ] 2.1 Create `src/eval/chunk_scorer.py` with `ChunkQualityScorer` class
- [ ] 2.2 Implement `structure_integrity()` — table regex detection (|...| 连续性)
- [ ] 2.3 Implement `structure_integrity()` — heading regex detection (编号/中文数字/条款)
- [ ] 2.4 Implement `structure_integrity()` — clause continuity detection
- [ ] 2.5 Implement `sbr()` — adjacent chunk cosine similarity, batch embed all chunks via `get_embeddings().embed_documents()`, then compute pairwise similarities, flag boundaries below 0.35
- [ ] 2.6 Implement `granularity_cv()` — length CV + extreme chunk detection, output includes both `score` (1 - min(cv,1)) and raw `cv` value
- [ ] 2.7 Implement `overall_score()` — weighted combination (0.4/0.3/0.3) + pass/fail, skip sub-dimensions with total=0
- [ ] 2.8 Implement `evaluate(chunks, parse_result) -> dict` — orchestrator that runs all 3 metrics and returns the eval JSON dict
- [ ] 2.9 Implement graceful degradation — if a metric fails (exception), record error in JSON and skip from overall score

## 3. Integration into Upload Pipeline

- [ ] 3.1 In `_process_document_task` (`src/api/documents.py`), after chunking and validate_chunks, call `ChunkQualityScorer.evaluate()` if `CHUNK_EVAL_ENABLED=true` (wrap in `asyncio.to_thread()` since the scorer is synchronous)
- [ ] 3.2 Write the eval dict into `document.meta_info` via a dedicated `UPDATE document SET meta_info = %s WHERE id = %s` query (not through `update_document_status`, which lacks a meta_info parameter)
- [ ] 3.3 Handle dedup: copy `meta_info.eval` from existing record when `dedup=true`
- [ ] 3.4 Handle partial failure: log warning if any metric fails, continue with remaining

## 4. Eval Report Storage (MySQL)

- [ ] 4.1 Add `eval_report` DDL and `UPDATE_DOCUMENT_META_INFO` query to `src/config/queries.py`
- [ ] 4.2 Add `insert_eval_report()` method to `MySQLDB` class in `src/infra/db/mysql_db.py`
- [ ] 4.3 Add `get_latest_eval_report(kb_id)` method to `MySQLDB` class
- [ ] 4.4 Add `update_document_meta_info(doc_id, meta_info)` method to `MySQLDB` class
- [ ] 4.5 Run migration: ensure eval_report table exists on startup or first use

## 5. RAGAS CLI Integration

- [ ] 5.1 In `src/cli/eval_ragas.py`, after evaluation completes, resolve kb_id from kb_name, compute overall_score, insert into `eval_report` table
- [ ] 5.2 Store per-question metric scores as `detail_json` in the eval_report row

## 6. Frontend API

- [ ] 6.1 Extend `DocumentListResponse` with `eval_score: float`, `eval_passed: bool`, `eval_detail: dict`
- [ ] 6.2 In `documents/list` endpoint, parse `meta_info.eval` JSON and populate the new response fields
- [ ] 6.3 Add KB-level API endpoint or extend existing KB detail endpoint to return latest `eval_report` summary

## 7. Frontend Display

- [ ] 7.1 Document list row: show eval_score + eval_passed icon (✓/✗) with color (green/red)
- [ ] 7.2 "查看详情" button on each row, opens first-level modal
- [ ] 7.3 First-level modal: three sections (structure_integrity / sbr / granularity_cv) with scores and broken counts
- [ ] 7.4 Second-level modals: clicking [查看] on a broken item shows broken detail with preview text
- [ ] 7.5 KB page header: show latest RAGAS eval summary (most recent eval_report record)

## 8. Testing & Verification

- [ ] 8.1 Unit test: `ChunkQualityScorer` with known-good and known-bad chunk data
- [ ] 8.2 Integration test: upload a file with `CHUNK_EVAL_ENABLED=true` and verify `meta_info.eval` is populated
- [ ] 8.3 Integration test: run `python -m src.cli.eval_ragas` and verify `eval_report` table has new row
- [ ] 8.4 Test edge cases: dedup file, partially failed metrics, toggle off, historical files
- [ ] 8.5 Run `ruff check .` and `pytest tests/ -v` to ensure no regressions
