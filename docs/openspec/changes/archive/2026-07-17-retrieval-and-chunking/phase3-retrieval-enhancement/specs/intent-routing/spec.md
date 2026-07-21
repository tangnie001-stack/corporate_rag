# intent-routing Specification

## ADDED Requirements

### Requirement: Route queries using L0 regex rules
The system SHALL implement an intent router with a two-tier classification strategy: L0 regex rules for fast classification, and L3 LLM fallback for queries that don't match any rule.

The router SHALL classify queries into one of four types:
- `simple`: Direct financial data queries answerable by LLM alone
- `vague`: Short/ambiguous queries needing historical context
- `medium`: Moderate-complexity queries requiring RAG retrieval
- `complex`: Complex queries (currently falls back to medium)

#### Scenario: Simple rule matched
- **WHEN** a query matches a SIMPLE_PATTERN (e.g., "2024年营收多少")
- **THEN** the router SHALL return "simple"

#### Scenario: Vague rule matched
- **WHEN** a query matches a VAGUE_PATTERN (e.g., "净利润")
- **THEN** the router SHALL return "vague"

#### Scenario: Medium rule matched
- **WHEN** a query matches a MEDIUM_PATTERN (e.g., "分析营收增长原因")
- **THEN** the router SHALL return "medium"

#### Scenario: No rule matched falls back to LLM
- **WHEN** no L0 rule matches the query
- **THEN** the router SHALL call the L3 LLM classifier (stub returning configured fallback)

### Requirement: Cache routing results
The router SHALL cache classification results by query text to avoid reclassifying identical queries.

#### Scenario: Repeated query returns cached result
- **WHEN** the same query is routed again
- **THEN** the router SHALL return the cached classification result
