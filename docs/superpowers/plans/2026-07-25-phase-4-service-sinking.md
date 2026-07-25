# Phase 4 Service Sinking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete business logic sinking from API layer to Service layer, eliminate infra/config direct calls from api/, and clean up dead code.

**Architecture:** Follow FastAPI layered architecture: api/ (routing only) → services/ (business orchestration) → infra/ (infrastructure). Auth becomes a proper service layer, document upload gets a single entry point, all infra/config access goes through AppService delegation.

**Tech Stack:** Python 3.11+ / FastAPI / bcrypt / MySQL / Redis / MinIO / ChromaDB / Loguru

## Global Constraints

- All imports must use absolute paths (`from src.xxx import yyy`)
- api/ MUST NOT import infra/ or config/ directly
- Single file MUST NOT exceed 400 lines; single function 80 lines
- File size rule applies to created files only (existing files < 400 are not targets for this phase)
- No ternary expressions (`a if cond else b`), use full if/else
- All functions must have docstrings (see docs/agents/rules.md)
- No print(), TODO, or debug code in final output
- `pytest tests/ -v` must pass after each task
- `ruff check . --fix` must pass after each task

---

## File Structure Map

```
Created:
  src/utils/auth_crypto.py              — password hashing & verification (pure functions, bcrypt)
  src/services/auth_service.py           — AuthService class (register/login/verify_token/logout)

Modified:
  src/api/auth.py                        — thin routes, delegate to AuthService
  src/api/documents.py                   — thin routes, delegate to DocumentService.store_and_process()
  src/services/document_service.py       — add store_and_process() method
  src/services/app_service.py            — add auth_service, settings property, delegate methods
  src/api/sessions.py                    — replace svc.db.* with svc.* delegates
  src/api/chat.py                        — replace svc.chat_manager.* with svc.* delegates; fix sse import
  src/api/kb_eval.py                     — replace svc.db.get_latest_eval_report with svc.get_latest_eval_report
  src/api/ragas_generate.py              — replace svc.db.get_kb_by_name with svc.get_kb_by_name
  src/api/health.py                      — replace from src.config import MAX_FILE_SIZE with svc.settings.MAX_FILE_SIZE
  src/api/llm_test.py                    — replace from src.config import ... with svc.settings.*
  src/rag/__init__.py                    — remove RAGChain from exports

Deleted:
  src/rag/chain.py                       — RAGChain class, zero production callers
  src/api/sse_utils.py                   — 3-line compat bridge, replace with direct import

Test files created:
  tests/utils/test_auth_crypto.py        — test hash_password / verify_password
  tests/services/test_auth_service.py    — test register / login / verify_token

Test files modified:
  tests/api/test_documents.py            — update for store_and_process
  tests/services/test_app_service.py     — remove 14 RAGChain mocks
  tests/eval/test_eval_ragas.py          — remove chat_with_citations mock

Test files deleted:
  tests/rag/test_chain.py                — references deleted RAGChain
  tests/rag/test_stream.py               — references deleted RAGChain
  tests/rag/test_prompt.py               — references deleted RAGChain
  tests/rag/test_rag_chain_tracing.py    — references deleted RAGChain
```

---

### Task 1: Create auth_crypto.py utility module

**Files:**
- Create: `src/utils/auth_crypto.py`
- Test: `tests/utils/test_auth_crypto.py`

**Interfaces:**
- Consumes: (none — pure function module)
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`

- [ ] **Step 1: Write tests**

```python
"""测试密码加密与校验工具模块。"""

import pytest
from src.utils.auth_crypto import hash_password, verify_password


def test_hash_password_returns_string():
    """hash_password 应该返回字符串。"""
    result = hash_password("mypassword123")
    assert isinstance(result, str)
    assert len(result) > 0


def test_hash_password_uses_salt():
    """每次调用应该返回不同的哈希值（含随机 salt）。"""
    h1 = hash_password("samepassword")
    h2 = hash_password("samepassword")
    assert h1 != h2


def test_verify_password_correct():
    """正确的密码应该验证通过。"""
    hashed = hash_password("correct_password")
    assert verify_password("correct_password", hashed) is True


def test_verify_password_wrong():
    """错误的密码应该验证失败。"""
    hashed = hash_password("real_password")
    assert verify_password("wrong_password", hashed) is False


