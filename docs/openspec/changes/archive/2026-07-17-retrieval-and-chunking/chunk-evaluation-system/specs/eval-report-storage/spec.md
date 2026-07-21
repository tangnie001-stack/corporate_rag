# eval-report-storage Specification

## Purpose

持久化 RAGAS 评估结果到 MySQL 数据库，支持前端在 KB 页面头部展示最新评分和报告链接。

## Requirements

### Requirement: eval_report table schema

The system SHALL create an `eval_report` table with the following schema:

```sql
CREATE TABLE IF NOT EXISTS eval_report (
    id                  VARCHAR(36)  PRIMARY KEY,
    kb_id               VARCHAR(36)  NOT NULL,
    run_type            VARCHAR(20)  NOT NULL,  -- 'manual' | 'sampling' | 'ci_gate'
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
```

#### Scenario: Table created on first eval
- **WHEN** the first RAGAS evaluation runs
- **THEN** the `eval_report` table SHALL be created if not exists

### Requirement: Evaluation result CRUD

The MySQLDB class SHALL provide methods to insert and query eval_report records.

#### Scenario: Insert eval report
- **WHEN** RAGAS evaluation completes
- **THEN** a new row SHALL be inserted into `eval_report` with all metric scores

#### Scenario: Query latest eval report for a KB
- **WHEN** querying the latest evaluation for a KB
- **THEN** the system SHALL return the most recent row for that `kb_id` ordered by `eval_date DESC`

### Requirement: Overall score computation

The overall_score SHALL be a weighted average of the four core RAGAS metrics: `0.30 × faithfulness + 0.30 × context_recall + 0.20 × context_precision + 0.20 × answer_relevancy`.

#### Scenario: All metrics available
- **WHEN** all 4 RAGAS metrics are computed
- **THEN** `overall_score` SHALL be computed using the weighted formula

#### Scenario: Metric missing
- **WHEN** one or more metrics are unavailable
- **THEN** the overall_score SHALL be computed from available metrics only, with weights renormalized

### Requirement: Pass/fail determination

The passed flag SHALL be true when `overall_score >= 0.70`.

#### Scenario: Score meets threshold
- **WHEN** `overall_score >= 0.70`
- **THEN** `passed` SHALL be set to 1

#### Scenario: Score below threshold
- **WHEN** `overall_score < 0.70`
- **THEN** `passed` SHALL be set to 0

### Requirement: Frontend KB header display

The KB document list page SHALL display the latest RAGAS evaluation summary at the page header, showing: run date, faithfulness, context_recall, and overall_score.

#### Scenario: Latest eval available
- **WHEN** a KB has at least one eval_report record
- **THEN** the KB page header SHALL show "最近 RAGAS 评估: Faith {score} Recall {score} 综合 {score}" with the latest eval date
