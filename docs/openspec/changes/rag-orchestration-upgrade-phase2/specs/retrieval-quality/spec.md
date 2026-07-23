# retrieval-quality Specification (Delta)

## MODIFIED Requirements

### Requirement: Short query handling
The system SHALL handle short queries (under 5 Chinese characters) by classifying them as "simple" route, triggering direct LLM answer without retrieval.

**Reason**: Short queries are simple factual questions that don't need vector search.

#### Scenario: Short query classified as simple
- **WHEN** user sends a query of fewer than 5 Chinese characters (e.g., "你好", "是的")
- **THEN** the classify node SHALL return route="simple"

### Requirement: Query classification for routing
The `classify_query()` function SHALL output one of three tiers: "simple", "medium", or "complex", replacing the previous four-way classification (clear/vague/complex/medium).

**Reason**: Three-tier routing aligns with the adaptive routing strategy supported by industry research.

#### Scenario: Single fact classified as simple
- **WHEN** a query asks about a single data point (e.g., "2024年营业收入是多少")
- **THEN** classify_query() SHALL return "simple"

#### Scenario: Multi-fact analysis classified as medium
- **WHEN** a query requires 2-3 related facts (e.g., "近三年营收变化趋势")
- **THEN** classify_query() SHALL return "medium"

#### Scenario: Multi-hop reasoning classified as complex
- **WHEN** a query requires multi-step reasoning or cross-document comparison (e.g., "对比A公司和B公司的偿债能力差异并分析原因")
- **THEN** classify_query() SHALL return "complex"