def test_verify_password_empty():
    """空密码验证。"""
    hashed = hash_password("")
    assert verify_password("", hashed) is True
    assert verify_password("x", hashed) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/utils/test_auth_crypto.py -v`
Expected: FAIL (ModuleNotFoundError — auth_crypto doesn't exist yet)

- [ ] **Step 3: Implement auth_crypto.py**

```python
"""密码加密与校验工具模块 — 使用 bcrypt 对明文密码进行哈希和校验。

本模块不依赖任何项目内部的业务模块，仅依赖 bcrypt 第三方库。
"""

import bcrypt


def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希（自动生成随机 salt）。

    Args:
        password: 明文密码

    Returns:
        哈希后的密码字符串（包含 salt，可直接存入数据库）
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希。

    Args:
        password: 待校验的明文密码
        password_hash: 数据库中存储的 bcrypt 哈希

    Returns:
        True 表示匹配，False 表示不匹配
    """
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/utils/test_auth_crypto.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Format and lint**

Run: `ruff format tests/utils/test_auth_crypto.py src/utils/auth_crypto.py && ruff check --fix tests/utils/test_auth_crypto.py src/utils/auth_crypto.py`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add tests/utils/test_auth_crypto.py src/utils/auth_crypto.py
git commit -m "feat: add auth_crypto utility module (bcrypt hash/verify)"
```

---

### Task 2: Create AuthService

**Files:**
- Create: `src/services/auth_service.py`
- Test: `tests/services/test_auth_service.py`

**Interfaces:**
- Consumes: `MySQLDB` instance (from `src.infra.db.mysql_db`), Redis client (from `src.infra.redis_client`, optional), `hash_password`/`verify_password` from Task 1
- Produces: `AuthService.__init__(db, redis_client)`, `register(account, password) -> dict`, `login(account, password) -> dict`, `verify_token(token) -> str|None`, `logout(token) -> None`

- [ ] **Step 1: Write tests**

```python
"""测试认证服务层。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.auth_service import AuthService
from src.utils.errors import BusinessError


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.get_user_by_account = AsyncMock(return_value=None)
    db.add_user = AsyncMock()
    db.update_user_token = AsyncMock()
    db.get_user_by_token = AsyncMock()
    return db


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.setex = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def auth_service(mock_db, mock_redis):
    return AuthService(db=mock_db, redis_client=mock_redis)


class TestRegister:
    async def test_register_success(self, auth_service, mock_db):
        """注册成功应返回 user_id 和 account。"""
        mock_db.get_user_by_account.return_value = None
        result = await auth_service.register("test_user", "password123")
        assert "user_id" in result
        assert result["account"] == "test_user"
        mock_db.add_user.assert_awaited_once()

    async def test_register_duplicate_account(self, auth_service, mock_db):
        """重复账号应抛出 BusinessError。"""
        mock_db.get_user_by_account.return_value = {"id": "existing", "account": "test_user"}
        with pytest.raises(BusinessError):
            await auth_service.register("test_user", "password123")


class TestLogin:
    async def test_login_success(self, auth_service, mock_db, mock_redis):
        """登录成功应返回 token 和 user_id。"""
        password_hash = "$2b$12$..."  # mock hash
        mock_db.get_user_by_account.return_value = {
            "id": "user_1", "account": "test_user", "password": password_hash
        }
        with patch("src.services.auth_service.hash_password", return_value=password_hash):
            with patch("src.services.auth_service.verify_password", return_value=True):
                result = await auth_service.login("test_user", "password123")
        assert "token" in result
        assert result["user_id"] == "user_1"
        mock_redis.setex.assert_called_once()
        mock_db.update_user_token.assert_awaited_once()

    async def test_login_wrong_password(self, auth_service, mock_db):
        """错误密码应抛出 BusinessError。"""
        mock_db.get_user_by_account.return_value = {
            "id": "user_1", "account": "test_user", "password": "hashed_pwd"
        }
        with patch("src.services.auth_service.verify_password", return_value=False):
            with pytest.raises(BusinessError) as exc:
                await auth_service.login("test_user", "wrong_password")


