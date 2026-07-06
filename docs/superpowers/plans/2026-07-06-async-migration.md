# Async Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert all synchronous I/O calls (MySQL, Redis, Langfuse) to async-native, keep ChromaDB/MinIO wrapped in `to_thread()`, and make `_process_document_task` a fully async function.

**Architecture:** The event loop always controls execution flow. Sync-only libraries (ChromaDB `PersistentClient`, `minio` SDK) are wrapped individually with `asyncio.to_thread()`. MySQL switches to `aiomysql` with native connection pool. Redis switches to `redis.asyncio` (same package). Langfuse replaces custom `urllib` code with the official SDK (built-in async). `_process_document_task` uses granular `to_thread()` per blocking call instead of one big thread.

**Tech Stack:** Python 3.11+ / FastAPI / aiomysql / redis.asyncio / langfuse (SDK) / ChromaDB / MinIO

## Global Constraints

- Remove threading.RLock — replace with asyncio.Lock where needed
- Connection pool config: minsize=2, maxsize=10 for MySQL
- Langfuse SDK v3 compatibility (server already on v3)
- All route handlers must remain `async def` (not `def`)
- `_process_document_task` uses `asyncio.Semaphore(3)` for concurrency control
- Test files must be updated to use `pytest.mark.asyncio` for async DB tests

---

### Task 1: Revert thread pool commit 32aa73c

**Files:**
- Revert: `src/api/routes/documents.py`
- Delete: `tests/api/test_background_task.py`

**Interfaces:**
- Produces: `_process_document` restored to `async def` (with no `await` inside — a blank slate for Task 5)
- Produces: `_dispatch_processing` and `_process_semaphore` removed — clean state

- [ ] **Step 1: Revert the commit**

```bash
git revert 32aa73c --no-edit
```

After revert, verify:
- `_process_document` is `async def` again
- `_process_semaphore` is gone
- `_dispatch_processing` is gone

- [ ] **Step 2: Verify the revert**

```bash
pytest tests/api/test_documents.py -v --tb=short 2>&1 | tail -5
# Expected: tests fail with 401 (pre-existing auth issue)
```

Run: `ruff check src/api/routes/documents.py`

Expected: No errors.

- [ ] **Step 3: Commit (already done by revert)**

---

### Task 2: MySQL async — rewrite MySQLDB with aiomysql

**Files:**
- Modify: `src/infra/mysql_db.py` (full rewrite)
- Modify: `pyproject.toml` (add aiomysql)
- Modify: `deploy/mysql/init/001_schema.sql` (no change needed — schema stays the same)
- Test: `tests/test_mysql_db.py`

