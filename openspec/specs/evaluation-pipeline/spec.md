# evaluation-pipeline Specification

## Purpose
TBD - created by archiving change mvp-core-features. Update Purpose after archive.
## Requirements
### Requirement: QA test pair coverage
The system SHALL maintain a minimum of 20 QA test pairs covering at least two distinct financial documents (e.g., 贵州茅台 2024 年报 and 厦门灿坤 2019 年报).

Test questions SHALL cover the following dimensions:
- Revenue and growth metrics
- Per-share earnings and shareholder structure
- Core business analysis
- Regional/segment performance
- Company basic information

#### Scenario: QA pair count meets minimum
- **WHEN** running `python -m src.eval_ragas --check`
- **THEN** the system SHALL report the total QA pair count and warn if below 20

### Requirement: Chunk size comparison
The system SHALL support automated comparison of different chunk sizes (512, 768, 1024 tokens) across all QA test pairs.

For each chunk size configuration, the system SHALL compute:
- faithfulness
- answer_relevancy
- context_precision
- context_recall

#### Scenario: Run chunk size comparison
- **WHEN** user runs `python -m scripts.compare_chunk`
- **THEN** the system SHALL generate a Markdown report at `data/reports/chunk_comparison.md` with all four metrics per chunk size

### Requirement: Evaluation report archival
All RAGAS evaluation results SHALL be saved to `data/reports/` with timestamped filenames in format `ragas_eval_<date>_<chunk_size>.csv`.

A summary Markdown report SHALL be generated after each evaluation run listing:
- Date and configuration
- QA pair count
- Per-question metric scores
- Aggregate metric averages

#### Scenario: Evaluation saves CSV and Markdown
- **WHEN** running `python -m src.eval_ragas`
- **THEN** a CSV file and a Markdown summary SHALL be created in `data/reports/`

### Requirement: Evaluation passes without crash on empty KB
The evaluation system SHALL handle the case where the knowledge base has no documents gracefully, logging a clear error message and exiting with a non-zero exit code.

#### Scenario: Empty KB evaluation
- **WHEN** running evaluation against a KB with no documents
- **THEN** the system SHALL log "Knowledge base is empty" and exit with code 1