class TestVerifyToken:
    async def test_verify_valid_token(self, auth_service, mock_redis):
        """有效 token 应返回 user_id。"""
        mock_redis.get.return_value = b"user_123"
        result = await auth_service.verify_token("valid_token")
        assert result == "user_123"

    async def test_verify_invalid_token(self, auth_service, mock_db):
        """无效 token 应返回 None。"""
        result = await auth_service.verify_token("invalid_token")
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/services/test_auth_service.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement AuthService**

```python
"""认证服务层 — 封装用户注册、登录、令牌验证等业务逻辑。"""

import uuid
from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import MySQLDB
from src.utils.errors import BusinessError
from src.utils.auth_crypto import hash_password, verify_password


class AuthService:
    """用户认证服务，负责注册、登录、令牌验证和登出。

    AuthService 接收 MySQLDB 和可选的 Redis 客户端，不依赖 AppService。
    密码哈希和校验委托给 src.utils.auth_crypto 模块。
    """

    PASSWORD_MIN_LENGTH = 6

    def __init__(self, db: MySQLDB, redis_client=None):
        """初始化 AuthService。

        Args:
            db: MySQLDB 实例（用于用户 CRUD）
            redis_client: Redis 客户端（可选，用于 token 缓存）
        """
        self._db = db
        self._redis = redis_client

    async def register(self, account: str, password: str) -> dict:
        """注册新用户。

        Args:
            account: 登录账号
            password: 明文密码

        Returns:
            dict: 包含 user_id 和 account 的字典

        Raises:
            BusinessError: 账号已存在或密码不符合要求
        """
        if not password or len(password) < self.PASSWORD_MIN_LENGTH:
            raise BusinessError("密码长度不能少于 {} 位".format(self.PASSWORD_MIN_LENGTH))

        existing = await self._db.get_user_by_account(account)
        if existing:
            raise BusinessError("账号已存在")

        user_id = str(uuid.uuid4())
        password_hash = hash_password(password)
        await self._db.add_user(user_id, account, password_hash)
        logger.info("User registered: user_id={} account={}", user_id, account)
        return {"user_id": user_id, "account": account}

    async def login(self, account: str, password: str) -> dict:
        """用户登录。

        Args:
            account: 登录账号
            password: 明文密码

        Returns:
            dict: 包含 token 和 user_id 的字典

        Raises:
            BusinessError: 账号不存在或密码错误
        """
        user = await self._db.get_user_by_account(account)
        if not user:
            raise BusinessError("账号不存在")

        if not verify_password(password, user["password"]):
            raise BusinessError("密码错误")

        token = str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")
        user_id = user["id"]

        # 写入 Redis 缓存（TTL: 7 天）
        if self._redis:
            await self._redis.setex(f"token:{token}", 604800, user_id)
        # 更新 MySQL token
        await self._db.update_user_token(user_id, token)

        logger.info("User logged in: user_id={} account={}", user_id, account)
        return {"token": token, "user_id": user_id}

    async def verify_token(self, token: str) -> Optional[str]:
        """验证会话令牌有效性。

        Args:
            token: 会话令牌

        Returns:
            有效的 user_id，无效时返回 None
        """
        if not token:
            return None

        # 优先查 Redis
        user_id = None
        if self._redis:
            try:
                cached = await self._redis.get(f"token:{token}")
                if cached:
                    user_id = cached.decode("utf-8") if isinstance(cached, bytes) else cached
                    return user_id
            except Exception:
                pass

        # 回退查 MySQL
        user = await self._db.get_user_by_token(token)
        if user:
            user_id = user["id"]
            # 同步回 Redis
            if self._redis and user_id:
                try:
                    await self._redis.setex(f"token:{token}", 604800, user_id)
                except Exception:
                    pass
            return user_id

        return None

    async def logout(self, token: str) -> None:
        """退出登录，清除 Redis 中的 token 缓存。

        Args:
            token: 要清除的会话令牌
        """
        if self._redis and token:
            try:
                await self._redis.delete(f"token:{token}")
            except Exception:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/services/test_auth_service.py -v`
Expected: PASS

- [ ] **Step 5: Format, lint, and commit**

```bash
ruff format tests/services/test_auth_service.py src/services/auth_service.py
ruff check --fix tests/services/test_auth_service.py src/services/auth_service.py
git add tests/services/test_auth_service.py src/services/auth_service.py
git commit -m "feat: add AuthService (register/login/verify_token/logout)"
```

---

