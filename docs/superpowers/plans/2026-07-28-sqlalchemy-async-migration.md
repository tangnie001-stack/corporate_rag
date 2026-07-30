# SQLAlchemy 2.0 Async ORM + Alembic 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将项目数据库层从手写 SQL（`aiomysql` + `queries.py`）迁移到 SQLAlchemy 2.0 async ORM + Alembic，保留 Repository 模式，不改 Repo 方法签名。

**Architecture:** 新增 `engine.py` 提供 async engine 和 session_factory；新增 `models/` 目录存放 ORM 模型；5 个 Repo 内部实现从 SQL 字符串改为 ORM 查询，方法签名不变；AppService 依赖从 `MySQLDB` 实例变为 `session_factory`；Alembic 管理 schema 版本；`ChunkResult`/`ChunkQueryResult` 从 `entities/search.py` 搬迁到 `vector_store/types.py`。

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 (async), aiomysql, Alembic, FastAPI

## Global Constraints

- 所有 Repo 方法签名保持不变（Service 层零感知）
- 所有 Repo 方法内部使用 `async with self._sf() as session:` 自己管理 session 生命周期
- `expire_on_commit=False`（commit 后对象仍可读）
- 异步驱动使用 `aiomysql`（`mysql+aiomysql://`）
- lifespan 不自动执行 Alembic，改为手动 `docker compose exec app alembic upgrade head`
- 全量迁移后删除 `src/config/queries.py`、`src/infra/db/entities/`（search.py 搬迁）、`src/infra/db/mysql_db/pool.py`
- 不引入跨 Repo 事务（当前无此需求）
- 不修改 VectorStore（ChromaDB）的接口
- 迁移时全量清理 MySQL + Redis + MinIO + ChromaDB

---

## 文件结构

### 新增文件
```
src/infra/db/base.py                    — DeclarativeBase + Mixins
src/infra/db/engine.py                  — async_engine + session_factory
src/infra/db/models/__init__.py         — 统一导出所有 Model
src/infra/db/models/user.py             — UserModel
src/infra/db/models/kb.py              — KbModel
src/infra/db/models/document.py         — DocModel
src/infra/db/models/chat.py            — SessionModel + MessageModel
src/infra/db/models/eval_report.py     — EvalReportModel
src/infra/db/vector_store/types.py      — ChunkResult + ChunkQueryResult（原 search.py 搬迁）
scripts/clean_all_data.py               — 全量清理脚本
alembic.ini + alembic/                  — Alembic 迁移管理
alembic/env.py                          — 异步引擎支持
alembic/versions/0001_init.py           — 第一版迁移脚本
```

### 修改文件
```
src/infra/db/__init__.py                — 改：删除 MySQLDB 导出
src/infra/db/mysql_db/user_repo.py      — 改：内部从手写 SQL → ORM
src/infra/db/mysql_db/eval_repo.py      — 改：同上
src/infra/db/mysql_db/kb_repo.py        — 改：同上
src/infra/db/mysql_db/chat_repo.py      — 改：同上
src/infra/db/mysql_db/document_repo.py  — 改：同上
src/infra/db/vector_store/__init__.py   — 改：ChunkResult import 路径
src/infra/db/vector_store/search.py     — 改：ChunkResult import 路径
src/services/app_service.py             — 改：session_factory 替换 mysql_db，EvalReportModel 替换 EvalReportEntity
src/services/__init__.py                — 改：确认无 MySQLDB 引用
src/rag/retrieval.py                    — 改：ChunkResult import 路径
src/infra/search/bm25_index.py          — 改：ChunkResult import 路径
src/agents/graph/state.py              — 改：ChunkResult import 路径
src/api/dependencies.py                 — 改：确认 AppService() 无参构造
src/cli/eval_ragas.py                   — 改：svc.db → svc._kb_repo
src/main.py                             — 改：删除 lifespan 中 db.init_db()
src/chat/persistence.py                 — 改：SessionEntity → SessionModel，MessageEntity → MessageModel
tests/conftest.py                       — 改：mysql_db fixture → session_factory fixture，svc.db 修复
tests/reset_data.py                     — 改：engine 替代 MySQLDB
tests/infra/db/test_mysql_db.py         — 改：session_factory 模式
tests/services/test_app_service.py      — 改：去掉 MySQLDB patch
tests/services/test_auth_service.py     — 改：UserEntity → MagicMock
tests/api/test_documents.py             — 改：DocEntity → MagicMock
tests/rag/test_retrieval.py             — 改：ChunkResult import 路径
tests/infra/search/test_bm25_index.py   — 改：ChunkResult import 路径
tests/agents/graph/test_grader.py       — 改：ChunkResult import 路径
```

### 删除文件
```
src/config/queries.py
src/infra/db/entities/__init__.py
src/infra/db/entities/user.py
src/infra/db/entities/kb.py
src/infra/db/entities/document.py
src/infra/db/entities/chat.py
src/infra/db/entities/eval_report.py
src/infra/db/entities/search.py          → 已搬迁到 vector_store/types.py
src/infra/db/mysql_db/pool.py
```

---

### Task 1: 创建 base.py——DeclarativeBase + Mixins

**Files:**
- Create: `src/infra/db/base.py`

**Interfaces:**
- Produces: `Base(DeclarativeBase)`, `IDMixin`, `TimestampMixin`, `UTCDateTime`, `new_id()`

- [ ] **Step 1: 创建 base.py**

```python
"""声明式基类与通用 Mixin。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Store instants in UTC and always return timezone-aware datetimes。

    SQLite drops timezone metadata for ``DateTime`` values。 The decorator keeps
    its persisted value in UTC and restores the missing UTC tzinfo on reads;
    timezone-aware databases retain their native timestamp semantics。
    """

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class IDMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/base.py
git commit -m "feat: add sqlalchemy declarative base and mixins"
```

---

### Task 2: 创建 engine.py——异步引擎 + Session 工厂

**Files:**
- Create: `src/infra/db/engine.py`

**Interfaces:**
- Produces: `engine`, `session_factory`（模块级变量）

