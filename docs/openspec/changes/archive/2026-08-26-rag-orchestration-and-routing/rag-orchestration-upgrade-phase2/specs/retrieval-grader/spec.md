# retrieval-grader Specification

## Purpose
Define the rule-based RetrievalGrader that evaluates retrieval quality and triggers query rewriting when score is below threshold.

## ADDED Requirements

### Requirement: Keyword coverage scoring
The RetrievalGrader SHALL calculate a quality score (0.0-1.0) based on keyword coverage: the proportion of query keywords present in the top-K reranked results.

#### Scenario: High keyword coverage returns high score
- **WHEN** all query keywords appear in the top-K reranked results
- **THEN** the grader SHALL return score >= 0.5

#### Scenario: Low keyword coverage returns low score
- **WHEN** none of the query keywords appear in the top-K reranked results
- **THEN** the grader SHALL return score < 0.5

### Requirement: Retry limit
The RetrievalGrader SHALL support a maximum retry count of 2. When retries are exhausted, the graph SHALL proceed to rerank regardless of score.

#### Scenario: Retries exhausted proceeds to rerank
- **WHEN** grader score < threshold and retry count >= 2
- **THEN** the graph SHALL proceed to the rerank node, not the rewrite node

### Requirement: No-keyword default pass
When the query has no extractable keywords (all tokens are stop words or single characters), the grader SHALL return a default pass score of 0.8.

#### Scenario: No keywords returns pass
- **WHEN** a query contains only single characters or stop words
- **THEN** the grader SHALL return 0.8, allowing the pipeline to continue
