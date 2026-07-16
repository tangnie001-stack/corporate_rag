# evaluation-pipeline Specification (Delta)

## MODIFIED Requirements

### Requirement: Evaluation report archival

**UPDATE**: RAGAS evaluation results SHALL be saved to `data/reports/` AND simultaneously inserted into the `eval_report` table. The CSV and Markdown reports remain as-is; the database write is additive.

The system SHALL support writing RAGAS evaluation results to the `eval_report` table after each evaluation run, containing:
- kb_id (resolved from knowledge base name)
- run_type (always 'manual' for CLI runs)
- qa_count (number of QA pairs evaluated)
- faithfulness, answer_relevancy, context_precision, context_recall (aggregate averages)
- overall_score (weighted: 0.3×faith + 0.3×recall + 0.2×precision + 0.2×relevancy)
- passed (true if overall_score >= 0.70)
- report_path (path to the CSV file)
- detail_json (JSON array of per-question scores with format: `[{"q_index": 0, "question": "...", "faithfulness": 0.95, "context_recall": 0.80}, ...]`)

#### Scenario: CLI eval writes to database
- **WHEN** running `python -m src.cli.eval_ragas`
- **THEN** the system SHALL insert a row into the `eval_report` table in addition to saving CSV/MD reports

#### Scenario: CLI eval with --gate writes to database
- **WHEN** running `python -m src.cli.eval_ragas --gate`
- **THEN** the system SHALL insert a row into `eval_report` with the gate check results, and `passed` SHALL reflect the gate outcome
