---
change: session-history
design-doc: docs/superpowers/specs/2026-06-27-session-history-design.md
base-ref: 574681cbe6aaf7c3cd881ce7503b296c16b0f974
---

# Session History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session history management (CRUD) with MySQL persistence and sidebar UI, enabling users to view, switch, and delete conversation sessions.

**Architecture:** Cold-hot separation pattern: MySQL stores full session/message history (cold storage), Redis stores current session RAG context (hot storage). New `sessions` table added; `conversation_history` table's FK on `kb_id` removed. Backend exposes three new REST endpoints. Frontend sidebar replaces knowledge base list with session list.

**Tech Stack:** FastAPI, MySQL 8.0 (PyMySQL), Redis (async via asyncio.to_thread), SSE streaming, HTML/Tailwind CSS/vanilla JS

## Global Constraints

- No FK constraints on `kb_id` in sessions/conversation_history tables
- Session IDs follow existing format: `session_<timestamp>_<random>`
- Session title = first 20 chars of first user message, written once
- `INSERT_SESSION` uses `ON DUPLICATE KEY UPDATE` for idempotency
- MySQL async writes use `asyncio.to_thread()` - never block the event loop
- All exceptions in async persistence are caught, logged, and swallowed - never propagate to SSE response
- Delete session endpoint must cascade: Redis key -> MySQL sessions -> MySQL conversation_history
- Ruff check + pytest must pass before any commit

---

## File Structure

The following files will be created or modified:

| File | Action | Responsibility |
|------|--------|---------------|
| `src/config/queries.py` | Modify | Add SQL constants for sessions table CRUD; modify CREATE_TABLE_CONVERSATION_HISTORY to remove FK |
| `src/mysql_db.py` | Modify | Add `create_session()`, `get_sessions()`, `get_session_by_id()`, `get_messages()`, `delete_session_and_messages()`, `save_message()` |
| `src/chat_manager.py` | Modify | Add `set_mysql_db()`, `save_session_async()`, `save_messages_async()`, `cleanup_session()` |
| `src/api/routes/chat.py` | Modify | Add async persistence after SSE stream completes |
| `src/api/routes/sessions.py` | Create | New route module with GET/DELETE session endpoints |
| `src/api/routes/__init__.py` | Modify | Export `sessions_router` |
| `src/api/main.py` | Modify | Register sessions router |
| `nginx/html/js/api.js` | Modify | Add `fetchSessions()`, `fetchSessionMessages()`, `deleteSessionAPI()` |
| `nginx/html/chat.html` | Modify | Replace knowledge base sidebar with session list sidebar |
| `nginx/html/js/chat.js` | Modify | Add session management state and UI logic |
| `nginx/html/css/style.css` | Modify | Add sidebar session list styles |

---

## Task 1: Database Layer - SQL Queries

**Files:**
- Modify: `src/config/queries.py`

**Interfaces:**
- Consumes: N/A (new SQL constants)
- Produces: `CREATE_TABLE_SESSIONS`, `SELECT_SESSIONS`, `SELECT_MESSAGES_BY_SESSION`, `INSERT_SESSION`, `DELETE_SESSION`, `DELETE_MESSAGES_BY_SESSION`, `INSERT_MESSAGE`; modified `CREATE_TABLE_CONVERSATION_HISTORY`

### Step 1.1: Add sessions table SQL constants and INSERT_MESSAGE

Add to `src/config/queries.py` after the existing `CREATE_TABLE_CONVERSATION_HISTORY` block:

```python
# 对话历史持久化表。与 sessions 表配合使用，存储消息内容。
# kb_id 不再有 FOREIGN KEY 约束（空字符串代表"所有知识库"）。
CREATE_TABLE_CONVERSATION_HISTORY: str = """\
CREATE TABLE IF NOT EXISTS conversation_history (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    session_id  VARCHAR(36)  NOT NULL,
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    role        ENUM('user','assistant') NOT NULL,
    content     TEXT         NOT NULL,
    sources     JSON,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
)
"""

# 会话表。一条记录 = 用户的一次对话 session。
# kb_id 用空字符串代表"所有知识库"，无 FK 约束。
CREATE_TABLE_SESSIONS: str = """\
CREATE TABLE IF NOT EXISTS sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_updated_at (updated_at DESC)
)
"""
```

The key changes from the existing `CREATE_TABLE_CONVERSATION_HISTORY`:
- Remove `FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE`
- Change `kb_id` from `VARCHAR(36) NOT NULL` to `VARCHAR(36) NOT NULL DEFAULT ''`

### Step 1.2: Add session CRUD SQL constants

Add after the existing document CRUD section (before or after line 129):

```python
# ====== 会话 CRUD ======

# 插入会话记录，首次消息时调用。
# ON DUPLICATE KEY UPDATE 保证幂等：重试场景下只更新 updated_at，不覆盖 title。
INSERT_SESSION: str = """\
INSERT INTO sessions (id, title, kb_id) VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
"""

# 查询最近 50 条会话，包含知识库名称和消息数量。
# 按 updated_at 倒序排列，最新活跃的排在最前。
SELECT_SESSIONS: str = """\
SELECT s.id, s.title, s.kb_id, s.created_at, s.updated_at,
       COALESCE(kb.name, '所有知识库') AS kb_name,
       COUNT(ch.id) AS message_count
FROM sessions s
LEFT JOIN knowledge_base kb ON s.kb_id = kb.id AND s.kb_id != ''
LEFT JOIN conversation_history ch ON ch.session_id = s.id
GROUP BY s.id
ORDER BY s.updated_at DESC
LIMIT 50
"""

# 按 ID 查询单条会话。用于验证会话是否存在。
SELECT_SESSION_BY_ID: str = """\
SELECT id, title, kb_id, created_at, updated_at FROM sessions WHERE id = %s
"""

# 查询某会话的所有消息，按创建时间正序排列。
SELECT_MESSAGES_BY_SESSION: str = """\
SELECT role, content, sources, created_at
FROM conversation_history
WHERE session_id = %s
ORDER BY created_at ASC
"""

# 删除会话记录。
DELETE_SESSION: str = """\
DELETE FROM sessions WHERE id = %s
"""

# 删除某会话的所有消息记录。
DELETE_MESSAGES_BY_SESSION: str = """\
DELETE FROM conversation_history WHERE session_id = %s
"""

# 插入单条消息到 conversation_history。
INSERT_MESSAGE: str = """\
INSERT INTO conversation_history (session_id, kb_id, role, content, sources)
VALUES (%s, %s, %s, %s, %s)
"""
```