**Interfaces:**
- Produces: `MySQLDB` all methods `async def`, connection pool via `aiomysql.create_pool()`
- Consumes: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` from config
- Destroys: `threading.RLock` — replaced with `asyncio.Lock` where needed

**Key design decisions:**
- Connection pool via `aiomysql.create_pool(minsize=2, maxsize=10)` — managed via `async with pool.acquire() as conn`
- `asyncio.Lock` only where truly needed (e.g., `get_or_create_kb` critical section)
- Remove `_ensure_connection()` — connection pool handles reconnect via `recycle` param
- Remove `_close_read_transaction()` — aiomysql doesn't have implicit transaction issue
- Remove `__enter__` / `__exit__` (context manager) — use `async with` on acquired connections
- `init_db()` called at startup via `asyncio.create_task()` or `await` in FastAPI lifespan

- [ ] **Step 1: Add aiomysql dependency**

In `pyproject.toml`, add: `"aiomysql>=0.2.0,<1.0.0"`

Then install: `pip install aiomysql`

- [ ] **Step 2: Remove old imports and constants**

Remove from `src/infra/mysql_db.py`:
```python
import threading
import time
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor
```

Replace with:
```python
import asyncio
import aiomysql
from aiomysql import DictCursor
```

- [ ] **Step 3: Rewrite `__init__` and connection management**

```python
class MySQLDB:
    """MySQL 数据库封装 — aiomysql 异步连接池版。

    异步连接池管理，自动回收和重连。
    所有方法均为 async def，调用方需 await。
    """

    def __init__(self):
        """初始化 MySQLDB，还不会立即创建连接池。

        连接池在首次调用 _pool() 时懒加载，
        或在 init_db() 中显式初始化。
        """
        self._pool: aiomysql.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> aiomysql.Pool:
        """获取或创建连接池（懒加载，线程安全）。"""
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            self._pool = await aiomysql.create_pool(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                db=MYSQL_DATABASE,
                charset="utf8mb4",
                cursorclass=DictCursor,
                minsize=2,
                maxsize=10,
                connect_timeout=10,
                recycle=3600,  # 1小时后回收连接，避免MySQL断开空闲连接
            )
            logger.info("MySQL connection pool created (minsize=2, maxsize=10)")
            return self._pool

    async def close(self) -> None:
        """关闭连接池，释放所有连接。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL connection pool closed")
```

- [ ] **Step 4: Example of rewritten method — `init_db`**

```python
async def init_db(self) -> None:
    """创建所有业务表（幂等操作）。"""
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(CREATE_TABLE_USERS)
            await cursor.execute(CREATE_TABLE_KNOWLEDGE_BASE)
            await cursor.execute(CREATE_TABLE_DOCUMENT)
            await cursor.execute(CREATE_TABLE_CONVERSATION_HISTORY)
            await cursor.execute(CREATE_TABLE_SESSIONS)
            try:
                await cursor.execute(DROP_CONVERSATION_HISTORY_FK)
            except Exception:
                pass
        await conn.commit()
    logger.info("Database tables initialized")
```

- [ ] **Step 5: Example of read method — `get_sessions`**

```python
async def get_sessions(self) -> list[dict]:
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(DictCursor) as cursor:
            await cursor.execute(SELECT_SESSIONS)
            rows = await cursor.fetchall()
        return rows
```

- [ ] **Step 6: Example of write method — `add_document`**

```python
async def add_document(self, doc_id: str, kb_id: str, filename: str,
                       file_type: str, file_size: int, **kwargs) -> None:
    pool = await self._get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(INSERT_DOCUMENT, (
                doc_id, kb_id, kwargs.get("user_id", ""),
                filename, file_type, file_size,
                kwargs.get("status", "pending"),
                kwargs.get("file_path"), kwargs.get("hash"),
                kwargs.get("processing_state"),
                kwargs.get("processing_progress", 0),
                kwargs.get("processing_message"),
                kwargs.get("chunk_strategy", "parent_child"),
                kwargs.get("meta_info"),
            ))
        await conn.commit()
```

- [ ] **Step 7: Example of exclusive method — `get_or_create_kb`**

```python
async def get_or_create_kb(self, user_id: str, name: str,
                           description: str = "") -> tuple[str, bool]:
    pool = await self._get_pool()
    kb_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_KNOWLEDGE_BASE, (kb_id, user_id, name, description))
            await conn.commit()
            return kb_id, True
        except aiomysql.IntegrityError:
            await conn.rollback()
            existing_id = await self.get_kb_by_name(user_id, name)
            if existing_id is None:
                raise RuntimeError(...)
            return existing_id, False
```

- [ ] **Step 8: Rewrite all remaining methods** using the same pattern:
  - All 20+ methods follow the pattern: `await self._get_pool()` → `async with pool.acquire() as conn:` → `async with conn.cursor() as cursor:` → `await cursor.execute(...)` → `await conn.commit()` or `await cursor.fetchall()`
  - Remove `self._lock` (threading.RLock) — no longer needed
  - Remove `_ensure_connection()` — pool handles reconnect
  - Remove `_close_read_transaction()` — no aiomysql equivalent needed
  - Remove `__enter__` / `__exit__` — use `open()` / `close()` lifecycle

- [ ] **Step 9: Write the failing test**

Create `tests/test_mysql_db.py`:

```python
"""aiomysql 异步数据库操作测试。"""

import uuid
import pytest
from src.infra.mysql_db import MySQLDB


@pytest.mark.asyncio
async def test_create_and_get_kb():
    db = MySQLDB()
    user_id = "test-user"
    name = f"test-kb-{uuid.uuid4().hex[:8]}"
    kb_id, is_new = await db.get_or_create_kb(user_id, name)
    assert is_new is True
    found_id = await db.get_kb_by_name(user_id, name)
    assert found_id == kb_id
    await db.close()


@pytest.mark.asyncio
async def test_document_crud():
    db = MySQLDB()
    doc_id = str(uuid.uuid4())
    await db.add_document(doc_id, "test-kb", "test.pdf", "pdf", 100)
    docs = await db.get_documents("test-kb")
    doc_ids = [d["id"] for d in docs]
    assert doc_id in doc_ids
    await db.close()
```

- [ ] **Step 10: Run tests**

```bash
pytest tests/test_mysql_db.py -v --tb=short
```

Expected: 2 tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/infra/mysql_db.py tests/test_mysql_db.py pyproject.toml
git commit -m "feat(db): migrate MySQLDB from PyMySQL to aiomysql async connection pool"
```

---

### Task 3: Redis async — switch to redis.asyncio

**Files:**
- Modify: `src/chat_manager.py`
- Modify: `src/infra/user_auth.py`
- Modify: `src/app_service.py`
- Modify: `src/api/middleware.py`
- Test: `tests/test_chat_manager.py`

- [ ] **Step 1: Update `ChatManager._init_redis`**

```python
import redis.asyncio as redis_async

class ChatManager:
    def _init_redis(self, redis_url: str) -> None:
        try:
            self._redis = redis_async.from_url(redis_url, decode_responses=True)
            logger.info("ChatManager: Redis async client created")
        except Exception as e:
            self._redis = None
            self._in_memory = True
            logger.warning("ChatManager: Redis unavailable, using InMemory: {}", e)
```

- [ ] **Step 2: Add async Redis methods**

```python
    async def _ensure_redis_async(self) -> None:
        if self._in_memory:
            try:
                c = redis_async.from_url(self._redis_url, decode_responses=True)
                await c.ping()
                self._redis = c
                self._in_memory = False
            except Exception:
                pass
            return
        try:
            await self._redis.ping()
        except Exception:
            self._redis = None
            self._in_memory = True

    async def add_message_async(self, session_id, role, content, **kwargs):
        await self._ensure_redis_async()
        if self._in_memory:
            return self.add_message(session_id, role, content, **kwargs)
        msg = {"role": role, "content": content}
        key = self._session_key(session_id)
        try:
            await self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
            await self._redis.expire(key, self.ttl)
        except Exception as e:
            logger.error("add_message_async failed: {}", e)

    async def get_history_async(self, session_id):
        await self._ensure_redis_async()
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))
        key = self._session_key(session_id)
        raw = await self._redis.lrange(key, 0, -1)
        return [json.loads(m) for m in raw]

    async def clear_history_async(self, session_id):
        await self._ensure_redis_async()
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return
        key = self._session_key(session_id)
        await self._redis.delete(key)
```

- [ ] **Step 3: Add async variants in `UserAuth`**

```python
class UserAuth:
    @staticmethod
    async def store_token_async(rc, token, uid, ttl=TOKEN_TTL):
        await rc.setex(f"token:{token}", ttl, uid)
    @staticmethod
    async def get_user_id_from_token_async(rc, token):
        uid = await rc.get(f"token:{token}")
        return uid.decode() if uid else None
    @staticmethod
    async def delete_token_async(rc, token):
        await rc.delete(f"token:{token}")
```

- [ ] **Step 4: Update `AppService` + `middleware`**

```python
# app_service.py: replace import
import redis.asyncio as redis_async
self._redis = redis_async.from_url(REDIS_URL)

# middleware.py: use async Redis
uid = await UserAuth.get_user_id_from_token_async(svc.redis_client, token)
```

- [ ] **Step 5: Test + Commit**

```python
# tests/test_chat_manager.py
@pytest.mark.asyncio
async def test_async_in_memory():
    cm = ChatManager()
    cm._in_memory = True
    await cm.add_message_async("s1", "user", "hello")
    h = await cm.get_history_async("s1")
    assert len(h) == 1 and h[0]["content"] == "hello"
```

```bash
pytest tests/test_chat_manager.py -v --tb=short
git add src/chat_manager.py src/infra/user_auth.py src/app_service.py src/api/middleware.py tests/test_chat_manager.py
git commit -m "feat(redis): add async Redis variants via redis.asyncio"
```

---

### Task 4: Langfuse — swap custom REST code for official SDK

**Files:**
- Rewrite: `src/infra/langfuse_tracing.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add langfuse dependency + rewrite tracer**

```bash
# pyproject.toml
"langfuse>=2.60.0,<3.0.0"
pip install langfuse
```

Replace `src/infra/langfuse_tracing.py` with official SDK wrapper. API surface (`start_trace`, `end_trace`, `start_generation`, `end_generation`) stays identical so `rag_chain.py` needs zero changes.

```python
from langfuse import Langfuse

class LangfuseTracer:
    def __init__(self):
        self._client = None
        self._initialized = False
        try:
            from src.config import LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST
            if not LANGFUSE_SECRET_KEY or not LANGFUSE_PUBLIC_KEY:
                return
            self._client = Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY, host=LANGFUSE_HOST.rstrip("/"))
            self._initialized = True
        except Exception as e:
            logger.warning("LangfuseTracer init failed: %s", e)

    def start_trace(self, name, input_data=None, session_id=None):
        if not self._initialized:
            return None
        return self._client.trace(name=name, input=input_data, session_id=session_id).id

    def end_trace(self, trace_id, output=None):
        if not self._initialized or not trace_id:
            return
        try:
            self._client.trace(id=trace_id, output=output)
        except Exception as e:
            logger.warning("end_trace failed: %s", e)

    def start_generation(self, trace_id, name, input_data=None, model=None, model_parameters=None):
        if not self._initialized:
            return None
        return self._client.generation(name=name, trace_id=trace_id, input=input_data, model=model, model_parameters=model_parameters).id

    def end_generation(self, gen_id, trace_id, output=None, usage=None):
        if not self._initialized:
            return
        try:
            self._client.generation(id=gen_id, trace_id=trace_id, output=output, usage=usage)
        except Exception as e:
            logger.warning("end_generation failed: %s", e)
```

- [ ] **Step 2: Test + Commit**

```bash
pytest tests/test_langfuse.py -v --tb=short
ruff check src/infra/langfuse_tracing.py
git add src/infra/langfuse_tracing.py pyproject.toml
git commit -m "feat(langfuse): replace custom REST tracing with official SDK"
```

---

### Task 5: `_process_document_task` — fully async with granular to_thread()

**Files:**
- Modify: `src/api/routes/documents.py`
- Test: `tests/api/test_background_task.py`

- [ ] **Step 1: Verify Task 1-4 commits exist**

```bash
git log --oneline -5
```

- [ ] **Step 2: Add semaphore + replace `_process_document`**

At module level: `_process_semaphore = asyncio.Semaphore(3)`

Replace the old `async def _process_document(...)` with `async def _process_document_task(...)`. Key pattern — each sync call wrapped in `to_thread()`, DB calls direct `await`:

```python
async def _process_document_task(svc, kb_id, doc_id, minio_key, filename, ext):
    async with _process_semaphore:
        tmp_path = None
        try:
            # DB is async — direct await
            await svc.db.update_document_status(doc_id, "processing", processing_state="extracting", processing_progress=0)

            # MinIO download — sync, to_thread
            contents = await asyncio.to_thread(FileStore().download, minio_key)
            if contents is None:
                raise RuntimeError(f"Cannot download: {minio_key}")

            # Temp file — sync I/O, to_thread
            tmp = await asyncio.to_thread(tempfile.NamedTemporaryFile, delete=False, suffix=ext)
            tmp_path = tmp.name
            await asyncio.to_thread(tmp.write, contents)
            await asyncio.to_thread(tmp.close)

            # Parser — CPU + file I/O, to_thread
            parse_result = await asyncio.to_thread(svc.router.parse, tmp_path)
            if parse_result.is_scanned:
                await svc.db.update_document_status(doc_id, "failed", error_msg="扫描件暂不支持")
                return

            # Chunking — CPU, to_thread
            full_text = "\n".join(c.content for c in parse_result.chunks)
            strategy = await asyncio.to_thread(ChunkRouter.detect_strategy, full_text, parse_result.chunks)
            chunker = await asyncio.to_thread(ChunkRouter.get_chunker, strategy)
            chunks = await asyncio.to_thread(chunker.chunk, full_text, {"source": filename, "doc_id": doc_id})

            # Validation — CPU, to_thread
            chunk_data_list = [ChunkData(content=c["content"], metadata=c["metadata"]) for c in chunks]
            quality = await asyncio.to_thread(validate_chunks, chunk_data_list)

            # ChromaDB — sync, to_thread
            count = await asyncio.to_thread(svc.vector_store.add_chunks, kb_id, chunk_data_list, doc_id)

            # DB update — async, direct await
            await svc.db.update_document_status(doc_id, "ready", chunk_count=count, processing_state="completed", processing_progress=100)

        except Exception as e:
            await svc.db.update_document_status(doc_id, "failed", error_msg=str(e))
        finally:
            if tmp_path:
                await asyncio.to_thread(os.unlink, tmp_path)
```

- [ ] **Step 3: Update `upload_document` call**

```python
asyncio.create_task(_process_document_task(svc, kb_id, doc_id, minio_key, file.filename, ext))
```

- [ ] **Step 4: Test + Commit**

```python
# tests/api/test_background_task.py
@pytest.mark.asyncio
async def test_semaphore_value():
    from src.api.routes.documents import _process_semaphore
    assert _process_semaphore._value == 3
```

```bash
pytest tests/api/test_background_task.py -v --tb=short
ruff check src/api/routes/documents.py
git add src/api/routes/documents.py tests/api/test_background_task.py
git commit -m "feat(upload): create async _process_document_task with granular to_thread()"
```

---

### Task 6: AppService + Routes — adapt async chain

**Files:**
- Modify: `src/app_service.py` (methods become async)
- Modify: `src/api/routes/knowledge_base.py` (add await)
- Modify: `src/api/routes/sessions.py` (add await)
- Modify: `src/api/routes/auth.py` (add await)
- Modify: `src/api/routes/documents.py` (add await to read endpoints + upload sync calls)
- Modify: `src/api/main.py` (lifespan await init_db)

- [ ] **Step 1: Make AppService methods async**

```python
class AppService:
    async def list_knowledge_bases(self, user_id=""):
        return await self.db.get_all_kb(user_id)
    async def create_knowledge_base(self, name, description="", user_id=""):
        return await self.db.get_or_create_kb(user_id, name, description)
    async def delete_knowledge_base(self, kb_id):
        ok = await self.db.delete_kb(kb_id)
        await asyncio.to_thread(self.vector_store.delete_collection, kb_id)
        return (True, "已删除") if ok else (False, "不存在")
    async def get_documents(self, kb_id):
        return await self.db.get_documents(kb_id)
```

- [ ] **Step 2: Add await to all route files**

Each route that calls `svc.db.*` or `svc.list_knowledge_bases()` etc. — add `await` prefix.

For `upload_document` route, wrap `fs.upload()` with `to_thread()`:

```python
if not await asyncio.to_thread(fs.upload, minio_key, contents):
    raise HTTPException(status_code=500, detail="上传失败")
```

- [ ] **Step 3: Update main.py lifespan**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    svc = AppService()
    await svc.db.init_db()
    yield
    await svc.db.close()
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v --tb=short -x 2>&1 | tail -20
ruff check src/
```

Expected: pre-existing auth test failures only (401), no new failures.

- [ ] **Step 5: Commit**

```bash
git add src/app_service.py src/api/routes/ src/api/main.py
git commit -m "feat(async): adapt AppService and routes to full async chain"
```

---

### Task 7: rag_chain.py — fix non-hybrid search + simplify ChatManager MySQL

**Files:**
- Modify: `src/rag_chain.py`
- Modify: `src/chat_manager.py`

- [ ] **Step 1: Fix non-hybrid search**

In `async def search()`, find direct calls to `self.vector_store.similarity_search()` without `to_thread()` and wrap them:

```python
# Before (non-hybrid path):
results = self.vector_store.similarity_search(kb_id, query, k=TOP_K_RETRIEVAL)

# After:
results = await asyncio.to_thread(self.vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL)
```

Same for `similarity_search_all()`.

- [ ] **Step 2: Simplify ChatManager MySQL calls**

Replace `asyncio.to_thread(self._mysql_db.create_session, ...)` with direct `await self._mysql_db.create_session(...)` (since MySQLDB is now async). Same for `save_message`.

- [ ] **Step 3: Verify**

```bash
ruff check src/rag_chain.py src/chat_manager.py
```

- [ ] **Step 4: Commit**

```bash
git add src/rag_chain.py src/chat_manager.py
git commit -m "fix(async): wrap non-hybrid search in to_thread, simplify ChatManager MySQL calls"
```

---
---

## 实施后记：审查发现与修正记录

以下为计划执行过程中通过代码审查发现的偏差，已全部修复。

### 1. `aiomysql.create_pool()` 参数名修正

- **提交**: `af76230 fix(db): rename pool_recycle to recycle`
- **问题**: 计划中`pool_recycle=3600`，aiomysql 正确参数名是`recycle=3600`。连接回收功能实际未生效。
- **影响**: 低。池会通过 autoping 机制处理空闲连接。
- **文件**: `src/infra/mysql_db.py:96`

### 2. `LangfuseTracer.end_generation()` 空值守卫恢复

- **提交**: `3372263 fix(langfuse): restore null guards in end_generation`
- **问题**: 换 SDK 后`gen_id/trace_id`空检查被简化掉。虽不崩溃，但行为退化。
- **影响**: 低。防御性编程恢复。
- **文件**: `src/infra/langfuse_tracing.py:136`

### 3. 预存问题（非本次改动引起）

- **auth middleware 401**: 现有测试因 auth middleware 返回 401 失败，Phase 4 引入鉴权后的遗留问题。
- **`src/cli/check_chunks.py`**: ruff 报 4 个 E402/E741 错误，不在本次改动范围。
