# retrieval-quality Specification (Delta)

## ADDED Requirements

### Requirement: Context rendering includes entity metadata
The system SHALL render entity metadata (company / report_period / sec_code / person / currency / report_type) into the retrieval context shown to the LLM, via `RAGContext.to_prompt_text()`. Only entities that exist SHALL be rendered, in the order defined by `ENTITY_RENDER_ORDER`.

The rendered format SHALL be shared between production prompt generation and RAGAS NLI evaluation context, so both see identical context.

#### Scenario: Context with entities
- **WHEN** a retrieved chunk has `entities={company: 东软集团, report_period: 2025年第一季度}` and is included in `format_context`
- **THEN** the resulting prompt context SHALL include both entity fields alongside source/page/content

#### Scenario: Context without entities
- **WHEN** a retrieved chunk has an empty entities dict
- **THEN** the prompt context SHALL contain only source/page/content, matching the previous format exactly

### Requirement: Rerank context passthrough
The system SHALL carry entity metadata from ChromaDB chunk metadata through the rerank stage into `RAGContext.entities`.

#### Scenario: Entities survive rerank
- **WHEN** a chunk with `company`/`report_period` metadata is returned by rerank
- **THEN** the corresponding `RAGContext` SHALL expose those values via its `entities` dict