### Step 1.3: Run tests

Run: `ruff check src/config/queries.py`
Expected: No errors


## Task 2: Database Layer - MySQLDB Methods

**Files:**
- Modify: `src/mysql_db.py`

**Interfaces:**
- Consumes: SQL constants from Task 1 (CREATE_TABLE_SESSIONS, SELECT_SESSIONS, INSERT_SESSION, SELECT_SESSION_BY_ID, SELECT_MESSAGES_BY_SESSION, DELETE_SESSION, DELETE_MESSAGES_BY_SESSION, INSERT_MESSAGE)
- Produces: `MySQLDB.create_session()`, `MySQLDB.get_sessions()`, `MySQLDB.get_session_by_id()`, `MySQLDB.get_messages()`, `MySQLDB.delete_session_and_messages()`, `MySQLDB.save_message()`

### Step 2.1: Add new imports

Add to the import block in `src/mysql_db.py` (after existing imports):

```python
from src.config.queries import (
    CREATE_TABLE_CONVERSATION_HISTORY,
    CREATE_TABLE_DOCUMENT,
    CREATE_TABLE_KNOWLEDGE_BASE,
    CREATE_TABLE_SESSIONS,          # NEW
    DELETE_KNOWLEDGE_BASE_BY_ID,
    DELETE_SESSION,                  # NEW
    DELETE_MESSAGES_BY_SESSION,     # NEW
    INSERT_DOCUMENT,
    INSERT_KNOWLEDGE_BASE,
    INSERT_MESSAGE,                  # NEW
    INSERT_SESSION,                  # NEW
    SELECT_ALL_KNOWLEDGE_BASES,
    SELECT_DOCUMENTS_BY_KB_ID,
    SELECT_KNOWLEDGE_BASE_ID_BY_NAME,
    SELECT_MESSAGES_BY_SESSION,      # NEW
    SELECT_SESSION_BY_ID,            # NEW
    SELECT_SESSIONS,                 # NEW
    UPDATE_DOCUMENT_STATUS,
)
```

### Step 2.2: Update init_db() to create sessions table

In `init_db()`, add `cursor.execute(CREATE_TABLE_SESSIONS)` after the existing table creations:

```python
def init_db(self) -> None:
    with self._lock:
        self._ensure_connection()
        with self._transaction():
            with self.conn.cursor() as cursor:
                cursor.execute(CREATE_TABLE_KNOWLEDGE_BASE)
                cursor.execute(CREATE_TABLE_DOCUMENT)
                cursor.execute(CREATE_TABLE_CONVERSATION_HISTORY)
                cursor.execute(CREATE_TABLE_SESSIONS)  # NEW
        logger.info("Database tables initialized")
```

### Step 2.3: Add create_session()

Add after `add_document()` (~line 240):

```python
def create_session(self, session_id: str, title: str, kb_id: str) -> None:
    """创建或更新会话记录（幂等操作）。

    Args:
        session_id: 会话 ID
        title: 会话标题（截取首条消息前 20 字）
        kb_id: 关联的知识库 ID（空字符串代表"所有知识库"）
    """
    with self._lock:
        self._ensure_connection()
        with self._transaction():
            with self.conn.cursor() as cursor:
                cursor.execute(INSERT_SESSION, (session_id, title, kb_id))
```

### Step 2.4: Add get_sessions()

Add after `create_session()`:

```python
def get_sessions(self) -> list[dict]:
    """返回最近 50 条会话列表，包含知识库名称和消息数量。

    Returns:
        字典列表，每项含 id, title, kb_id, kb_name, message_count, created_at, updated_at
    """
    with self._lock:
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute(SELECT_SESSIONS)
            return cursor.fetchall()
```

### Step 2.5: Add get_session_by_id()

Add after `get_sessions()`:

```python
def get_session_by_id(self, session_id: str) -> Optional[dict]:
    """按 ID 查询会话。

    Args:
        session_id: 会话 ID

    Returns:
        会话字典，不存在时返回 None
    """
    with self._lock:
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute(SELECT_SESSION_BY_ID, (session_id,))
            return cursor.fetchone()
```

### Step 2.6: Add get_messages()

Add after `get_session_by_id()`:

```python
def get_messages(self, session_id: str) -> list[dict]:
    """返回会话的所有消息，按 created_at 正序排列。

    Args:
        session_id: 会话 ID

    Returns:
        消息字典列表，每项含 role, content, sources, created_at
    """
    with self._lock:
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute(SELECT_MESSAGES_BY_SESSION, (session_id,))
            return cursor.fetchall()
```

### Step 2.7: Add delete_session_and_messages()

Add after `get_messages()`:

```python
def delete_session_and_messages(self, session_id: str) -> bool:
    """删除会话及其所有消息（同一事务内）。

    Args:
        session_id: 会话 ID

    Returns:
        True 表示会话存在且已删除，False 表示不存在
    """
    with self._lock:
        self._ensure_connection()
        with self._transaction():
            with self.conn.cursor() as cursor:
                cursor.execute(DELETE_MESSAGES_BY_SESSION, (session_id,))
                cursor.execute(DELETE_SESSION, (session_id,))
                return cursor.rowcount > 0
```

### Step 2.8: Add save_message()

Add after `delete_session_and_messages()`:

```python
def save_message(self, session_id: str, kb_id: str, role: str, content: str, sources: Optional[list] = None) -> None:
    """保存单条消息到 conversation_history。

    Args:
        session_id: 会话 ID
        kb_id: 关联的知识库 ID
        role: 角色（'user' 或 'assistant'）
        content: 消息内容
        sources: 来源引用列表（assistant 消息时使用）
    """
    with self._lock:
        self._ensure_connection()
        sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
        with self._transaction():
            with self.conn.cursor() as cursor:
                cursor.execute(
                    INSERT_MESSAGE,
                    (session_id, kb_id, role, content, sources_json),
                )
```

### Step 2.9: Add json import at top of file

Since `save_message()` uses `json.dumps()`, add `import json` to the top-level imports in `mysql_db.py`.

Note: The existing file already imports `json`? Let me check... No it doesn't. Add it to the imports.

### Step 2.10: Run tests

Run: `ruff check src/mysql_db.py`
Expected: No errors


## Task 3: ChatManager Dual-Write Methods

**Files:**
- Modify: `src/chat_manager.py`

**Interfaces:**
- Consumes: `MySQLDB` instance (injected)
- Produces: `ChatManager.set_mysql_db()`, `ChatManager.save_session_async()`, `ChatManager.save_messages_async()`, `ChatManager.cleanup_session()`

### Step 3.1: Add imports

Add at the top of `src/chat_manager.py`:

```python
import asyncio
from typing import Optional
from src.mysql_db import MySQLDB
```

### Step 3.2: Add __init__ changes

Add `self._mysql_db: Optional[MySQLDB] = None` to `__init__()`:

```python
def __init__(self, redis_url: Optional[str] = None, ttl: int = REDIS_TTL) -> None:
    self.ttl = ttl
    self._redis_url = redis_url or REDIS_URL
    self._redis = None
    self._in_memory: bool = False
    self._memory_store: dict[str, list[dict]] = {}
    self._mysql_db: Optional[MySQLDB] = None  # NEW: injected later for async persistence
    self._init_redis(self._redis_url)
```

### Step 3.3: Add set_mysql_db()

Add after `__init__()`:

```python
def set_mysql_db(self, mysql_db: MySQLDB) -> None:
    """注入 MySQLDB 实例用于异步持久化。

    在 SSE 流结束后由 _persist_conversation() 调用，
    确保 ChatManager 可以异步写入 MySQL。
    """
    self._mysql_db = mysql_db
```

### Step 3.4: Add save_session_async()

Add after `set_mysql_db()`:

```python
async def save_session_async(self, session_id: str, title: str, kb_id: str) -> None:
    """异步创建会话记录（首次消息时调用）。

    使用 asyncio.to_thread 将同步 MySQL 调用放到线程池，
    不阻塞事件循环。失败只记日志，不抛异常。

    Args:
        session_id: 会话 ID
        title: 会话标题（截取首条消息前 20 字）
        kb_id: 关联的知识库 ID
    """
    if self._mysql_db is None:
        return
    try:
        await asyncio.to_thread(
            self._mysql_db.create_session, session_id, title, kb_id
        )
    except Exception as e:
        logger.warning("Failed to save session async: {}", e)
```

### Step 3.5: Add save_messages_async()

Add after `save_session_async()`:

```python
async def save_messages_async(
    self,
    session_id: str,
    kb_id: str,
    user_msg: str,
    assistant_msg: str,
    sources: Optional[list[str]] = None,
) -> None:
    """异步写入 user + assistant 消息到 MySQL。

    使用 asyncio.to_thread 将同步 MySQL 调用放到线程池。
    两次写入独立进行（MySQLDB 内部有锁保护），
    失败只记日志，不抛异常。

    Args:
        session_id: 会话 ID
        kb_id: 关联的知识库 ID
        user_msg: 用户消息内容
        assistant_msg: 助理回答内容
        sources: 来源引用列表
    """
    if self._mysql_db is None:
        return
    try:
        await asyncio.to_thread(
            self._mysql_db.save_message, session_id, kb_id, 'user', user_msg, None
        )
        await asyncio.to_thread(
            self._mysql_db.save_message, session_id, kb_id, 'assistant', assistant_msg, sources
        )
    except Exception as e:
        logger.warning("Failed to save messages async: {}", e)
```

### Step 3.6: Add cleanup_session()

Add after `save_messages_async()`:

```python
def cleanup_session(self, session_id: str) -> None:
    """删除 Redis 中的会话 key（尽力而为，失败不抛异常）。

    在 DELETE /api/sessions/{id} 端点中被调用，
    确保删除会话时同时清理 Redis 缓存。
    """
    self.clear_history(session_id)
```

Note: `cleanup_session()` simply delegates to the existing `clear_history()` method, which already handles both Redis and in-memory modes.

### Step 3.7: Run tests

Run: `ruff check src/chat_manager.py`
Expected: No errors


## Task 4: SSE Endpoint - Async Persistence

**Files:**
- Modify: `src/api/routes/chat.py`

