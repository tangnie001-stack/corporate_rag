# Dataclass Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert 5 core data structures from TypedDict/bare dict to `@dataclass`, enabling attribute access instead of `.get("key")` patterns across ~25 files.

**Architecture:** Unified dataclass approach: AgentState (LangGraph state), ChunkData (chunker output), ChatMessage (dialogue history), TokenUsage (LLM token usage), EvalReportEntity (evaluation reports). All share the same pattern: centralized defaults + attribute access.

**Tech Stack:** Python 3.11, dataclasses, LangGraph

## Global Constraints

- All 5 data structures use `@dataclass`, not NamedTuple or Pydantic
- AgentState `trace_id` defaults to `"unknown"`, `grader_score` defaults to `0`
- List fields use `field(default_factory=list)` — never mutable default arguments
- Nodes still return plain dicts (LangGraph merges them correctly)
- `score is not None and` retained in grader_node (grade() can return None), removed in route_by_grader (or 0 handles it)
- Existing API response models (Pydantic) not modified
- No business logic changes — only type/access pattern changes
- ruff check must pass, pytest must pass

---
## File Structure

| Data Structure | File to Create/Modify | Role |
|---------------|----------------------|------|
| AgentState | `src/agents/graph/state.py` | TypedDict → dataclass, add `make_initial_state()` |
| nodes.py access | `src/agents/graph/nodes.py` | All `state.get("key")` → `state.key` |
| workflow.py access | `src/agents/graph/workflow.py` | `route_by_intent`/`route_by_grader` attribute access |
| ChunkData | `src/infra/chunking/strategies/base.py` | Interface `chunk() -> list[ChunkData]` |
| ChunkData | `src/infra/chunking/strategies/{parent_child,table_preserving,qa}.py` | Return ChunkData objects |
| Chunk consumers | `src/services/document_service.py` | `c["content"]` → `c.content` |
| Chunk consumers | `src/eval/chunk_scorer.py` | `c["content"]` → `c.content` |
| ChatMessage | `src/chat/manager.py` | New dataclass, return `list[ChatMessage]` |
| ChatMessage consumers | `src/rag/retrieval.py`, `src/rag/prompt.py` | `msg["role"]` → `msg.role` |
| TokenUsage | `src/rag/stream.py` | New dataclass, unified shape |
| TokenUsage consumer | `src/agents/graph/nodes.py` | `usage.total_tokens` |
| EvalReport | `src/services/app_service.py` | Method sig `EvalReportEntity` |
| EvalReport | `src/cli/eval_ragas.py` | Construct entity instead of dict |
| Tests | `tests/agents/graph/test_state.py` | Update for dataclass access |

---

### Task 1: AgentState — Convert TypedDict to dataclass

**Files:**
- Modify: `src/agents/graph/state.py`
- Test: `tests/agents/graph/test_state.py`

**Interfaces:**
- Consumes: `RAGQueryIntent`, `ChunkResult`, `RAGContext` (existing imports)
- Produces: `AgentState.make_initial_state(session_id, kb_id, query, trace_id, history)` -> `AgentState`
- Also: `RAGQueryIntent(route="", rewritten=False)`

- [ ] **Step 1: Convert state.py TypedDict to dataclass**

Replace `src/agents/graph/state.py` entirely:

```python
# src/agents/graph/state.py
from dataclasses import dataclass, field
from typing import Optional
from src.infra.db.entities import ChunkResult
from src.rag.context import RAGContext


@dataclass
class RAGQueryIntent:
    route: str = ""
    rewritten: bool = False


@dataclass
class AgentState:
    # 输入
    session_id: str = ""
    kb_id: str = ""
    query: str = ""
    trace_id: str = "unknown"
    # 中间态
    intent: RAGQueryIntent = field(default_factory=RAGQueryIntent)
    rewritten_query: str = ""
    retrieval_results: list[ChunkResult] = field(default_factory=list)
    contexts: list[RAGContext] = field(default_factory=list)
    grader_score: Optional[float] = None
    retrieval_retries: int = 0
    # 输出
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    # 降级
    downgraded: bool = False
    downgrade_reason: str = ""
    # 内部
    _history: list[dict] = field(default_factory=list)
    _token_usage: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)

    @classmethod
    def make_initial_state(cls, session_id, kb_id, query, trace_id, history):
        return cls(session_id=session_id, kb_id=kb_id, query=query,
                   trace_id=trace_id, _history=history)
```

- [ ] **Step 2: Update test_state.py for dataclass**