### Task 3: Rewrite api/auth.py to thin routes

**Files:**
- Modify: `src/api/auth.py`
- Dependencies: `AuthService` from Task 2

**Interfaces:**
- Consumes: `AuthService` via `svc.auth_service` (added to AppService)

- [ ] **Step 1: Read current auth.py to understand exact logic**

Read: `src/api/auth.py` — pay attention to login/verify/logout/anonymous routes.

- [ ] **Step 2: Add auth_service property to AppService**

In `src/services/app_service.py`, add:

```python
from src.services.auth_service import AuthService
# ... other imports ...

class AppService:
    def __init__(self, ...):
        # ... existing init ...
        self._auth_service: Optional[AuthService] = None

    @property
    def auth_service(self) -> AuthService:
        if self._auth_service is None:
            from src.infra.redis_client import get_redis_client
            self._auth_service = AuthService(
                db=self._db,
                redis_client=get_redis_client(),
            )
        return self._auth_service
```

- [ ] **Step 3: Rewrite auth.py routes**

Replace the business logic with thin route handlers:

```python
"""认证端点 — login/verify/logout/anonymous。"""

import uuid

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse
from loguru import logger

from src.api.model.request import LoginRequest
from src.api.model.response import LoginResponse, VerifyResponse
from src.services.app_service import AppService
from src.api.dependencies import get_app_service
from src.config.response_codes import Code
from src.utils.errors import AuthError

router = APIRouter()


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    response: Response,
    svc: AppService = Depends(get_app_service),
) -> LoginResponse:
    """用户登录或自动注册。"""
    from src.utils.errors import BusinessError

    # 先尝试注册，账号已存在则跳过
    try:
        await svc.auth_service.register(body.account, body.password)
    except BusinessError:
        pass

    # 登录
    result = await svc.auth_service.login(body.account, body.password)

    response.set_cookie(
        key="token",
        value=result["token"],
        httponly=True,
        max_age=604800,  # 7 天
        path="/",
    )
    logger.info("Login success: user_id={}", result["user_id"])
    return LoginResponse(token=result["token"], user_id=result["user_id"])


@router.post("/auth/verify")
async def verify_token(
    token: str = Cookie(None),
    svc: AppService = Depends(get_app_service),
) -> VerifyResponse:
    """校验登录 token 是否有效。"""
    user_id = await svc.auth_service.verify_token(token)
    if user_id:
        return VerifyResponse(valid=True, user_id=user_id)
    return VerifyResponse(valid=False, user_id=None)


@router.post("/auth/logout")
async def logout(
    token: str = Cookie(None),
    svc: AppService = Depends(get_app_service),
) -> JSONResponse:
    """退出登录，清除 token。"""
    await svc.auth_service.logout(token)
    return JSONResponse({"message": "已退出登录"})


@router.post("/auth/anonymous")
async def get_anonymous_id(
    user_id: str = Cookie(None),
    response: Response = None,
) -> JSONResponse:
    """获取或生成匿名用户 ID。"""
    if not user_id:
        user_id = str(uuid.uuid4())
        response.set_cookie(
            key="user_id",
            value=user_id,
            httponly=True,
            max_age=31536000,  # 1 年
            path="/",
        )
    return JSONResponse({"user_id": user_id})
```

Clean up imports: remove `from src.infra.auth.user_auth`, `from src.infra.redis_client`.

- [ ] **Step 4: Run existing auth tests**

Run: `pytest tests/api/test_auth.py -v`
Expected: PASS (after adjusting AppService dependency)

- [ ] **Step 5: Format, lint, and commit**

```bash
ruff format src/services/app_service.py src/api/auth.py
ruff check --fix src/services/app_service.py src/api/auth.py
git add src/services/app_service.py src/api/auth.py
git commit -m "refactor: sink auth logic to AuthService, api/auth.py now thin routes"
```

---

### Task 4: Add store_and_process() to DocumentService

**Files:**
- Modify: `src/services/document_service.py`
- Modify: `src/api/documents.py`
- Modify: `tests/api/test_documents.py`

**Interfaces:**
- Consumes: `FileStore`, `MySQLDB`, `VectorStore` (already in DocumentService constructor)
- Produces: `DocumentService.store_and_process(kb_id, filename, content: bytes, ext: str) -> dict`

- [ ] **Step 1: Add store_and_process() to DocumentService**

