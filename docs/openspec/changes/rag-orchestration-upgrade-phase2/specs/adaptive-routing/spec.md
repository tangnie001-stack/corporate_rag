# adaptive-routing Specification

## Purpose
Define the three-tier routing strategy that directs queries to different pipelines based on complexity.

## ADDED Requirements

### Requirement: Three-tier query routing
The classify node SHALL route queries into three tiers based on intent classification: simple, medium, complex.

#### Scenario: Simple query skips retrieval
- **WHEN** a query is classified as "simple" (e.g., "2024年营收多少")
- **THEN** the graph SHALL skip all retrieval and rerank nodes, going directly to generate

#### Scenario: Medium query uses retrieval + rerank
- **WHEN** a query is classified as "medium" (e.g., "近三年营收变化趋势")
- **THEN** the graph SHALL execute rewrite → retrieve → rerank → generate, skipping the grader node

#### Scenario: Complex query uses full pipeline
- **WHEN** a query is classified as "complex" (e.g., "对比两家公司偿债能力差异并分析原因")
- **THEN** the graph SHALL execute rewrite → retrieve → grader → rerank → generate

### Requirement: Simple routing cost optimization
The simple path SHALL use build_simple_prompt() with direct LLM answer, avoiding vector database queries entirely.

#### Scenario: Simple query returns without DB query
- **WHEN** a simple route is taken
- **THEN** no vector store or BM25 index query SHALL be executed
