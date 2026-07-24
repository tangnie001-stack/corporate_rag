# Architecture Refactoring Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up architectural debt from Phase 1/2: eliminate duplicate RAG pipeline (Path A), sink business logic from API to Service layer, consolidate utility files, and clean up dead code.

**Architecture:** 5 independent phases executed sequentially. Each phase produces a compilable/testable state. Phase 1 (file moves) + Phase 2 (Graph fix) are fully independent. Phase 3 (dependency restructuring) depends on clean imports from Phase 1. Phase 4 (Path A deletion) depends on all prior phases. Phase 5 (tests + verification) finalizes.

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph / LangChain / ChromaDB

## Global Constraints

- All functions MUST have docstrings per docs/agents/rules.md
- Follow existing code style: no ternary expressions (`a if cond else b`)
- Layer rules: `api/` MUST NOT import `infra/` or `config/` directly (must go through `services/`)
- Single file MUST NOT exceed 400 lines; single function 80 lines
- All imports must use absolute paths (`from src.xxx import yyy`)
- Trace ID format: `trace_<uuid>`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `src/utils/__init__.py` | Package init (empty) |
| `src/utils/sse.py` | SSE event format helpers (moved from `infra/sse_utils.py`) |
| `src/utils/errors.py` | AppError/BusinessError/AuthError/SystemError hierarchy (moved from `infra/errors.py`) |
| `src/utils/desensitize.py` | Desensitize helper (moved from `infra/desensitize.py`) |

### Modified Files
| File | What Changes |
|------|-------------|
| `src/agents/graph/nodes.py` | `retrieve_node` → async, `_search()` remove `asyncio.new_event_loop()` |
| `src/agents/graph/workflow.py` | No code change (async node already compatible) |
| `src/chat/manager.py` | Delete sync methods: `get_history`, `add_message`, `clear_history`, `get_window`, `cleanup_session`, `_ensure_redis`, `_get_sync_redis` |
| `src/services/agent_service.py` | `add_message()` → `await add_message_async()`; `llm`/`reranker` → optional params with `models.py` fallback |
| `src/services/app_service.py` | Remove `self.rag_chain`, `self.chat`; add `self.chat_manager`, `self.bm25`; delete `chat()` method |
| `src/services/document_service.py` | Add `async process_document()`; copy `_enrich_chunk_pages()`, `_merge_tiny_chunks()`; delete sync `upload_and_process()` |
| `src/api/documents.py` | `_process_document_task` → one-line delegate; delete `_enrich_chunk_pages`, `_merge_tiny_chunks` |
| `src/api/chat.py` | `svc.rag_chain.chat_manager` → `svc.chat_manager`; update import paths |
| `src/api/sessions.py` | `asyncio.to_thread(cleanup_session)` → `await clear_history_async()`; `svc.rag_chain.chat_manager` → `svc.chat_manager`; update import paths |
| `src/rag/chain.py` | Delete `chat_with_citations()` + 6 sub-functions + 5 query delegate wrappers |
| `src/cli/eval_ragas.py` | Replace `RAGChain().chat_with_citations()` with `graph.ainvoke()` |
| `src/main.py` | Update import: `src.infra.errors` → `src.utils.errors` |
| `src/api/auth.py` | Update import: `src.infra.errors` → `src.utils.errors` |
| `src/api/knowledge_base.py` | Update import: `src.infra.errors` → `src.utils.errors` |

### Deleted Files
| File | Reason |
|------|--------|
| `src/services/chat_service.py` | Dead code (no production callers) |
| `src/infra/sse_utils.py` | Moved to `src/utils/sse.py` |
| `src/infra/errors.py` | Moved to `src/utils/errors.py` |
| `src/infra/desensitize.py` | Moved to `src/utils/desensitize.py` |
| `src/infra/chunking/enhancer.py` | Dead code (old ParentChildChunker, superseded by `strategies/parent_child.py`) |

### Test Files Modified
| File | What Changes |
|------|-------------|
| `tests/middleware/test_api_error.py` | Update import: `src.infra.errors` → `src.utils.errors` |
| `tests/services/test_app_service.py` | Delete `test_chat_*` and `test_upload_and_process_*`; add `test_process_document_*`; update import |
| `tests/rag/test_chain.py` | Delete `test_chat_with_citations_*` and `test_handles_*_route` test methods |
| `tests/services/test_chat_service.py` | Delete entire file |
| `tests/chat/test_chat_manager.py` | Delete sync method tests (`test_get_history`, `test_add_message`, `test_clear_history`, `test_get_window`) |

---

## Phase 1: 文件搬迁 (Tasks 1–4)

### Task 1: 创建 `utils/` 目录并搬迁 SSE 工具函数

**Files:**
- Create: `src/utils/__init__.py`
- Create: `src/utils/sse.py`
- Delete: `src/infra/sse_utils.py`
- Modify: `src/services/agent_service.py:1-16` (update import)
- Modify: `src/api/chat.py:1-14` (update import)

