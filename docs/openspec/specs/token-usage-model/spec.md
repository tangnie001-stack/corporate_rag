# token-usage-model Specification

## Purpose
TBD - created by archiving change agent-state-dataclass. Update Purpose after archive.

## Requirements

### Requirement: TokenUsage SHALL be a dataclass with consistent shape

The system SHALL define a `TokenUsage` dataclass with `prompt_tokens`, `completion_tokens`, and `total_tokens` fields to replace the two inconsistent dict shapes.

#### Scenario: LLM native token metadata maps to TokenUsage

- **WHEN** LLM stream returns `usage_metadata` with `input_tokens` / `output_tokens`
- **THEN** `stream_answer()` SHALL map them to `TokenUsage(prompt_tokens=..., completion_tokens=..., total_tokens=...)`

#### Scenario: Estimated usage maps to same shape

- **WHEN** LLM does not provide `usage_metadata` and `estimate_usage()` is called
- **THEN** it SHALL return `TokenUsage(input, output, input+output)` — same shape

### Requirement: TokenUsage's total_tokens shall always be correct

The `total_tokens` field SHALL always be the sum of `prompt_tokens + completion_tokens`.

#### Scenario: generate_node reads total_tokens correctly

- **WHEN** `generate_node` receives a `TokenUsage` instance
- **THEN** `usage.total_tokens` SHALL return the correct sum
- **AND** the `usage.get("total", 0)` pattern SHALL be removed
