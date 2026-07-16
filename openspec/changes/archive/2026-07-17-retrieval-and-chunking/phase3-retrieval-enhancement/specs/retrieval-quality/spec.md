# retrieval-quality Specification (Delta)

## MODIFIED Requirements

### Requirement: Retrieval parameter configuration
The system SHALL keep `TOP_K_RETRIEVAL` (default: 50), `TOP_K_RERANK` (default: 5), `HYBRID_SEARCH_ENABLED`, and `BM25_INDEX_DIR` configurable via environment variables in `src/config/settings.py`.

The system SHALL support overriding these values at evaluation time without modifying source code.

#### Scenario: Parameter override via environment
- **WHEN** user sets `TOP_K_RETRIEVAL=15` and `TOP_K_RERANK=8` in `.env`
- **THEN** the RAG pipeline SHALL use 15 initial retrieval results and keep 8 after reranking

## ADDED Requirements

### Requirement: Hybrid search toggle
The system SHALL support toggling between pure Dense retrieval and Hybrid (Dense + BM25) retrieval via the `HYBRID_SEARCH_ENABLED` configuration flag.

#### Scenario: Hybrid search enabled
- **WHEN** `HYBRID_SEARCH_ENABLED=true`
- **THEN** the `search()` method SHALL execute both Dense and BM25 retrieval in parallel and fuse results via RRF

#### Scenario: Hybrid search disabled
- **WHEN** `HYBRID_SEARCH_ENABLED=false`
- **THEN** the `search()` method SHALL use pure Dense retrieval only

### Requirement: Intent routing integration
The RAG chain SHALL route queries through the intent router before retrieval. Simple queries SHALL skip retrieval entirely and answer directly. Vague/complex queries SHALL trigger query rewriting before retrieval.

#### Scenario: Simple query skips RAG
- **WHEN** a query is classified as "simple"
- **THEN** the system SHALL answer directly without performing vector search

#### Scenario: Vague query triggers rewriting
- **WHEN** a query is classified as "vague"
- **THEN** the system SHALL apply query rewriting before searching

### Requirement: Query rewriting integration
The RAG chain SHALL apply query rewriting based on the classification result. Rewritten queries SHALL replace the original query for the search step, while the original query is retained for display.

#### Scenario: Rewritten query used for search
- **WHEN** a query is rewritten (expanded, condensed, or decomposed)
- **THEN** the rewritten version SHALL be used for vector/BM25 search while the original query is shown in the chat UI
