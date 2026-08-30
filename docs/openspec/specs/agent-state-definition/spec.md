# agent-state-definition Specification

## Purpose
TBD - created by archiving change agent-state-dataclass. Update Purpose after archive.

## Requirements

### Requirement: AgentState SHALL be a dataclass with defaults

The system SHALL define AgentState as a `@dataclass` with all fields having sensible default values. Callers SHALL access fields via `state.key` instead of `state.get("key", default)`.

- `trace_id` SHALL default to `"unknown"`
- `grader_score` SHALL default to `0`
- List/collection fields SHALL use `field(default_factory=list)` or `field(default_factory=dict)`
- `RAGQueryIntent` SHALL also be a dataclass with `route=""` and `rewritten=False`

#### Scenario: Default values apply on construction

- **WHEN** AgentState is constructed with `session_id="s1", kb_id="kb-1"`
- **THEN** `state.trace_id` SHALL be `"unknown"`
- **AND** `state.retrieval_retries` SHALL be `0`
- **AND** `state.retrieval_results` SHALL be `[]`

#### Scenario: Custom values override defaults

- **WHEN** AgentState is constructed with `trace_id="trace_abc", grader_score=0.85`
- **THEN** `state.trace_id` SHALL be `"trace_abc"`
- **AND** `state.grader_score` SHALL be `0.85`

### Requirement: AgentState SHALL provide make_initial_state factory method

The AgentState class SHALL provide a `@classmethod make_initial_state()` that creates an initial graph state with input fields only. Intermediate and output fields SHALL use their defaults.

- Input: `session_id, kb_id, query, trace_id, history`
- All other fields SHALL be set to their dataclass defaults

#### Scenario: make_initial_state sets input fields correctly

- **WHEN** `AgentState.make_initial_state("s1", "kb-1", "2024年营收多少", "trace_abc", [])` is called
- **THEN** `state.session_id` SHALL be `"s1"`
- **AND** `state.kb_id` SHALL be `"kb-1"`
- **AND** `state.query` SHALL be `"2024年营收多少"`
- **AND** `state.trace_id` SHALL be `"trace_abc"`
- **AND** `state.retrieval_retries` SHALL be `0`
- **AND** `state.downgraded` SHALL be `False`
- **AND** `state.intent.route` SHALL be `""`

### Requirement: Nodes SHALL access state via attribute access

All LangGraph node functions SHALL access state fields via `state.field` instead of `state.get("field", default)`. Node functions SHALL still return plain dicts for state updates.

#### Scenario: classify_node accesses state.query

- **WHEN** classify_node receives an AgentState with `query="2024年营收多少"`
- **THEN** it SHALL use `state.query` to read the query
- **AND** it SHALL return `{"intent": RAGQueryIntent(route=..., rewritten=False)}` (value 是 dataclass 对象，非 dict)

### Requirement: Nodes handle None retrieval results gracefully

When retrieval returns None (not just empty list), the node SHALL handle it without AttributeError.

#### Scenario: retrieve_node with None results

- **WHEN** `search()` returns `None`
- **THEN** retrieve_node SHALL set `results = []`
- **AND** continue normally with empty results
