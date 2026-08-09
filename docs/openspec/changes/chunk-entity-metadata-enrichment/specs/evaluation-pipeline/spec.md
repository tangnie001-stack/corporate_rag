# evaluation-pipeline Specification (Delta)

## ADDED Requirements

### Requirement: NLI context uses shared entity rendering
The RAGAS evaluation SHALL use `RAGContext.to_prompt_text()` (the same rendering as production) for the NLI context, so entity metadata (company / report_period / sec_code / person / currency / report_type) present in chunk metadata SHALL be visible to the faithfulness NLI check.

#### Scenario: Faithfulness context includes time anchor
- **WHEN** a table chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and the statement references "2025年第一季度"
- **THEN** the NLI context SHALL include the `report_period` entity, giving the statement an anchor to verify against

#### Scenario: Evaluation output unchanged for empty entities
- **WHEN** evaluation runs against a KB whose chunks have no entity metadata
- **THEN** the NLI context SHALL match the pre-change format, and the evaluation SHALL still produce a report without crashing

### Requirement: Current date in evaluation context
The system prompt SHALL include the current date (e.g., "今天是 2026年8月9日") in both production and evaluation paths, via `PromptManager.get_system_prompt()`.

#### Scenario: Current date present in system prompt
- **WHEN** a generation or evaluation run invokes `get_system_prompt()`
- **THEN** the returned prompt SHALL contain today's date, enabling relative-time queries ("本报告期", "今年") to be anchored
