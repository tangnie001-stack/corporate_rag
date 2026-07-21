## ADDED Requirements

### Requirement: ChromaDB vector storage
The system SHALL store document chunk vectors using ChromaDB PersistentClient (embedded mode).
- SHALL create one ChromaDB collection per knowledge base
- SHALL use collection naming pattern `kb_{uuid_hex}` for namespacing
- SHALL configure HNSW index with cosine distance, M=8, ef_construction=64
- SHALL persist data to mounted Docker volume for data durability
- SHALL support collection deletion when knowledge base is removed

#### Scenario: Store document chunks
- **WHEN** document chunks are parsed and vectorized
- **THEN** system stores vectors with document content and metadata (source, page, chunk_index) in ChromaDB

#### Scenario: Delete knowledge base
- **WHEN** user deletes a knowledge base
- **THEN** system removes the corresponding ChromaDB collection permanently

### Requirement: Semantic search with text-embedding-v3
The system SHALL embed user queries using DashScope text-embedding-v3 and perform semantic similarity search.
- SHALL call DashScope Embedding API for each query
- SHALL return top-K most similar chunks (configurable K, default 5)
- SHALL implement retry with exponential backoff for API calls (max 3 retries, 1s initial interval, 2x backoff)

#### Scenario: Search with query
- **WHEN** user asks a question
- **THEN** system embeds the question and retrieves top-K relevant document chunks from ChromaDB

#### Scenario: Handle Embedding API failure
- **WHEN** DashScope Embedding API returns an error
- **THEN** system retries with exponential backoff, and shows error if all retries exhausted

### Requirement: Retrieval quality check (CLI)
The system SHALL provide a CLI tool (src/cli/check_retrieval.py) to evaluate retrieval quality.
- SHALL print retrieved chunks with relevance scores for a given query
- SHALL display chunk count, source distribution, and sample content

#### Scenario: Run retrieval check
- **WHEN** developer runs `python src/cli/check_retrieval.py` with a query
- **THEN** system prints retrieved document chunks and their metadata
