# Phase4 Architecture Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement user auth, MinIO file persistence, multi-strategy chunking, retrieval parent_context fix, and token usage tracking.

**Architecture:** Five independent subsystems: (1) database schema migration — add `users` table + `user_id` + new fields; (2) user auth — auto-register/login, token middleware, anonymous Cookie; (3) MinIO file storage — persist uploaded files; (4) chunking strategy — ChunkRouter + 3 chunkers with block_type from parsers; (5) retrieval fix — parent_content to LLM + DashScope token capture. Tasks are ordered by dependency.

**Tech Stack:** Python 3.11+ / FastAPI / ChromaDB / LangChain / DashScope / MySQL 8.0 / Redis 7 / MinIO

## Global Constraints

- All metadata changes to ChromaDB's `add_chunks()` must preserve existing fields (`source`, `page`, `chunk_index`, `chunk_total`, `doc_id`) and add new ones (`chunk_strategy`, `heading_path`, `parent_content`, `tokens`, `entities`)
- Cookie names: `token` (login session), `user_id` (anonymous)
- MinIO bucket: `documents`, key pattern: `documents/{user_id}/{kb_id}/{doc_id}/{filename}`
- `.env` config prefixes: `MINIO_*` for MinIO, `AUTH_*` for auth
- DB changes must be backwards-compatible: new columns with defaults, no DROP
- Token TTL: 30 days (2592000 seconds) in Redis

---

### Task 1: Database Schema Migration

**Files:**
- Modify: `deploy/mysql/init/001_schema.sql`
- Modify: `src/config/queries.py`
- Modify: `src/infra/mysql_db.py`
- Test: `tests/test_mysql_db.py`

**Interfaces:**
- Produces: Updated `CREATE_TABLE_DOCUMENT`, `CREATE_TABLE_CONVERSATION_HISTORY`, `INSERT_DOCUMENT`, `UPDATE_DOCUMENT_STATUS`, `SELECT_DOCUMENTS_BY_KB_ID`, `INSERT_MESSAGE`, `INSERT_SESSION` queries with new fields
- Produces: `add_user()`, `get_user_by_account()`, `update_user_token()`, `get_user_by_token()` methods on MySQLDB

- [ ] **Step 1: Update 001_schema.sql**

Replace the existing `CREATE TABLE` statements with the new schema:

```sql
-- New table: users
CREATE TABLE IF NOT EXISTS users (
    id         VARCHAR(36)  PRIMARY KEY,
    account    VARCHAR(100) NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    token      VARCHAR(64),
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
);

-- Recreate tables with new fields
DROP TABLE IF EXISTS conversation_history;
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS document;
DROP TABLE IF EXISTS knowledge_base;

CREATE TABLE knowledge_base (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_kb (user_id, name)
);

CREATE TABLE document (
    id                  VARCHAR(36)  PRIMARY KEY,
    user_id             VARCHAR(36)  NOT NULL DEFAULT '',
    kb_id               VARCHAR(36)  NOT NULL,
    filename            VARCHAR(255) NOT NULL,
    file_type           VARCHAR(10)  NOT NULL,
    file_size           INT          NOT NULL DEFAULT 0,
    file_path           VARCHAR(512),
    hash                VARCHAR(32),
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
    processing_state    VARCHAR(20),
    processing_progress INTEGER      DEFAULT 0,
    processing_message  VARCHAR(255),
    error_msg           TEXT,
    chunk_strategy      VARCHAR(50)  DEFAULT 'parent_child',
    chunk_count         INTEGER      DEFAULT 0,
    meta_info           JSON,
    created_at          DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE,
    INDEX idx_user_kb (user_id, kb_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sessions (
    id          VARCHAR(36)  PRIMARY KEY,
    user_id     VARCHAR(36)  NOT NULL DEFAULT '',
    title       VARCHAR(20)  NOT NULL DEFAULT '',
    kb_id       VARCHAR(36)  NOT NULL DEFAULT '',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_updated_at (updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE conversation_history (
    id                 INT          AUTO_INCREMENT PRIMARY KEY,
    session_id         VARCHAR(36)  NOT NULL,
    kb_id              VARCHAR(36)  NOT NULL DEFAULT '',
    role               ENUM('user','assistant') NOT NULL,
    content            TEXT         NOT NULL,
    sources            JSON,
    prompt_tokens      INT          DEFAULT 0,
    completion_tokens  INT          DEFAULT 0,
    total_tokens       INT          DEFAULT 0,
    model_name         VARCHAR(100) DEFAULT '',
    created_at         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: Update queries.py**

Read `src/config/queries.py` and update every constant:

1. Replace `CREATE_TABLE_KNOWLEDGE_BASE` — new schema with user_id + joint unique
2. Replace `CREATE_TABLE_DOCUMENT` — all 20 fields
3. Replace `CREATE_TABLE_CONVERSATION_HISTORY` — add token fields
4. Replace `CREATE_TABLE_SESSIONS` — add user_id
5. Add new constants for `users` table:
   ```sql
   CREATE_TABLE_USERS = """\
   CREATE TABLE IF NOT EXISTS users (
       id         VARCHAR(36)  PRIMARY KEY,
       account    VARCHAR(100) NOT NULL UNIQUE,
       password   VARCHAR(255) NOT NULL,
       token      VARCHAR(64),
       created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
   )
   """
   INSERT_USER = """\
   INSERT INTO users (id, account, password) VALUES (%s, %s, %s)
   """
   SELECT_USER_BY_ACCOUNT = """\
   SELECT id, account, password, token, created_at FROM users WHERE account = %s
   """
   UPDATE_USER_TOKEN = """\
   UPDATE users SET token = %s WHERE id = %s
   """
   SELECT_USER_BY_TOKEN = """\
   SELECT id, account FROM users WHERE token = %s
   """
   ```
6. `INSERT_DOCUMENT` — add `user_id`, `file_path`, `hash`, `processing_state`, `processing_progress`, `processing_message`, `chunk_strategy`, `meta_info`
7. `INSERT_MESSAGE` — add `prompt_tokens`, `completion_tokens`, `total_tokens`, `model_name`
8. `INSERT_SESSION` — add `user_id`
9. `SELECT_ALL_KNOWLEDGE_BASES` — filter by user_id, add field
10. `SELECT_DOCUMENTS_BY_KB_ID` — return new fields

- [ ] **Step 3: Update mysql_db.py**

Add new CRUD methods:

```python
def add_user(self, user_id: str, account: str, password_hash: str) -> None:
    self._execute_sql(queries.INSERT_USER, (user_id, account, password_hash))

