## ADDED Requirements

### Requirement: RAG answer generation with Qwen-max
The system SHALL generate answers using DashScope Qwen-max LLM based on retrieved document context.
- SHALL stream tokens via generator for real-time output
- SHALL include retrieved context chunks in the LLM prompt
- SHALL implement retry with exponential backoff for API calls
- SHALL enforce financial-specific prompt constraints
- SHALL support configurable temperature (default 0.1)

#### Scenario: Generate answer with context
- **WHEN** user asks a financial question with available document context
- **THEN** system streams answer tokens in real-time based ONLY on retrieved content

### Requirement: System prompt with financial constraints
The LLM system prompt SHALL include the following constraints:
1. SHALL NOT calculate ratios or summaries not explicitly present in retrieved documents
2. SHALL label data with corresponding year/reporting period in answers
3. SHALL explicitly state "未在文档中找到相关数据" when information is unavailable

#### Scenario: Answer with source year labeling
- **WHEN** user asks "去年营收是多少?"
- **THEN** system answers with specific year label, e.g., "根据2023年年报，公司营业收入为..."

#### Scenario: Reject calculation not in context
- **WHEN** user asks "利润率是多少?" but the retrieved context only contains separate revenue and profit figures without a calculated ratio
- **THEN** system states that the ratio is not directly available in the documents

### Requirement: Citation generation
The system SHALL append source citations after each answer.
- SHALL include source filename, page number, and content snippet (max 200 chars)
- SHALL format citations as Markdown blockquote after the answer
- SHALL render citations in Gradio Chatbot component

#### Scenario: Display citations with answer
- **WHEN** system generates an answer based on retrieved chunks
- **THEN** the answer is followed by a Markdown citation block showing source file, page number, and content excerpt

### Requirement: Document reranking
The system SHALL apply gte-rerank-v2 reranking to improve retrieval precision.
- SHALL rerank top-K retrieved chunks before passing to LLM
- SHALL return top-N (configurable, default 5) after reranking

#### Scenario: Rerank search results
- **WHEN** semantic search returns initial results
- **THEN** system reranks results using gte-rerank-v2 before sending to LLM