**Interfaces:**
- Consumes: None (new files)
- Produces: `from src.utils.sse import sse_status, sse_token, sse_citation, sse_done, sse_error`
- All 5 SSE functions keep identical signatures from `infra/sse_utils.py`

- [ ] **Step 1: Create `src/utils/` directory and __init__.py**

```bash
mkdir -p src/utils
```

Write `src/utils/__init__.py` as empty file.

- [ ] **Step 2: Copy SSE functions to `src/utils/sse.py`**

Read `src/infra/sse_utils.py`, write identical content to `src/utils/sse.py`.

File: `src/utils/sse.py`
```python
"""SSE (Server-Sent Events) 格式化工具函数。"""
import json


def sse_status(stage: str, message: str, detail: str | None = None) -> str:
    """构建 SSE status 事件。"""
    data: dict[str, str] = {"stage": stage, "message": message}
    if detail:
        data["detail"] = detail
    return f"event: status\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_token(token: str) -> str:
    """构建 SSE token 事件。"""
    return f"event: token\ndata: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"


def sse_citation(source: str, page: int, snippet: str, score: float = 0.0,
                 highlighted_snippet: str | None = None) -> str:
    """构建 SSE citation 事件。"""
    data = {"source": source, "page": page, "snippet": snippet, "score": score,
            "highlighted_snippet": highlighted_snippet}
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_done() -> str:
    """构建 SSE done 事件。"""
    return "event: done\ndata: {}\n\n"


def sse_error(error: str) -> str:
    """构建 SSE error 事件。"""
    return f"event: error\ndata: {json.dumps({'error': error}, ensure_ascii=False)}\n\n"
```

- [ ] **Step 3: Update imports in `agent_service.py`**

In `src/services/agent_service.py`, change line 16:
```python
# Before:
from src.infra.sse_utils import sse_status, sse_token, sse_citation, sse_done, sse_error
# After:
from src.utils.sse import sse_status, sse_token, sse_citation, sse_done, sse_error
```

- [ ] **Step 4: Update imports in `api/chat.py`**

In `src/api/chat.py`, change line 14:
```python
# Before:
from src.infra.sse_utils import sse_citation, sse_done, sse_error, sse_status, sse_token
# After:
from src.utils.sse import sse_citation, sse_done, sse_error, sse_status, sse_token
```

- [ ] **Step 5: Run tests to verify no regression**

```bash
pytest tests/ -v -x -k "not integration"
```
Expected: All tests pass, no import errors.

- [ ] **Step 6: Delete `src/infra/sse_utils.py`**

```bash
rm src/infra/sse_utils.py
```

- [ ] **Step 7: Final test + commit**

```bash
pytest tests/ -v -x
ruff check .
git add src/utils/ src/services/agent_service.py src/api/chat.py
git rm src/infra/sse_utils.py
git commit -m "refactor: move sse_utils from infra/ to utils/"
```

---

### Task 2: 搬迁 `errors.py` 到 `utils/`

**Files:**
- Create: `src/utils/errors.py`
- Delete: `src/infra/errors.py`
- Modify: `src/services/document_service.py:12`, `src/main.py:27`, `src/api/sessions.py:17`, `src/api/documents.py:38`, `src/api/knowledge_base.py:10`, `src/api/auth.py:14`, `tests/middleware/test_api_error.py:3`, `tests/services/test_app_service.py:7`

**Interfaces:**
- Consumes: `AppError`, `BusinessError`, `AuthError`, `ValidationError`, `SystemError` from infra/errors.py
- Produces: Same classes from `src.utils.errors`

- [ ] **Step 1: Copy `infra/errors.py` to `src/utils/errors.py`**

```bash
cp src/infra/errors.py src/utils/errors.py
```

- [ ] **Step 2: Update all source imports**

Replace `from src.infra.errors import` with `from src.utils.errors import` in these files:
- `src/services/document_service.py:12`
- `src/main.py:27`
- `src/api/sessions.py:17`
- `src/api/documents.py:38`
- `src/api/knowledge_base.py:10`
- `src/api/auth.py:14`

- [ ] **Step 3: Update all test imports**

Replace same pattern in:
- `tests/middleware/test_api_error.py:3`
- `tests/services/test_app_service.py:7`

- [ ] **Step 4: Run tests + delete old file**

```bash
pytest tests/ -v -x
```

Verify no `ModuleNotFoundError: No module named 'src.infra.errors'` errors.

```bash
rm src/infra/errors.py
git add src/utils/errors.py src/services/document_service.py src/main.py src/api/*.py tests/middleware/test_api_error.py tests/services/test_app_service.py
git rm src/infra/errors.py
git commit -m "refactor: move errors from infra/ to utils/"
```

---

### Task 3: 搬迁 `desensitize.py` 到 `utils/`

**Files:**
- Create: `src/utils/desensitize.py`
- Delete: `src/infra/desensitize.py`
- Modify: `src/cli/eval_ragas_generate.py:206`

**Interfaces:**
- Consumes: `desensitize` function from `infra/desensitize.py`
- Produces: Same function from `src.utils.desensitize`

- [ ] **Step 1: Copy and update imports**