```python
"""Tests for AgentState definition."""
from src.agents.graph.state import AgentState, RAGQueryIntent
from src.rag.context import RAGContext


def test_agent_state_defaults():
    state = AgentState(query="2024年营收多少", kb_id="kb-1")
    assert state.query == "2024年营收多少"
    assert state.kb_id == "kb-1"


def test_agent_state_with_contexts():
    state = AgentState(
        query="净利润多少",
        contexts=[
            RAGContext(content="净利润100亿", source="财报.pdf",
                       page=5, doc_id="doc-1", chunk_id="chunk-1", score=0.95)
        ],
    )
    assert len(state.contexts) == 1
    assert state.contexts[0].content == "净利润100亿"


def test_agent_state_downgrade_fields():
    state = AgentState(query="test", downgraded=True, downgrade_reason="rerank_empty")
    assert state.downgraded is True
    assert state.downgrade_reason == "rerank_empty"


def test_agent_state_trace_id():
    state = AgentState(query="test", trace_id="trace_abc123")
    assert state.trace_id == "trace_abc123"
```

- [ ] **Step 3: Run the new test to verify it fails (dataclass not yet consumed by nodes)**

```bash
pytest tests/agents/graph/test_state.py -v
```
Expected: PASS (state.py and test_state.py self-consistent)

- [ ] **Step 4: Commit**

```bash
git add src/agents/graph/state.py tests/agents/graph/test_state.py
git commit -m "refactor: convert AgentState TypedDict to dataclass"
```

---

### Task 2: AgentState — Update nodes.py and workflow.py

**Files:**
- Modify: `src/agents/graph/nodes.py`
- Modify: `src/agents/graph/workflow.py`
- Modify: `src/services/agent_service.py`

**Interfaces:**
- Consumes: `AgentState(key)`. from Task 1
- Produces: Node functions using `state.key` pattern

- [ ] **Step 1: Update nodes.py `_tid()` and imports**

```python
from src.agents.graph.state import AgentState, RAGQueryIntent

def _tid(state: AgentState) -> str:
    return state.trace_id
```

- [ ] **Step 2: Update nodes.py classify_node**

```python
def classify_node(state: AgentState) -> dict:
    tid = _tid(state)
    logger.info("[{}] classify_node start: query={}", tid, state.query[:50])
    router = QueryRouter()
    raw_route = router.route(state.query)
    route_map = {"simple": "simple", "vague": "medium", "medium": "medium", "complex": "complex"}
    route = route_map.get(raw_route, "medium")
    logger.info("[{}] classify_node: raw={} mapped={} -> {}", tid, raw_route, route, ROUTE_LABELS.get(route))
    return {"intent": RAGQueryIntent(route=route, rewritten=False)}
```

- [ ] **Step 3: Update nodes.py rewrite_node**

```python
def rewrite_node(state: AgentState) -> dict:
    tid = _tid(state)
    query = state.query
    rewritten = rewrite_query(query, state._history or [])
    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)
    result = {"rewritten_query": rewritten}
    if rewritten != query:
        result["intent"] = RAGQueryIntent(route=state.intent.route or "medium", rewritten=True)
        logger.info("[{}] rewrite_node: {} -> {}", tid, query[:30], rewritten[:30])
    else:
        logger.info("[{}] rewrite_node: no rewrite", tid)
    return result
```

- [ ] **Step 4: Update nodes.py retrieve_node**

```python
async def retrieve_node(state: AgentState) -> dict:
    tid = _tid(state)
    q = state.rewritten_query or state.query
    kb_id = state.kb_id
    logger.info("[{}] retrieve_node start: query={} kb_id={}", tid, q[:50], kb_id)
    results = await search(q, kb_id, vector_store, bm25)
    if results is None:
        results = []
        logger.warning("[{}] retrieve_node: search returned None, using empty list", tid)
    first = results[0] if results else None
    logger.info("[DIAG][{}] retrieve_node done: results={} first_score={} first_source={}", tid,
        len(results), first.distance if first else "", first.metadata.get("source", "") if first else "")
    return {"retrieval_results": results}
```

- [ ] **Step 5: Update nodes.py grader_node**

