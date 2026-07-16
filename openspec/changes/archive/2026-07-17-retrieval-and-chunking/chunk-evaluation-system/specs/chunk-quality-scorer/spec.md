# chunk-quality-scorer Specification

## Purpose

在上传文件的后台处理流程中，对分块结果自动运行 3 个轻量质量指标，评估结果写入 document.meta_info JSON 列，用于前端展示和问题定位。

## Requirements

### Requirement: Global toggle for chunk evaluation

The system SHALL provide a global toggle `CHUNK_EVAL_ENABLED` (default false) in `settings.py` to control whether chunk quality evaluation runs during document processing.

#### Scenario: Toggle off skips evaluation
- **WHEN** `CHUNK_EVAL_ENABLED=false` and a file is uploaded
- **THEN** the document SHALL be processed normally without running quality evaluation, and `meta_info` SHALL NOT contain an `eval` field

#### Scenario: Toggle on runs evaluation
- **WHEN** `CHUNK_EVAL_ENABLED=true` and a file is uploaded
- **THEN** the system SHALL run all 3 metrics after chunking and before ChromaDB insertion
- **AND** `ChunkQualityScorer.evaluate()` is a synchronous call, SHALL be wrapped in `asyncio.to_thread()` within `_process_document_task` to avoid blocking the event loop
- **AND** results SHALL be written to `document.meta_info`

### Requirement: Structure integrity check

The system SHALL detect structural breaks in chunked content, covering three sub-dimensions:

- **table**: Detect if markdown table rows (`|...|` pattern) are split across chunks. Report total tables and broken count.
- **heading**: Detect if heading lines (matched by patterns `^[一二三四五六七八九十]+、`, `^（[一二三四五六七八九十]+）`, `^\d+[\.、]`, `^第[一二三四五六七八九十]+条`) are separated from their following body text across chunks. If regex detection proves unreliable, this check may be downgraded to only table + clause.
- **clause**: Detect if numbered list items (`1.`, `1、`, `(1)`, `•`) are split across chunks.

#### Scenario: Table fully contained in one chunk
- **WHEN** a markdown table is entirely within a single chunk
- **THEN** the table integrity score SHALL be 1.0 for that table

#### Scenario: Table split across chunks
- **WHEN** consecutive `|...|` lines of a markdown table span multiple chunks
- **THEN** the table SHALL be marked as broken in `meta_info.eval.structure_integrity.table.broken`

#### Scenario: Heading detected and intact
- **WHEN** a heading regex matches and the heading's body text is in the same chunk
- **THEN** the heading integrity score SHALL be 1.0 for that heading

#### Scenario: Heading separated from body
- **WHEN** a heading regex matches but the heading is at the end of chunk N and its body is in chunk N+1
- **THEN** the heading SHALL be marked as broken in `meta_info.eval.structure_integrity.heading.broken`

### Requirement: Semantic Breakage Rate (SBR)

The system SHALL compute semantic similarity between adjacent chunks using `get_embeddings()` (the project's default embedding model, `text-embedding-v1` via DashScope). For efficiency, all chunk texts SHALL be embedded in a single batch call via `embed_documents()` before computing pairwise cosine similarity. A boundary with cosine similarity below 0.35 is considered a semantic break.

#### Scenario: Adjacent chunks are semantically continuous
- **WHEN** two adjacent chunks have cosine similarity >= 0.35
- **THEN** the boundary SHALL NOT be flagged as broken

#### Scenario: Adjacent chunks have semantic gap
- **WHEN** two adjacent chunks have cosine similarity < 0.35
- **THEN** the boundary SHALL be recorded in `meta_info.eval.sbr.broken_boundaries` with the similarity value and 50-char previews

### Requirement: Granularity consistency (CV)

The system SHALL compute the coefficient of variation of chunk token counts, and detect chunks that are extremely small (< 50 tokens) or oversized (> 2× mean length).

#### Scenario: Normal variation
- **WHEN** all chunk sizes are within 0.5× to 2× of the mean
- **THEN** the granularity CV SHALL be the computed coefficient, and `extreme_chunks` SHALL be empty

#### Scenario: Extreme chunks detected
- **WHEN** a chunk has < 50 tokens or > 2× mean length
- **THEN** it SHALL be recorded in `meta_info.eval.granularity_cv.extreme_chunks` with index, token count, and type (`tiny` or `oversized`)

### Requirement: Score normalization

All sub-metrics SHALL be normalized to a 0-1 range where 1.0 = best quality:

- `structure_integrity.score` = average of available sub-dimension scores, skipping any with `total=0`. Each sub-dimension score = `1 - broken/total`.
- `sbr.score` = (1 - broken_boundaries/total_boundaries), **not** the raw similarity value
- `granularity_cv.score` = (1 - min(cv, 1)), with cv = std/mean of token counts

Each metric SHALL expose a `score` field in its JSON output.

### Requirement: Overall score and pass/fail

The system SHALL compute an overall score as weighted average: `0.40 × structure_integrity.score + 0.30 × sbr.score + 0.30 × granularity_cv.score`. A score >= 0.70 SHALL be marked as passed.

#### Scenario: Score meets threshold
- **WHEN** `overall_score >= 0.70`
- **THEN** `meta_info.eval.passed` SHALL be `true`

#### Scenario: Score below threshold
- **WHEN** `overall_score < 0.70`
- **THEN** `meta_info.eval.passed` SHALL be `false`

### Requirement: Graceful degradation on metric failure

If any single metric fails (e.g., embedding model timeout for SBR), the system SHALL skip that metric and compute overall score from remaining valid metrics. If all metrics fail, overall_score SHALL be null.

#### Scenario: Partial metric failure
- **WHEN** SBR fails due to embedding timeout but structure integrity and CV succeed
- **THEN** the failed metric SHALL have `"error": "reason"` and `"score": null`, and overall_score SHALL be computed from the 2 valid metrics

### Requirement: Dedup copies eval data

When a file is deduplicated (MD5 match within same KB), the system SHALL copy the `meta_info.eval` from the existing document record to the new one.

#### Scenario: Dedup preserves eval scores
- **WHEN** an upload is detected as a duplicate and returns `dedup=true`
- **THEN** the new document record SHALL have the same `meta_info.eval` as the original