def get_user_by_account(self, account: str) -> dict | None:
    rows = self._execute_sql(queries.SELECT_USER_BY_ACCOUNT, (account,), fetch=True)
    return rows[0] if rows else None

def update_user_token(self, user_id: str, token: str) -> None:
    self._execute_sql(queries.UPDATE_USER_TOKEN, (token, user_id))

def get_user_by_token(self, token: str) -> dict | None:
    rows = self._execute_sql(queries.SELECT_USER_BY_TOKEN, (token,), fetch=True)
    return rows[0] if rows else None
```

Update `add_document()` to accept new params. Update `add_message()` to accept token params.

- [ ] **Step 4: Write and run tests**

```python
# tests/test_mysql_db.py
import uuid
import pytest
from src.infra.mysql_db import MySQLDB

def test_add_user():
    db = MySQLDB()
    uid = str(uuid.uuid4())
    db.add_user(uid, "test_user", "hash123")
    user = db.get_user_by_account("test_user")
    assert user["id"] == uid

def test_update_user_token():
    db = MySQLDB()
    uid = str(uuid.uuid4())
    db.add_user(uid, "tok_user", "hash")
    db.update_user_token(uid, "tok_abc")
    user = db.get_user_by_account("tok_user")
    assert user["token"] == "tok_abc"

def test_get_user_by_token():
    db = MySQLDB()
    uid = str(uuid.uuid4())
    db.add_user(uid, "lookup_user", "hash")
    db.update_user_token(uid, "tok_lookup")
    user = db.get_user_by_token("tok_lookup")
    assert user["id"] == uid
```

- [ ] **Step 5: Commit**

```bash
git add deploy/mysql/init/001_schema.sql src/config/queries.py src/infra/mysql_db.py tests/test_mysql_db.py
git commit -m "feat(db): add users table, user_id fields, document metadata, token tracking"
```

---

### Task 2: Parser block_type Metadata

**Files:**
- Modify: `src/parsers/pymupdf_parser.py`
- Modify: `src/parsers/docx_parser.py`

- [ ] **Step 1: Add table regex and block_type to pymupdf_parser.py**

In the chunk creation loop (around line 99-107), add block_type detection:

```python
import re
TABLE_PATTERN = re.compile(r'^\|.+\|[\s\S]*?^\|.+\|', re.MULTILINE)

# ... existing code ...
texts = splitter.split_text(page_text)
for i, t in enumerate(texts):
    block_type = "table" if TABLE_PATTERN.search(t) else "text"
    chunks.append(
        ChunkData(
            content=t,
            metadata={"source": source, "page": page_num, "block_type": block_type},
            chunk_id=f"{source}:p{page_num}:{i}",
        )
    )
```

- [ ] **Step 2: Add same pattern to docx_parser.py**

Same approach — apply the TABLE_PATTERN regex after text splitting.

- [ ] **Step 3: Verify parsers import correctly**

```bash
python3 -c "from src.parsers.pymupdf_parser import PyMuPDFParser; print('PDF OK')"
python3 -c "from src.parsers.docx_parser import DocxParser; print('DOCX OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/parsers/pymupdf_parser.py src/parsers/docx_parser.py
git commit -m "feat(parser): add block_type metadata to chunks"
```

---

### Task 3: Chunking Strategy — ChunkRouter + Three Chunkers

**Files:**
- Create: `src/infra/chunk_router.py`
- Create: `src/infra/chunkers/__init__.py`
- Create: `src/infra/chunkers/base.py`
- Create: `src/infra/chunkers/parent_child.py`
- Create: `src/infra/chunkers/qa.py`
- Create: `src/infra/chunkers/table_preserving.py`
- Test: `tests/test_chunking.py`

- [ ] **Step 1: Create `src/infra/chunkers/base.py`**

```python
"""分块器基类。"""