```bash
cp src/infra/desensitize.py src/utils/desensitize.py
```

In `src/cli/eval_ragas_generate.py`, change:
```python
from src.utils.desensitize import desensitize
```

- [ ] **Step 2: Run tests + delete old file**

```bash
pytest tests/ -v -x
rm src/infra/desensitize.py
git add src/utils/desensitize.py src/cli/eval_ragas_generate.py
git rm src/infra/desensitize.py
git commit -m "refactor: move desensitize from infra/ to utils/"
```

---

### Task 4: 删除死代码 `infra/chunking/enhancer.py`

**Files:**
- Delete: `src/infra/chunking/enhancer.py`
- Modify: None (no code references this file)

- [ ] **Step 1: Confirm no references**

```bash
grep -rn "enhancer" src/ --include="*.py"
```
Expected output: No matches (the file is truly dead code).

- [ ] **Step 2: Delete file + commit**

```bash
git rm src/infra/chunking/enhancer.py
git commit -m "refactor: remove dead code infra/chunking/enhancer.py"
```

---

## Phase 2: Graph 层修复 (Tasks 5–6)

### Task 5: `retrieve_node` 改为 async 节点

**Files:**
- Modify: `src/agents/graph/nodes.py:57-74`

**Interfaces:**
- Consumes: `search(query, kb_id, vector_store, bm25)` — from `src.rag.retrieval`
- Produces: `async retrieve_node(state: AgentState) -> dict` — same interface, now async

- [ ] **Step 1: Modify `_search()` to be async**

In `src/agents/graph/nodes.py`, replace the `_search` inner function:

```python
# Before:
def make_retrieve_node(vector_store, bm25) -> Callable:
    def _search(query, kb_id):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(search(query, kb_id, vector_store, bm25))
        finally:
            loop.close()

    def retrieve_node(state: AgentState) -> dict:
        tid = _tid(state)
        q = state.get("rewritten_query") or state.get("query", "")
        kb_id = state.get("kb_id", "")
        logger.info("[{}] retrieve_node start: query={} kb_id={}", tid, q[:50], kb_id)
        results = _search(q, kb_id)
        logger.info("[{}] retrieve_node done: results={}", tid, len(results))
        return {"retrieval_results": results}
    return retrieve_node

# After:
def make_retrieve_node(vector_store, bm25) -> Callable:
    async def retrieve_node(state: AgentState) -> dict:
        tid = _tid(state)
        q = state.get("rewritten_query") or state.get("query", "")
        kb_id = state.get("kb_id", "")
        logger.info("[{}] retrieve_node start: query={} kb_id={}", tid, q[:50], kb_id)
        results = await search(q, kb_id, vector_store, bm25)
        logger.info("[{}] retrieve_node done: results={}", tid, len(results))
        return {"retrieval_results": results}
    return retrieve_node
```

Note: The `asyncio` import at the top of `nodes.py` can be removed since we no longer use `asyncio.new_event_loop()`.

- [ ] **Step 2: Run existing tests**

```bash
pytest tests/ -v -x
```
Expected: All existing tests pass. The API tests (which mock AppService) are unaffected. The graph is only exercised through integration/e2e tests.

- [ ] **Step 3: Commit**

```bash
git add src/agents/graph/nodes.py
git commit -m "refactor: make retrieve_node async, remove asyncio.new_event_loop()"
```

---

### Task 6: 确认 workflow async 节点兼容

**Files:**
- Inspect: `src/agents/graph/workflow.py`

No code changes needed. LangGraph `astream_events()` supports mixed sync/async nodes. The `route_by_grader` and `route_by_intent` are pure sync edge functions that only read state — they are unaffected by whether `retrieve_node` is async.

- [ ] **Step 1: Verify the git diff for `nodes.py`**

```bash
git diff src/agents/graph/nodes.py
```

Confirm `retrieve_node` is now `async def` and no `asyncio.new_event_loop()` exists in the file.

- [ ] **Step 2: Quick sanity check on workflow.py**

Read `src/agents/graph/workflow.py` and confirm `add_conditional_edges` and `add_edge` calls that include `retrieve` as a target node will work with async functions. LangGraph's `astream_events` (used in `agent_service.py:83`) handles this transparently.

No code changes needed. Commit is not required for this task.

---

## Phase 3: 依赖重组 (Tasks 7–11)

### Task 7: ChatManager 删除同步方法

**Files:**
- Modify: `src/chat/manager.py`

- [ ] **Step 1: Delete sync methods and helpers**

In `src/chat/manager.py`, delete these methods:
- `_get_sync_redis()` (lines ~147-149)
- `_ensure_redis()` (lines ~151-192)
- `get_history()` (lines ~205-225)
- `add_message()` (lines ~227-277)
- `get_window()` (lines ~279-297)
- `clear_history()` (lines ~299-315)
- `cleanup_session()` (lines ~117-123)

Keep:
- `__init__`
- `set_mysql_db`
- `save_session_async`, `save_messages_async`
- `_ensure_redis_async`, `_session_key`
- `add_message_async`, `get_history_async`, `clear_history_async`
- All in-memory store and persistence logic