```python
def grader_node(state: AgentState) -> dict:
    tid = _tid(state)
    query = state.rewritten_query or state.query
    results = state.retrieval_results or []
    grader = RetrievalGrader()
    score = grader.grade(query, results, results)
    retries = state.retrieval_retries
    logger.info("[{}] grader_node: score={:.2f} retries={}", tid, score, retries)
    will_retry = (score is None or score < 0.5) and retries < 2
    logger.info("[DIAG][{}] grader_node: score={:.2f} retries={} threshold=0.5 will_retry={}", tid, score, retries, will_retry)
    retries = state.retrieval_retries
    if score is not None and score >= 0.5:
        return {"grader_score": score, "retrieval_retries": 0}
    if retries < 2:
        return {"grader_score": score, "retrieval_retries": retries + 1}
    return {"grader_score": score, "retrieval_retries": retries + 1, "downgraded": True, "downgrade_reason": "grader_retries_exhausted"}
```

- [ ] **Step 6: Update nodes.py rerank_node**

```python
def rerank_node(state: AgentState) -> dict:
    tid = _tid(state)
    query = state.rewritten_query or state.query
    results = state.retrieval_results or []
    if not results:
        return {"contexts": []}
    contexts = rerank_results(query, results, reranker)
    logger.info("[{}] rerank_node: contexts={}", tid, len(contexts))
    return {"contexts": contexts}
```

- [ ] **Step 7: Update nodes.py generate_node**

```python
def generate_node(state: AgentState) -> dict:
    tid = _tid(state)
    query = state.rewritten_query or state.query
    contexts = state.contexts or []
    # (DIAG log follows — and state.intent.route, state.intent.rewritten, etc.)
    # The new DIAG log will come from Task 6 after TokenUsage is done;
    # for now update the state field accesses
    if not contexts:
        prompt = build_simple_prompt(query, state._history or [], prompt_manager)
    else:
        context_str = format_context(contexts)
        prompt = build_prompt(query, context_str, state._history or [], prompt_manager)
    full_text = ""
    for token in stream_answer(prompt, llm, tracer, tid):
        full_text += token
    usage = estimate_usage(prompt, full_text)
    result = {"answer": full_text, "_token_usage": usage}
    if not contexts:
        result["downgraded"] = True
        result["downgrade_reason"] = "rerank_empty"
    logger.info("[{}] generate_node done: answer_len={} tokens={}", tid, len(full_text), usage.get("total", 0))
    return result
```

- [ ] **Step 8: Update nodes.py format_node**

```python
def format_node(state: AgentState) -> dict:
    tid = _tid(state)
    contexts = state.contexts or []
    seen = set()
    citations = []
    for ctx in contexts:
        key = (ctx.source, ctx.page)
        if key in seen:
            continue
        seen.add(key)
        citations.append({"source": ctx.source, "page": ctx.page, "snippet": ctx.content[:200], "score": ctx.score})
    logger.info("[{}] format_node: citations={}", tid, len(citations))
    return {"citations": citations}
```

- [ ] **Step 9: Update workflow.py route_by_intent**

```python
def route_by_intent(state: AgentState) -> str:
    intent = state.intent
    return intent.route or "medium"
```

- [ ] **Step 10: Update workflow.py route_by_grader**

```python
def route_by_grader(state: AgentState) -> str:
    if state.downgraded:
        logger.info("route_by_grader: downgraded=true -> pass")
        return "pass"
    score = state.grader_score or 0
    retries = state.retrieval_retries
    if score >= 0.5:
        logger.info("route_by_grader: score={:.2f} >= 0.5 -> pass", score)
        return "pass"
    if retries < 3:
        logger.info("route_by_grader: score={:.2f} retries={} < 3 -> rewrite", score, retries)
        return "rewrite"
    return "pass"
```

- [ ] **Step 11: Update agent_service.py**

Import only `AgentState` (make_initial_state is a classmethod):

```python
from src.agents.graph.state import AgentState

initial_state = AgentState.make_initial_state(session_id, kb_id, query, trace_id, history)
```

- [ ] **Step 12: Run tests**

```bash
pytest tests/agents/graph/ tests/api/test_sessions.py -v
```
Expected: All pass (18 tests)

- [ ] **Step 13: Commit**

```bash
git add src/agents/graph/nodes.py src/agents/graph/workflow.py src/services/agent_service.py
git commit -m "refactor: update nodes/workflow/agent_service for AgentState dataclass"
```

---

### Task 3: ChunkData — Standardize chunker return type

**Files:**
- Modify: `src/infra/chunking/strategies/base.py`
- Modify: `src/infra/chunking/strategies/parent_child.py`
- Modify: `src/infra/chunking/strategies/table_preserving.py`
- Modify: `src/infra/chunking/strategies/qa.py`
- Modify: `src/services/document_service.py`
- Modify: `src/eval/chunk_scorer.py`