from abc import ABC, abstractmethod


class BaseChunker(ABC):
    """分块器抽象基类。所有分块器必须继承此类并实现 chunk()。"""

    chunk_strategy: str = ""

    @abstractmethod
    def chunk(self, text: str, metadata: dict) -> list[dict]:
        ...

    @staticmethod
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 2)

    @staticmethod
    def inject_heading_prefix(content: str, heading_path: str) -> str:
        if not heading_path:
            return content
        parts = heading_path.split(" > ")
        prefix = " > ".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        return f"【{prefix}】{content}"
```

- [ ] **Step 2: Create `src/infra/chunkers/parent_child.py`**

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.infra.chunkers.base import BaseChunker


class ParentChildChunker(BaseChunker):
    chunk_strategy = "parent_child"
    CHILD_SIZE = 256
    PARENT_SIZE = 1024
    OVERLAP = 25
    SEPARATORS = ["\n\n", "\n", "。", ".", " "]

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.PARENT_SIZE, chunk_overlap=self.OVERLAP,
            length_function=self.count_tokens, separators=self.SEPARATORS,
        )
        parent_docs = parent_splitter.create_documents([text])
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.CHILD_SIZE, chunk_overlap=self.OVERLAP,
            length_function=self.count_tokens, separators=self.SEPARATORS,
        )
        result = []
        for pi, parent in enumerate(parent_docs):
            child_docs = child_splitter.create_documents([parent.page_content])
            for ci, child in enumerate(child_docs):
                result.append({
                    "content": self.inject_heading_prefix(
                        child.page_content, metadata.get("heading_path", "")
                    ),
                    "metadata": {
                        **metadata,
                        "parent_content": parent.page_content,
                        "tokens": self.count_tokens(child.page_content),
                        "chunk_strategy": self.chunk_strategy,
                    },
                })
        return result
```

- [ ] **Step 3: Create `src/infra/chunkers/qa.py`**

```python
import re
from src.infra.chunkers.base import BaseChunker


class QAChunker(BaseChunker):
    chunk_strategy = "qa"

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        qa_pattern = re.compile(r'(问[：:].*?答[：:].*?)(?=问[：:]|\Z)', re.DOTALL)
        qa_pairs = qa_pattern.findall(text)
        if not qa_pairs:
            qa_pairs = [text]
        result = []
        for i, pair in enumerate(qa_pairs):
            pair = pair.strip()
            if not pair:
                continue
            result.append({
                "content": self.inject_heading_prefix(pair, metadata.get("heading_path", "")),
                "metadata": {
                    **metadata,
                    "parent_content": None,
                    "tokens": self.count_tokens(pair),
                    "chunk_strategy": self.chunk_strategy,
                },
            })
        return result
```

- [ ] **Step 4: Create `src/infra/chunkers/table_preserving.py`**

```python
import re
from src.infra.chunkers.base import BaseChunker
from src.infra.chunkers.parent_child import ParentChildChunker


class TablePreservingChunker(BaseChunker):
    chunk_strategy = "table_preserving"
    TABLE_PATTERN = re.compile(r'(^\|.+\|[\s\S]*?^\|.+\|)', re.MULTILINE)

    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments = self._split_by_table_boundary(text)
        parent_child = ParentChildChunker()
        result = []
        for seg in segments:
            is_table = bool(self.TABLE_PATTERN.search(seg))
            if is_table:
                result.append({
                    "content": self.inject_heading_prefix(seg, metadata.get("heading_path", "")),
                    "metadata": {
                        **metadata,
                        "parent_content": seg,
                        "tokens": self.count_tokens(seg),
                        "chunk_strategy": self.chunk_strategy,
                    },
                })
            else:
                text_chunks = parent_child.chunk(seg, metadata)
                for c in text_chunks:
                    c["metadata"]["chunk_strategy"] = self.chunk_strategy
                result.extend(text_chunks)
        return result

    @staticmethod
    def _split_by_table_boundary(text: str) -> list[str]:
        lines = text.split("\n")
        segments, current, in_table = [], [], False
        for line in lines:
            is_table_line = bool(re.match(r'^\|.*\|$', line.strip()))
            if is_table_line != in_table:
                if current:
                    segments.append("\n".join(current))
                    current = []
                in_table = is_table_line
            current.append(line)
        if current:
            segments.append("\n".join(current))
        return segments
```

- [ ] **Step 5: Create `src/infra/chunkers/__init__.py`**