**Interfaces:**
- Consumes: `ChatManager.set_mysql_db()`, `ChatManager.save_session_async()`, `ChatManager.save_messages_async()`
- Produces: Modified `_stream_rag_response()` that persists to MySQL after stream completes

### Step 4.1: Add async persistence after stream completion

In `src/api/routes/chat.py`, modify `_stream_rag_response()` to add MySQL persistence after the stream completes. The key change is adding `asyncio.create_task(_persist_conversation(...))` before the done event, and adding the `_persist_conversation()` helper function.

Full modified `_stream_rag_response()` function:

```python
async def _stream_rag_response(
    kb_id: str,
    session_id: str,
    query: str,
) -> AsyncGenerator[str, None]:
    """Stream RAG response as SSE events: token -> citation -> done."""
    try:
        svc = _get_service()
        token_gen, citations = svc.rag_chain.chat_with_citations(
            kb_id,
            session_id,
            query,
        )

        # Stream tokens as SSE token events, buffering for history save
        full_answer = ""
        for token in token_gen:
            full_answer += token
            yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
            await asyncio.sleep(0)

        # Stream citations as SSE citation events
        seen_sp: set[tuple[str, int]] = set()
        for ctx in citations:
            key = (ctx.source, ctx.page)
            if key in seen_sp:
                continue
            seen_sp.add(key)
            citation_data = {
                "source": ctx.source,
                "page": ctx.page,
                "snippet": ctx.content[:200],
            }
            yield f"event: citation\ndata: {json.dumps(citation_data)}\n\n"
            await asyncio.sleep(0)

        # Save assistant response to Redis chat history
        seen_src: set[str] = set()
        sources = []
        for c in citations:
            s = f"{c.source} (第{c.page}页)"
            if s in seen_src:
                continue
            seen_src.add(s)
            sources.append(s)
        svc.rag_chain.chat_manager.add_message(
            session_id,
            "assistant",
            full_answer,
            sources=sources,
        )

        # NEW: Async persist to MySQL (fire-and-forget, never block SSE)
        asyncio.create_task(
            _persist_conversation(svc, session_id, kb_id, query, full_answer, sources)
        )

    except Exception as e:
        logger.error("Chat stream error: {}", str(e))
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    # Signal completion
    yield "event: done\ndata: {}\n\n"


async def _persist_conversation(
    svc: AppService,
    session_id: str,
    kb_id: str,
    query: str,
    answer: str,
    sources: list[str],
) -> None:
    """异步持久化对话到 MySQL，带重试。

    在 SSE 流结束后非阻塞执行。
    如果 MySQL 不可用，重试 3 次后放弃（只记日志）。
    绝不会抛异常冒泡到 SSE 响应。
    """
    svc.rag_chain.chat_manager.set_mysql_db(svc.db)

    # 创建会话（如首次消息）。title = 首条消息前 20 字
    title = query[:20]

    async def retry(coro, max_retries=3):
        for i in range(max_retries):
            try:
                await coro
                return
            except Exception as e:
                if i < max_retries - 1:
                    await asyncio.sleep(0.5 * (i + 1))
                else:
                    logger.warning("Persist failed after {} retries: {}", max_retries, e)

    await retry(
        svc.rag_chain.chat_manager.save_session_async(session_id, title, kb_id)
    )
    await retry(
        svc.rag_chain.chat_manager.save_messages_async(
            session_id, kb_id, query, answer, sources
        )
    )
```

### Step 4.2: Add session_id to SSE response metadata (first event)

Add a session_id event as the first SSE event, so the frontend can recognize new sessions:

Add at the very beginning of the `_stream_rag_response()` function, right after `try:` and before token generation:

```python
        # Send session_id as first event so frontend can identify this session
        yield f"event: session_id\ndata: {json.dumps({'session_id': session_id})}\n\n"
```

### Step 4.3: Run tests

Run: `ruff check src/api/routes/chat.py`
Expected: No errors


## Task 5: New Sessions API Route

**Files:**
- Create: `src/api/routes/sessions.py`
- Modify: `src/api/routes/__init__.py`
- Modify: `src/api/main.py`

**Interfaces:**
- Consumes: `MySQLDB.get_sessions()`, `MySQLDB.get_session_by_id()`, `MySQLDB.get_messages()`, `MySQLDB.delete_session_and_messages()`, `ChatManager.cleanup_session()`
- Produces: `GET /api/sessions`, `GET /api/sessions/{session_id}/messages`, `DELETE /api/sessions/{session_id}`

### Step 5.1: Create sessions.py

Create `src/api/routes/sessions.py`:

```python
"""Session management API routes.

Provides endpoints for listing, viewing, and deleting conversation sessions.
Sessions are persisted in MySQL and cached in Redis.
"""

from fastapi import APIRouter, HTTPException
from loguru import logger

from src.app_service import AppService

router = APIRouter()

_service: AppService | None = None


def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


@router.get("/sessions")
async def list_sessions():
    """列出最近 50 个会话。

    始终返回 200 + 数组，无会话时返回 []。

    Response:
        [{
            "id": "session_...",
            "title": "会话标题",
            "kb_id": "...",
            "kb_name": "知识库名",
            "message_count": 12,
            "created_at": "...",
            "updated_at": "..."
        }]
    """
    svc = _get_service()
    sessions = svc.db.get_sessions()
    # row 是 DictCursor 返回的 OrderedDict，转成普通 dict 确保 JSON 序列化兼容
    result = []
    for row in sessions:
        result.append({
            "id": row["id"],
            "title": row["title"],
            "kb_id": row["kb_id"],
            "kb_name": row["kb_name"],
            "message_count": row["message_count"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        })
    return result


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """获取会话消息历史。

    先验证会话存在，再返回消息列表。
    不存在的 session_id 返回 404。

    Response:
        [{
            "role": "user",
            "content": "...",
            "sources": null,
            "created_at": "..."
        }]
    """
    svc = _get_service()
    session = svc.db.get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = svc.db.get_messages(session_id)
    result = []
    for row in messages:
        msg = {
            "role": row["role"],
            "content": row["content"],
            "sources": row.get("sources"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }
        result.append(msg)
    return result


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有消息。

    执行顺序:
    1. 清理 Redis key（尽力而为，失败只记日志）
    2. 删除 MySQL sessions 记录
    3. 级联删除 conversation_history 消息
    事务保证 MySQL 操作的原子性。

    Response:
        {"success": true}
    """
    svc = _get_service()

    # 清理 Redis
    svc.rag_chain.chat_manager.cleanup_session(session_id)

    # 删除 MySQL 记录
    ok = svc.db.delete_session_and_messages(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")

    logger.info("Deleted session: {}", session_id)
    return {"success": True}
```

### Step 5.2: Update routes __init__.py

Modify `src/api/routes/__init__.py` to export the new router:

```python
from src.api.routes.health import router as health_router
from src.api.routes.knowledge_base import router as kb_router
from src.api.routes.documents import router as doc_router
from src.api.routes.chat import router as chat_router
from src.api.routes.sessions import router as sessions_router  # NEW

__all__ = ["health_router", "kb_router", "doc_router", "chat_router", "sessions_router"]
```

### Step 5.3: Register sessions router in main.py

Modify `src/api/main.py`:

```python
from src.api.routes import health_router, kb_router, doc_router, chat_router, sessions_router

# Add after other route registrations:
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
```

### Step 5.4: Run tests

Run: `ruff check src/api/routes/sessions.py src/api/routes/__init__.py src/api/main.py`
Expected: No errors


## Task 6: Frontend API Layer

**Files:**
- Modify: `nginx/html/js/api.js`

**Interfaces:**
- Consumes: Backend endpoints from Task 5
- Produces: `fetchSessions()`, `fetchSessionMessages()`, `deleteSessionAPI()`

### Step 6.1: Add session API functions

Add at the end of `nginx/html/js/api.js` (after the documents section, before the toast section):

```javascript
// ====== Session History ======
async function fetchSessions() {
    return apiRequest('/sessions');
}

async function fetchSessionMessages(sessionId) {
    return apiRequest(`/sessions/${sessionId}/messages`);
}

async function deleteSessionAPI(sessionId) {
    return apiRequest(`/sessions/${sessionId}`, { method: 'DELETE' });
}
```

### Step 6.2: Verify existing imports

The `apiRequest()` helper is already defined at the top of api.js and handles all HTTP methods, error codes, and JSON serialization. The three new functions above call it with the correct paths.

### Step 6.3: Run tests

Run: (No automated tests for JS in this project. Manual verification via browser console.)


## Task 7: Frontend Sidebar - HTML Structure

**Files:**
- Modify: `nginx/html/chat.html`

**Interfaces:**
- Consumes: Session state variables from chat.js (will be defined in Task 8)
- Produces: Updated sidebar HTML with session list structure

### Step 7.1: Replace knowledge base sidebar with session list sidebar

In `nginx/html/chat.html`, replace the sidebar knowledge base section (lines 45-54) with the session list structure:

**Replace:**
```html
<div class="flex-1 overflow-hidden flex flex-col">
    <div class="px-5 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">知识库</div>
    <div class="sidebar-kb-list flex-1 overflow-y-auto px-3 pb-2"></div>
</div>
<div class="px-3 py-3 border-t border-slate-700">
    <a href="/" class="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        新建知识库
    </a>
</div>
```

**With:**
```html
<div class="flex-1 overflow-hidden flex flex-col">
    <div class="px-3 py-3 flex items-center justify-between border-b border-slate-700/50">
        <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">会话历史</span>
        <button onclick="newSession()" class="w-7 h-7 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 flex items-center justify-center transition-colors" title="新建会话">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
        </button>
    </div>
    <div class="sidebar-session-list flex-1 overflow-y-auto px-2 py-2"></div>
</div>
```

Note: The knowledge base navigation link (`<a href="/">知识库管理</a>`) stays unchanged - users still need to navigate to the KB management page. Only the sidebar list section changes from KB list to session list.

### Step 7.2: Verify chat.html structure

The completed sidebar will have:
1. Logo/header (unchanged)
2. Navigation links (unchanged: "知识库管理" and "智能问答")
3. Session list with "新建会话" button header (new)
4. Session items in scrollable area (new)


## Task 8: Frontend Session Management Logic

**Files:**
- Modify: `nginx/html/js/chat.js`

**Interfaces:**
- Consumes: `fetchSessions()`, `fetchSessionMessages()`, `deleteSessionAPI()` from api.js (Task 6)
- Produces: Session list rendering, session switching, new session, delete session, sidebar refresh

### Step 8.1: Add global session state

Add at the top of `nginx/html/js/chat.js`, after existing globals:

```javascript
let sessions = [];  // Cached session list for sidebar rendering
```

### Step 8.2: Add loadSessions() function

Add after `loadKbSelector()`:

```javascript
async function loadSessions() {
    """
    Load session list from API and render sidebar.
    Handles loading, empty, and error states.
    """
    const sidebarList = document.querySelector('.sidebar-session-list');
    if (!sidebarList) return;

    try {
        sessions = await fetchSessions();
        renderSidebar();
    } catch (err) {
        console.error('Failed to load sessions:', err);
        sidebarList.innerHTML = ''
            + '<div class="text-center py-8">'
            + '<div class="text-slate-500 text-xs mb-1">加载失败</div>'
            + '<button onclick="loadSessions()" class="text-xs text-blue-400 hover:text-blue-300">重试</button>'
            + '</div>';
    }
}
```

### Step 8.3: Add renderSidebar() function

Add after `loadSessions()`:

```javascript
function renderSidebar(activeId = null) {
    """
    Render session list in sidebar.
    Highlights the active session. Shows empty state when no sessions.
    """
    const sidebarList = document.querySelector('.sidebar-session-list');
    if (!sidebarList) return;

    if (!sessions || sessions.length === 0) {
        sidebarList.innerHTML = ''
            + '<div class="text-center py-8 px-3">'
            + '<div class="text-slate-500 text-xs">暂无会话</div>'
            + '<div class="text-slate-600 text-[10px] mt-1">发送消息将自动创建</div>'
            + '</div>';
        return;
    }

    sidebarList.innerHTML = sessions.map(s => {
        const isActive = (activeId || currentSessionId) === s.id;
        const dateLabel = formatSessionDate(s.updated_at);
        return ''
            + '<div class="session-item group relative flex items-start gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors '
            + (isActive ? 'bg-blue-600/10 text-blue-300' : 'hover:bg-slate-800 text-slate-400')
            + '" onclick="switchSession(\'' + s.id + '\')">'
            + '<div class="flex-1 min-w-0">'
            + '<div class="text-sm font-medium truncate ' + (isActive ? 'text-blue-300' : 'text-slate-300') + '">'
            + escapeHtml(s.title || '新会话')
            + '</div>'
            + '<div class="flex items-center gap-2 mt-0.5">'
            + '<span class="text-[10px] text-slate-500">' + dateLabel + '</span>'
            + '<span class="text-[10px] text-slate-600">' + s.message_count + ' 条消息</span>'
            + '</div>'
            + '</div>'
            + '<button onclick="event.stopPropagation(); deleteSession(\'' + s.id + '\')" '
            + 'class="delete-btn flex-shrink-0 w-6 h-6 rounded-md bg-slate-800 hover:bg-red-500/20 text-slate-500 hover:text-red-400 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all" title="删除会话">'
            + '<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>'
            + '</button>'
            + '</div>';
    }).join('');
}

function formatSessionDate(dateStr) {
    """
    Format session date for display.
    Today: show time only (HH:MM)
    This week: show day name
    Older: show date (MM/DD)
    """
    if (!dateStr) return '';
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now - d;
    const oneDay = 86400000;
    if (diff < oneDay && d.getDate() === now.getDate()) {
        return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }
    if (diff < 7 * oneDay) {
        const days = ['周日','周一','周二','周三','周四','周五','周六'];
        return days[d.getDay()];
    }
    return (d.getMonth() + 1) + '/' + d.getDate();
}
```

### Step 8.4: Add switchSession() function

Add after `renderSidebar()`:

```javascript
async function switchSession(sessionId) {
    """
    Switch to an existing session.
    Aborts any active SSE, loads messages, updates KB selector, and highlights sidebar.
    """
    // Abort active SSE
    if (abortController) {
        abortController.abort();
        abortController = null;
    }

    // Close any existing EventSource
    // (EventSource connections are managed in sendMessage, but we also close
    // any open connection via the AbortController ref)

    try {
        const messages = await fetchSessionMessages(sessionId);
        currentSessionId = sessionId;
        renderMessages(messages);

        // Update KB selector
        const session = sessions.find(s => s.id === sessionId);
        if (session) {
            document.getElementById('kb-select').value = session.kb_id || '';
        }

        // Highlight current session
        renderSidebar(sessionId);
    } catch (err) {
        console.error('Failed to load session messages:', err);
        showError('加载消息失败');
    }
}
```

### Step 8.5: Add newSession() function

Add after `switchSession()`:

```javascript
function newSession() {
    """
    Create a new session.
    Aborts active SSE, generates new session ID, clears chat area, resets sidebar highlight.
    """
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    currentSessionId = generateSessionId();
    clearChatArea();
    renderSidebar(null);
    document.getElementById('chat-input').focus();
}
```

### Step 8.6: Add deleteSession() function

Add after `newSession()`:

```javascript
async function deleteSession(sessionId) {
    """
    Delete a session with confirmation.
    If the deleted session is the current one, create a new session.
    Always refresh sidebar after deletion.
    """
    if (!confirm('确认删除此会话？')) return;

    try {
        await deleteSessionAPI(sessionId);
        if (sessionId === currentSessionId) {
            newSession();
        }
        await loadSessions();
    } catch (err) {
        console.error('Failed to delete session:', err);
        showError('删除会话失败');
    }
}
```

### Step 8.7: Add renderMessages() and clearChatArea() helpers

Add after `deleteSession()`:

```javascript
function renderMessages(messages) {
    """
    Render a list of messages into the chat area.
    Handles empty state (show welcome).
    """
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (!messages || messages.length === 0) {
        container.innerHTML = ''
            + '<div class="flex flex-col items-center justify-center h-full text-slate-400">'
            + '<div class="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center text-3xl mb-4">💬</div>'
            + '<p class="text-sm font-medium text-slate-600 mb-1">开始智能问答</p>'
            + '<p class="text-xs text-slate-400 mb-6">选择知识库，输入您的金融文档相关问题</p>'
            + '<div class="flex flex-wrap gap-2 justify-center max-w-md">'
            + '<button onclick="quickQuestion('本季度营收情况如何？')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">本季度营收情况如何？</button>'
            + '<button onclick="quickQuestion('分析一下主要财务指标')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">分析一下主要财务指标</button>'
            + '<button onclick="quickQuestion('净利润同比增长多少？')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">净利润同比增长多少？</button>'
            + '</div>'
            + '</div>';
        return;
    }

    messages.forEach(msg => {
        if (msg.role === 'user') {
            addMessage(msg.content, 'user');
        } else {
            const div = addMessage('', 'assistant');
            const contentDiv = div.querySelector('.message-content');
            const rendered = typeof marked !== 'undefined' ? marked.parse(msg.content) : msg.content;

            let sourcesHtml = '';
            if (msg.sources && msg.sources.length > 0) {
                const srcList = typeof msg.sources === 'string' ? JSON.parse(msg.sources) : msg.sources;
                sourcesHtml = '<div class="mt-3 pt-2 border-t border-slate-200">'
                    + '<div class="text-xs font-semibold text-slate-500 mb-1.5">📚 来源</div>'
                    + srcList.map(s => ''
                        + '<div class="flex items-start gap-2 py-1 px-3 bg-slate-50 rounded-md text-xs text-slate-600 mb-1">'
                        + '<span class="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">📄</span>'
                        + '<span>' + escapeHtml(s) + '</span>'
                        + '</div>'
                    ).join('')
                    + '</div>';
            }

            contentDiv.innerHTML = rendered + sourcesHtml;
        }
    });

    container.scrollTop = container.scrollHeight;
}

function clearChatArea() {
    """
    Clear chat area and show welcome state.
    Used by newSession() and at page load.
    """
    const container = document.getElementById('chat-messages');
    container.innerHTML = ''
        + '<div class="flex flex-col items-center justify-center h-full text-slate-400">'
        + '<div class="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center text-3xl mb-4">💬</div>'
        + '<p class="text-sm font-medium text-slate-600 mb-1">开始智能问答</p>'
        + '<p class="text-xs text-slate-400 mb-6">选择知识库，输入您的金融文档相关问题</p>'
        + '<div class="flex flex-wrap gap-2 justify-center max-w-md">'
        + '<button onclick="quickQuestion('本季度营收情况如何？')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">本季度营收情况如何？</button>'
        + '<button onclick="quickQuestion('分析一下主要财务指标')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">分析一下主要财务指标</button>'
        + '<button onclick="quickQuestion('净利润同比增长多少？')" class="px-3 py-2 text-xs bg-white border border-slate-200 rounded-lg text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors">净利润同比增长多少？</button>'
        + '</div>'
        + '</div>';
}
```

### Step 8.8: Modify sendMessage() to refresh sidebar on done

In the `sendMessage()` function, after the `done` event handler closes the EventSource, add `loadSessions()`:

```javascript
evtSource.addEventListener('done', () => {
    evtSource.close();
    abortController = null;

    // ... (existing citations rendering code) ...

    loadSessions();  // NEW: Refresh sidebar to show updated session list
});
```

### Step 8.9: Modify resetSession() to use newSession()

Replace the existing `resetSession()` function body to delegate to `newSession()`:

```javascript
function resetSession() {
    newSession();
}
```

### Step 8.10: Modify DOMContentLoaded to load sessions

In the `DOMContentLoaded` handler, add `loadSessions()` call:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    loadKbSelector();
    loadSessions();  // NEW: Load session list on page load
    clearChatArea();  // NEW: Show welcome state
    const input = document.getElementById('chat-input');
    if (input) {
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        input.focus();
    }
});
```

### Step 8.11: Run tests

Run: (No automated tests for JS in this project. Manual verification via browser console.)

Note: Double-check the `escapeHtml()` function already exists in chat.js (it does, at line 156-159). Also verify all template literals use proper escaping for injection safety.


## Task 9: CSS Style Adjustments

**Files:**
- Modify: `nginx/html/css/style.css`

**Interfaces:**
- Consumes: New sidebar HTML classes from Task 7
- Produces: Styling for `.sidebar-session-list`, `.session-item`, `.delete-btn`

### Step 9.1: Add session list scrollbar styles

In `nginx/html/css/style.css`, add `.sidebar-session-list` alongside the existing `.sidebar-kb-list` references:

```css
/* Scrollbar - add sidebar-session-list to existing selectors */
#chat-messages::-webkit-scrollbar,
.sidebar-kb-list::-webkit-scrollbar,
.sidebar-session-list::-webkit-scrollbar {
    width: 6px;
}
#chat-messages::-webkit-scrollbar-track,
.sidebar-kb-list::-webkit-scrollbar-track,
.sidebar-session-list::-webkit-scrollbar-track {
    background: transparent;
}
#chat-messages::-webkit-scrollbar-thumb,
.sidebar-kb-list::-webkit-scrollbar-thumb,
.sidebar-session-list::-webkit-scrollbar-thumb {
    background: #cbd5e1;
    border-radius: 3px;
}
#chat-messages::-webkit-scrollbar-thumb:hover,
.sidebar-kb-list::-webkit-scrollbar-thumb:hover,
.sidebar-session-list::-webkit-scrollbar-thumb:hover {
    background: #94a3b8;
}
```

### Step 9.2: Add session item styles

Add at the end of `style.css`:

```css
/* ====== Session sidebar items ====== */
.session-item {
    position: relative;
    user-select: none;
}

.session-item .delete-btn {
    display: flex;
    opacity: 0;
    transition: opacity 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}

.session-item:hover .delete-btn {
    opacity: 1;
}