In `src/services/document_service.py`, add the method before `process_document`:

```python
async def store_and_process(
    self,
    kb_id: str,
    filename: str,
    content: bytes,
    ext: str,
) -> dict:
    """封装文件上传后的全流程：校验 → 去重 → MinIO 上传 → DB 写入 → 后台处理。

    Args:
        kb_id: 知识库 UUID
        filename: 原始文件名
        content: 文件二进制内容
        ext: 文件扩展名（如 .pdf, .docx, .txt）

    Returns:
        dict: 包含 doc_id, status, filename 和可选的 dedup 信息

    Raises:
        ValidationError: 文件类型不支持或文件过大
    """
    from src.config import MAX_FILE_SIZE
    from src.infra.db.file_store import FileStore

    # 1. 文件大小校验
    if len(content) > MAX_FILE_SIZE:
        from src.utils.errors import ValidationError
        raise ValidationError("文件大小超过限制")

    # 2. 文件类型校验
    ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
    if ext.lower() not in ALLOWED_EXTENSIONS:
        from src.utils.errors import ValidationError
        raise ValidationError("不支持的文件类型")

    # 3. MD5 去重
    import hashlib
    file_hash = hashlib.md5(content).hexdigest()
    existing_docs = await self.db.get_documents(kb_id)
    for doc in existing_docs:
        if doc.get("hash") == file_hash:
            logger.info("Duplicate document detected: hash={} existing_doc_id={}", file_hash, doc["id"])
            return {"doc_id": doc["id"], "filename": filename, "dedup": True}

    # 4. 生成 doc_id 并上传到 MinIO
    import uuid
    doc_id = str(uuid.uuid4())
    file_store = FileStore()
    minio_key = await file_store.upload(kb_id, doc_id, ext, content)

    # 5. 写入 MySQL 元信息
    await self.db.add_document(
        doc_id=doc_id,
        kb_id=kb_id,
        filename=filename,
        file_type=ext.lstrip("."),
        file_size=len(content),
        hash=file_hash,
        status="processing",
    )

    # 6. 启动后台处理任务
    asyncio.create_task(
        self.process_document(kb_id, doc_id, minio_key, filename, ext)
    )

    logger.info("Document submitted for processing: doc_id={} kb_id={} filename={}", doc_id, kb_id, filename)
    return {"doc_id": doc_id, "status": "processing", "filename": filename}
```

- [ ] **Step 2: Rewrite api/documents.py upload handler**

Replace the upload_document function body:

```python
@router.post("/kbs/documents/upload", status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form(...),
    request: Request = None,
    svc: AppService = Depends(get_app_service),
) -> UploadDocumentResponse:
    """上传文档到知识库。"""
    content = await file.read()
    ext = os.path.splitext(file.filename)[1].lower()
    result = await svc.document.store_and_process(
        kb_id=kb_id,
        filename=file.filename,
        content=content,
        ext=ext,
    )

    if result.get("dedup"):
        return UploadDocumentResponse(
            doc_id=result["doc_id"],
            status="ready",
            filename=result["filename"],
        )

    return UploadDocumentResponse(
        doc_id=result["doc_id"],
        status=result["status"],
        filename=result["filename"],
    )
```

Clean up imports in api/documents.py: remove `from src.config import MAX_FILE_SIZE, MAX_TABLE_TOKENS`, `from src.infra.db.file_store import FileStore`, `hashlib`, `asyncio` (if no other usage).

- [ ] **Step 3: Update tests/api/test_documents.py**

```python
"""测试文档上传与处理 API。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_upload_document_success(svc, client):
    """上传文档应返回 doc_id 和 processing 状态。"""
    mock_result = {"doc_id": "test_doc_id", "status": "processing", "filename": "test.pdf"}
    svc.document.store_and_process = AsyncMock(return_value=mock_result)

    # POST with file...
    file_content = b"%PDF-1.4 test content"
    response = await client.post(
        "/kbs/documents/upload",
        data={"kb_id": "test_kb_id"},
        files={"file": ("test.pdf", file_content, "application/pdf")},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["doc_id"] == "test_doc_id"
    assert data["status"] == "processing"
```

- [ ] **Step 4: Run document tests**

Run: `pytest tests/api/test_documents.py -v`
Expected: PASS