```python
from src.infra.chunkers.base import BaseChunker
from src.infra.chunkers.parent_child import ParentChildChunker
from src.infra.chunkers.qa import QAChunker
from src.infra.chunkers.table_preserving import TablePreservingChunker

__all__ = ["BaseChunker", "ParentChildChunker", "QAChunker", "TablePreservingChunker"]
```

- [ ] **Step 6: Create `src/infra/chunk_router.py`**

```python
import re
from src.infra.chunkers.base import BaseChunker
from src.infra.chunkers.parent_child import ParentChildChunker
from src.infra.chunkers.qa import QAChunker
from src.infra.chunkers.table_preserving import TablePreservingChunker


class ChunkRouter:
    QA_THRESHOLD = 0.20

    @staticmethod
    def detect_strategy(full_text: str, parsed_chunks: list) -> str:
        for chunk in parsed_chunks:
            if chunk.metadata.get("block_type") == "table":
                return "table_preserving"
        if ChunkRouter._is_qa_document(full_text):
            return "qa"
        return "parent_child"

    @staticmethod
    def get_chunker(strategy: str) -> BaseChunker:
        return {
            "qa": QAChunker,
            "table_preserving": TablePreservingChunker,
            "parent_child": ParentChildChunker,
        }.get(strategy, ParentChildChunker)()

    @staticmethod
    def _is_qa_document(text: str) -> bool:
        if not text.strip():
            return False
        sentences = [s.strip() for s in re.split(r'[。！\n]', text) if s.strip()]
        if not sentences:
            return False
        q_count = sum(1 for s in sentences if s.rstrip().endswith(("？", "?")))
        return (q_count / len(sentences)) > ChunkRouter.QA_THRESHOLD
```

- [ ] **Step 7: Write tests**

```python
# tests/test_chunking.py
import pytest
from src.infra.chunkers.parent_child import ParentChildChunker
from src.infra.chunkers.qa import QAChunker
from src.infra.chunkers.table_preserving import TablePreservingChunker
from src.infra.chunkers.base import BaseChunker
from src.infra.chunk_router import ChunkRouter


def test_heading_injection():
    r = BaseChunker.inject_heading_prefix("营收100亿", "2024年 > 利润表 > 主要项目")
    assert r == "【利润表 > 主要项目】营收100亿"


def test_parent_child_has_parent_content():
    chunker = ParentChildChunker()
    text = ("这是第一段内容。" * 50) + ("这是第二段内容。" * 50)
    result = chunker.chunk(text, {"source": "t.txt", "doc_id": "d1"})
    assert len(result) > 0
    assert result[0]["metadata"]["chunk_strategy"] == "parent_child"
    assert "parent_content" in result[0]["metadata"]


def test_qa_no_parent():
    chunker = QAChunker()
    text = "问：营收多少？\n答：100亿\n问：利润多少？\n答：20亿"
    result = chunker.chunk(text, {"source": "q.txt", "doc_id": "d2"})
    for r in result:
        assert r["metadata"]["parent_content"] is None
        assert r["metadata"]["chunk_strategy"] == "qa"


def test_table_preserving_keeps_table():
    chunker = TablePreservingChunker()
    text = "开头\n| 项目 | 金额 |\n|--- |--- |\n| 营收 | 100亿 |\n| 利润 | 20亿 |\n结尾"
    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d3"})
    table_chunks = [r for r in result if "| 营收" in r["content"]]
    assert len(table_chunks) >= 1
    for tc in table_chunks:
        assert "| 营收 | 100亿 |" in tc["content"]
        assert "| 利润 | 20亿 |" in tc["content"]


def test_chunk_router_qa():
    from src.infra.chunk_enhancer import ChunkData
    text = "问：你好吗？\n答：我很好。\n问：吃了吗？\n答：吃了。"
    chunks = [ChunkData("a", {"block_type": "text"}, "0"), ChunkData("b", {"block_type": "text"}, "1")]
    assert ChunkRouter.detect_strategy(text, chunks) == "qa"


def test_chunk_router_table():
    from src.infra.chunk_enhancer import ChunkData
    text = "普通文本。\n| 项目 |\n|--- |\n| 数据 |"
    chunks = [ChunkData("txt", {"block_type": "text"}, "0"), ChunkData("| 项目 |", {"block_type": "table"}, "1")]
    assert ChunkRouter.detect_strategy(text, chunks) == "table_preserving"


def test_chunk_router_default():
    from src.infra.chunk_enhancer import ChunkData
    text = ("这是一段普通的说明文字。" * 10)
    chunks = [ChunkData(text, {"block_type": "text"}, "0")]
    assert ChunkRouter.detect_strategy(text, chunks) == "parent_child"
```

- [ ] **Step 8: Run tests**

```bash
pytest tests/test_chunking.py -v
```

Expected: 7 tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/infra/chunk_router.py src/infra/chunkers/ tests/test_chunking.py
git commit -m "feat(chunk): add ChunkRouter with QA, table_preserving, parent_child strategies"
```

---

### Task 4: MinIO File Storage

**Files:**
- Create: `src/infra/file_store.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add minio dependency**