**Interfaces:**
- Consumes: `ChunkData(content, metadata, tokens)` from `src/infra/chunking/validator.py`
- Produces: All chunker `chunk()` methods return `list[ChunkData]`
- Producers change: `document_service.py` processes `ChunkData` objects
- Consumers change: `chunk_scorer.py` accesses `chunk.content`

- [ ] **Step 1: Update base.py interface**

```python
from src.infra.chunking.validator import ChunkData

@abstractmethod
def chunk(self, text: str, metadata: dict) -> list[ChunkData]: ...
```

- [ ] **Step 2: Update parent_child.py**

```python
def chunk(self, text: str, metadata: dict) -> list[ChunkData]:
    ...
    result = []
    for pi, parent in enumerate(parent_docs):
        child_docs = child_splitter.create_documents([parent.page_content])
        for ci, child in enumerate(child_docs):
            content = self.inject_heading_prefix(
                child.page_content, metadata.get("heading_path", "")
            )
            chunk_metadata = {
                **metadata,
                "parent_content": parent.page_content,
                "tokens": self.count_tokens(child.page_content),
                "chunk_strategy": self.chunk_strategy,
            }
            result.append(ChunkData(content=content, metadata=chunk_metadata,
                                    tokens=self.count_tokens(child.page_content)))
    # Update the log line:
    logger.info("[parent_child] chunks={} parents={} tokens={}",
                len(result), len(parent_docs), sum(c.tokens for c in result))
    return result
```

- [ ] **Step 3: Update table_preserving.py**

Similar pattern — replace each `{"content": ..., "metadata": ...}` with `ChunkData(content=..., metadata=...)`.

Look for lines building dicts in `for` loops and replace with `ChunkData(...)`.

- [ ] **Step 4: Update qa.py**

Same pattern — `ChunkData(content=..., metadata=...)` instead of dict.

- [ ] **Step 5: Update `document_service.py` — import and `_merge_tiny_chunks`**

Function signature:
```python
def _merge_tiny_chunks(
    self, chunks: list[ChunkData], strategy: str = "", min_tokens: int = 50
) -> list[ChunkData]:
```

Inside the function, replace:
- `chunks: list[dict]` → `chunks: list[ChunkData]`
- `c["metadata"].get("tokens", 0)` → `c.tokens or BaseChunker.count_tokens(c.content)`
- `c["content"]` → `c.content`
- `merged[-1]["content"]` → `merged[-1].content`
- `merged[-1]["metadata"]["tokens"]` → `BaseChunker.count_tokens(merged[-1].content)`

The inner function at line 44 (`_merge_tiny_chunks`) must accept `list[ChunkData]`.

- [ ] **Step 6: Update `document_service.py` — `enrich_chunk_pages` and `_enrich_chunk_pages`**

Replace:
- `chunks: list[dict]` → `chunks: list[ChunkData]`
- `chunk["content"]` → `chunk.content`
- `chunk["metadata"]["page"]` → `chunk.metadata["page"]`

- [ ] **Step 7: Update `document_service.py` — remove ChunkData wrapping (line 391-393)**

The wrapping code (currently building `ChunkData` objects from dicts) becomes unnecessary:
```python
# Before:
chunk_data_list = [ChunkData(content=c["content"], metadata=c["metadata"]) for c in chunks]
# After:
chunk_data_list = chunks  # already list[ChunkData]
```

- [ ] **Step 8: Update `eval/chunk_scorer.py`**

Replace all `chunk["content"]` with `chunk.content`. The type annotation `list[dict]` → `list[ChunkData]`.

- [ ] **Step 9: Run tests**

```bash
ruff check . 2>&1 | head -20
pytest tests/ -v 2>&1 | tail -20
```
Expected: ruff passes, pytest passes (pre-existing flaky vector store tests excluded)

- [ ] **Step 10: Commit**

```bash
git add src/infra/chunking/ src/services/document_service.py src/eval/chunk_scorer.py
git commit -m "refactor: standardize chunker return type to list[ChunkData]"
```

---

### Task 4: ChatMessage — Type dialogue history

**Files:**
- Modify: `src/chat/manager.py` (needs import, return type change)
- Modify: `src/rag/retrieval.py` (history type)
- Modify: `src/rag/prompt.py` (history type)

**Interfaces:**
- Consumes: `ChatMessage(role, content)` from new dataclass
- Produces: `ChatManager.get_history_async() -> list[ChatMessage]`