- [ ] **Step 5: Format, lint, full test run**

```bash
ruff format src/services/document_service.py src/api/documents.py tests/api/test_documents.py
ruff check --fix src/services/document_service.py src/api/documents.py tests/api/test_documents.py
pytest tests/ -v
git add src/services/document_service.py src/api/documents.py tests/api/test_documents.py
git commit -m "refactor: consolidate document upload to store_and_process, api layer now thin"
```

---

### Task 5: Fix api/sessions.py direct db/chat_manager calls

**Files:**
- Modify: `src/api/sessions.py`
- Modify: `src/services/app_service.py` (add delegate methods)

**Interfaces:**
- Consumes: AppService with new delegate methods

- [ ] **Step 1: Add delegate methods to AppService**

In `src/services/app_service.py`, add:

```python
# ==== Session/Message Delegates ====

async def get_sessions(self) -> list[dict]:
    """获取最近 50 个会话。"""
    return await self._db.get_sessions()

async def get_session_by_id(self, session_id: str) -> Optional[dict]:
    """按 ID 查询会话。"""
    return await self._db.get_session_by_id(session_id)

async def get_messages(self, session_id: str) -> list[dict]:
    """获取会话消息。"""
    return await self._db.get_messages(session_id)

async def delete_session_and_messages(self, session_id: str) -> bool:
    """删除会话及其消息。"""
    await self._chat_manager.clear_history_async(session_id)
    return await self._db.delete_session_and_messages(session_id)
```

- [ ] **Step 2: Update api/sessions.py**

Replace all `svc.db.xxx()` calls with `svc.xxx()`:
- `svc.db.get_sessions()` → `svc.get_sessions()`
- `svc.db.get_session_by_id()` → `svc.get_session_by_id()`
- `svc.db.get_messages()` → `svc.get_messages()`
- `svc.db.delete_session_and_messages()` → `svc.delete_session_and_messages()`
- `svc.chat_manager.clear_history_async()` → (already in delete_session_and_messages)

Remove unused imports: `from src.infra.db.mysql_db import MySQLDB` (if present).

- [ ] **Step 3: Run session tests**

Run: `pytest tests/api/test_sessions.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
ruff format src/services/app_service.py src/api/sessions.py
ruff check --fix src/services/app_service.py src/api/sessions.py
git add src/services/app_service.py src/api/sessions.py
git commit -m "refactor: add session delegate methods to AppService, fix api/sessions.py layer violations"
```

---

### Task 6: Fix api/chat.py chat_manager calls and sse import

**Files:**
- Modify: `src/api/chat.py`
- Modify: `src/services/app_service.py` (add delegate methods)
- Delete: `src/api/sse_utils.py`

**Interfaces:**
- Consumes: AppService with new chat manager delegate methods

- [ ] **Step 1: Add chat manager delegate methods to AppService**

In `src/services/app_service.py`, add:

```python
# ==== Chat Manager Delegates ====

async def set_mysql_db(self, db: MySQLDB) -> None:
    """设置 chat_manager 的 MySQL DB 实例。"""
    await self._chat_manager.set_mysql_db(db)

async def save_session_async(self, session_id: str, title: str, kb_id: str, user_id: str = "") -> None:
    """持久化保存会话。"""
    await self._chat_manager.save_session_async(session_id, title, kb_id, user_id)

async def save_messages_async(self, session_id: str, kb_id: str, messages: list[dict]) -> None:
    """批量持久化保存消息。"""
    await self._chat_manager.save_messages_async(session_id, kb_id, messages)
```

- [ ] **Step 2: Update api/chat.py**

Replace:
- `svc.chat_manager.set_mysql_db(...)` → `svc.set_mysql_db(...)`
- `svc.chat_manager.save_session_async(...)` → `svc.save_session_async(...)`
- `svc.chat_manager.save_messages_async(...)` → `svc.save_messages_async(...)`

Fix SSE import:
```python
# Before:
from src.api.sse_utils import sse_done, sse_error
# After:
from src.utils.sse import sse_done, sse_error
```

- [ ] **Step 3: Delete api/sse_utils.py**

Run: `rm src/api/sse_utils.py`

- [ ] **Step 4: Run chat tests**