- [ ] **Step 1: 创建 engine.py**

```python
"""SQLAlchemy 异步引擎与 Session 工厂。"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)

DSN = (
    f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)

engine = create_async_engine(
    DSN,
    pool_size=10,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
```

- [ ] **Step 2: 验证无导入异常**

```bash
python -c "from src.infra.db.engine import engine, session_factory; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/infra/db/engine.py
git commit -m "feat: add async engine and session factory"
```

---

### Task 3: 搬迁 ChunkResult / ChunkQueryResult 到 vector_store/types.py

**Files:**
- Create: `src/infra/db/vector_store/types.py`
- Delete: `src/infra/db/entities/search.py`

- [ ] **Step 1: 创建 types.py（内容同 search.py，路径不同）**

```python
"""检索结果类型 — ChromaDB 语义检索和 BM25 词法检索的统一输出类型。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class ChunkResult:
    """检索结果统一类型。"""
    id: str
    content: str
    metadata: dict = field(default_factory=dict)
    distance: Optional[float] = None
    bm25_score: Optional[float] = None


@dataclass(slots=True)
class ChunkQueryResult:
    """分块分页查询结果。"""
    items: list[ChunkResult]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 2: 删除旧的 search.py**

```bash
git rm src/infra/db/entities/search.py
```

- [ ] **Step 3: 更新 src/infra/db/vector_store/__init__.py 的 import**

```python
# 改这一行
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
# 为
from src.infra.db.vector_store.types import ChunkResult, ChunkQueryResult
```

- [ ] **Step 4: 更新 src/infra/db/vector_store/search.py 的 import**

```python
# 改这一行
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
# 为
from src.infra.db.vector_store.types import ChunkResult, ChunkQueryResult
```

- [ ] **Step 5: 更新 src/rag/retrieval.py 的 import**

```python
# 改这一行
from src.infra.db.entities import ChunkResult
# 为
from src.infra.db.vector_store.types import ChunkResult
```

- [ ] **Step 6: 更新 src/infra/search/bm25_index.py 的 import**

```python
# 改这一行
from src.infra.db.entities import ChunkResult
# 为
from src.infra.db.vector_store.types import ChunkResult
```

- [ ] **Step 7: 更新 src/agents/graph/state.py 的 import**

```python
# 改这一行
from src.infra.db.entities import ChunkResult
# 为
from src.infra.db.vector_store.types import ChunkResult
```

- [ ] **Step 8: 更新测试文件中的 import（3 个文件）**

`tests/rag/test_retrieval.py`：
```python
from src.infra.db.entities.search import ChunkResult
# 改为
from src.infra.db.vector_store.types import ChunkResult
```

`tests/infra/search/test_bm25_index.py`：
```python
from src.infra.db.entities import ChunkResult
# 改为
from src.infra.db.vector_store.types import ChunkResult
```

`tests/agents/graph/test_grader.py`：
```python
from src.infra.db.entities import ChunkResult
# 改为
from src.infra.db.vector_store.types import ChunkResult
```

`tests/api/test_documents.py`：
```python
from src.infra.db.entities import DocEntity, ChunkResult, ChunkQueryResult
# 改为（ChunkResult/ChunkQueryResult 改路径，DocEntity 以后替换为 MagicMock）
from src.infra.db.vector_store.types import ChunkResult, ChunkQueryResult
```

- [ ] **Step 9: 运行测试验证**

```bash
python -c "from src.infra.db.vector_store.types import ChunkResult, ChunkQueryResult; print('OK')"
```

- [ ] **Step 10: Commit**

```bash
git add src/infra/db/vector_store/types.py src/infra/db/vector_store/__init__.py src/infra/db/vector_store/search.py
git add src/services/app_service.py src/rag/retrieval.py src/infra/search/bm25_index.py src/agents/graph/state.py
git add tests/rag/test_retrieval.py tests/infra/search/test_bm25_index.py tests/agents/graph/test_grader.py tests/api/test_documents.py
git rm src/infra/db/entities/search.py
git commit -m "refactor: move ChunkResult/ChunkQueryResult to vector_store/types.py"
```

---

### Task 4: 创建 UserModel

**Files:**
- Create: `src/infra/db/models/__init__.py`
- Create: `src/infra/db/models/user.py`

**Interfaces:**
- Produces: `UserModel`（ORM 模型，替代 UserEntity + 对应 CREATE TABLE）

- [ ] **Step 1: 创建 models/__init__.py**

```python
"""SQLAlchemy ORM 模型统一导出。"""
```

- [ ] **Step 2: 创建 models/user.py**

```python
"""用户表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class UserModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "users"

    account: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="登录账号")
    password: Mapped[str] = mapped_column(String(256), nullable=False, comment="bcrypt 哈希")
    token: Mapped[str | None] = mapped_column(String(256), comment="当前登录 token")
```

- [ ] **Step 3: 验证可导入**

```bash
python -c "from src.infra.db.models.user import UserModel; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/infra/db/models/
git commit -m "feat: add UserModel ORM model"
```

---

### Task 5: 创建 KbModel

**Files:**
- Create: `src/infra/db/models/kb.py`

- [ ] **Step 1: 创建 models/kb.py**

```python
"""知识库表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class KbModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "knowledge_base"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="所属用户")
    name: Mapped[str] = mapped_column(String(256), nullable=False, comment="知识库名称")
    description: Mapped[str] = mapped_column(String(1024), default="", comment="描述")
    doc_count: Mapped[int] = mapped_column(Integer, default=0, comment="关联文档数")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0, comment="软删除标志")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uk_user_kb"),
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/models/kb.py
git commit -m "feat: add KbModel ORM model"
```

---

### Task 6: 创建 DocModel

**Files:**
- Create: `src/infra/db/models/document.py`

- [ ] **Step 1: 创建 models/document.py**

```python
"""文档表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class DocModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "document"

    kb_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="所属知识库")
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), default="", comment="文件类型")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(String(1024), comment="MinIO 路径")
    user_id: Mapped[str] = mapped_column(String(32), default="", comment="上传用户")
    md5: Mapped[str | None] = mapped_column(String(64), comment="文件 MD5")
    hash: Mapped[str | None] = mapped_column(String(64), comment="备用哈希")
    status: Mapped[str] = mapped_column(String(32), default="pending", comment="pending/processing/ready/failed")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_strategy: Mapped[str | None] = mapped_column(String(64))
    processing_state: Mapped[str | None] = mapped_column(String(64))
    processing_progress: Mapped[int] = mapped_column(Integer, default=0)
    processing_message: Mapped[str | None] = mapped_column(String(512))
    error_msg: Mapped[str | None] = mapped_column(String(1024))
    meta_info: Mapped[str | None] = mapped_column(Text, comment="JSON 扩展信息")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/models/document.py
git commit -m "feat: add DocModel ORM model"
```

---

### Task 7: 创建 SessionModel + MessageModel + EvalReportModel

**Files:**
- Create: `src/infra/db/models/chat.py`
- Create: `src/infra/db/models/eval_report.py`

- [ ] **Step 1: 创建 models/chat.py**

```python
"""会话和消息表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, Text, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class SessionModel(Base, IDMixin, TimestampMixin):
    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(256), default="新对话")
    is_deleted: Mapped[int] = mapped_column(Integer, default=0)


