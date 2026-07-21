## ADDED Requirements

### Requirement: RAGAS evaluation pipeline
The system SHALL provide a RAGAS evaluation script (eval_ragas.py).
- SHALL load test QA pairs from a structured format
- SHALL evaluate faithfulness, answer_relevancy, context_recall, context_precision
- SHALL support configurable test dataset (questions + ground_truth)
- SHALL output evaluation results to CSV file
- SHALL use Qwen-max as evaluator LLM (noting potential bias)

#### Scenario: Run full evaluation
- **WHEN** developer runs `python src/eval_ragas.py` with test QA pairs
- **THEN** system generates answers, computes RAGAS metrics, and saves results to CSV

### Requirement: Chunk quality check (CLI)
The system SHALL provide a CLI tool (src/cli/check_chunks.py) for chunk quality analysis.
- SHALL report total document count and total chunk count
- SHALL report average chunk length (characters)
- SHALL report chunk length distribution (P10 / P50 / P90)
- SHALL report actual overlap ratio
- SHALL detect and count table fragments within chunks (target: 0)
- SHALL print first 5 chunk previews

#### Scenario: Run chunk quality check
- **WHEN** developer runs `python src/cli/check_chunks.py`
- **THEN** system prints a structured quality report with distribution metrics and table fragment count
