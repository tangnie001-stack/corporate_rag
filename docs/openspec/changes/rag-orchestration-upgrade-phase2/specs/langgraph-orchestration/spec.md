# langgraph-orchestration Specification

## Purpose
Define the LangGraph StateGraph that orchestrates the RAG pipeline with 7 nodes and 3-tier routing.

## ADDED Requirements

### Requirement: StateGraph definition
The system SHALL define a LangGraph StateGraph with the following 7 nodes: classify, rewrite, retrieve, grader, rerank, generate, format.

#### Scenario: All nodes registered
- **WHEN** the graph is compiled
- **THEN** all 7 nodes SHALL be registered and connected via edges

### Requirement: AgentState
The system SHALL define an AgentState TypedDict extending RAGState with fields: downgraded (bool), downgrade_reason (str), retrieval_retries (int).

#### Scenario: State carries trace_id
- **WHEN** a node function is called
- **THEN** the state SHALL contain trace_id for log correlation

### Requirement: Node logging
Each node function SHALL emit `[trace_id] nodename action: detail` format INFO logs at entry and exit.

#### Scenario: Node entry and exit logged
- **WHEN** any node function starts and ends execution
- **THEN** the log SHALL contain the trace_id, node name, and action description