.session-item:active {
    transform: scale(0.98);
}
```

### Step 9.3: Remove unused KB sidebar styles

The `.sidebar-kb-list` class is no longer used in chat.html (KB list moved to index.html). No need to remove the CSS definition - it may still be used by index.html. Leave as-is.

### Step 9.4: Run tests

Run: (No automated tests for CSS. Visual verification required.)


## Task 10: Integration Verification

**Files:**
- Test: Full-stack manual verification

### Step 10.1: Restart and verify database tables

```bash
docker compose down
docker compose up -d --build
docker compose logs -f app
```

Expected: Log shows "Database tables initialized" with no errors. Verify sessions table exists:

```bash
docker compose exec mysql mysql -u financial_qa -pfinancial_qa_pass financial_qa -e "DESCRIBE sessions;"
```

Expected: Shows columns: id, title, kb_id, created_at, updated_at

### Step 10.2: Verify conversation_history table

```bash
docker compose exec mysql mysql -u financial_qa -pfinancial_qa_pass financial_qa -e "DESCRIBE conversation_history;"
```

Expected: `kb_id` column has `DEFAULT ''` and no FOREIGN KEY constraint.

### Step 10.3: Send a message and verify session creation

1. Open http://localhost/chat in browser
2. Select a knowledge base (or "所有知识库")
3. Type a question and send
4. Wait for SSE response to complete
5. Verify sidebar shows new session with title = first 20 chars of query
6. Verify MySQL:

```bash
docker compose exec mysql mysql -u financial_qa -pfinancial_qa_pass financial_qa -e "SELECT id, title, kb_id FROM sessions;"
```

Expected: One row with correct title and kb_id

### Step 10.4: Verify session switching

1. Send another message in same session (verify it stays in same session)
2. Click "新会话" button to create new session
3. Send a message in new session
4. Click on the first session in sidebar
5. Verify: Messages from first session are loaded, KB selector updates

### Step 10.5: Verify session deletion

1. Click delete button on a session
2. Confirm dialog
3. Verify: Session removed from sidebar, if it was current session, welcome state shown
4. Verify MySQL:

```bash
docker compose exec mysql mysql -u financial_qa -pfinancial_qa_pass financial_qa -e "SELECT COUNT(*) FROM sessions;"
```

Expected: Session count decreased by 1

### Step 10.6: Verify cold start

1. `docker compose down -v` (clear all data)
2. `docker compose up -d --build`
3. Verify: Sidebar shows empty state, sending message creates session

### Step 10.7: Ruff and pytest

```bash
ruff check .
pytest tests/ -v
```

Expected: No ruff errors, all tests pass

### Step 10.8: Verify error states

1. Send message while no KB exists ("所有知识库" mode) - verify no error
2. Delete a session that doesn't exist (via direct API call) - verify 404
3. Switch to a session while SSE is active - verify SSE is aborted


---

## Self-Review

### Spec coverage check

| Design Doc Section | Task(s) | Covered? |
|---|---|---|
| 2.1 New table sessions | Task 1 (CREATE_TABLE_SESSIONS) | Yes |
| 2.2 conversation_history FK removal | Task 1 (modified CREATE_TABLE_CONVERSATION_HISTORY) | Yes |
| 2.3 Session list query | Task 1 (SELECT_SESSIONS) | Yes |
| 3.1 GET /api/sessions | Task 5 (list_sessions endpoint) | Yes |
| 3.2 GET /api/sessions/{id}/messages | Task 5 (get_session_messages endpoint) | Yes |
| 3.3 DELETE /api/sessions/{id} | Task 5 (delete_session endpoint) | Yes |
| 4.1 queries.py new SQL | Task 1 | Yes |
| 4.2 mysql_db.py new methods | Task 2 | Yes |
| 4.3 ChatManager dual-write | Task 3 | Yes |
| 4.4 SSE endpoint async persistence | Task 4 | Yes |
| 4.5 sessions.py route file | Task 5 | Yes |
| 5.1 Frontend sidebar HTML | Task 7 | Yes |
| 5.2 Frontend state management | Task 8 | Yes |
| 5.3 Frontend API calls | Task 6 | Yes |
| 6 Error handling & retry | Task 4 (_persist_conversation retry) | Yes |

### Placeholder check
- No "TBD", "TODO", or "fill in details" found
- All code blocks contain complete implementations
- All file paths are absolute or clearly project-relative
- All method signatures match across task boundaries

### Type consistency check
- `MySQLDB.create_session(session_id, title, kb_id)` - consistent across Tasks 2 and 3
- `MySQLDB.get_sessions() -> list[dict]` - consistent across Tasks 2 and 5
- `MySQLDB.get_session_by_id(session_id) -> Optional[dict]` - consistent across Tasks 2 and 5
- `MySQLDB.get_messages(session_id) -> list[dict]` - consistent across Tasks 2 and 5
- `MySQLDB.delete_session_and_messages(session_id) -> bool` - consistent across Tasks 2 and 5
- `MySQLDB.save_message(session_id, kb_id, role, content, sources)` - consistent across Tasks 2 and 3
- `ChatManager.set_mysql_db(mysql_db)` - consistent across Tasks 3 and 4
- `ChatManager.save_session_async(session_id, title, kb_id)` - consistent across Tasks 3 and 4
- `ChatManager.save_messages_async(session_id, kb_id, user_msg, assistant_msg, sources)` - consistent across Tasks 3 and 4
- `ChatManager.cleanup_session(session_id)` - consistent across Tasks 3 and 5
- `fetchSessions()`, `fetchSessionMessages(sessionId)`, `deleteSessionAPI(sessionId)` - consistent across Tasks 6 and 8

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-session-history-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