After deletion, verify file is under 400 lines.

- [ ] **Step 2: Run tests**

```bash
pytest tests/chat/ -v -x
```
Expected: All ChatManager async tests pass. The sync method tests will fail — they'll be deleted in Task 22 (Phase 5).

- [ ] **Step 3: Commit**

```bash
git add src/chat/manager.py
git commit -m "refactor: remove ChatManager sync methods"
```

---

### Task 8: AgentService 和 sessions.py 改为 async 调用

**Files:**
- Modify: `src/services/agent_service.py:66` (also needs the previously changed import from Task 1)
- Modify: `src/api/sessions.py:124`

**Interfaces:**
- Consumes: `chat_manager.add_message_async(session_id, role, content)` — async method that was preserved in Task 7
- Consumes: `chat_manager.clear_history_async(session_id)` — async method preserved in Task 7

- [ ] **Step 1: Change `agent_service.py:66` to async**

In `src/services/agent_service.py`, find:
```python
self._chat_manager.add_message(session_id, "user", query)
```

Change to:
```python
await self._chat_manager.add_message_async(session_id, "user", query)
```

- [ ] **Step 2: Change `sessions.py:124` to async**

In `src/api/sessions.py`, find:
```python
await asyncio.to_thread(svc.rag_chain.chat_manager.cleanup_session, session_id)
```

Change to:
```python
await svc.chat_manager.clear_history_async(session_id)
```

Note: The import `asyncio` may still be needed in sessions.py for other uses — check before removing.

- [ ] **Step 3: Run tests**