- [ ] **Step 1: Add ChatMessage dataclass**

Create new file `src/infra/llm/chat_message.py` (or add to `state.py` / `context.py` — pick `src/rag/context.py` since it already holds RAG-related dataclasses):

Actually, create a new file for clarity:
`src/infra/llm/chat_message.py`:
```python
"""对话消息数据类型。"""

from dataclasses import dataclass


@dataclass
class ChatMessage:
    """单条对话消息。

    Attributes:
        role: 角色 ("user" | "assistant")
        content: 消息文本内容
    """

    role: str
    content: str
```

- [ ] **Step 2: Update chat/manager.py**

Import:
```python
from src.infra.llm.chat_message import ChatMessage
```

Update `get_history_async()`:
```python
async def get_history_async(self, session_id: str) -> list[ChatMessage]:
    await self._ensure_redis_async()
    if self._in_memory:
        return [ChatMessage(**msg) for msg in self._memory_store.get(session_id, [])]
    key = self._session_key(session_id)
    try:
        raw = await self._redis.lrange(key, 0, -1)
        return [ChatMessage(**json.loads(m)) for m in raw]
    except Exception as e:
        logger.warning("get_history_async failed: {}", e)
        return []
```

Update `add_message_async()` — store is still dict for JSON serialization, but the in-memory store type annotation changes:
```python
self._memory_store: dict[str, list[dict]] = {}
```
(Type annotation stays — the stored value is still dict for JSON purposes)

- [ ] **Step 3: Update rag/retrieval.py — expand_query and rewrite_query**

```python
from src.infra.llm.chat_message import ChatMessage

def expand_query(query: str, history: list[ChatMessage]) -> str:
    if not history:
        return query
    for msg in reversed(history):
        if msg.role == "user" and msg.content != query:
            return f"{msg.content} {query}"
    return query


def rewrite_query(query: str, history: list[ChatMessage]) -> str | list[str]:
    ...
```

- [ ] **Step 4: Update rag/prompt.py — build_prompt and build_simple_prompt**

```python
from src.infra.llm.chat_message import ChatMessage

def build_prompt(query: str, context: str, history: list[ChatMessage], prompt_manager) -> list:
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    user_content = prompt_manager.get_user_template(context=context, query=query)
    messages.append(HumanMessage(content=user_content))
    return messages


def build_simple_prompt(query: str, history: list[ChatMessage], prompt_manager) -> list:
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=query))
    return messages
```

- [ ] **Step 5: Update state.py — `_history` type (override from Task 1)**

In `state.py`, change `_history: list[dict]` (set in Task 1) to `_history: list[ChatMessage]`:

```python
from src.infra.llm.chat_message import ChatMessage

_history: list[ChatMessage] = field(default_factory=list)
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/ -v 2>&1 | tail -20
```
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/infra/llm/chat_message.py src/chat/manager.py src/rag/retrieval.py src/rag/prompt.py
git commit -m "refactor: type dialogue history as ChatMessage dataclass"
```

---

### Task 5: TokenUsage — Unify token shapes and fix bug

**Files:**
- Modify: `src/rag/stream.py`
- Modify: `src/agents/graph/nodes.py` (generate_node's `usage.get("total")` → `usage.total_tokens`)

**Interfaces:**
- Consumes: `TokenUsage(prompt_tokens, completion_tokens, total_tokens)` from new dataclass
- Produces: `estimate_usage() -> TokenUsage` instead of `dict`

- [ ] **Step 1: Add TokenUsage dataclass**

In `src/rag/stream.py`:
```python
@dataclass
class TokenUsage:
    """Token 用量统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
```

- [ ] **Step 2: Update `estimate_usage()`**

```python
def estimate_usage(messages: list, output: str) -> TokenUsage:
    input_text = " ".join(
        getattr(m, "content", "") for m in messages if hasattr(m, "content")
    )
    input_tokens = max(1, len(input_text) // 2)
    output_tokens = max(1, len(output) // 2)
    return TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
```

- [ ] **Step 3: Update `stream_answer()` — LLM native path**

```python
last_token_usage = TokenUsage()
...
if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
    u = chunk.usage_metadata
    last_token_usage = TokenUsage(
        prompt_tokens=u.get("input_tokens", 0),
        completion_tokens=u.get("output_tokens", 0),
        total_tokens=u.get("total_tokens", 0),
    )
```

- [ ] **Step 4: Update `stream_answer()` — estimated fallback path**

```python
if not last_token_usage.prompt_tokens and not last_token_usage.completion_tokens:
    last_token_usage = estimate_usage(messages, full_output)
```

- [ ] **Step 5: Update `stream_answer()` — logging**

```python
logger.info(
    "Generation completed: chars={} latency={:.0f}ms "
    "| tokens: prompt={} completion={} total={}",
    len(full_output), _gen_latency,
    last_token_usage.prompt_tokens,
    last_token_usage.completion_tokens,
    last_token_usage.total_tokens,
)
```

- [ ] **Step 6: Update `stream_answer()` — tracer**

```python
tracer.end_generation(
    gen_id, trace_id,
    output=full_output,
    usage={"prompt_tokens": last_token_usage.prompt_tokens,
           "completion_tokens": last_token_usage.completion_tokens,
           "total_tokens": last_token_usage.total_tokens},
)
```

(The tracer expects dict — keep that interface. Only the codebase-internal usage changes.)

- [ ] **Step 7: Update nodes.py generate_node (modified by Task 2)**

In the generate_node that was updated in Task 2, change `usage.get("total", 0)` to `usage.total_tokens`:

```python
# Inside generate_node, after estimate_usage():
usage = estimate_usage(prompt, full_text)
logger.info(..., usage.total_tokens)  # was usage.get("total", 0)
```

- [ ] **Step 8: Run tests**

```bash
pytest tests/ -v 2>&1 | tail -20
```
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add src/rag/stream.py src/agents/graph/nodes.py
git commit -m "refactor: unify token usage as TokenUsage dataclass, fix total_tokens=0 bug"
```

---

### Task 6: EvalReport — Type method signature

**Files:**
- Modify: `src/services/app_service.py`
- Modify: `src/cli/eval_ragas.py`

**Interfaces:**
- Consumes: `EvalReportEntity` from `src.infra.db.entities.eval_report`
- Produces: `AppService.insert_eval_report(report: EvalReportEntity)`

- [ ] **Step 1: Update `app_service.py` signature**

```python
from src.infra.db.entities import EvalReportEntity

async def insert_eval_report(self, report: EvalReportEntity) -> None:
    await self._eval_repo.insert_report(report)
```

(The entity already has all fields set by the caller, so the `EvalReportEntity(..., kb_id=...)` constructor call is removed.)

- [ ] **Step 2: Update `eval_ragas.py` caller**

Replace the dict literal with `EvalReportEntity(...)` constructor:

```python
from src.infra.db.entities.eval_report import EvalReportEntity

entity = EvalReportEntity(
    id=str(uuid.uuid4()),
    kb_id=kb_id,
    run_type="manual",
    qa_count=qa_count,
    faithfulness=faith,
    answer_relevancy=relevancy,
    context_precision=precision,
    context_recall=recall,
    overall_score=overall,
    passed=passed,
    report_path=report_path,
    triggered_by="cli",
    detail_json=detail_json,
)
await svc.insert_eval_report(entity)
```

- [ ] **Step 3: Build the container and test**

```bash
docker compose up -d --build app
```
Wait for health check: `curl -s http://localhost:8000/api/health` → `{"status":"ok"}`

- [ ] **Step 4: Commit**

```bash
git add src/services/app_service.py src/cli/eval_ragas.py
git commit -m "refactor: type EvalReportEntity in insert_eval_report signature"
```

---

### Task 7: Integration verification

- [ ] **Step 1: Full lint check**

```bash
ruff check .
```
Expected: All checks passed

- [ ] **Step 2: Full test suite**

```bash
pytest tests/ -v 2>&1 | tail -20
```
Expected: 249+ passed (2 pre-existing flaky ChromaDB tests may fail)

- [ ] **Step 3: Code review — ensure no residual dict access patterns**

Search for remaining patterns:
```bash
grep -rn 'state\.get(' src/agents/graph/ || echo "No state.get() remaining ✓"
grep -rn 'c\["content"\]' src/services/document_service.py || echo "No c[\"content\"] remaining ✓"
grep -rn 'msg\["role"\]' src/rag/ || echo "No msg[\"role\"] remaining ✓"
grep -rn 'usage\.get("total"' src/ || echo "No usage.get(\"total\") remaining ✓"
grep -rn 'report\["' src/services/app_service.py || echo "No report[key] remaining ✓"
```

- [ ] **Step 4: Build and deploy**

```bash
docker compose up -d --build app
curl -s http://localhost:8000/api/health
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: verification cleanup after dataclass refactoring"
```