Run: `pytest tests/api/test_chat.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff format src/services/app_service.py src/api/chat.py
ruff check --fix src/services/app_service.py src/api/chat.py
git add src/services/app_service.py src/api/chat.py
git rm src/api/sse_utils.py
git commit -m "refactor: add chat manager delegates to AppService, fix sse import path, delete bridge"
```

---

### Task 7: Fix api/kb_eval.py, ragas_generate.py, health.py, llm_test.py

**Files:**
- Modify: `src/api/kb_eval.py`
- Modify: `src/api/ragas_generate.py`
- Modify: `src/api/health.py`
- Modify: `src/api/llm_test.py`
- Modify: `src/services/app_service.py` (add delegates and settings property)

**Interfaces:**
- Consumes: AppService with new delegates and `.settings` property

- [ ] **Step 1: Add remaining delegate methods and settings property to AppService**

In `src/services/app_service.py`, add the `settings` property and remaining delegates:

```python
# ==== Config Property ====

@property
def settings(self):
    """返回配置模块，允许 api 层通过 svc.settings.X 访问配置。"""
    import src.config.settings as _settings
    return _settings

# ==== Eval Report Delegate ====

async def get_latest_eval_report(self, kb_id: str) -> dict | None:
    """获取知识库最新的 RAGAS 评估报告。"""
    return await self._db.get_latest_eval_report(kb_id)

# ==== KB Name Lookup Delegate ====

async def get_kb_by_name(self, user_id: str, name: str) -> str | None:
    """按名称查询知识库 ID。"""
    return await self._db.get_kb_by_name(user_id, name)
```

- [ ] **Step 2: Fix kb_eval.py**

Replace:
```python
# Before:
result = await svc.db.get_latest_eval_report(body.kb_id)
# After:
result = await svc.get_latest_eval_report(body.kb_id)
```

- [ ] **Step 3: Fix ragas_generate.py**

Replace:
```python
# Before:
from src.config import RAGAS_DATA_DIR, RAGAS_USER_ID
svc = AppService()
kb_id = await svc.db.get_kb_by_name(RAGAS_USER_ID, body.kb_name)
# After:
from src.services.app_service import AppService
svc = AppService()
kb_id = await svc.get_kb_by_name(svc.settings.RAGAS_USER_ID, body.kb_name)
```

Also replace `RAGAS_DATA_DIR` with `svc.settings.RAGAS_DATA_DIR`.

- [ ] **Step 4: Fix health.py**

Replace:
```python
# Before:
from src.config import MAX_FILE_SIZE
class _ConfigService:
    async def get_max_upload_size(self):
        return MAX_FILE_SIZE
# After:
# 将 _ConfigService 改为从 svc.settings 读取
```

Better approach: replace the `_ConfigService` class usage with direct access to svc.settings. Since health endpoints don't use Depends(get_app_service), we need to either:
1. Add Depends to health routes
2. Keep a standalone config class but inject settings

Since health.py is a simple non-DB endpoint, the cleanest fix is to use the existing route + import settings through a thin layer. But the rule says api/ must not import config/ directly.

For health.py, we can:
- Add `svc: AppService = Depends(get_app_service)` to the config endpoint
- Use `svc.settings.MAX_FILE_SIZE`

```python
@router.post("/config")
async def app_config(svc: AppService = Depends(get_app_service)) -> AppConfigResponse:
    return AppConfigResponse(max_upload_size=svc.settings.MAX_FILE_SIZE)
```

Remove `from src.config import MAX_FILE_SIZE` from imports.

- [ ] **Step 5: Fix llm_test.py**

Replace:
```python
# Before:
from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
class LlmTestRequest(BaseModel):
    model: str = settings.LLM_MODEL
    ...

@router.post("/llm/test")
async def llm_test(body: LlmTestRequest) -> BaseResponse:
    llm = ChatOpenAI(model=body.model, temperature=body.temperature,
                     api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)
# After:
@router.post("/llm/test")
async def llm_test(body: LlmTestRequest, svc: AppService = Depends(get_app_service)) -> BaseResponse:
    llm = ChatOpenAI(model=body.model, temperature=body.temperature,
                     api_key=svc.settings.DASHSCOPE_API_KEY,
                     base_url=svc.settings.DASHSCOPE_BASE_URL)
```