In `pyproject.toml`, add: `"minio>=7.2.0,<8.0.0"`

- [ ] **Step 2: Create `src/infra/file_store.py`**

```python
from io import BytesIO
from typing import Optional
from minio import Minio
from minio.error import S3Error
from loguru import logger


class FileStore:
    def __init__(self, endpoint: str = "minio:9000", access_key: str = "minio",
                 secret_key: str = "miniosecret", bucket: str = "documents", secure: bool = False):
        self._bucket = bucket
        self._client = Minio(endpoint, access_key, secret_key, secure=secure)
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)
            logger.info("Created MinIO bucket '{}'", self._bucket)

    @staticmethod
    def build_path(user_id: str, kb_id: str, doc_id: str, filename: str) -> str:
        return f"documents/{user_id}/{kb_id}/{doc_id}/{filename}"

    def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        try:
            self._client.put_object(self._bucket, key, BytesIO(data), len(data), content_type=content_type)
            return True
        except S3Error as e:
            logger.error("MinIO upload failed: {} - {}", key, e)
            return False

    def download(self, key: str) -> Optional[bytes]:
        try:
            resp = self._client.get_object(self._bucket, key)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return data
        except S3Error as e:
            logger.warning("MinIO download failed: {} - {}", key, e)
            return None

    def delete(self, key: str) -> bool:
        try:
            self._client.remove_object(self._bucket, key)
            return True
        except S3Error as e:
            logger.warning("MinIO delete failed: {} - {}", key, e)
            return False
```

- [ ] **Step 3: Write tests**

```python
# tests/test_file_store.py
from src.infra.file_store import FileStore

def test_build_path():
    p = FileStore.build_path("u1", "kb1", "d1", "rpt.pdf")
    assert p == "documents/u1/kb1/d1/rpt.pdf"
```

- [ ] **Step 4: Commit**

```bash
git add src/infra/file_store.py tests/test_file_store.py pyproject.toml
git commit -m "feat(storage): add MinIO file store"
```

---

### Task 5: User Authentication

**Files:**
- Create: `src/infra/user_auth.py`
- Create: `src/api/routes/auth.py`
- Create: `src/api/middleware.py`
- Create: `nginx/html/login.html`
- Modify: `src/api/main.py`
- Modify: `nginx/html/index.html`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Create `src/infra/user_auth.py`**

```python
import hashlib
import uuid
from typing import Optional


class UserAuth:
    TOKEN_TTL = 2592000

    @staticmethod
    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return UserAuth.hash_password(password) == password_hash

    @staticmethod
    def generate_token() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def store_token(redis_client, token: str, user_id: str, ttl: int = TOKEN_TTL) -> None:
        redis_client.setex(f"token:{token}", ttl, user_id)

    @staticmethod
    def get_user_id_from_token(redis_client, token: str) -> Optional[str]:
        uid = redis_client.get(f"token:{token}")
        return uid.decode() if uid else None

    @staticmethod
    def delete_token(redis_client, token: str) -> None:
        redis_client.delete(f"token:{token}")
```

- [ ] **Step 2: Create `src/api/routes/auth.py`**

```python
import uuid
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from src.app_service import AppService
from src.infra.user_auth import UserAuth

router = APIRouter()

_service: AppService | None = None

def _get_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()
    return _service


@router.post("/auth/login")
async def login(account: str, password: str):
    svc = _get_service()
    pw_hash = UserAuth.hash_password(password)
    user = svc.db.get_user_by_account(account)
    if user:
        if user["password"] != pw_hash:
            raise HTTPException(401, "密码错误")
        user_id = user["id"]
    else:
        user_id = str(uuid.uuid4())
        svc.db.add_user(user_id, account, pw_hash)
        logger.info("New user registered: {}", account)
    token = UserAuth.generate_token()
    UserAuth.store_token(svc.redis_client, token, user_id)
    svc.db.update_user_token(user_id, token)
    return {"token": token, "user_id": user_id}


@router.get("/auth/verify")
async def verify_token(token: str = Cookie(None)):
    if not token:
        return {"valid": False}
    svc = _get_service()
    uid = UserAuth.get_user_id_from_token(svc.redis_client, token)
    return {"user_id": uid, "valid": uid is not None}


@router.post("/auth/logout")
async def logout(token: str = Cookie(None)):
    if token:
        svc = _get_service()
        UserAuth.delete_token(svc.redis_client, token)
    return JSONResponse({"message": "已退出登录"})


@router.get("/auth/anonymous")
async def get_anonymous_id(user_id: str = Cookie(None)):
    if not user_id:
        user_id = str(uuid.uuid4())
    resp = JSONResponse({"user_id": user_id})
    resp.set_cookie("user_id", user_id, max_age=31536000, path="/")
    return resp
```

- [ ] **Step 3: Create `src/api/middleware.py`**

