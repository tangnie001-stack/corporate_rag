## MODIFIED Requirements

### Requirement: QA test pair coverage

The system SHALL maintain a minimum of **50** QA test pairs covering at least two distinct financial documents (e.g., 贵州茅台 2024 年报 and 厦门灿坤 2019 年报).

Test questions SHALL cover the following dimensions:
- Revenue and growth metrics
- Per-share earnings and shareholder structure
- Core business analysis
- Regional/segment performance
- Company basic information

#### Scenario: QA pair count meets minimum
- **WHEN** running `python -m src.cli.eval_ragas --check`
- **THEN** the system SHALL report the total QA pair count and exit with 0

#### Scenario: QA pair count below minimum
- **WHEN** QA pair count is below 50
- **THEN** the system SHALL print guidance message and exit with 1 when in standalone `--check` mode, or print a warning and continue evaluation when in companion mode

## REMOVED Requirements

### Requirement: Chunk size comparison
**Reason**: Project has fully migrated to Parent-Child chunking; `--chunk-size` parameter can no longer reflect actual chunking behavior. `compare_chunk.py` has been deleted.
**Migration**: Use actual RAGAS evaluation on real KB after ingestion to compare chunking effectiveness.

### Requirement: Evaluation report filename includes chunk_size
**Reason**: `--chunk-size` parameter removed; no longer needed in filename.
**Migration**: Evaluation reports use default naming `ragas_eval_<timestamp>.csv` without chunk size tag.
