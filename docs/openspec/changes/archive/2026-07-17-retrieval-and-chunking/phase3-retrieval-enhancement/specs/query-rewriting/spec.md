# query-rewriting Specification

## ADDED Requirements

### Requirement: Classify query type before rewriting
The system SHALL classify user queries into four types before applying rewriting strategies: clear, fuzzy_short, colloquial, and compound.

Classification rules:
- `clear`: queries >= 10 characters without rewrite-triggering keywords
- `fuzzy_short`: queries < 10 characters
- `colloquial`: queries containing "分析", "解释", "说明", "为什么"
- `compound`: queries containing "对比", "比较", "差异", "versus", "vs"

#### Scenario: Clear query passthrough
- **WHEN** a user query is >= 10 characters and contains no rewrite-triggering keywords
- **THEN** the query SHALL be classified as "clear" and passed through without rewriting

#### Scenario: Short query detected
- **WHEN** a user query is fewer than 10 Chinese characters
- **THEN** the query SHALL be classified as "fuzzy_short" for expansion

### Requirement: Expand fuzzy short queries
When a query is classified as "fuzzy_short", the system SHALL attempt to expand it using the last user message from conversation history as context.

#### Scenario: Short query with history
- **WHEN** a "fuzzy_short" query is received and conversation history exists
- **THEN** the system SHALL prepend the last user message to the current query

#### Scenario: Short query without history
- **WHEN** a "fuzzy_short" query is received but no history exists
- **THEN** the system SHALL return the original query unchanged

### Requirement: Condense colloquial queries
When a query is classified as "colloquial", the system SHALL strip rewrite-triggering keywords ("分析", "解释", "说明", "为什么") to produce a concise retrieval query.

#### Scenario: Colloquial query condensed
- **WHEN** a query like "分析营收增长原因" is received
- **THEN** the system SHALL produce a condensed query like "营收增长"

### Requirement: Decompose compound queries
When a query is classified as "compound", the system SHALL split it into multiple sub-queries using separators ("对比", "比较", "差异", "versus", "vs", "和", "与").

#### Scenario: Compound query decomposed
- **WHEN** a query like "对比2023年和2024年营收" is received
- **THEN** the system SHALL produce sub-queries ["2023年营收", "2024年营收"]