```python
from fastapi import Request, Response
from fastapi.responses import JSONResponse
import uuid as uuid_mod
from src.app_service import AppService
from src.infra.user_auth import UserAuth


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/auth/"):
        return await call_next(request)

    svc = AppService()

    if path.startswith("/api/kbs/"):
        token = request.cookies.get("token")
        if not token:
            return JSONResponse(401, {"detail": "未登录，请先登录"})
        uid = UserAuth.get_user_id_from_token(svc.redis_client, token)
        if not uid:
            return JSONResponse(401, {"detail": "Token 无效或已过期"})
        request.state.user_id = uid
        return await call_next(request)

    if path.startswith("/api/chat/") or path.startswith("/api/sessions/"):
        token = request.cookies.get("token")
        if token:
            uid = UserAuth.get_user_id_from_token(svc.redis_client, token)
            if uid:
                request.state.user_id = uid
                return await call_next(request)
        uid = request.cookies.get("user_id")
        if not uid:
            uid = str(uuid_mod.uuid4())
        request.state.user_id = uid
        resp: Response = await call_next(request)
        if not request.cookies.get("user_id"):
            resp.set_cookie("user_id", uid, max_age=31536000, path="/")
        return resp

    return await call_next(request)
```

- [ ] **Step 4: Register in main.py**

In `src/api/main.py`, add:
```python
from src.api.routes import auth as auth_routes
from src.api.middleware import auth_middleware

app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
app.middleware("http")(auth_middleware)
```

Make sure `AppService` exposes `redis_client` (add property if needed).