For the default model in `LlmTestRequest`, we can't easily use svc.settings at class level. Options:
- Keep a temporary import for the default value only
- Make the model default `""` and fall back to svc.settings.LLM_MODEL in the handler
- Accept that Pydantic class-level defaults need a lightweight import

The cleanest approach for this edge case:
```python
class LlmTestRequest(BaseModel):
    model: str = ""  # Empty means use default
    prompt: str = "你好，请回复OK"
    temperature: float = 0
```

Then in the handler:
```python
model_name = body.model or svc.settings.LLM_MODEL
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
ruff format src/services/app_service.py src/api/kb_eval.py src/api/ragas_generate.py src/api/health.py src/api/llm_test.py
ruff check --fix src/services/app_service.py src/api/kb_eval.py src/api/ragas_generate.py src/api/health.py src/api/llm_test.py
git add src/services/app_service.py src/api/kb_eval.py src/api/ragas_generate.py src/api/health.py src/api/llm_test.py
git commit -m "refactor: fix remaining api/ layer violations — kb_eval, ragas_generate, health, llm_test"
```

---

### Task 8: Delete RAGChain dead code and stale test files

**Files:**
- Delete: `src/rag/chain.py`
- Delete: `tests/rag/test_chain.py`
- Delete: `tests/rag/test_stream.py`
- Delete: `tests/rag/test_prompt.py`
- Delete: `tests/rag/test_rag_chain_tracing.py`
- Modify: `src/rag/__init__.py` (remove RAGChain export)

- [ ] **Step 1: Update rag/__init__.py**

Change `src/rag/__init__.py` from:
```python
"""RAG 问答流水线 — 检索、重排序、Prompt 构建、流式生成。"""

from src.rag.chain import RAGChain
from src.rag.context import RAGContext

__all__ = ["RAGChain", "RAGContext"]
```
to:
```python
"""RAG 问答流水线 — 检索、重排序、Prompt 构建、流式生成。"""

from src.rag.context import RAGContext

__all__ = ["RAGContext"]
```

- [ ] **Step 2: Delete dead files**

```bash
git rm src/rag/chain.py
git rm tests/rag/test_chain.py
git rm tests/rag/test_stream.py
git rm tests/rag/test_prompt.py
git rm tests/rag/test_rag_chain_tracing.py
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: PASS (no more import errors from deleted files)

- [ ] **Step 4: Commit**

```bash
git add src/rag/__init__.py
git commit -m "cleanup: remove RAGChain dead code and 4 stale test files"
```

---

### Task 9: Fix stale test files in test_app_service.py and test_eval_ragas.py

**Files:**
- Modify: `tests/services/test_app_service.py`
- Modify: `tests/eval/test_eval_ragas.py`

- [ ] **Step 1: Read and fix test_app_service.py**

Read `tests/services/test_app_service.py` and remove all `@patch("src.services.app_service.RAGChain")` decorators. These mocks reference a class that no longer exists.

For each test that uses `mock_rag_chain`, replace with direct instantiation of `AppService()` since it no longer depends on RAGChain.

- [ ] **Step 2: Read and fix test_eval_ragas.py**

Read `tests/eval/test_eval_ragas.py` and remove any mocks referencing `chat_with_citations` (deleted in Phase 3).

- [ ] **Step 3: Run affected tests**

Run: `pytest tests/services/test_app_service.py tests/eval/test_eval_ragas.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
ruff format tests/services/test_app_service.py tests/eval/test_eval_ragas.py
ruff check --fix tests/services/test_app_service.py tests/eval/test_eval_ragas.py
git add tests/services/test_app_service.py tests/eval/test_eval_ragas.py
git commit -m "fix: remove stale RAGChain mocks from test_app_service and test_eval_ragas"
```

---

### Task 10: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```
Expected: ALL PASS

- [ ] **Step 2: Run lint check**

```bash
ruff check .
```
Expected: No errors

- [ ] **Step 3: Scan for debug code**

```bash
grep -rn "print(" src/ --include="*.py" | grep -v "#.*print" | grep -v "logger"
```
Expected: No print() statements in production code (only allowed in cli/ or tests/)

- [ ] **Step 4: Verify layer compliance**

```bash
grep -rn "^from src\.infra\|^from src\.config" src/api/ --include="*.py"
```
Expected: No output (api/ should not import infra/ or config/)

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: final verification — all tests pass, lint clean, layer rules enforced"
```