```bash
pytest tests/ -v -x
```
Expected: All tests pass (sessions API tests mock AppService, so they're unaffected).

- [ ] **Step 4: Commit**

```bash
git add src/services/agent_service.py src/api/sessions.py
git commit -m "refactor: change chat_manager calls to async"
```

---

### Task 9: AgentService 模型获取改为可选参数

**Files:**
- Modify: `src/services/agent_service.py:29-48`

**Interfaces:**
- Consumes: `get_llm()`, `get_rerank()` from `src.models`
- Produces: `AgentService(vector_store, bm25, chat_manager, llm=None, reranker=None, prompt_manager=None)`

- [ ] **Step 1: Change constructor signature**

In `src/services/agent_service.py`, change `__init__`:

```python
# Before:
def __init__(
    self,
    vector_store: VectorStore,
    bm25: BM25Index | None,
    llm,
    reranker,
    chat_manager: ChatManager,
    prompt_manager: PromptManager | None = None,
):
    self._vector_store = vector_store
    self._bm25 = bm25
    self._llm = llm
    self._reranker = reranker
    self._chat_manager = chat_manager
    self._prompt_manager = prompt_manager or PromptManager()
    self._tracer = LangfuseTracer()
    self._graph = build_graph(
        vector_store, bm25, llm, reranker, self._prompt_manager, self._tracer
    )

# After:
def __init__(
    self,
    vector_store: VectorStore,
    bm25: BM25Index | None,
    chat_manager: ChatManager,
    llm=None,
    reranker=None,
    prompt_manager: PromptManager | None = None,
):
    from src.models import get_llm, get_rerank

    self._vector_store = vector_store
    self._bm25 = bm25
    self._llm = llm or get_llm()
    self._reranker = reranker or get_rerank()
    self._chat_manager = chat_manager
    self._prompt_manager = prompt_manager or PromptManager()
    self._tracer = LangfuseTracer()
    self._graph = build_graph(
        vector_store, bm25, self._llm, self._reranker,
        self._prompt_manager, self._tracer,
    )
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -x
```
Expected: All tests pass. No tests construct `AgentService` directly, so this change only affects production code path through `AppService`.

- [ ] **Step 3: Commit**

```bash
git add src/services/agent_service.py
git commit -m "refactor: make AgentService llm/reranker optional with models.py fallback"
```

---

### Task 10: AppService 依赖重组

**Files:**
- Modify: `src/services/app_service.py`

**Interfaces:**
- Consumes: `ChatManager()`, `BM25Index()`, `HYBRID_SEARCH_ENABLED` from config
- Produces: `AppService.chat_manager` — public attribute for `api/chat.py` and `api/sessions.py`
- Produces: `AppService.bm25` — public attribute
- Removes: `AppService.rag_chain` — no longer available
- Removes: `AppService.chat` — no longer available

- [ ] **Step 1: Rewrite AppService.__init__**

Replace lines 29-51 in `src/services/app_service.py`:

```python
# Before:
def __init__(self, mysql_db=None, vector_store=None, router=None,
             rag_chain=None, agent_service=None):
    self.db = mysql_db or MySQLDB()
    self.vector_store = vector_store or VectorStore()
    self.router = router or DocRouter()
    self.rag_chain = rag_chain or RAGChain()
    self.agent_service = agent_service or AgentService(
        vector_store=self.vector_store,
        bm25=self.rag_chain.bm25 if hasattr(self.rag_chain, 'bm25') else None,
        llm=self.rag_chain.llm if hasattr(self.rag_chain, 'llm') else None,
        reranker=self.rag_chain.reranker if hasattr(self.rag_chain, 'reranker') else None,
        chat_manager=self.rag_chain.chat_manager,
    )
    self.kb = KBService(self.db)
    self.document = DocumentService(self.db, self.vector_store, self.router)
    self.chat = ChatService(self.rag_chain)

# After:
def __init__(
    self,
    mysql_db: Optional[MySQLDB] = None,
    vector_store: Optional[VectorStore] = None,
    router: Optional[DocRouter] = None,
    chat_manager: Optional[ChatManager] = None,
    agent_service: Optional[AgentService] = None,
) -> None:
    self.db = mysql_db or MySQLDB()
    self.vector_store = vector_store or VectorStore()
    self.router = router or DocRouter()
    self.chat_manager = chat_manager or ChatManager()
    self.bm25 = (
        BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
    )
    self.agent_service = agent_service or AgentService(
        vector_store=self.vector_store,
        bm25=self.bm25,
        chat_manager=self.chat_manager,
    )
    self.kb = KBService(self.db)
    self.document = DocumentService(self.db, self.vector_store, self.router)
```

Remove import `from src.rag.chain import RAGChain, RAGContext` (line 15) — unless still used elsewhere (check if `RAGContext` is used in `app_service.py`). Also remove `from src.services.chat_service import ChatService`.

- [ ] **Step 2: Delete `chat()` method**

Delete the `chat()` method (lines ~104-110):
```python
# Remove these lines entirely:
def chat(self, ...):
    return self.chat.chat(...)
```

- [ ] **Step 3: Update `rag_chain` references in AppService**

Search for remaining `rag_chain` references in `app_service.py`. None should remain after the above changes.

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v -x
```

Some tests in `test_app_service.py` will fail (tests that use `svc.rag_chain.chat_manager` or `svc.chat()`). That's expected — they'll be fixed in Phase 5.

- [ ] **Step 5: Commit**

```bash
git add src/services/app_service.py
git commit -m "refactor: restructure AppService dependencies, remove RAGChain/ChatService"
```

---

### Task 11: 更新 API 层 `chat_manager` 引用

**Files:**
- Modify: `src/api/chat.py:183,204,207`

- [ ] **Step 1: Replace `svc.rag_chain.chat_manager` with `svc.chat_manager`**

In `src/api/chat.py`, find all occurrences of `svc.rag_chain.chat_manager` and replace with `svc.chat_manager`:

```python
# Line 183:
svc.chat_manager.set_mysql_db(svc.db)

# Line 204:
lambda: svc.chat_manager.save_session_async(session_id, title, kb_id)

# Line 207:
lambda: svc.chat_manager.save_messages_async(
    session_id, kb_id, query, answer, sources
)
```

- [ ] **Step 2: Verify test_api_error.py imports are already updated**

```bash
grep "from src.utils.errors import BusinessError" tests/middleware/test_api_error.py
```
Should already be updated from Task 2.

- [ ] **Step 3: Run tests**

```bash
pytest tests/ -v -x
```

- [ ] **Step 4: Commit**

```bash
git add src/api/chat.py
git commit -m "refactor: replace rag_chain.chat_manager with app_service.chat_manager"
```

---

## Phase 4: 业务逻辑下沉 (Tasks 12–14)

### Task 12: DocumentService 新增 async `process_document()`

**Files:**
- Modify: `src/services/document_service.py`

**Interfaces:**
- Consumes: `doc_id, kb_id, minio_key, filename, ext` (same params as api/documents.py's `_process_document_task`)
- Produces: `async process_document(kb_id, doc_id, minio_key, filename, ext) -> None`
- Adds private helpers: `_enrich_chunk_pages`, `_merge_tiny_chunks`

- [ ] **Step 1: Copy `_enrich_chunk_pages` to DocumentService**

In `src/services/document_service.py`, add `_enrich_chunk_pages` as a private method. Content identical to current `api/documents.py:232-254`:

```python
def _enrich_chunk_pages(self, chunks: list[dict], parse_chunks: list, full_text: str) -> None:
    """从解析器分块反推 chunk 页码。"""
    offset = 0
    page_map = []
    for c in parse_chunks:
        page = c.metadata.get("page", 1)
        page_map.append((offset, offset + len(c.content), page))
        offset += len(c.content) + 2
    for chunk in chunks:
        text = chunk["content"]
        pos = full_text.find(text)
        if pos < 0:
            continue
        end = pos + len(text)
        pages = {p for s, e, p in page_map if s < end and e > pos}
        chunk["metadata"]["page"] = min(pages)
```

- [ ] **Step 2: Copy `_merge_tiny_chunks` to DocumentService**

In `src/services/document_service.py`, add `_merge_tiny_chunks` as a private method. Content identical to current `api/documents.py:257-290`.

- [ ] **Step 3: Add `async process_document()` method**

Add this method to `DocumentService`. The full implementation is identical to `api/documents.py:_process_document_task` (lines 293-448) — 直接将函数体复制过来，替换 `svc.db` → `self.db`、`svc.vector_store` → `self.vector_store`、`svc.router` → `self.router`。

```python
async def process_document(
    self,
    kb_id: str,
    doc_id: str,
    minio_key: str,
    filename: str,
    ext: str,
) -> None:
    """后台异步处理文档：下载 → 解析 → 分块 → 向量化入库。

    Args:
        kb_id: 知识库 UUID
        doc_id: 文档 UUID
        minio_key: MinIO 存储路径
        filename: 文件名
        ext: 文件扩展名（含点号）
    """
    # 完整实现来自 api/documents.py:_process_document_task 的精确复制：
    # 1. 更新 DB 状态为 processing/extracting
    # 2. asyncio.to_thread FileStore().download(minio_key)
    # 3. asyncio.to_thread 写入临时文件
    # 4. asyncio.to_thread self.router.parse(tmp_path)
    # 5. ChunkRouter.detect_strategy + chunker.chunk
    # 6. self._enrich_chunk_pages() + self._merge_tiny_chunks()
    # 7. validate_chunks
    # 8. ChunkQualityScorer.evaluate（若 CHUNK_EVAL_ENABLED）
    # 9. asyncio.to_thread self.vector_store.add_chunks()
    # 10. DB update_document_status → ready
    # 11. except → DB update_document_status → failed
    # 12. finally → os.unlink(tmp_path)
```

Note: The `_process_semaphore` (`asyncio.Semaphore(3)`) needs to be added at module level in `document_service.py`:

```python
# Add near the top of document_service.py, after imports:
_process_semaphore = asyncio.Semaphore(3)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v -x
```

- [ ] **Step 5: Commit**

```bash
git add src/services/document_service.py
git commit -m "feat: add async process_document to DocumentService"
```

---

### Task 13: API 层文档处理改为委托并删除旧逻辑

**Files:**
- Modify: `src/api/documents.py`

- [ ] **Step 1: Replace `_process_document_task` with one-line delegate**

In `src/api/documents.py`, change the background task launch (line 215-216):

```python
# Before:
asyncio.create_task(
    _process_document_task(svc, kb_id, doc_id, minio_key, file.filename, ext)
)

# After:
asyncio.create_task(
    svc.document.process_document(kb_id, doc_id, minio_key, file.filename, ext)
)
```

- [ ] **Step 2: Delete `_enrich_chunk_pages`, `_merge_tiny_chunks`, `_process_document_task`**

Delete these functions from `src/api/documents.py`:
- `_enrich_chunk_pages` (lines ~232-254)
- `_merge_tiny_chunks` (lines ~257-290)
- `_process_document_task` (lines ~293-448)

- [ ] **Step 3: Remove now-unused imports**

Check if these imports in `api/documents.py` are now unused and can be removed:
- `from src.infra.chunking.router import ChunkRouter`
- `from src.infra.chunking.strategies.base import BaseChunker`
- `from src.infra.chunking.validator import ChunkData, validate_chunks`
- `from src.eval.chunk_scorer import ChunkQualityScorer`
- `from src.config import CHUNK_EVAL_ENABLED, MAX_TABLE_TOKENS`

Keep: `MAX_FILE_SIZE` (used for file size check), `FileStore` (used for MD5 hash dedup lookup).

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v -x
```

- [ ] **Step 5: Commit**

```bash
git add src/api/documents.py
git commit -m "refactor: delegate document processing from api/ to DocumentService"
```

---

### Task 14: 删除 DocumentService 同步版 upload_and_process

**Files:**
- Modify: `src/services/document_service.py`

- [ ] **Step 1: Delete sync `upload_and_process`**

Delete `upload_and_process()` (lines ~64-124) from `DocumentService`. Keep the `enrich_chunk_pages` (the async version renamed to private `_enrich_chunk_pages` was added in Task 12).

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -x
```

Expected: Tests that call `svc.document.upload_and_process()` will fail — will be fixed in Phase 5.

- [ ] **Step 3: Commit**

```bash
git add src/services/document_service.py
git commit -m "refactor: remove sync DocumentService.upload_and_process (dead code)"
```

---

## Phase 5: 删除 Path A (Tasks 15–19)

### Task 15: RAGChain 精简 — 删除 chat_with_citations

**Files:**
- Modify: `src/rag/chain.py`

- [ ] **Step 1: Delete `chat_with_citations` method**

Delete lines 106-182 (the entire `chat_with_citations` method) and its sub-functions:
- `_handle_simple_route`
- `_handle_short_query`
- `_handle_search_error`
- `_handle_no_results`
- `_rewrite_if_needed`

These are private methods only called from `chat_with_citations`.

- [ ] **Step 2: Delete query delegate methods**

Delete these thin wrapper methods:
- `_classify_query` (lines ~235-239)
- `_rewrite_query` (lines ~241-243) — Note: this is a different method from the module-level `rewrite_query` in `retrieval.py`
- `_expand_query` (lines ~245-249)
- `_condense_query` (lines ~251-255)
- `_decompose_query` (lines ~257-261)

- [ ] **Step 3: Remove unused imports**

Remove these now-unused imports from `chain.py`:
- `from src.infra.search.query_router import QueryRouter`
- `from src.infra.search.bm25_index import BM25Index`
- `from src.infra.db.vector_store import VectorStore`
- `from src.infra.db.mysql_db import MySQLDB`
- `from src.chat import ChatManager`
- `from src.rag.context import RAGContext` (only used in method signatures that are now deleted)

Keep:
- `Generator`, `Optional`, `time` — check if still needed
- `LangfuseTracer`, `PromptManager` — needed for eval interfaces
- `get_embeddings`, `get_llm`, `get_rerank` — needed for model lifecycle
- `search`, `rerank_results`, `rewrite_query` — needed for eval interfaces
- `build_prompt`, `build_simple_prompt`, `format_context` — check
- `stream_answer` — needed for eval interfaces
- `RAGContext` — used in `rerank()` method signature

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v -x
```

Expected: `test_chat_with_citations_*` tests will fail — will be fixed in Phase 5.

- [ ] **Step 5: Commit**

```bash
git add src/rag/chain.py
git commit -m "refactor: remove chat_with_citations from RAGChain, keep eval interface"
```

---

### Task 16: 删除 ChatService 文件

**Files:**
- Delete: `src/services/chat_service.py`

- [ ] **Step 1: Delete the file**

```bash
git rm src/services/chat_service.py
```

- [ ] **Step 2: Verify no remaining imports of ChatService**

```bash
grep -rn "chat_service" src/ --include="*.py"
grep -rn "ChatService" src/ --include="*.py"
```
Expected: No matches (already removed from `app_service.py` in Task 10).

- [ ] **Step 3: Commit**

```bash
git commit -m "refactor: delete ChatService (dead code)"
```

---

### Task 17: CLI eval 改用 graph.ainvoke()

**Files:**
- Modify: `src/cli/eval_ragas.py`

**Interfaces:**
- Consumes: `build_graph()` from `src.agents.graph.workflow`
- Consumes: `VectorStore`, `BM25Index`, `ChatManager`, `get_llm`, `get_rerank` from their respective modules
- Produces: Same eval output format (no external contract change)

- [ ] **Step 1: Read current eval code to understand exact usage**

Read `src/cli/eval_ragas.py` around line 417 to see how `RAGChain` is currently constructed and used.

- [ ] **Step 2: Replace RAGChain construction with graph construction**

```python
from src.rag.chain import RAGChain
rag_chain = RAGChain(vector_store=vector_store, mysql_db=mysql_db, ...)
gen, citations = rag_chain.chat_with_citations(kb_id, session_id, query)

# After:
from src.agents.graph.workflow import build_graph
from src.config import HYBRID_SEARCH_ENABLED, BM25_INDEX_DIR
from src.infra.search.bm25_index import BM25Index
from src.models import get_llm, get_rerank
from src.infra.llm.prompt_manager import PromptManager
from src.infra.llm.langfuse_tracing import LangfuseTracer

llm = get_llm()
reranker = get_rerank()
prompt_manager = PromptManager()
tracer = LangfuseTracer()
bm25 = BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
graph = build_graph(vector_store, bm25, llm, reranker, prompt_manager, tracer)

final_state = await graph.ainvoke({
    "kb_id": kb_id,
    "session_id": session_id or str(uuid.uuid4()),
    "query": query,
    "trace_id": f"trace_{uuid.uuid4().hex[:12]}",
    "retrieval_retries": 0,
    "downgraded": False,
    "downgrade_reason": "",
    "_history": [],
})
answer = final_state.get("answer", "")
citations = final_state.get("citations", [])
```

Note: The eval script uses `def` (sync), not `async def`. `graph.ainvoke()` is async and needs an event loop. Add:

```python
import asyncio
# ...
loop = asyncio.new_event_loop()
final_state = loop.run_until_complete(graph.ainvoke({...}))
```

- [ ] **Step 3: Run eval script to verify**

```bash
python -m src.cli.eval_ragas --help
```
Expected: Script loads without import errors.

- [ ] **Step 4: Commit**

```bash
git add src/cli/eval_ragas.py
git commit -m "refactor: eval CLI uses graph.ainvoke() instead of RAGChain"
```

---

### Task 18: 更新 main.py 的 exception handler import

**Files:**
- Modify: `src/main.py:27`

- [ ] **Step 1: Update import**

```python
# Before:
from src.infra.errors import AppError
# After:
from src.utils.errors import AppError
```

- [ ] **Step 2: Commit**

```bash
git add src/main.py
git commit -m "refactor: update main.py errors import path"
```

---

## Phase 6: 测试清理 (Tasks 19–23)

### Task 19: 更新 test_api_error.py import

**Files:**
- Modify: `tests/middleware/test_api_error.py:3`

Already done in Task 2. Verify:

```bash
grep "from src.utils.errors import BusinessError" tests/middleware/test_api_error.py
```

Should report a match. If not, fix it:
```python
from src.utils.errors import BusinessError
```

---

### Task 20: 更新 test_chain.py

**Files:**
- Modify: `tests/rag/test_chain.py`

Delete test methods related to `chat_with_citations`:
- `test_chat_with_citations_delegates_to_split_methods`
- `test_saves_user_message_to_history`
- `test_handle_simple_route`
- `test_handle_short_query`
- `test_handle_search_error`
- `test_handle_no_results`
- `test_simple_route_direct_answer`
- `test_vague_route_with_rewrite`
- `test_complex_route_with_rewrite`

Also delete tests for the query delegate methods if they only test `_classify_query`, `_rewrite_query`, etc.

Keep tests for:
- `test_rerank_results` (tests `rerank()` method which is kept for eval)
- `test_stream_answer` (tests `stream_answer()` which is kept)
- `test_rewrite_query` — if it tests the module-level `rewrite_query` from `retrieval.py` (used by Graph too), keep it; if it tests `RAGChain._rewrite_query()`, delete it.

- [ ] **Step 1: Delete test methods**

```bash
# Before editing, read the test file to identify exact test methods
grep -n "def test_" tests/rag/test_chain.py
```

Identify which to delete vs keep, then edit the file.

- [ ] **Step 2: Run remaining test_chain tests**

```bash
pytest tests/rag/test_chain.py -v
```
Expected: Remaining tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/rag/test_chain.py
git commit -m "test: remove chat_with_citations tests from test_chain"
```

---

### Task 21: 删除 test_chat_service.py

**Files:**
- Delete: `tests/services/test_chat_service.py`

```bash
git rm tests/services/test_chat_service.py
git commit -m "test: delete test_chat_service (ChatService deleted)"
```

---

### Task 22: 更新 test_app_service.py

**Files:**
- Modify: `tests/services/test_app_service.py`

- [ ] **Step 1: Delete test methods for `chat` and `upload_and_process`**

Delete:
- `test_chat_returns_answer_and_citations` (or similar name)
- `test_chat_saves_conversation` (or similar)
- `test_upload_and_process` (sync version deleted)

- [ ] **Step 2: Add test for async `process_document`**

Add a test for the new `process_document` method. This test mocks the external dependencies (MinIO, parser, VectorStore):

```python
@pytest.mark.asyncio
async def test_process_document_success(mock_router, mock_vs, mock_db):
    """文档处理成功时状态更新为 ready。"""
    svc = AppService(mysql_db=mock_db, vector_store=mock_vs, router=mock_router)
    await svc.document.process_document(
        kb_id="test-kb",
        doc_id="test-doc",
        minio_key="path/to/file.pdf",
        filename="test.pdf",
        ext=".pdf",
    )
    mock_db.update_document_status.assert_called_with(
        "test-doc", "ready", chunk_count=ANY, processing_state="completed",
        processing_progress=100, processing_message=ANY, chunk_strategy=ANY,
    )
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/services/test_app_service.py -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/services/test_app_service.py
git commit -m "test: update test_app_service for DocumentService changes"
```

---

### Task 23: 更新 test_chat_manager.py

**Files:**
- Modify: `tests/chat/test_chat_manager.py`

Delete sync method tests:
- `test_get_history`
- `test_add_message`
- `test_clear_history`
- `test_get_window`

Keep async method tests:
- `test_add_message_async`
- `test_get_history_async`
- `test_clear_history_async`

- [ ] **Step 1: Delete sync test methods**

```bash
grep -n "def test_" tests/chat/test_chat_manager.py
```
Identify which to delete, then edit.

- [ ] **Step 2: Run remaining tests**

```bash
pytest tests/chat/test_chat_manager.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/chat/test_chat_manager.py
git commit -m "test: remove sync method tests from test_chat_manager"
```

---

## Phase 7: 最终验证 (Task 24)

### Task 24: 验证

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```
Expected: All tests pass. Track any failures and fix them before proceeding.

- [ ] **Step 2: Lint check**

```bash
ruff check .
```
Expected: No errors. Fix any lint issues.

- [ ] **Step 3: Check for debug code**

```bash
grep -rn "print(" src/ --include="*.py" | grep -v "__repr__\|__str__\|# "
grep -rn "TODO" src/ --include="*.py" | grep -v "docstring\|# TODO"
```
Expected: No stray print() or TODO statements.

- [ ] **Step 4: Check line count limits**

```bash
wc -l src/chat/manager.py src/services/agent_service.py src/services/app_service.py
```
Each file should be under 400 lines.

- [ ] **Step 5: Manual smoke test**

Start the application:
```bash
docker compose up -d
```

Send a test SSE request:
```bash
curl -N "http://localhost:8000/api/chat/stream?session_id=test_$(date +%s)&kb_id=&query=2024%E5%B9%B4%E8%90%A5%E4%B8%9A%E6%94%B6%E5%85%A5"
```

Expected: SSE event stream with status/token/citation/done events. No 500 errors.

- [ ] **Step 6: Final diff review**

```bash
git diff HEAD~10 --stat
git diff HEAD~10
```
Review all changes for unexpected modifications.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-25-architecture-refactoring-phase3.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