- [ ] **Step 5: Create login.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>登录 — Corporate RAG</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
body{background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0}
.card{background:#fff;border-radius:16px;padding:40px;width:380px;box-shadow:0 20px 60px rgba(0,0,0,.15)}
.card h1{font-size:24px;font-weight:700;color:#1e293b;text-align:center;margin-bottom:8px}
.card p{font-size:14px;color:#94a3b8;text-align:center;margin-bottom:32px}
.card input{width:100%;padding:12px 16px;border:1.5px solid #e2e8f0;border-radius:10px;font-size:14px;outline:none;box-sizing:border-box;margin-bottom:16px;transition:border-color .2s}
.card input:focus{border-color:#667eea}
.card button{width:100%;padding:12px;border:none;border-radius:10px;font-size:15px;font-weight:600;color:#fff;background:linear-gradient(135deg,#667eea,#764ba2);cursor:pointer;transition:opacity .2s}
.card button:hover{opacity:.9}
.card button:disabled{opacity:.5;cursor:not-allowed}
.error{color:#ef4444;font-size:13px;margin-top:12px;text-align:center;display:none}
</style></head>
<body><div class="card">
<h1>Corporate RAG</h1><p>登录后管理知识库与文档</p>
<input type="text" id="account" placeholder="账号" autocomplete="username">
<input type="password" id="password" placeholder="密码" autocomplete="current-password">
<button id="login-btn" onclick="handleLogin()">登录</button>
<div id="error" class="error"></div>
</div>
<script>
const API='/api';
async function handleLogin(){
  const acct=document.getElementById('account').value.trim();
  const pw=document.getElementById('password').value;
  const btn=document.getElementById('login-btn');
  const err=document.getElementById('error');
  if(!acct||!pw){err.textContent='请输入账号和密码';err.style.display='block';return}
  btn.disabled=true;btn.textContent='登录中...';err.style.display='none';
  try{
    const r=await fetch(`${API}/auth/login`,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({account:acct,password:pw})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.detail||'登录失败');
    document.cookie=`token=${d.token};path=/;max-age=${30*24*3600}`;
    const rd=new URLSearchParams(location.search).get('redirect')||'/';
    location.href=decodeURIComponent(rd);
  }catch(e){err.textContent=e.message;err.style.display='block';btn.disabled=false;btn.textContent='登录'}
}
document.addEventListener('keydown',e=>{if(e.key==='Enter')handleLogin()});
</script></body></html>
```

- [ ] **Step 6: Update index.html auth check**

At the start of the `<script>` block in `index.html`:

```javascript
(async function checkAuth() {
    try {
        const r = await fetch('/api/auth/verify');
        const d = await r.json();
        if (!d.valid) {
            location.href = '/login.html?redirect=' + encodeURIComponent(location.pathname + location.search);
        }
    } catch (e) {
        location.href = '/login.html?redirect=' + encodeURIComponent(location.pathname);
    }
})();
```

- [ ] **Step 7: Write tests**

```python
# tests/test_auth.py
import pytest
from src.infra.user_auth import UserAuth
from unittest.mock import Mock

def test_hash_consistent():
    assert UserAuth.hash_password("abc") == UserAuth.hash_password("abc")

def test_verify_correct():
    h = UserAuth.hash_password("correct")
    assert UserAuth.verify_password("correct", h)

def test_verify_wrong():
    h = UserAuth.hash_password("correct")
    assert not UserAuth.verify_password("wrong", h)

def test_token_format():
    t = UserAuth.generate_token()
    assert len(t.split("-")) == 5

def test_store_and_retrieve():
    r = Mock()
    r.get.return_value = b"uid"
    UserAuth.store_token(r, "tok", "uid")
    assert UserAuth.get_user_id_from_token(r, "tok") == "uid"

def test_invalid_token():
    r = Mock()
    r.get.return_value = None
    assert UserAuth.get_user_id_from_token(r, "bad") is None
```

- [ ] **Step 8: Commit**

```bash
git add src/infra/user_auth.py src/api/routes/auth.py src/api/middleware.py src/api/main.py nginx/html/login.html nginx/html/index.html tests/test_auth.py
git commit -m "feat(auth): add user authentication, token validation, login page"
```

---

### Task 6: Wire Upload Pipeline

**Files:**
- Modify: `src/api/routes/documents.py`
- Modify: `src/infra/mysql_db.py` (update_document_status)

- [ ] **Step 1: Rewrite upload endpoint**

Read and modify `src/api/routes/documents.py`. Key changes:
1. Add imports: `hashlib`, `uuid`, `FileStore`, `ChunkRouter`
2. In `upload_document()`:
   - Get `user_id` from `request.state.user_id`
   - Compute MD5 hash, check dedup within KB
   - Upload to MinIO first, fail early if it fails
   - Then INSERT MySQL with all new fields
3. In `_process_document()`:
   - Download from MinIO instead of reading directly
   - Use `ChunkRouter.detect_strategy()` to select chunker
   - Use `ChunkRouter.get_chunker().chunk()` instead of direct call
   - Update progress/state during each stage
   - Store `chunk_strategy` in final DB update

- [ ] **Step 2: Update mysql_db.py update_document_status**

Update the method to accept and store `processing_state`, `processing_progress`, `processing_message`, and `chunk_strategy`:

```python
def update_document_status(self, doc_id, status, chunk_count=0, error_msg="",
                           processing_state=None, processing_progress=None,
                           processing_message=None, chunk_strategy=None):
    query = """UPDATE document SET status=%s, chunk_count=%s, error_msg=%s,
        processing_state=%s, processing_progress=%s, processing_message=%s,
        chunk_strategy=COALESCE(%s, chunk_strategy)
        WHERE id=%s"""
    self._execute_sql(query, (status, chunk_count, error_msg,
                              processing_state, processing_progress,
                              processing_message, chunk_strategy, doc_id))
```

- [ ] **Step 3: Commit**

```bash
git add src/api/routes/documents.py src/infra/mysql_db.py
git commit -m "feat(upload): wire MinIO, ChunkRouter, and processing state machine"
```

---

### Task 7: Retrieval Enhancement

**Files:**
- Modify: `src/rag_chain.py`
- Modify: `src/api/routes/chat.py`
- Modify: `src/chat_manager.py`
- Modify: `src/infra/vector_store.py`

- [ ] **Step 1: Fix add_chunks metadata preservation**

In `vector_store.py`, replace the metadata dict construction:

```python
# Before (around line 202-209):
metadatas.append({
    "source": chunk.metadata.get("source", ""),
    "page": chunk.metadata.get("page", 0),
    "chunk_index": i,
    "chunk_total": len(chunks),
    "doc_id": doc_id,
})

# After:
meta = dict(chunk.metadata)
meta.update({"chunk_index": i, "chunk_total": len(chunks), "doc_id": doc_id})
meta.setdefault("source", "")
meta.setdefault("page", 0)
metadatas.append(meta)
```

- [ ] **Step 2: RAGContext reads parent_content**

In `rag_chain.py`, in `_rerank_results()`:

```python
metadata = r.get("metadata", {})
pc = metadata.get("parent_content")
RAGContext(
    content=pc if pc else r["content"],
    source=metadata.get("source", ""),
    page=metadata.get("page", 0),
    doc_id=metadata.get("doc_id", ""),
    chunk_id=r["id"],
    score=score,
)
```

- [ ] **Step 3: Capture token usage from DashScope**

In `rag_chain.py`, in `_stream_answer()`, add after the streaming loop:

```python
self.last_token_usage = {}
try:
    stream = self.llm.astream(messages)
    full_output = ""
    async for chunk in stream:
        if chunk.content:
            full_output += chunk.content
            yield chunk.content
        if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
            u = chunk.usage_metadata
            self.last_token_usage = {
                "prompt_tokens": u.get("input_tokens", 0),
                "completion_tokens": u.get("output_tokens", 0),
                "total_tokens": u.get("total_tokens", 0),
            }
    if not self.last_token_usage:
        usage = _estimate_usage(messages, full_output)
        self.last_token_usage = {"prompt_tokens": usage["input"],
                                  "completion_tokens": usage["output"],
                                  "total_tokens": usage["input"] + usage["output"]}
except Exception as e:
    logger.error("Stream failed: {}", e)
    raise
```

- [ ] **Step 4: Update chat.py citation and add_message**

In `src/api/routes/chat.py`:

Citation — use parent_content in snippet:
```python
snippet = getattr(ctx, 'parent_content', None) or ctx.content
yield sse_citation(ctx.source, ctx.page, snippet[:200], ctx.score, highlighted_snippet=highlighted)
```

After streaming, pass token usage to add_message:
```python
tu = getattr(svc.rag_chain, 'last_token_usage', {})
svc.rag_chain.chat_manager.add_message(
    session_id, "assistant", full_answer, sources=sources,
    prompt_tokens=tu.get("prompt_tokens", 0),
    completion_tokens=tu.get("completion_tokens", 0),
    total_tokens=tu.get("total_tokens", 0),
    model_name="qwen-max",
)
```

- [ ] **Step 5: Update chat_manager.add_message()**

Accept new params:
```python
def add_message(self, session_id, role, content, sources=None,
                prompt_tokens=0, completion_tokens=0, total_tokens=0, model_name=""):
    # ... redis logic unchanged ...
    params = (session_id, "", role, content,
              json.dumps(sources) if sources else None,
              prompt_tokens, completion_tokens, total_tokens, model_name)
    self.db._execute_sql(queries.INSERT_MESSAGE, params)
```

- [ ] **Step 6: Commit**

```bash
git add src/rag_chain.py src/api/routes/chat.py src/chat_manager.py src/infra/vector_store.py
git commit -m "feat(retrieval): add parent_content context, fix metadata, capture token usage"
```

---

### Task 8: Frontend Adaptations

**Files:**
- Modify: `nginx/html/index.html`

- [ ] **Step 1: Add chunk_strategy badge in chunk preview**

In the chunk card template of `renderChunkPage()`, after `分块 ${start + i + 1}`:

```javascript
const badgeLabel = chunk.chunk_strategy === 'qa' ? 'QA'
    : chunk.chunk_strategy === 'table_preserving' ? '表格'
    : '父子';
const badgeColor = chunk.chunk_strategy === 'qa' ? 'bg-green-100 text-green-700'
    : chunk.chunk_strategy === 'table_preserving' ? 'bg-orange-100 text-orange-700'
    : 'bg-blue-100 text-blue-700';
```

Add to the HTML:
```html
<span class="text-[10px] px-1.5 py-0.5 rounded-full ${badgeColor} ml-2">${badgeLabel}</span>
```

- [ ] **Step 2: Add parent_content collapsible panel**

After the chunk card body `</pre></div>`:
```javascript
${chunk.parent_content ? `
<details class="border-t border-slate-100">
    <summary class="px-4 py-2 text-xs text-slate-500 cursor-pointer hover:bg-slate-50 font-medium select-none">查看父段落上下文</summary>
    <div class="px-4 py-3 bg-slate-50 border-t border-slate-100">
        <pre class="text-xs text-slate-600 leading-relaxed whitespace-pre-wrap font-mono">${escapeHtml(chunk.parent_content)}</pre>
    </div>
</details>` : ''}
```

- [ ] **Step 3: Add upload loading modal**

```html
<!-- ====== Upload Loading Modal ====== -->
<div id="upload-loading" class="fixed inset-0 z-50 hidden">
    <div class="absolute inset-0 bg-black/40"></div>
    <div class="absolute inset-0 flex items-center justify-center">
        <div class="bg-white rounded-2xl px-10 py-8 shadow-2xl flex flex-col items-center">
            <div class="spinner mb-4" style="width:32px;height:32px;border-width:3px"></div>
            <p class="text-sm font-medium text-slate-700">正在同步上传中...</p>
            <p class="text-xs text-slate-400 mt-1">请勿关闭页面</p>
        </div>
    </div>
</div>
```

In `handleUpload()`, wrap with `document.getElementById('upload-loading').classList.remove('hidden')` before fetch and `.add('hidden')` after.

- [ ] **Step 4: Commit**

```bash
git add nginx/html/index.html
git commit -m "feat(ui): add chunk strategy badge, parent_content panel, upload loading modal"
```

---

### Task 9: Integration & Verification

- [ ] **Step 1: Add .env entries**

```bash
MINIO_ENDPOINT=http://minio:9000
MINIO_ACCESS_KEY=minio
MINIO_SECRET_KEY=miniosecret
MINIO_DOC_BUCKET=documents
AUTH_TOKEN_TTL=2592000
```

- [ ] **Step 2: Build and restart**

```bash
docker compose build app nginx
docker compose up -d
```

- [ ] **Step 3: Verify everything**

```bash
# Health check
curl -s http://localhost/api/health

# Login
curl -s -X POST http://localhost/api/auth/login -d "account=admin&password=admin123"

# Upload a file (with token)
TOKEN=... # from login response
curl -s -X POST -b "token=$TOKEN" http://localhost/api/kbs/{kb_id}/documents/upload -F "file=@test.txt"

# Run tests
pytest tests/ -v --tb=short
ruff check .
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: integrate phase4 architecture overhaul"
```
