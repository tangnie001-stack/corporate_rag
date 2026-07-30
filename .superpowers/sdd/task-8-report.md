# Task 8 Report: classify_node 改造 + workflow 条件边

## Summary

Replaced the synchronous `classify_node` with an async `make_classify_node(llm)` factory that uses `QueryRouter(llm=llm)` and passes the classified result (intent, entities, missing, confidence) into AgentState. Updated `rewrite_node` to pass `intent_route` to `rewrite_query()`. Added `clarify→END` branch to the workflow conditional edges.

## Files Changed

| File | Change |
|---|---|
| `src/agents/graph/nodes.py` | Replaced `classify_node` with `make_classify_node(llm)` factory; updated `rewrite_node` to pass `intent_route` |
| `src/agents/graph/workflow.py` | Updated `route_by_intent` to check `state.missing_entities`; import `make_classify_node`; add `clarify→END` edge |
| `src/rag/retrieval.py` | Added `intent_route` optional parameter to `rewrite_query()` |
| `tests/agents/graph/test_graph.py` | Replaced old tests with 4 new tests (3 per brief + 1 async integration test) |

## What Changed

### nodes.py
- Old `classify_node(state)` was synchronous, called `QueryRouter().route(query)` returning a string, then mapped `vague→medium` via route_map
- New `make_classify_node(llm)` is a factory returning an async function that calls `router.route(state.query, state._history)` returning a dict, and updates all 4 new fields: `intent`, `extracted_entities`, `missing_entities`, `classification_confidence`
- `rewrite_node` now passes `intent_route=state.intent.route or "medium"` to `rewrite_query()`

### workflow.py
- `route_by_intent` now checks `state.missing_entities` first → returns `"clarify"`
- Conditional edges now include `"clarify": END`
- Builder uses `make_classify_node(llm)` instead of direct `classify_node`

### retrieval.py
- `rewrite_query` accepts optional `intent_route` parameter; when provided, skips internal `classify_query()` call

## TDD Evidence

```
tests/agents/graph/test_graph.py::test_make_classify_node_returns_callable PASSED
tests/agents/graph/test_graph.py::test_route_by_intent_returns_clarify    PASSED
tests/agents/graph/test_graph.py::test_route_by_intent_returns_normal     PASSED
tests/agents/graph/test_graph.py::test_make_classify_node_returns_expected_keys PASSED
```

## ruff check

All checks passed (21 files reformatted, 0 errors).

## Status

**Complete.** All specs implemented, tests pass, ruff clean.
