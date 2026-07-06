# hybrid-search Specification

## ADDED Requirements

### Requirement: BM25 lexical search index
The system SHALL maintain a BM25 index (`rank_bm25.BM25Okapi`) per knowledge base, stored as pickle files. Chinese text SHALL be tokenized by individual characters for BM25 processing.

The index SHALL be append-only; deleted documents are filtered post-search via MySQL doc_id checks.

#### Scenario: BM25 search finds relevant results
- **WHEN** a user queries a knowledge base with an existing BM25 index
- **THEN** the system SHALL return search results with `bm25_score` metadata

#### Scenario: BM25 search on unknown KB returns empty
- **WHEN** a user queries a knowledge base with no BM25 index
- **THEN** the system SHALL return an empty list

### Requirement: Hybrid search with RRF fusion
When hybrid search is enabled (`HYBRID_SEARCH_ENABLED=true`), the system SHALL execute Dense (ChromaDB) and BM25 searches in parallel using `asyncio.gather`, then fuse results using Reciprocal Rank Fusion (RRF) with k=60, returning the top 50 results.

#### Scenario: Hybrid search returns fused results
- **WHEN** hybrid search is enabled and both Dense and BM25 return results
- **THEN** the system SHALL return RRF-fused results with no duplicate document IDs

#### Scenario: Only one search type returns results
- **WHEN** only Dense or only BM25 returns results
- **THEN** the RRF fusion SHALL return all results from the non-empty source

#### Scenario: Both searches return empty
- **WHEN** both Dense and BM25 return empty
- **THEN** RRF SHALL return an empty list