class MessageModel(Base):
    __tablename__ = "conversation_history"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, comment="user/assistant/system")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[str | None] = mapped_column(Text, comment="来源引用 JSON")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model_name: Mapped[str | None] = mapped_column(String(64), comment="模型名称")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: 创建 models/eval_report.py**

```python
"""评估报告表 ORM 模型。"""

from datetime import datetime

from sqlalchemy import String, Integer, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infra.db.base import Base, UTCDateTime


class EvalReportModel(Base):
    __tablename__ = "eval_report"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    kb_id: Mapped[str] = mapped_column(String(32), nullable=False)
    run_type: Mapped[str] = mapped_column(String(64), default="")
    qa_count: Mapped[int] = mapped_column(Integer, default=0)
    faithfulness: Mapped[float] = mapped_column(default=0.0)
    answer_relevancy: Mapped[float] = mapped_column(default=0.0)
    context_precision: Mapped[float] = mapped_column(default=0.0)
    context_recall: Mapped[float] = mapped_column(default=0.0)
    overall_score: Mapped[float] = mapped_column(default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    report_path: Mapped[str | None] = mapped_column(String(512))
    triggered_by: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[str | None] = mapped_column(Text, comment="JSON 详细评估数据")
    eval_date: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 3: 更新 models/__init__.py 导出**

```python
"""SQLAlchemy ORM 模型统一导出。"""

from src.infra.db.models.user import UserModel
from src.infra.db.models.kb import KbModel
from src.infra.db.models.document import DocModel
from src.infra.db.models.chat import SessionModel, MessageModel
from src.infra.db.models.eval_report import EvalReportModel

__all__ = [
    "UserModel",
    "KbModel",
    "DocModel",
    "SessionModel",
    "MessageModel",
    "EvalReportModel",
]
```

- [ ] **Step 4: 验证所有模型可导入**

```bash
python -c "from src.infra.db.models import *; print('All models OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/infra/db/models/
git commit -m "feat: add chat and eval_report ORM models"
```

---

### Task 8: Alembic 初始化 + 第一版迁移

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/0001_init.py`（autogenerate 生成）

- [ ] **Step 1: 初始化 Alembic**

```bash
pip install alembic
cd /mnt/d/code/demo/AIAgent/corporate_rag
alembic init alembic
```

- [ ] **Step 2: 编辑 alembic.ini**

```ini
sqlalchemy.url = mysql+aiomysql://root:financial_qa_pass@localhost:3306/financial_qa?charset=utf8mb4
```

- [ ] **Step 3: 编辑 alembic/env.py（异步支持）**

替换 `run_migrations_online()` 为：

```python
from sqlalchemy.ext.asyncio import create_async_engine

from src.infra.db.base import Base
from src.infra.db.models import *  # noqa: F403 — 加载所有 Model 确保 metadata 完整

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """异步迁移 wrapper。"""
    import asyncio
    asyncio.run(run_async_migrations())
```

- [ ] **Step 4: 生成第一版迁移**

```bash
alembic revision --autogenerate -m "init models"
```

检查生成的 `alembic/versions/` 文件，确认包含所有 6 张表的 CREATE TABLE 语句。

- [ ] **Step 5: 审查迁移脚本**

确认每张表的字段和约束与旧 CREATE TABLE 一一对应：
- `users`: id, account(unique), password, token, created_at
- `knowledge_base`: id, user_id, name, description, doc_count, is_deleted, created_at, updated_at + uk_user_kb
- `document`: id, kb_id, filename, file_type, file_size, file_path, user_id, md5, hash, status, chunk_count, ...
- `sessions`: id, user_id, kb_id, title, is_deleted, created_at, updated_at
- `conversation_history`: id, session_id, kb_id, role, content, sources, prompt_tokens, ...
- `eval_report`: id, kb_id, run_type, qa_count, faithfulness, ...

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: initialize alembic with init migration"
```

---

### Task 9: 重写 UserRepo（独立表，3 个方法）

**Files:**
- Modify: `src/infra/db/mysql_db/user_repo.py`

- [ ] **Step 1: 重写 UserRepo**

```python
"""用户 Repo — users 表 CRUD。"""

from typing import Optional
from sqlalchemy import select
from src.infra.db.models.user import UserModel


class UserRepo:
    """用户 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        async with self._sf() as session:
            user = UserModel(id=user_id, account=account, password=password_hash)
            session.add(user)
            await session.commit()

    async def get_user_by_account(self, account: str) -> Optional[UserModel]:
        async with self._sf() as session:
            stmt = select(UserModel).where(UserModel.account == account)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update_user_token(self, user_id: str, token: str) -> None:
        async with self._sf() as session:
            user = await session.get(UserModel, user_id)
            if user:
                user.token = token
                await session.commit()

    async def get_user_by_token(self, token: str) -> Optional[UserModel]:
        async with self._sf() as session:
            stmt = select(UserModel).where(UserModel.token == token)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
```

- [ ] **Step 2: 验证可导入**

```bash
python -c "from src.infra.db.mysql_db.user_repo import UserRepo; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/infra/db/mysql_db/user_repo.py
git commit -m "refactor: rewrite UserRepo with sqlalchemy orm"
```

---

### Task 10: 重写 EvalRepo（独立表，2 个方法）

**Files:**
- Modify: `src/infra/db/mysql_db/eval_repo.py`

- [ ] **Step 1: 重写 EvalRepo**

```python
"""评估报告 Repo — eval_report 表 CRUD。"""

import json
import uuid
from typing import Optional
from sqlalchemy import select
from src.infra.db.models.eval_report import EvalReportModel


class EvalRepo:
    """评估报告 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def insert_report(self, report: EvalReportModel) -> None:
        async with self._sf() as session:
            detail_str = (
                json.dumps(report.detail_json, ensure_ascii=False)
                if report.detail_json
                else None
            )
            record = EvalReportModel(
                id=str(uuid.uuid4()),
                kb_id=report.kb_id,
                run_type=report.run_type,
                qa_count=report.qa_count,
                faithfulness=report.faithfulness,
                answer_relevancy=report.answer_relevancy,
                context_precision=report.context_precision,
                context_recall=report.context_recall,
                overall_score=report.overall_score,
                passed=report.passed,
                report_path=report.report_path,
                triggered_by=report.triggered_by,
                detail_json=detail_str,
                eval_date=report.eval_date,
            )
            session.add(record)
            await session.commit()

    async def get_latest_report(self, kb_id: str) -> Optional[EvalReportModel]:
        async with self._sf() as session:
            stmt = (
                select(EvalReportModel)
                .where(EvalReportModel.kb_id == kb_id)
                .order_by(EvalReportModel.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/mysql_db/eval_repo.py
git commit -m "refactor: rewrite EvalRepo with sqlalchemy orm"
```

---

### Task 11: 重写 KbRepo（核心，6 个方法）

**Files:**
- Modify: `src/infra/db/mysql_db/kb_repo.py`

- [ ] **Step 1: 重写 KbRepo**

```python
"""知识库 Repo — knowledge_base 表 CRUD。"""

from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from src.infra.db.models.kb import KbModel


class KbRepo:
    """知识库 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        async with self._sf() as session:
            try:
                kb = KbModel(user_id=user_id, name=name, description=description)
                session.add(kb)
                await session.commit()
                return kb.id, True
            except IntegrityError:
                await session.rollback()
                stmt = select(KbModel).where(
                    KbModel.user_id == user_id, KbModel.name == name
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is None:
                    raise RuntimeError(
                        f"IntegrityError on '{name}' but query returned None"
                    )
                return existing.id, False

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        async with self._sf() as session:
            stmt = select(KbModel).where(
                KbModel.user_id == user_id, KbModel.name == name
            )
            result = await session.execute(stmt)
            kb = result.scalar_one_or_none()
            return kb.id if kb else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            return kb.name if kb else None

    async def get_all_kb(self, user_id: str = "") -> list[KbModel]:
        async with self._sf() as session:
            stmt = select(KbModel).where(KbModel.is_deleted == 0)
            if user_id:
                stmt = stmt.where(KbModel.user_id == user_id)
            result = await session.execute(stmt.order_by(KbModel.created_at.desc()))
            return list(result.scalars().all())

    async def delete_kb(self, kb_id: str) -> bool:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            if kb is None:
                return False
            await session.delete(kb)
            await session.commit()
            return True

    async def soft_delete_kb(self, kb_id: str) -> bool:
        async with self._sf() as session:
            kb = await session.get(KbModel, kb_id)
            if kb is None:
                return False
            kb.is_deleted = 1
            await session.commit()
            return True
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/mysql_db/kb_repo.py
git commit -m "refactor: rewrite KbRepo with sqlalchemy orm"
```

---

### Task 12: 重写 ChatRepo（JOIN 查询最复杂）

**Files:**
- Modify: `src/infra/db/mysql_db/chat_repo.py`

- [ ] **Step 1: 重写 ChatRepo**

```python
"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

import json
from typing import Optional
from sqlalchemy import select, func, delete
from src.infra.db.models.kb import KbModel
from src.infra.db.models.chat import SessionModel, MessageModel


class ChatRepo:
    """会话/消息 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_session(self, session) -> None:
        """session: 带 .id .user_id .title .kb_id 属性的对象。"""
        async with self._sf() as s:
            s_obj = SessionModel(
                id=session.id,
                user_id=session.user_id,
                title=session.title,
                kb_id=session.kb_id,
            )
            s.add(s_obj)
            await s.commit()

    async def get_sessions(self, user_id: str = "") -> list:
        """返回格式与旧 SessionListItem 兼容的行对象（支持 .id 属性访问）。"""
        async with self._sf() as session:
            stmt = select(
                SessionModel.id,
                SessionModel.title,
                SessionModel.kb_id,
                SessionModel.created_at,
                SessionModel.updated_at,
                func.coalesce(KbModel.name, "所有知识库").label("kb_name"),
                func.count(MessageModel.id).label("message_count"),
            ).outerjoin(
                KbModel,
                (SessionModel.kb_id == KbModel.id) & (SessionModel.kb_id != ""),
            ).outerjoin(
                MessageModel, MessageModel.session_id == SessionModel.id
            ).where(SessionModel.is_deleted == 0)

            if user_id:
                stmt = stmt.where(SessionModel.user_id == user_id)

            stmt = stmt.group_by(SessionModel.id).order_by(
                SessionModel.updated_at.desc()
            ).limit(50)

            result = await session.execute(stmt)
            return list(result.all())  # ← Row 对象，同时支持 .id 和 ["id"]

    async def get_session_by_id(self, session_id: str) -> Optional[SessionModel]:
        async with self._sf() as session:
            return await session.get(SessionModel, session_id)

    async def get_messages(self, session_id: str) -> list[MessageModel]:
        async with self._sf() as session:
            stmt = (
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def save_message(self, msg) -> None:
        """msg: 支持 .session_id .role .content .sources 等属性的对象"""
        async with self._sf() as session:
            sources_json = (
                json.dumps(msg.sources, ensure_ascii=False)
                if msg.sources else None
            )
            m = MessageModel(
                session_id=msg.session_id,
                kb_id=getattr(msg, "kb_id", ""),
                role=msg.role,
                content=msg.content,
                sources=sources_json,
                prompt_tokens=getattr(msg, "prompt_tokens", 0),
                completion_tokens=getattr(msg, "completion_tokens", 0),
                total_tokens=getattr(msg, "total_tokens", 0),
                model_name=getattr(msg, "model_name", ""),
            )
            session.add(m)
            await session.commit()

    async def delete_session_and_messages(self, session_id: str) -> bool:
        async with self._sf() as session:
            await session.execute(
                delete(MessageModel).where(MessageModel.session_id == session_id)
            )
            session_obj = await session.get(SessionModel, session_id)
            if session_obj:
                await session.delete(session_obj)
                await session.commit()
                return True
            await session.commit()
            return False
```

- [ ] **Step 2: 注意**

`ChatRepo.get_sessions()` 返回的是 `list[dict]` 而非 ORM 对象，因为旧方法返回 `SessionListItem` dataclass，其字段包含 `kb_name`（JOIN 结果）和 `message_count`（聚合结果），这些不能直接从单个 ORM 模型映射。保持 dict 返回与上层 `AppService.get_sessions()` 兼容。

- [ ] **Step 3: Commit**

```bash
git add src/infra/db/mysql_db/chat_repo.py
git commit -m "refactor: rewrite ChatRepo with sqlalchemy orm"
```

---

### Task 13: 重写 DocumentRepo（最大，8+ 个方法）

**Files:**
- Modify: `src/infra/db/mysql_db/document_repo.py`

- [ ] **Step 1: 重写 DocumentRepo**

```python
"""文档 Repo — document 表 CRUD。"""

import json
from typing import Optional
from sqlalchemy import select, update, delete
from src.infra.db.models.document import DocModel


class DocumentRepo:
    """文档 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def add_document(self, doc) -> None:
        async with self._sf() as session:
            d = DocModel(
                id=doc.id,
                kb_id=doc.kb_id,
                filename=doc.filename,
                file_type=getattr(doc, "file_type", ""),
                file_size=getattr(doc, "file_size", 0),
                file_path=getattr(doc, "file_path", None),
                user_id=getattr(doc, "user_id", ""),
                md5=getattr(doc, "md5", None),
            )
            session.add(d)
            await session.commit()

    async def get_documents(self, kb_id: str) -> list[DocModel]:
        async with self._sf() as session:
            stmt = (
                select(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
                .order_by(DocModel.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_document(self, doc_id: str) -> Optional[DocModel]:
        async with self._sf() as session:
            return await session.get(DocModel, doc_id)

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        if not doc_ids:
            return {}
        async with self._sf() as session:
            stmt = select(DocModel).where(DocModel.id.in_(doc_ids))
            result = await session.execute(stmt)
            return {d.id: d.filename for d in result.scalars().all()}

    async def update_document_status(self, doc_id: str, status: str, **kwargs) -> None:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            doc.status = status
            for key, value in kwargs.items():
                if hasattr(doc, key):
                    setattr(doc, key, value)
            await session.commit()

    async def update_document_meta_info(self, doc_id: str, meta: dict) -> None:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return
            existing = json.loads(doc.meta_info) if doc.meta_info else {}
            existing.update(meta)
            doc.meta_info = json.dumps(existing, ensure_ascii=False)
            await session.commit()

    async def soft_delete_document(self, doc_id: str) -> bool:
        async with self._sf() as session:
            doc = await session.get(DocModel, doc_id)
            if doc is None:
                return False
            doc.is_deleted = 1
            await session.commit()
            return True

    async def soft_delete_documents_by_kb(self, kb_id: str) -> None:
        async with self._sf() as session:
            stmt = (
                update(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
                .values(is_deleted=1)
            )
            await session.execute(stmt)
            await session.commit()

    async def get_documents_by_kb(self, kb_id: str) -> list[DocModel]:
        """获取知识库中所有未删除的文档。"""
        async with self._sf() as session:
            stmt = (
                select(DocModel)
                .where(DocModel.kb_id == kb_id, DocModel.is_deleted == 0)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
```

- [ ] **Step 2: Commit**

```bash
git add src/infra/db/mysql_db/document_repo.py
git commit -m "refactor: rewrite DocumentRepo with sqlalchemy orm"
```

---

### Task 14: 更新 AppService——session_factory + EvalReportModel + import 修正

**Files:**
- Modify: `src/services/app_service.py`

- [ ] **Step 1: 修改 AppService.__init__**

替换 `__init__` 参数和内部初始化逻辑：

```python
class AppService:
    def __init__(
        self,
        session_factory=None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        chat_manager: Optional[ChatManager] = None,
        agent_service: Optional[AgentService] = None,
    ) -> None:
        if session_factory is None:
            from src.infra.db.engine import session_factory as _sf
            session_factory = _sf
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.chat_manager = chat_manager or ChatManager()
        self.bm25 = (
            BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None
        )

        self._kb_repo = KbRepo(session_factory)
        self._doc_repo = DocumentRepo(session_factory)
        self._chat_repo = ChatRepo(session_factory)
        self._user_repo = UserRepo(session_factory)
        self._eval_repo = EvalRepo(session_factory)
        # ... 以下保持不变
```

- [ ] **Step 2: 更新 import**

```python
# 删除：
from src.infra.db.mysql_db import (
    MySQLDB,
    KbRepo,
    ...
)
from src.infra.db.entities import EvalReportEntity
from src.infra.db.entities.search import ChunkQueryResult

# 改为：
from src.infra.db.mysql_db import (
    KbRepo,
    DocumentRepo,
    ChatRepo,
    UserRepo,
    EvalRepo,
)
from src.infra.db.vector_store.types import ChunkQueryResult
```

- [ ] **Step 3: 修改 insert_eval_report 参数类型**

```python
async def insert_eval_report(self, report) -> None:
    """插入 RAGAS 评估报告。"""
    await self._eval_repo.insert_report(report)
```

参数类型文档改为 `EvalReportModel`（Python 3.11+ 可用 `from __future__ import annotations` 避免导入时类型检查，但这里直接去掉类型标注用 `report` 更简单。CLI 调用方传入的始终是 `EvalReportModel` 实例）。

- [ ] **Step 4: Commit**

```bash
git add src/services/app_service.py
git commit -m "refactor: update AppService to use session_factory"
```

---

### Task 15: 修复其他引用方

**Files:**
- Modify: `src/api/dependencies.py`
- Modify: `src/cli/eval_ragas.py`
- Modify: `src/main.py`
- Modify: `src/infra/db/__init__.py`
- Modify: `src/chat/persistence.py`

- [ ] **Step 1: 修改 src/api/dependencies.py**

```python
"""FastAPI 依赖注入。"""
from src.services.app_service import AppService

_service: AppService | None = None


async def get_app_service() -> AppService:
    global _service
    if _service is None:
        _service = AppService()          # 无参构造，session_factory 走默认懒导入
    return _service
```

代码无需改动（`AppService()` 无参构造依然正常工作），只需要确认 import 正确。

- [ ] **Step 2: 修改 src/cli/eval_ragas.py**

找到第 587 行附近：

```python
# 当前
kbs = await svc.db.get_all_kb(RAGAS_USER_ID)

# 改为
kbs = await svc._kb_repo.get_all_kb(RAGAS_USER_ID)
```

- [ ] **Step 3: 修改 src/main.py lifespan**

```python
# 删除或其他
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 不再调用 db.init_db()
    # Alembic 迁移通过 docker compose exec app alembic upgrade head 手动执行
    yield
```

- [ ] **Step 4: 修改 src/infra/db/__init__.py**

```python
"""db 模块。"""
# 删除 from src.infra.db.mysql_db import MySQLDB
```

- [ ] **Step 5: 修改 src/chat/persistence.py**——替换 SessionEntity/MessageEntity

```python
"""对话历史持久化 — MySQL 异步写入。"""

import json
from typing import Optional

from loguru import logger

from src.infra.db.mysql_db import ChatRepo
from src.infra.db.models.chat import SessionModel, MessageModel


class PersistenceService:
    """对话历史 MySQL 持久化。"""

    def __init__(self, chat_repo: ChatRepo) -> None:
        self._chat_repo = chat_repo

    async def save_session(
        self,
        session_id: str,
        title: str,
        kb_id: str,
        user_id: str = "",
    ) -> None:
        try:
            session = SessionModel(
                id=session_id,
                user_id=user_id,
                title=title,
                kb_id=kb_id,
            )
            await self._chat_repo.create_session(session)
        except Exception as e:
            logger.warning("Failed to save session async: {}", e)

    async def save_messages(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: Optional[list[str]] = None,
    ) -> None:
        try:
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="user",
                    content=user_msg,
                )
            )
            sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
            await self._chat_repo.save_message(
                MessageModel(
                    session_id=session_id,
                    kb_id=kb_id,
                    role="assistant",
                    content=assistant_msg,
                    sources=sources_json,
                )
            )
        except Exception as e:
            logger.warning("Failed to save messages async: {}", e)

    def cleanup_session(self, session_id: str) -> None:
        pass
```

注意：`PersistenceService.__init__` 参数 `chat_repo: ChatRepo` 不需要改，ChatRepo 的构造参数变了但类型名没变。

- [ ] **Step 6: Commit**

```bash
git add src/cli/eval_ragas.py src/main.py src/infra/db/__init__.py src/chat/persistence.py
git commit -m "fix: repair svc.db references, lifespan, and persistence for sqlalchemy"
```

---

### Task 16: 删除旧代码

- [ ] **Step 1: 删除 queries.py**

```bash
git rm src/config/queries.py
```

- [ ] **Step 2: 删除 entities/（search.py 已搬迁）**

```bash
git rm src/infra/db/entities/__init__.py
git rm src/infra/db/entities/user.py
git rm src/infra/db/entities/kb.py
git rm src/infra/db/entities/document.py
git rm src/infra/db/entities/chat.py
git rm src/infra/db/entities/eval_report.py
```

- [ ] **Step 3: 删除 pool.py**

```bash
git rm src/infra/db/mysql_db/pool.py
```

- [ ] **Step 4: 验证无残留引用**

```bash
# 确认没有文件再引用旧的模块
grep -r "from src.config.queries" src/ || echo "No queries references"
grep -r "from src.infra.db.entities import" src/ || echo "No entities references"
grep -r "MySQLDB" src/ || echo "No MySQLDB references"
```

- [ ] **Step 5: Commit**

```bash
git commit -m "cleanup: remove old queries.py, entities/, pool.py"
```

---

### Task 17: 创建 clean_all_data.py

**Files:**
- Create: `scripts/clean_all_data.py`

- [ ] **Step 1: 创建清理脚本**

```python
"""全量清理脚本 — 删库、清 Redis、删 MinIO 文件、清 ChromaDB。

适用场景：开发/测试环境重置、表结构大改后重建。
"""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    REDIS_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MINIO_DOC_BUCKET, CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR,
)


async def drop_mysql():
    dsn = f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}"
    engine = create_async_engine(dsn)
    async with engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS `{MYSQL_DATABASE}`"))
        await conn.execute(
            text(f"CREATE DATABASE `{MYSQL_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
        await conn.commit()
        print(f"[MySQL] 已删除并重建 database: {MYSQL_DATABASE}")
    await engine.dispose()


async def flush_redis():
    import redis.asyncio as redis_async
    client = redis_async.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    print(f"[Redis] 已清空: {REDIS_URL}")


async def clean_minio():
    from minio import Minio
    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                   secret_key=MINIO_SECRET_KEY, secure=False)
    if client.bucket_exists(MINIO_DOC_BUCKET):
        for obj in client.list_objects(MINIO_DOC_BUCKET, recursive=True):
            client.remove_object(MINIO_DOC_BUCKET, obj.object_name)
        client.remove_bucket(MINIO_DOC_BUCKET)
        print(f"[MinIO] 已删除 bucket: {MINIO_DOC_BUCKET}")
    client.make_bucket(MINIO_DOC_BUCKET)
    print(f"[MinIO] 已重建 bucket: {MINIO_DOC_BUCKET}")


async def reset_chromadb():
    import chromadb
    from chromadb.config import Settings
    from pathlib import Path
    persist_path = Path(CHROMA_PERSIST_DIR)
    if not persist_path.exists():
        print(f"[ChromaDB] 持久化目录不存在，跳过: {CHROMA_PERSIST_DIR}")
        return
    client = chromadb.PersistentClient(
        path=str(persist_path),
        settings=Settings(anonymized_telemetry=False),
    )
    names = [c.name for c in client.list_collections()
             if c.name.startswith(CHROMA_COLLECTION_PREFIX)]
    for name in names:
        client.delete_collection(name)
    print(f"[ChromaDB] 已删除 {len(names)} 个 collection")


async def main():
    print("即将执行：")
    print("  1. MySQL — DROP DATABASE + 重建")
    print("  2. Redis — FLUSHDB")
    print("  3. MinIO — 清空 bucket + 删除后重建")
    print("  4. ChromaDB — 删除所有 collection")
    confirm = input("输入 YES 确认执行: ")
    if confirm != "YES":
        print("已取消。")
        return
    await drop_mysql()
    await flush_redis()
    await clean_minio()
    await reset_chromadb()
    print("\n✅ 全部清理完成。")
    print("   下一步：alembic upgrade head 重建 MySQL 表结构")


if __name__ == "__main__":
    asyncio.run(main())
```

注意：`clean_all_data.py` 从 `src.config` import，需要项目路径在 PYTHONPATH 中才能运行。通常使用：

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
PYTHONPATH=. python scripts/clean_all_data.py
```

- [ ] **Step 2: Commit**

```bash
git add scripts/clean_all_data.py
git commit -m "feat: add clean_all_data.py for full data reset"
```

---

### Task 18: 修复测试——conftest.py

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/reset_data.py`

- [ ] **Step 1: 修改 tests/conftest.py**

```python
"""测试共享 fixture 和配置。"""

from __future__ import annotations

import os
import uuid
from typing import Generator, AsyncGenerator

import pytest
from loguru import logger

from src.services.app_service import AppService
from src.infra.db.vector_store import VectorStore

# ==================== 路径常量 ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_DOCS_DIR = os.path.join(PROJECT_ROOT, "data", "test_docs")

# ==================== 数据库直连验证 ====================


@pytest.fixture(scope="session")
def service() -> Generator[AppService, None, None]:
    """提供 AppService 实例，用于直接验证数据库状态。"""
    svc = AppService()
    yield svc


@pytest.fixture(scope="session")
def vector_store() -> Generator[VectorStore, None, None]:
    """提供 VectorStore 实例，用于验证 ChromaDB 状态。"""
    vs = VectorStore()
    yield vs


@pytest.fixture
async def test_kb_name() -> AsyncGenerator[str, None]:
    """生成唯一的测试知识库名称，teardown 时自动删除。"""
    unique_id = uuid.uuid4().hex[:8]
    name = f"__test__{unique_id}"
    yield name
    await _cleanup_kb(name)


async def _cleanup_kb(name: str) -> None:
    """根据知识库名称删除对应的数据库和向量数据。"""
    try:
        svc = AppService()
        kb_id = await svc.get_kb_by_name("", name)
        if kb_id:
            await svc._kb_repo.delete_kb(kb_id)
            import asyncio
            await asyncio.to_thread(svc.vector_store.delete_collection, kb_id)
            logger.info("Cleaned up test KB: {} ({})", name, kb_id)
    except Exception:
        logger.exception("Failed to cleanup test KB: {}", name)
```

关键改动：
- 删除 `mysql_db` fixture（不再需要，Service 层直接操作 ORM 通过 `AppService` 方法验证）
- `_cleanup_kb` 改为 async，使用 `await svc.get_kb_by_name("", name)` 替代 `svc.db.get_kb_by_name(name)`

- [ ] **Step 2: 修改 tests/reset_data.py**

```python
"""测试数据重置工具。"""

import asyncio
from typing import Optional

from loguru import logger

from src.infra.db.engine import engine
from src.infra.db.base import Base
from src.config import CHROMA_PERSIST_DIR, REDIS_URL


async def reset_mysql() -> None:
    """删除并重建所有 MySQL 表。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("MySQL tables recreated")


async def flush_redis() -> None:
    """清空 Redis。"""
    import redis.asyncio as redis_async
    client = redis_async.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    logger.info("Redis flushed")


async def reset_chromadb() -> None:
    """删除所有 ChromaDB collection。"""
    import chromadb
    from chromadb.config import Settings
    from src.config import CHROMA_COLLECTION_PREFIX

    persist_path = CHROMA_PERSIST_DIR
    client = chromadb.PersistentClient(
        path=persist_path,
        settings=Settings(anonymized_telemetry=False),
    )
    names = [c.name for c in client.list_collections()
             if c.name.startswith(CHROMA_COLLECTION_PREFIX)]
    for name in names:
        client.delete_collection(name)
    logger.info(f"ChromaDB collections cleared: {len(names)}")


async def reset_all() -> None:
    """一键重置全部数据存储。"""
    logger.info("========== 开始重置所有数据 ==========")
    await reset_mysql()
    await flush_redis()
    await reset_chromadb()
    logger.info("========== 重置完成 ==========")


if __name__ == "__main__":
    asyncio.run(reset_all())
```

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py tests/reset_data.py
git commit -m "test: update conftest and reset_data for sqlalchemy"
```

---

### Task 19: 重写 test_mysql_db.py——session_factory 模式

**Files:**
- Modify: `tests/infra/db/test_mysql_db.py`

- [ ] **Step 1: 重写测试**

```python
"""MySQL 数据层集成测试 — 使用 session_factory。"""

import uuid
import pytest
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db.kb_repo import KbRepo
from src.infra.db.mysql_db.document_repo import DocumentRepo
from src.infra.db.models.document import DocModel


@pytest.fixture
def repos():
    """提供使用 session_factory 的 Repo 实例。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    return kb_repo, doc_repo


@pytest.mark.asyncio
async def test_create_and_get_kb():
    """测试创建和查询知识库的完整流程。"""
    kb_repo = KbRepo(session_factory)
    user_id = "test-user"
    name = f"test-kb-{uuid.uuid4().hex[:8]}"
    kb_id, is_new = await kb_repo.get_or_create_kb(user_id, name)
    assert is_new is True
    found_id = await kb_repo.get_kb_by_name(user_id, name)
    assert found_id == kb_id


@pytest.mark.asyncio
async def test_document_crud():
    """测试文档的增删查操作。"""
    kb_repo = KbRepo(session_factory)
    doc_repo = DocumentRepo(session_factory)
    user_id = "test-user"
    kb_name = f"test-doc-kb-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, kb_name)
    doc_id = str(uuid.uuid4())
    doc = DocModel(id=doc_id, kb_id=kb_id, filename="test.pdf")
    await doc_repo.add_document(doc)
    docs = await doc_repo.get_documents(kb_id)
    doc_ids = [d.id for d in docs]
    assert doc_id in doc_ids


@pytest.mark.asyncio
async def test_get_kb_name_by_id():
    """测试根据知识库 ID 查询名称。"""
    kb_repo = KbRepo(session_factory)
    user_id = "test-user"
    name = f"test-kb-name-{uuid.uuid4().hex[:8]}"
    kb_id, _ = await kb_repo.get_or_create_kb(user_id, name)
    result = await kb_repo.get_kb_name_by_id(kb_id)
    assert result == name
    result = await kb_repo.get_kb_name_by_id(str(uuid.uuid4()))
    assert result is None
```

- [ ] **Step 2: Commit**

```bash
git add tests/infra/db/test_mysql_db.py
git commit -m "test: rewrite mysql_db tests with session_factory"
```

---

### Task 20: 修复单元测试——去掉 MySQLDB patch

**Files:**
- Modify: `tests/services/test_app_service.py`
- Modify: `tests/services/test_auth_service.py`
- Modify: `tests/api/test_documents.py`

- [ ] **Step 1: 修改 test_app_service.py**

删除所有 `@patch("src.services.app_service.MySQLDB")` 装饰器（9 处）。

```python
# 旧（每个测试类都有）：
@patch("src.services.app_service.MySQLDB")
@patch("src.services.app_service.VectorStore")
@patch("src.services.app_service.DocRouter")
@patch("src.services.app_service.KbRepo")
@patch("src.services.app_service.DocumentRepo")
# ...

# 新（去掉 MySQLDB）：
@patch("src.services.app_service.VectorStore")
@patch("src.services.app_service.DocRouter")
@patch("src.services.app_service.KbRepo")
@patch("src.services.app_service.DocumentRepo")
# ...
```

替换 `from src.infra.db.entities import DocEntity, KbListItem` 为 `from unittest.mock import MagicMock`。

mock 返回值改为 MagicMock：

```python
# 旧
from src.infra.db.entities import KbListItem
mock_kb_repo.return_value.get_all_kb = AsyncMock(
    return_value=[KbListItem(id="id1", user_id="u1", name="KB1", ...)]
)

# 新
mock_kb_repo.return_value.get_all_kb = AsyncMock(
    return_value=[MagicMock(id="id1", user_id="u1", name="KB1", doc_count=0,
                            description="", created_at=None)]
)
```

`DocEntity` 同理替换为 MagicMock。

- [ ] **Step 2: 修改 test_auth_service.py**

```python
# 删除
from src.infra.db.entities import UserEntity

# 改为使用 MagicMock：
def test_register_duplicate_account(self, auth_service, mock_user_repo):
    from unittest.mock import MagicMock
    mock_user_repo.get_user_by_account.return_value = MagicMock(
        id="existing", account="test_user", password="pwd"
    )
```

所有 `UserEntity(id=..., account=...)` 替换为 `MagicMock(id=..., account=...)`。

- [ ] **Step 3: 修改 test_documents.py**

删除 `from src.infra.db.entities import DocEntity`，改为 `from unittest.mock import MagicMock`。

```python
# 旧
from src.infra.db.entities import DocEntity
mock_doc = DocEntity(id="d1", kb_id="kb1", filename="t.pdf", ...)

# 新
from unittest.mock import MagicMock
mock_doc = MagicMock(id="d1", kb_id="kb1", filename="t.pdf", ...)
```

- [ ] **Step 4: Commit**

```bash
git add tests/services/test_app_service.py tests/services/test_auth_service.py tests/api/test_documents.py
git commit -m "test: remove MySQLDB patches, use MagicMock for entities"
```

---

### Task 21: 端到端验证

- [ ] **Step 1: 运行全部测试**

```bash
# 先确保数据库已就绪
docker compose up -d mysql redis
sleep 10  # 等 MySQL 就绪

# 清理 → 迁移
cd /mnt/d/code/demo/AIAgent/corporate_rag
PYTHONPATH=. python scripts/clean_all_data.py <<< "YES"
alembic upgrade head

# 跑测试
pytest tests/ -v --tb=short
```

预期：全部通过。

- [ ] **Step 2: 检查 lint**

```bash
ruff check .
ruff format . --check
```

- [ ] **Step 3: 启动服务手动验证**

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

验证端到端流程：
1. 访问健康检查端点
2. 注册/登录
3. 创建知识库
4. 上传文档
5. 发起问答

- [ ] **Step 4: 验证无参构造路径**

```bash
# 确认 AppService() 无参构造正常工作
python -c "
from src.services.app_service import AppService
svc = AppService()
print('AppService() OK')
"
```

- [ ] **Step 5: 最终 commit**

```bash
git add -A
git commit -m "feat: migrate to sqlalchemy 2.0 async orm + alembic"
```
