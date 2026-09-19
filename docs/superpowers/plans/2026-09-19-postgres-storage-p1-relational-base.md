# PostgreSQL 关系型底座（postgres-storage-consolidation / P1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把关系型存储（7 张表）从 MySQL 迁到 PostgreSQL，并把建表机制从「init SQL + 针对已存在表的增量 diff 迁移」换成「一条从零建表的新 baseline（8 张表，含 `chunks` 与 `CREATE EXTENSION vector`）」。

**Architecture:** 一条 DSN 单一来源（`src/config`）+ 一条 alembic 链（根 `alembic/`）+ 从 ORM 模型自动生成的新 baseline。应用仍用构造函数注入的 `session_factory`；分块与向量仍走 Chroma、词法仍是旧 `rank_bm25` —— 本阶段**只换关系型后端**，检索链路行为不变。

**Tech Stack:** PostgreSQL 15 + pgvector 0.8.6 / SQLAlchemy 2.0 async + asyncpg / Alembic / Docker Compose

**Spec:** `docs/openspec/changes/postgres-storage-consolidation/`（`proposal.md` / `design.md` / `specs/`）；实测证据 `docs/tmp/postgres-probe-2026-09-19.md`

## 阶段定位（重要，先读）

本 change 跨 4 个可独立交付的子系统。按 writing-plans 的 Scope Check，**每个子系统一份 plan**，本文件是 **P1**：

| 阶段 | 范围 | 状态 |
|---|---|---|
| **P1（本文件）** | 依赖/配置 → compose PG 服务 → alembic baseline（8 表）→ 合并 ORM → 统一 `ChunkData` → engine DSN → repo 幂等写入 → 退役 MySQL | 本次 |
| P2 | `vector_store/` 换 pgvector、`ChunkResult.metadata` 回填契约、`similarity_search_all` 删除、Chroma→PG 数据搬迁、dense 等价性验收（top-k 重合率 ≥ 0.9） | 待 P1 落地后写 |
| P3 | jieba 分词入口 + 查询串构造与转义 + `tsv` 生成列 + 词项命中探针选型 + `rrf_fusion` 迁移 + 删除 `bm25_index.py` | 待 P2 落地后写 |
| P4 | 入库/删除两条路径同事务 + 故障注入验收 + 依赖与卷清理 + prod compose 与 dev 同构（**不指向 RDS、不安装**）+ 文档与 ADR + 归档 | 待 P3 落地后写 |

**执行范围（用户 2026-09-19 决定）：本轮只做本地。远程 RDS 不处理，prod 不进行安装。** prod 的 `docker-compose.prod.yml` 只做与 dev 同构的**文件**调整（继续使用本地 PG 实例），不部署、不验证；**RDS 托管化与 RDS 侧的扩展清单/权限是遗留项**（见文末）。

**为什么不一次写完 P2–P4：** 它们要改的正是 P1 改过的文件（`document_service.py` / `vector_store/` / `retrieval.py`），把 P2–P4 的代码级步骤现在写死，会在 P1 落地时全部失准。P1 是唯一设计已完全确定、且不依赖后续阶段的部分。

## 本 plan 覆盖的 spec requirement（自查表）

| capability | requirement | 覆盖它的 Task |
|---|---|---|
| `database-orm` | ORM 模型定义（MySQL 表 → PostgreSQL；模型与迁移的单一事实源） | Task 2、5、7 |
| `database-orm` | 搜索类型搬迁（引用方列表移除 `bm25_index.py`） | **不在 P1**（P3） |
| `database-migrations` | 第一版迁移（改为从零建表 baseline，8 张表） | Task 5 Step 4–6 |
| `database-migrations` | 扩展由超级用户创建 + 迁移做可操作的前置断言 | Task 0、Task 4 Step 1/6、Task 5 Step 5/9 |
| `chunk-data-model` | `ChunkData` 是唯一标准类型（修正既有未满足项） | Task 1 |
| `typed-data-layer` | 检索结果统一类型 / `ChunkResult` 分路字段 / `metadata` 回填契约 | **不在 P1**（P2） |
| `hybrid-retrieval` | 两路取数与融合位置 / 来源可辨 / 参数可配 / 分块按知识库归属 / 写入与查询分词同源 / 查询条件构造与转义 | **不在 P1**（P2 覆盖归属与分路，P3 覆盖分词与融合） |
| `retrieval-quality` | 迁移等价性与词项命中探针 / rerank passthrough | **不在 P1**（P2 dense 等价性、P3 词法探针、P2 rerank） |
| `observability-logging` | 两路贡献可见 / 生成层可观测 | **不在 P1**（P3） |
| `architecture-tidy` | AppService 不再持有 `BM25Index` | **不在 P1**（P3） |
| `agent-service` / `multi-query-retrieval` / `chunk-entity-enrichment` / `model-config` / `request-abort` / `streaming-run` / `kb-routing` | 纯命名同步 | **不在 P1**（P2/P3，随各自改动的文件一起） |

**P1 的 `chunks` 表只建表、不接线** —— 它由 P2（向量存储）与 P3（词法检索）分别使用。P1 只保证表结构与 `CREATE EXTENSION` 就位。

## Global Constraints

- **契约（P1 不得破坏）**：`ChunkResult.distance` 是**余弦距离**，消费方用 `score = 1 - distance`（`rag_tools.py:168`、`retrieval.py:157-158`）。P1 不碰检索，但**不得**顺手改这些消费点。
- **分块 id 格式** `{doc_id}:{chunk_index}`（`store.py:44`）不变。
- **不得引入三元表达式**（`a if cond else b`）—— 写完整 `if/else`。
- **显式类型检查**：不用 `getattr(x, "attr", default)` 隐式兜底；用 `x.attr if x is not None else default` 或 `isinstance`。
- **硬编码集中管理**：新增常量/阈值/文案进 `src/config/`（`settings.py` 环境变量 / `const.py` 固定值 / `prompts.py` 提示词），不得散落在业务代码。
- **文档与注释**：所有函数写 docstring；dataclass 每个字段加行内注释；注释写**当前状态**不写变更历史；注释陈述契约不写推理记录。
- **文件红线**：单文件 ≤ 400 行、单函数 ≤ 80 行，超了先拆。
- **门禁（每个 Task 结束都跑）**：`ruff check .` 无错、`pyright src/` 不新增 error、无遗留 `print()`/TODO/调试代码。
- **契约同步**：改了公共方法签名或响应结构时，同步 `docs/agents/api_contract.md` 与受影响测试断言。
- **PG 数据目录**：必须继续用 docker **named volume**，**绝不**绑到 `/mnt/d`（9p 的 fsync/原子性弱）。
- **本地 PG 大版本**：用 `pgvector/pgvector:pg15`（= PG 15.19 + pgvector 0.8.6），与 RDS 对齐。

---

## Task 0: 范围与本地前置确认（不碰 RDS / 不装 prod）

**Files:**
- Modify: `docs/tmp/postgres-probe-2026-09-19.md`（追加本地权限实测结论）

**Interfaces:**
- Consumes: 无
- Produces: 对「本地谁能建扩展」的确证结论，Task 4 与 Task 5 直接依赖它

**范围（用户 2026-09-19 决定）**：**本轮只做本地。远程 RDS 不处理，prod 不进行安装。** 因此原「RDS 三件事」前置核查**整体移出本计划**，降级为遗留项（见文末「遗留项」）。prod 的 `docker-compose.prod.yml` 只做与 dev 同构的**文件**调整，不部署、不验证。

- [ ] **Step 1: 确证「扩展必须由超级用户创建」这条本地事实**

已实测（2026-09-19，`pgvector/pgvector:pg15`）：`vector.control` **没有 `trusted = true`**；应用账号（业务库属主、非超级用户）执行 `CREATE EXTENSION vector` 得到
`ERROR: permission denied to create extension "vector"` / `HINT: Must be superuser to create this extension.`；
而 compose 的 `POSTGRES_USER`（`langfuse`）实测 `rolsuper = t`，能建；建好后应用账号可正常建含 `vector(1024)` 列的表、插入与 `<=>` 排序。

复现（可选，3 秒）：

```bash
docker run -d --name pgcheck -e POSTGRES_USER=langfuse -e POSTGRES_PASSWORD=probe \
  pgvector/pgvector:pg15
sleep 3
docker exec pgcheck psql -U langfuse -d langfuse -c \
  "SELECT rolname, rolsuper FROM pg_roles WHERE rolname='langfuse';"
docker exec pgcheck psql -U langfuse -d langfuse -c \
  "CREATE ROLE app LOGIN PASSWORD 'x'; CREATE DATABASE appdb OWNER app;"
docker exec pgcheck psql -U app -d appdb -c "CREATE EXTENSION vector;"   # 预期被拒
docker exec pgcheck psql -U langfuse -d appdb -c \
  "CREATE EXTENSION vector; SELECT extversion FROM pg_extension WHERE extname='vector';"
docker rm -f pgcheck
```

- [ ] **Step 2: 把结论追加到证据文件**

在 `docs/tmp/postgres-probe-2026-09-19.md` 的 `## 1.1` 小节末尾追加一段「**扩展创建权限归属（本地，2026-09-19）**」，写清：`vector` 非 trusted → 应用账号不可建；`langfuse` 是超级用户可建；建后应用账号可正常使用；**迁移不建扩展、只做前置断言**；RDS 侧同项改为遗留。

- [ ] **Step 3: 提交**

```bash
git add docs/tmp/postgres-probe-2026-09-19.md
git commit -m "docs: 补记本地扩展创建权限实测结论（应用账号不可建，须超级用户）"
```

---

## Task 1: 统一 `ChunkData` 为一份定义

**Files:**
- Modify: `src/chunking/validator.py:9-21`（成为唯一类型）
- Modify: `src/parsers/base.py:17-31`（改为 re-export）
- Test: `tests/chunking/test_chunk_data_unified.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `ChunkData` 唯一类，位于 `src.chunking.validator`；`src.parsers.base` 继续可 `from src.parsers.base import ChunkData`（re-export），字段为 `content: str`、`metadata: dict`、`chunk_id: str = ""`、`tokens: int = 0`

**为什么这样定字段：** 两个既有定义各有一个对方没有的字段 —— `parsers/base.py` 有 `chunk_id: str`（必填），`chunking/validator.py` 有 `tokens: int = 0`。三个 parser 都以关键字传 `chunk_id`（`txt_parser.py:64-68`、`docx_parser.py:83-87`、`pymupdf_parser.py:217-226`），写入侧（`validator` 版）从不传。因此**合并时保留 `chunk_id` 并给默认值 `""`**，两侧构造点都不用改。

- [ ] **Step 1: 写会失败的测试**

新建 `tests/chunking/test_chunk_data_unified.py`：

```python
"""ChunkData 只有一个定义的守卫测试。"""

from src.chunking.validator import ChunkData
from src.parsers.base import ChunkData as ParserChunkData


def test_single_definition():
    """两个 import 路径必须指向同一个类，不是两份定义。"""
    assert ChunkData is ParserChunkData


def test_parser_side_constructs_with_chunk_id():
    """三个 parser 以关键字传 chunk_id，必须仍能构造。"""
    chunk = ChunkData(
        content="营业收入同比增长",
        metadata={"source": "a.pdf", "page": 1, "block_type": "text"},
        chunk_id="a.pdf:p1:0",
    )
    assert chunk.chunk_id == "a.pdf:p1:0"
    assert chunk.tokens == 0


def test_writer_side_omits_chunk_id_and_tokens():
    """写入侧（chunking/validator 的调用方）从不传 chunk_id / tokens。"""
    chunk = ChunkData(content="文本", metadata={"source": "a.pdf"})
    assert chunk.chunk_id == ""
    assert chunk.tokens == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/chunking/test_chunk_data_unified.py -v`
Expected: FAIL —— `test_single_definition` 断言失败（两个不同的类），且 `test_writer_side_omits_chunk_id_and_tokens` 在构造时不报错但第一条已足够证明未合并。

- [ ] **Step 3: 改 `src/chunking/validator.py` 为唯一定义**

把 `ChunkData` 改成（保留原有 docstring 语义，补字段行内注释）：

```python
@dataclass
class ChunkData:
    """分块数据的唯一标准类型。

    content/metadata 是解析与写入两侧共用的载荷；chunk_id 供解析阶段标识来源位置，
    tokens 供分块质量评估使用（解析阶段不填，默认 0）。
    """

    content: str
    metadata: dict
    # 解析阶段生成的来源标识（形如 "{source}:{index}" 或 "{source}:p{page}:{index}"）；
    # 写入侧不填，默认空串
    chunk_id: str = ""
    # 分块后的 token 数，由分块质量评估填充；解析阶段不填，默认 0
    tokens: int = 0
```

- [ ] **Step 4: 改 `src/parsers/base.py` 为 re-export**

删掉该文件里的 `ChunkData` 类定义，改为：

```python
from src.chunking.validator import ChunkData

__all__ = ["ChunkData", ...]  # 保留该文件原有的其它导出符号
```

（`src/parsers/base.py` 原本还有别的符号，**只替换 `ChunkData` 那一段**，不要动其它内容。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/chunking/test_chunk_data_unified.py tests/parsers/ tests/chunking/ -v`
Expected: PASS，且 `tests/parsers/` 与 `tests/chunking/` 全绿。

- [ ] **Step 6: 跑门禁并提交**

```bash
ruff check src/chunking src/parsers && pyright src/chunking/validator.py src/parsers/base.py
git add src/chunking/validator.py src/parsers/base.py tests/chunking/test_chunk_data_unified.py
git commit -m "refactor: 统一 ChunkData 为一份定义（保留 chunk_id 字段，消除双份类型）"
```

---

## Task 2: 删除死代码那套 ORM 模型，并让 alembic 只指向一套

**Files:**
- Delete: `src/infra/db/mysql_db/models/`（整目录）
- Delete: `src/infra/db/mysql_db/alembic/`（整目录）
- Modify: `src/infra/db/mysql_db/__init__.py`（若它 import 了 `models`）
- Test: `tests/infra/db/test_single_model_source.py`（新建）

**Interfaces:**
- Consumes: Task 1 无关
- Produces: 全局唯一的 ORM 模型来源 `src.infra.db.models`；`Base.metadata` 只含 7 张业务表

**实测依据：** `src/infra/db/mysql_db/models/` 除自身 `__init__.py` 与**不生效的** `src/infra/db/mysql_db/alembic/env.py:16` 外**无人 import**（全仓已 grep 确认）—— 是死代码。生效链是根 `alembic/env.py:9` 的 `from src.infra.db.models import *`。两套的 `e6304ba3a9ef_init_models.py` **操作完全一致**，仅 `UTCDateTime` 引用写法不同，所以删掉不生效那套不会丢任何表/列定义。

- [ ] **Step 1: 写会失败的测试**

新建 `tests/infra/db/test_single_model_source.py`：

```python
"""ORM 模型与迁移来源必须唯一。"""

import importlib

import pytest

from src.infra.db.base import Base


def test_dead_model_package_is_gone():
    """不生效的那套模型目录必须已删除。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.db.mysql_db.models")


def test_dead_alembic_dir_is_gone():
    """不生效的那套 alembic 目录必须已删除。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.infra.db.mysql_db.alembic.env")


def test_metadata_has_all_business_tables():
    """Base.metadata 必须含 7 张业务表（chunks 由 alembic baseline 建，不在 ORM 里）。"""
    expected = {
        "users",
        "knowledge_base",
        "document",
        "sessions",
        "conversation_history",
        "eval_report",
        "feedback",
    }
    assert expected <= set(Base.metadata.tables)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/db/test_single_model_source.py -v`
Expected: FAIL —— 前两条 `pytest.raises(ModuleNotFoundError)` 不触发（模块仍在），第三条例外。

- [ ] **Step 3: 删除两套死代码目录**

```bash
git rm -r src/infra/db/mysql_db/models src/infra/db/mysql_db/alembic
# 这两个目录里有未跟踪的 __pycache__（.pyc 是 git 不跟踪的），必须一并清掉，
# 否则测试里的 ModuleNotFoundError 断言可能被残留字节码干扰
rm -rf src/infra/db/mysql_db/models/__pycache__ src/infra/db/mysql_db/alembic/versions/__pycache__
```

- [ ] **Step 4: 检查 `mysql_db/__init__.py` 与全仓 import**

```bash
grep -rn "mysql_db.models\|mysql_db.alembic" --include="*.py" .
```

预期：无输出。若有输出，逐处改到 `src.infra.db.models`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/infra/db/test_single_model_source.py -v`
Expected: PASS（3 条全绿）。

- [ ] **Step 6: 跑门禁并提交**

```bash
ruff check . && pyright src/infra/db
git add -A
git commit -m "refactor(db): 删除不生效的第二套 ORM 模型与 alembic 目录（死代码）"
```

---

## Task 3: `src/config` 增加 PostgreSQL 连接配置（单一 DSN 来源）

**Files:**
- Modify: `src/config/settings.py:145-149`（新增 PG 变量，`MYSQL_*` 暂时保留）
- Modify: `.env`（新增 PG 变量；已 gitignore，不进提交）
- Test: `tests/config/test_pg_settings.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DATABASE`（模块级常量，经 `src.config.__init__` 的 `from src.config.settings import *` 可见）；以及一个构造函数 `build_postgres_dsn() -> str`，返回 `postgresql+asyncpg://...`

**为什么要有 `build_postgres_dsn()`：** DSN 目前有**两个来源** —— `src/infra/db/engine.py:16` 拼一个、`alembic.ini:89` 硬编码一个。二者漂移会让迁移与运行指着不同的库。P1 起改为**一处拼装、两处引用**。

- [ ] **Step 1: 写会失败的测试**

新建 `tests/config/test_pg_settings.py`：

```python
"""PostgreSQL 连接配置与 DSN 拼装的守卫测试。"""

import pytest

from src.config import settings


def test_dsn_scheme_is_asyncpg(monkeypatch):
    """DSN 必须用 asyncpg 驱动。"""
    monkeypatch.setattr(settings, "POSTGRES_HOST", "db.example")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(settings, "POSTGRES_USER", "appuser")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "p@ss")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "corporate_rag")

    dsn = settings.build_postgres_dsn()

    assert dsn.startswith("postgresql+asyncpg://")
    assert "db.example:5432/corporate_rag" in dsn


def test_password_with_special_chars_is_quoted(monkeypatch):
    """密码含 @ / : 时必须按 URL 规则转义，否则 DSN 会被解析错。"""
    monkeypatch.setattr(settings, "POSTGRES_HOST", "h")
    monkeypatch.setattr(settings, "POSTGRES_PORT", 5432)
    monkeypatch.setattr(settings, "POSTGRES_USER", "u")
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "p@ss:word")
    monkeypatch.setattr(settings, "POSTGRES_DATABASE", "d")

    dsn = settings.build_postgres_dsn()

    assert "p%40ss%3Aword" in dsn


def test_missing_password_fails_fast(monkeypatch):
    """密码为空必须报错，不能静默拼出一个连不上的 DSN。"""
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "")
    with pytest.raises(RuntimeError):
        settings.build_postgres_dsn()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/test_pg_settings.py -v`
Expected: FAIL —— `AttributeError: module 'src.config.settings' has no attribute 'POSTGRES_HOST'`。

- [ ] **Step 3: 在 `src/config/settings.py` 新增 PG 变量与 DSN 拼装**

在 `MYSQL_*` 那段（`:145-149`）之后追加：

```python
# --- PostgreSQL（应用关系型库；Langfuse 用同实例的另一个 database）---
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "corporate_rag")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DATABASE: str = os.getenv("POSTGRES_DATABASE", "corporate_rag")


def build_postgres_dsn() -> str:
    """拼装应用库的 SQLAlchemy 异步 DSN。

    Returns:
        postgresql+asyncpg:// 形式的连接串，账号密码按 URL 规则转义。

    Raises:
        RuntimeError: 密码为空时抛出 —— 空密码只会得到一个连不上的 DSN，
            失败会推迟到首次查询，不如在这里直接失败。
    """
    if not POSTGRES_PASSWORD:
        raise RuntimeError("POSTGRES_PASSWORD 未配置，无法构造 PostgreSQL DSN")
    user = urllib.parse.quote_plus(POSTGRES_USER)
    password = urllib.parse.quote_plus(POSTGRES_PASSWORD)
    return (
        f"postgresql+asyncpg://{user}:{password}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DATABASE}"
    )
```

在 `settings.py` 顶部补 `import urllib.parse`。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/config/test_pg_settings.py -v`
Expected: PASS（3 条全绿）。

- [ ] **Step 5: 在 `.env` 补上 PG 变量**

```bash
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=corporate_rag
POSTGRES_PASSWORD=<与 compose 一致的密码>
POSTGRES_DATABASE=corporate_rag
```

同时把这几行补进 `.env.example`（若有）。**不要**在日志或提交里出现明文密码。

- [ ] **Step 6: 跑门禁并提交**

```bash
ruff check src/config && pyright src/config
git add src/config/settings.py tests/config/test_pg_settings.py
git commit -m "feat(config): 新增 PostgreSQL 连接配置与单一 DSN 拼装入口"
```

---

## Task 4: compose 增加 pgvector PostgreSQL 服务与应用库

**Files:**
- Create: `deploy/postgres/init/001_app_database.sh`
- Modify: `docker-compose.yml:48-68`（postgres 服务）、`:224-250`（app 增加 `depends_on`）、`:256-269`（卷）
- Modify: `docker-compose.prod.yml:49-68`（同）
- Test: 无自动化测试（用 `docker compose config` + `psql` 验证）

**Interfaces:**
- Consumes: Task 3 的 `POSTGRES_*` 变量名
- Produces: 一个名为 `postgres` 的 PG 服务，镜像 `pgvector/pgvector:pg15`，含 `langfuse` 与 `corporate_rag` 两个 database；应用库属主 `corporate_rag`

**关键事实（照做，别想当然）：**
- `postgres:15-alpine` **不自带 pgvector**（官方 postgres 镜像不含第三方扩展）→ 必须换 `pgvector/pgvector:pg15`。
- **init 脚本必须是 `.sh` 而不是 `.sql`**：要让 SQL 用到 compose 传进来的 `POSTGRES_APP_PASSWORD`，只能靠 shell 取环境变量再以 psql 变量传入；`.sql` 文件里写 `:'app_password'` 拿不到环境变量。
- `postgres` 镜像只在**数据目录首次初始化**时执行 `docker-entrypoint-initdb.d` 下的脚本。**既有卷 `corporate_rag_postgres_data` 不会重跑** → 建应用库有两条路：① 一次性手工 `CREATE DATABASE`；② 删卷重建并挂载 init 脚本。**两条都要在本任务里写明**。
- dev 的 `postgres` 现在有 `profiles: ["langfuse"]` → 应用即将依赖它，**必须去掉**。
- `app` 现在**不**依赖 postgres（`docker-compose.yml:224-250` 只依赖 mysql/redis）→ 增加 `postgres: {condition: service_healthy}`。
- `mem_limit: 256m` 对一个跑业务表 + 向量 + 全文索引的库偏紧 → 上调到 `1g`（dev 机器 3.8G，`mem_reservation: 256m`）。
- **PG 数据目录继续用 named volume，绝不改 bind mount 到 `/mnt/d`。**

- [ ] **Step 1: 写 init 脚本 `deploy/postgres/init/001_app_database.sh`**

```bash
#!/bin/bash
# 仅在 PG 数据目录首次初始化时执行（既有卷不会重跑）。
# 本脚本以超级用户（POSTGRES_USER）身份运行，因此是本轮唯一能创建 vector 扩展的地方。
# 建应用库与应用账号；Langfuse 的库/账号由 compose 的 POSTGRES_DB/POSTGRES_USER 提供。
set -euo pipefail

: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_APP_USER:=corporate_rag}"
: "${POSTGRES_APP_DB:=corporate_rag}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v app_user="$POSTGRES_APP_USER" \
     -v app_password="$POSTGRES_APP_PASSWORD" \
     -v app_db="$POSTGRES_APP_DB" <<'SQL'
-- 建角色（幂等）。注意是普通 LOGIN 角色，不是超级用户 —— 应用不需要也更不该有超级用户权限。
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
\gexec

-- CREATE DATABASE 不能在事务块内执行，故用 \gexec 做幂等
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'app_db')
\gexec
SQL

# 扩展必须在应用库内创建。vector 不是 trusted 扩展（实测：非超级用户会得到
# "permission denied to create extension / Must be superuser"），所以只能在这里
# 以超级用户身份做；迁移（用应用账号连库）建不了它，只做前置断言。
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_APP_DB" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

加可执行位：`chmod +x deploy/postgres/init/001_app_database.sh`（entrypoint 对 `.sh` 直接执行，对非可执行文件会 `source`；显式加位更稳）。

> ⚠ **为什么扩展放在初始化脚本里，而初稿特意说"不要放这里"？** 初稿的理由是「initdb 脚本只在数据目录首次初始化时执行，既有卷不重跑、托管实例不参与」—— 这个理由**依然成立**，但**迁移里也做不到**（迁移用的是应用账号，而 `vector` 不是 trusted 扩展）。两条路都不完美，实际落地是**两者都用**：初始化脚本覆盖全新卷，一次性命令覆盖既有卷，迁移做断言兜住"两者都没做"的情况。详见 `design.md` D3 的实测更正。

- [ ] **Step 2: 改 `docker-compose.yml` 的 postgres 服务**

把 `:48-68` 改为：

```yaml
  postgres:
    image: pgvector/pgvector:pg15
    mem_limit: 1g
    mem_reservation: 256m
    container_name: corporate-rag-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: ${LANGFUSE_POSTGRES_PASS:-langfuse_pass}
      # 应用库的应用账号密码，供 init 脚本使用
      POSTGRES_APP_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./deploy/postgres/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 15s
    networks:
      - app-network
```

注意：**删掉 `profiles: ["langfuse"]`**（应用现在依赖它，不能再挂在 Langfuse profile 下）。

- [ ] **Step 3: 给 dev 的 app 增加 postgres 依赖与 PG 环境变量**

`docker-compose.yml:224-250` 的 `app`：

- `environment` 下补 `POSTGRES_HOST: postgres`、`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?}`、`POSTGRES_USER: corporate_rag`、`POSTGRES_DATABASE: corporate_rag`。
- `depends_on` 下补：

```yaml
      postgres:
        condition: service_healthy
```

- [ ] **Step 4: 同步改 `docker-compose.prod.yml`（只改文件，不安装）**

`docker-compose.prod.yml:49-68` 的 `postgres` 与 `:196-222` 的 `app` 做同样修改（`mem_limit` prod 保持 `4g` 不变）。

**范围（用户 2026-09-19 决定）：本轮 prod 只做与 dev 同构的结构调整，继续使用本地 PG 实例，`不指向 RDS`、不部署、不验证。** 之所以仍要改这个文件：Task 9 会从依赖里删掉 `aiomysql` 与 `MYSQL_*` 配置，若 prod compose 还留着 MySQL 服务与 `MYSQL_HOST`，仓库就处于自相矛盾的状态。

**并在文件顶部加注释**：

```yaml
# ⚠ prod 与 dev 必须在不同机器上运行：两份 compose 的 project name、容器名与
# 全部卷名完全相同，同机执行会因容器名冲突直接失败（--force-recreate 还会拆掉先起的那套）。
# 本轮 prod 不部署：RDS 托管化另案（见 postgres-storage-consolidation 的遗留项）。
```

- [ ] **Step 5: 校验 compose 语法**

Run: `docker compose config >/dev/null && docker compose -f docker-compose.prod.yml config >/dev/null`
Expected: 无输出、exit 0。

- [ ] **Step 6: 起 PG 并建应用库 + 建扩展**

先起服务：

```bash
docker compose up -d postgres
```

然后**二选一**。

**路 A（推荐，不动既有卷）—— 手工一次性建库 + 建扩展：**

```bash
# 从 .env 取密码到环境变量，不打印
set -a; . ./.env; set +a
: "${POSTGRES_PASSWORD:?请在 .env 配置 POSTGRES_PASSWORD}"

# 建角色（幂等，普通 LOGIN 角色、非超级用户）
docker compose exec -T postgres psql -U langfuse -d langfuse <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'corporate_rag') THEN
        CREATE ROLE corporate_rag LOGIN PASSWORD '${POSTGRES_PASSWORD}';
    END IF;
END
\$\$;
SQL

# 建库（CREATE DATABASE 不能在事务块内，故单独一条且先查存在性）
docker compose exec -T postgres psql -U langfuse -d langfuse -tAc \
  "SELECT 1 FROM pg_database WHERE datname='corporate_rag'" | grep -q 1 || \
docker compose exec -T postgres psql -U langfuse -d langfuse -c \
  "CREATE DATABASE corporate_rag OWNER corporate_rag"

# ⚠ 关键一步：在【应用库】里以超级用户创建扩展。
# 应用账号建不了它（vector 不是 trusted 扩展，实测 Must be superuser），
# 而 alembic 迁移用的正是应用账号 —— 所以这一步漏掉，Task 5 的迁移会按设计报"扩展缺失"。
docker compose exec -T postgres psql -U langfuse -d corporate_rag \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**路 B（数据可丢时更彻底）—— 删卷重建，让 `deploy/postgres/init/` 脚本一次性做完建库 + 建扩展：**

```bash
docker compose down
docker volume rm corporate_rag_postgres_data
docker compose up -d postgres
docker compose logs postgres | grep -i "app_database\|CREATE EXTENSION" || true
```

⚠ 路 B 会**同时清掉 dev 的 Langfuse 数据**（同一个实例）。若 dev 上有想留的 trace，走路 A。

> 本步骤就是 design.md D1 里"评审 F2"指出的两条不同动作 —— **不得只写"改文件路径"**。改完 compose 后必须真的执行其中一条，否则应用库不存在。

- [ ] **Step 7: 验证连接与扩展已装在应用库里**

```bash
# 1) 应用账号能连上自己的库
docker compose exec postgres psql -U corporate_rag -d corporate_rag \
  -c "SELECT current_database(), current_user;"

# 2) 扩展已装在【应用库】里（注意：连的是 corporate_rag，不是 langfuse）
docker compose exec postgres psql -U langfuse -d corporate_rag \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

# 3) 应用账号确认自己【没有】超级用户权限（证明最小权限落到位）
docker compose exec postgres psql -U corporate_rag -d corporate_rag \
  -c "SELECT rolsuper FROM pg_roles WHERE rolname='corporate_rag';"
```

Expected：
1. `corporate_rag | corporate_rag`
2. `vector | 0.8.6`
3. `rolsuper = f`

> 第 3 条不是形式主义：它同时验证了 §"账号前置条件"—— 正因为应用账号不是超级用户，扩展才必须由超级用户预先创建。若这里返回 `t`，说明账号建错了（给了超级用户），应改回普通 LOGIN 角色。

- [ ] **Step 8: 提交**

```bash
git add deploy/postgres/init/001_app_database.sh docker-compose.yml docker-compose.prod.yml
git commit -m "feat(deploy): 增加 pgvector PostgreSQL 服务与应用库（dev/prod 同构）"
```

---

## Task 5: 重写 alembic 为一条从零建表的 PG baseline

**Files:**
- Delete: `alembic/versions/e6304ba3a9ef_init_models.py`
- Create: `alembic/versions/0001_pg_baseline.py`（由 autogenerate 生成后手工增补）
- Modify: `alembic/env.py:36-46`、`alembic.ini:89`
- Test: `tests/infra/db/test_pg_baseline.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `build_postgres_dsn()`；Task 4 的 PG 服务
- Produces: 一条 alembic 链（`alembic upgrade head` 从空库建出 8 张表）；`chunks` 表结构见下

**为什么不能移植旧迁移：** 根 `alembic/versions/e6304ba3a9ef_init_models.py` 通篇是 `op.alter_column(..., existing_type=mysql.*)` / `drop_index` / `create_foreign_key` —— 它是**针对 `deploy/mysql/init/001_schema.sql` 已建好的表**做 MySQL 增量 diff，且**假定 `eval_report` 已存在**（`:241-249` 直接对它 `add_column`，而全仓没有任何地方 CREATE 它）。换 PG 必须换成一条**从零建表**的 baseline。

**baseline 必须建全 8 张表**（这是本变更顺带修掉的既有缺陷：全新部署下 `eval_report` 与 `feedback` 从未被创建，评测与反馈功能在全新环境上是坏的）：

| # | 表 | 权威来源 |
|---|---|---|
| 1 | `users` | ORM `models/user.py`（含 `updated_at`） |
| 2 | `knowledge_base` | ORM `models/kb.py`（含 `UniqueConstraint("user_id","name",name="uk_user_kb")`） |
| 3 | `document` | ORM `models/document.py`（含 `md5` / `is_deleted` / `updated_at`） |
| 4 | `sessions` | ORM `models/chat.py:12-23`（含 `agent`） |
| 5 | `conversation_history` | ORM `models/chat.py:26-51`（含 `process`、`status`） |
| 6 | `eval_report` | ORM `models/eval_report.py` |
| 7 | `feedback` | ORM `models/feedback.py`（含 `trace_id`） |
| 8 | `chunks` | **本变更新增，手写**（见下） |

- [ ] **Step 1: 写会失败的测试**

新建 `tests/infra/db/test_pg_baseline.py`：

```python
"""PG baseline 迁移的验收测试（需真实 PostgreSQL）。

本测试自建引擎、不依赖 src.infra.db.engine —— 后者的切换在 Task 6，
本任务只验证「迁移把库建对了」。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import build_postgres_dsn

pytestmark = pytest.mark.asyncio

EXPECTED_TABLES = {
    "users",
    "knowledge_base",
    "document",
    "sessions",
    "conversation_history",
    "eval_report",
    "feedback",
    "chunks",
}


async def _collect(sql: str, **params) -> list:
    """一次性连接执行查询并返回全部行，用完即释放连接。"""
    engine = create_async_engine(build_postgres_dsn(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            return list(result)
    finally:
        await engine.dispose()


async def _columns(table: str) -> dict[str, str]:
    rows = await _collect(
        "SELECT column_name, is_generated FROM information_schema.columns "
        "WHERE table_name = :t",
        t=table,
    )
    return {row[0]: row[1] for row in rows}


async def test_all_eight_tables_exist():
    rows = await _collect(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    got = {row[0] for row in rows}
    assert EXPECTED_TABLES <= got


async def test_vector_extension_installed():
    rows = await _collect(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    )
    assert rows, "vector 扩展必须已创建"


async def test_critical_columns_present():
    """两个模型的差异列与既有缺陷表的关键列必须都在。"""
    assert "agent" in await _columns("sessions")
    assert "process" in await _columns("conversation_history")
    assert "trace_id" in await _columns("feedback")
    assert "md5" in await _columns("document")


async def test_chunks_tsv_is_generated_column():
    cols = await _columns("chunks")
    assert cols.get("tsv") == "ALWAYS", "tsv 必须是生成列"


async def test_knowledge_base_unique_constraint():
    rows = await _collect(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'knowledge_base'::regclass AND contype = 'u'"
    )
    assert rows, "knowledge_base 必须有 (user_id, name) 唯一约束"
```

**运行前提**：Task 4 的 PG 已起、`POSTGRES_*` 已配、`alembic upgrade head` 已跑过。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/db/test_pg_baseline.py -v`
Expected: FAIL —— 多数表不存在（`chunks` 一定不存在）。

- [ ] **Step 3: 让 alembic 从 `src/config` 取 DSN（单一来源）**

改 `alembic/env.py` 的 `run_async_migrations()`：

```python
async def run_async_migrations() -> None:
    """用应用自己的 DSN 跑迁移，避免 alembic.ini 与引擎配置漂移。"""
    from src.config.settings import build_postgres_dsn

    connectable = create_async_engine(build_postgres_dsn(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()
```

同时删掉 `alembic.ini:89` 的 `sqlalchemy.url` 行（改由上面提供），或把它改成注释说明「URL 由 `src/config` 提供」。

- [ ] **Step 4: 删掉旧链，生成新 baseline**

```bash
git rm alembic/versions/e6304ba3a9ef_init_models.py
alembic revision --autogenerate -m "pg baseline" --rev-id 0001
```

预期：生成 `alembic/versions/0001_pg_baseline.py`，内含 7 张表的 `op.create_table(...)`。

- [ ] **Step 5: 在 baseline 前加扩展前置断言、末尾追加 `chunks` 表**

**不要**写 `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` —— 迁移用的是应用账号，而 `vector` 不是 trusted 扩展，实测会得到 `Must be superuser to create this extension`（见 Task 0）。也**不要**用 try/except 吞掉它：那会把权限问题伪装成"迁移成功"。

在 `upgrade()` **最前面**插入前置断言：

```python
    # 扩展由超级用户预先创建（见 deploy/postgres/init/001_app_database.sh）。
    # 迁移账号是应用账号（非超级用户），建不了 vector —— vector 不是 trusted 扩展。
    # 这里显式断言，使失败可归因，而不是让下面的 vector(1024) 报难以定位的类型错误。
    conn = op.get_bind()
    installed = conn.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if installed is None:
        raise RuntimeError(
            "应用库中未安装 vector 扩展，迁移无法创建 chunks.embedding。\n"
            "请由具备超级用户权限的账号先执行一次：\n"
            "    psql -d <应用库> -c \"CREATE EXTENSION IF NOT EXISTS vector;\"\n"
            "本地 dev 可用 compose 的 POSTGRES_USER（langfuse，超级用户）：\n"
            "    docker compose exec -T postgres psql -U langfuse -d corporate_rag "
            "-c 'CREATE EXTENSION IF NOT EXISTS vector;'"
        )
```

在 `upgrade()` **末尾**追加（DDL 已在 PG 15 + pgvector 0.8.6 上实测通过）：

```python
    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kb_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_total", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_seg", sa.Text(), nullable=False),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content_seg)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "embedding",
            pgvector.sqlalchemy.Vector(1024),
            nullable=True,
            comment="DashScope text-embedding-v3",
        ),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("page", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_base.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kb_id", "doc_id", "chunk_index", name="uq_chunks_kb_doc_idx"
        ),
    )
    op.create_index("ix_chunks_kb_id", "chunks", ["kb_id"])
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    # 不建 HNSW：164 MB / 4 万分块规模下 kb_id btree + 精确扫描足够且召回精确，
    # 顺带避开「近似索引 + WHERE 过滤导致返回不足 k 条」。将来加是纯增量。
```

`downgrade()` 必须能真正回滚（Step 8 会验证）：autogenerate 已经为 7 张表生成了反向的 `op.drop_table(...)`，**按依赖倒序**排列即可（`conversation_history` → `document` → `eval_report` → `feedback` → `sessions` → `knowledge_base` → `users`），并在其**之前**加：

```python
    op.drop_table("chunks")
```

**不要**在 `downgrade()` 里 `DROP EXTENSION vector` —— 同一实例上的 Langfuse 库可能也用它，删扩展会波及另一个 database。

顶部 import 补：

```python
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql
```

- [ ] **Step 6: 在空库上跑迁移**

```bash
alembic upgrade head
alembic current
```

Expected: `upgrade` 无报错；`alembic current` 显示 `0001 (head)`。

- [ ] **Step 7: 跑测试确认通过**

Run: `pytest tests/infra/db/test_pg_baseline.py -v`
Expected: PASS（5 条全绿）。

- [ ] **Step 8: 验证 `alembic downgrade base` 再 `upgrade head` 可循环**

```bash
alembic downgrade base && alembic upgrade head
```

Expected: 两条都无报错。**若不通过，先修 baseline**，不要带着不可回滚的迁移往下走。

- [ ] **Step 9: 验证扩展缺失时的失败是"可操作的"（不是裸报错）**

在一个**没有装扩展**的临时库里跑迁移，确认拿到的是那条带命令的 `RuntimeError`，而不是 `type "vector" does not exist` 之类的难归因错误：

```bash
docker compose exec -T postgres psql -U langfuse -d langfuse \
  -c "DROP DATABASE IF EXISTS pg_baseline_guard_probe;"
docker compose exec -T postgres psql -U langfuse -d langfuse \
  -c "CREATE DATABASE pg_baseline_guard_probe OWNER corporate_rag;"

POSTGRES_DATABASE=pg_baseline_guard_probe alembic upgrade head 2>&1 | tail -8

docker compose exec -T postgres psql -U langfuse -d langfuse \
  -c "DROP DATABASE pg_baseline_guard_probe;"
```

Expected: 输出里出现 `应用库中未安装 vector 扩展` 与该提示命令；**不出现** `type "vector" does not exist`。

> 这一步把 database-migrations spec 的「扩展缺失时给出可操作的失败」scenario 变成可执行验收。若你看到的是裸的 `permission denied` 或类型错误，说明断言位置不对（必须在 `create_table("chunks")` 之前）。

- [ ] **Step 10: 跑门禁并提交**

```bash
ruff check alembic && pyright alembic
git add -A alembic tests/infra/db/test_pg_baseline.py
git commit -m "feat(db): 重写 alembic 为从零建表的 PG baseline（8 张表 + 扩展前置断言）"
```

---

## Task 6: 引擎与迁移切到 PostgreSQL 驱动

**Files:**
- Modify: `src/infra/db/engine.py:8-32`
- Test: `tests/infra/db/test_engine_pg.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `build_postgres_dsn()`
- Produces: `src.infra.db.engine.engine`（`postgresql+asyncpg` 方言）、`session_factory`（签名不变，5 个 repo 的构造注入契约不变）

- [ ] **Step 1: 写会失败的测试**

新建 `tests/infra/db/test_engine_pg.py`：

```python
"""引擎方言与连接池配置的守卫测试。"""

from src.infra.db.engine import engine, session_factory  # noqa: F401


def test_engine_uses_postgresql_dialect():
    """驱动必须是 asyncpg，不能还是 aiomysql。"""
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "asyncpg"


def test_pool_budget_is_declared():
    """连接预算是共享的（同一实例还有 Langfuse），必须显式且可核算。"""
    pool = engine.pool
    assert pool.size() == 10
    assert pool._max_overflow == 10
```

> `test_pool_budget_is_declared` 的意义：`docker-compose.prod.yml` 实为 `--workers 4`，4 × (10 + 10) ≈ 80 连接，再加 Langfuse → 可能触及 RDS `max_connections`。P1 只**把预算写死并断言**，不处置 worker 数（那是独立变更）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/infra/db/test_engine_pg.py -v`
Expected: FAIL —— `dialect.name == 'mysql'`。

- [ ] **Step 3: 改 `src/infra/db/engine.py`**

```python
"""SQLAlchemy 异步引擎与 Session 工厂。"""

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import build_postgres_dsn

DSN = build_postgres_dsn()

engine = create_async_engine(
    DSN,
    # 连接预算是共享的：同一 PostgreSQL 实例还承载 Langfuse。
    # prod 实为 --workers 4，4 × (10 + 10) ≈ 80 连接，须作为 RDS 规格的输入。
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

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/infra/db/test_engine_pg.py -v`
Expected: PASS。

- [ ] **Step 5: 全量跑一遍存储侧测试看破在哪**

Run: `pytest tests/infra/db/ -v`
Expected: 部分失败 —— 接下来两个 Task 处理。

- [ ] **Step 6: 提交**

```bash
ruff check src/infra/db && pyright src/infra/db
git add src/infra/db/engine.py tests/infra/db/test_engine_pg.py
git commit -m "feat(db): 引擎与迁移切到 PostgreSQL（asyncpg）"
```

---

## Task 7: 去掉 MySQL 方言类型与 `IntegrityError` 式幂等写入

**Files:**
- Modify: `src/infra/db/models/chat.py:6,43-45`
- Modify: `src/infra/db/mysql_db/chat_repo.py:25-44`
- Modify: `src/infra/db/mysql_db/kb_repo.py:19-50`
- Test: `tests/infra/db/test_repo_upsert.py`（新建）

**Interfaces:**
- Consumes: Task 6 的 `session_factory`
- Produces: **签名与返回语义完全不变** —— `ChatRepo.create_session(session) -> None`（`session` 是带 `.id` / `.user_id` / `.title` / `.kb_id` / `.agent` 属性的对象）与 `KbRepo.get_or_create_kb(user_id, name, description="") -> tuple[str, bool]`（`(kb_id, created)`）

> ⚠ **本任务收窄了 change 里 tasks §3.2 的措辞。** tasks §3.2 写「5 个 Repo 的幂等写入改为 `INSERT ... ON CONFLICT DO UPDATE`」。实读代码后：只有 `chat_repo.create_session` 是**纯幂等插入**，可以安全改写；`kb_repo.get_or_create_kb` 是**三态语义**（新建 / 复活软删 / 已存在活跃），`ON CONFLICT DO UPDATE` 表达不了「已存在活跃 → 返回 False」，硬改会改掉返回值。另外 3 个 repo（document / eval / user）**根本没有** `IntegrityError` 捕获。因此本任务只改 1 处，并在 change 文档里注明该收窄（见 Step 7）。**Step 2 是特性化测试（characterization test）**：它在改写前后都必须通过 —— 这是重构，不是新功能，所以不存在「先失败」的步骤。

- [ ] **Step 1: 写特性化测试（改写前必须先通过）**

新建 `tests/infra/db/test_repo_upsert.py`：

```python
"""幂等写入的语义守卫测试（需真实 PostgreSQL）。

这些是特性化测试：改写实现前后行为必须完全一致。
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import ChatRepo, KbRepo

pytestmark = pytest.mark.asyncio


@dataclass
class _SessionStub:
    """create_session 需要的最小会话对象（对应 ChatRepo.create_session 的入参）。"""

    id: str
    user_id: str
    title: str
    kb_id: str
    agent: str


async def _scalar(sql: str, **params):
    async with session_factory() as session:
        return (await session.execute(text(sql), params)).scalar_one()


@pytest.fixture
async def chat_repo():
    repo = ChatRepo(session_factory)
    yield repo
    async with session_factory() as session:
        await session.execute(text("DELETE FROM sessions WHERE id LIKE 'upsert-%'"))
        await session.commit()


@pytest.fixture
async def kb_repo():
    repo = KbRepo(session_factory)
    yield repo
    async with session_factory() as session:
        await session.execute(
            text("DELETE FROM knowledge_base WHERE user_id = 'upsert-user'")
        )
        await session.commit()


async def test_create_session_twice_is_idempotent(chat_repo):
    """同一 session_id 连续创建两次不得抛错，且只留一行。"""
    stub = _SessionStub(
        id="upsert-sess-1", user_id="u1", title="t", kb_id="kb1", agent=""
    )

    await chat_repo.create_session(stub)
    await chat_repo.create_session(stub)

    assert await _scalar("SELECT count(*) FROM sessions WHERE id = 'upsert-sess-1'") == 1


async def test_create_session_does_not_overwrite_existing(chat_repo):
    """已存在时必须保留原值（等价于 on_conflict_do_nothing，不是 do_update）。"""
    await chat_repo.create_session(
        _SessionStub(id="upsert-sess-2", user_id="u1", title="原值", kb_id="kb1", agent="")
    )
    await chat_repo.create_session(
        _SessionStub(id="upsert-sess-2", user_id="u1", title="新值", kb_id="kb2", agent="x")
    )

    assert await _scalar("SELECT title FROM sessions WHERE id = 'upsert-sess-2'") == "原值"
    assert await _scalar("SELECT kb_id FROM sessions WHERE id = 'upsert-sess-2'") == "kb1"


async def test_get_or_create_kb_is_idempotent(kb_repo):
    """同名第二次必须返回同一 id，且 created 为 False。"""
    first_id, first_created = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb")
    second_id, second_created = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb")

    assert first_id == second_id
    assert first_created is True
    assert second_created is False


async def test_get_or_create_kb_revives_soft_deleted(kb_repo):
    """软删过的同名 KB 必须被复活（同 id），并返回 created=True。"""
    kb_id, _ = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb-soft")
    assert await kb_repo.soft_delete_kb(kb_id) is True

    revived_id, created = await kb_repo.get_or_create_kb("upsert-user", "upsert-kb-soft")

    assert revived_id == kb_id
    assert created is True
    assert await _scalar(
        "SELECT is_deleted FROM knowledge_base WHERE id = :i", i=kb_id
    ) == 0
```

> **这两个 `created` 返回值必须在改写前后一致** —— 它们被 `services/kb_service.py` 消费（决定是否发「新建成功」文案）。若改写后 `test_get_or_create_kb_is_idempotent` 的 `second_created` 变成 `True`，就是行为回归。

- [ ] **Step 2: 跑测试确认（应全部通过 —— 这是特性化测试）**

Run: `pytest tests/infra/db/test_repo_upsert.py -v`
Expected: **PASS（5 条）**。若有失败，说明当前实现的行为与你的理解不符 —— **先照着实际行为改测试**，再进入下一步；不要带着错误的理解改写代码。

- [ ] **Step 3: 去掉 `MEDIUMTEXT`**

`src/infra/db/models/chat.py`：

- 删掉 `:6` 的 `from sqlalchemy.dialects.mysql import MEDIUMTEXT`
- `:43-45` 改为：

```python
    process: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="过程事件JSON（历史回放）"
    )
```

- [ ] **Step 4: 把 `chat_repo.create_session` 改成 `ON CONFLICT DO NOTHING`**

`src/infra/db/mysql_db/chat_repo.py:19-44` 改为（**签名 `(self, session)` 不变**）：

```python
    async def create_session(self, session) -> None:
        """session: 带 .id .user_id .title .kb_id .agent 属性的对象。

        幂等：同一 session_id 已存在（多轮对话重复持久化）时静默跳过，
        不再依赖捕获主键冲突异常。
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        agent = session.agent
        if agent is None:
            agent = ""

        async with self._sf() as s:
            stmt = (
                pg_insert(SessionModel)
                .values(
                    id=session.id,
                    user_id=session.user_id,
                    title=session.title,
                    kb_id=session.kb_id,
                    agent=agent,
                )
                # 已存在 → 保留原行，不改任何列（与改写前"静默跳过"等价）
                .on_conflict_do_nothing(index_elements=[SessionModel.id])
            )
            await s.execute(stmt)
            await s.commit()
```

把 `from sqlalchemy.exc import IntegrityError` 从该文件的 import 中删掉（如果它不再被使用 —— 先 grep 确认）。

**不要**改成 `on_conflict_do_update` —— 那会覆盖已有会话的 `title`/`kb_id`/`agent`，直接违反 `test_create_session_does_not_overwrite_existing`。

- [ ] **Step 5: `kb_repo.get_or_create_kb` 保持异常兜底不改，只补注释**

`src/infra/db/mysql_db/kb_repo.py:16-50` 的**代码逻辑不动**（它是三态语义，见上方 Interfaces 的说明），只在 docstring 里补上契约：

```python
    async def get_or_create_kb(
        self, user_id: str, name: str, description: str = ""
    ) -> tuple[str, bool]:
        """按 (user_id, name) 取或建知识库。

        三态语义（返回值被 kb_service 消费，不可随意改）：
        - 新建 → (新 id, True)
        - 同名但被软删 → 复活原记录，返回 (原 id, True)
        - 同名且活跃 → (原 id, False)

        实现仍走"插入撞唯一键 → 回滚 → 回读"：三态用 ON CONFLICT DO UPDATE 表达不了
        （RETURNING 只能看到更新后的行，无法区分"原本活跃"与"刚被复活"），
        强行改写会改掉返回值语义。
        """
```

- [ ] **Step 6: 跑测试确认行为未变**

Run: `pytest tests/infra/db/ -v`
Expected: PASS（含 Step 1 的 5 条特性化测试，全部行为与改写前一致）。

- [ ] **Step 7: 在 change 文档注明 tasks §3.2 的收窄**

在 `docs/openspec/changes/postgres-storage-consolidation/tasks.md` 的 §3.2 后追加一行：

```markdown
  > ⚠ **收窄（P1 实施时据实修正）**：实读代码后只有 `chat_repo.create_session` 是纯幂等插入，
  > 可安全改为 `ON CONFLICT DO NOTHING`；`kb_repo.get_or_create_kb` 是三态语义
  > （新建 / 复活软删 / 已存在活跃），`ON CONFLICT DO UPDATE` 表达不了「已存在活跃 → False」，
  > 保持异常兜底并补 docstring 说明；document / eval / user 三个 repo 无 `IntegrityError` 捕获。
  > 故实际改动为 **1 处**，非 5 处。
```

- [ ] **Step 8: 跑门禁并提交**

```bash
ruff check src/infra/db && pyright src/infra/db
git add src/infra/db/models/chat.py src/infra/db/mysql_db/chat_repo.py \
        src/infra/db/mysql_db/kb_repo.py tests/infra/db/test_repo_upsert.py \
        docs/openspec/changes/postgres-storage-consolidation/tasks.md
git commit -m "refactor(db): create_session 改用 ON CONFLICT DO NOTHING；去 MySQL 方言类型"
```

- [ ] **Step 7: 跑门禁并提交**

```bash
ruff check src/infra/db && pyright src/infra/db
git add src/infra/db/models/chat.py src/infra/db/mysql_db/chat_repo.py src/infra/db/mysql_db/kb_repo.py tests/infra/db/test_repo_upsert.py
git commit -m "refactor(db): 幂等写入改用 ON CONFLICT，去掉 MySQL 方言类型"
```

---

## Task 8: 测试与脚手架改造（真实 PG）

**Files:**
- Delete: `tests/infra/db/test_mysql_db.py`
- Create: `tests/infra/db/test_db.py`（由前者改写，273 行 / 11 个测试）
- Modify: `tests/reset_data.py:23-41`（`reset_mysql` → `reset_pg`）
- 无需改动但需确认：`tests/infra/search/test_query_router.py:179,211,239`（patch 路径 `src.infra.db.mysql_db.DocumentRepo` 保持不变 —— P1 不改包名）、`tests/rag/test_temporal.py:12`、`scripts/rebuild_kb_data.py:24`
- Test: 本身

**Interfaces:**
- Consumes: Task 5 的 8 张表、Task 6 的引擎
- Produces: `tests/reset_data.py` 提供 `reset_pg()`（清空全部业务表并按需重建）

**要保住的 11 个测试行为**（逐一照搬，不要重写断言）：`test_create_and_get_kb`、`test_document_crud`、`test_get_kb_name_by_id`、`test_get_doc_names`、`test_get_all_kb_doc_count`、`test_create_session_idempotent`、`test_message_status_default_complete`、`test_save_message_passthrough_status`、`test_user_created_at_before_assistant`、`test_bind_session_agent_is_bind_once`、`test_create_session_with_agent_persists`。

- [ ] **Step 1: 改名并跑一遍，看方言相关断言破在哪**

```bash
git mv tests/infra/db/test_mysql_db.py tests/infra/db/test_db.py
pytest tests/infra/db/test_db.py -v
```

Expected: 多数通过（SQLAlchemy 屏蔽了方言差异），少数因 MySQL 专有行为失败。**逐条记录失败原因再改**，不要盲改。

- [ ] **Step 2: 修 `tests/reset_data.py` 为单库重置**

把 `reset_mysql()`（`:23-41` 附近）改为：

```python
async def reset_pg() -> None:
    """清空 PostgreSQL 中本项目的全部业务表。

    用 TRUNCATE ... RESTART IDENTITY CASCADE 一次性清空并重置序列；
    表名硬编码于此，避免误删同实例上的 Langfuse 库（本函数只连应用库）。
    """
    from sqlalchemy import text

    from src.infra.db.engine import engine

    tables = (
        "conversation_history, document, eval_report, feedback, "
        "sessions, knowledge_base, users, chunks"
    )
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
```

调用点同步改名；`src/infra/db/mysql_db/alembic/` 已删（Task 2），确认 `reset_data.py` 不再引用它。

- [ ] **Step 3: 修 patch 路径**

`tests/infra/search/test_query_router.py:179,211,239` 的
`patch("src.infra.db.mysql_db.DocumentRepo", ...)` —— 本阶段**不改包名**，路径保持原样即可；只需确认它仍能 import 成功。

- [ ] **Step 4: 跑全量测试**

Run: `pytest tests/ -v`
Expected: 全绿。若有失败，**逐个定位**；共同的失败原因是"还在连 MySQL"，回查 Task 4/6 的 DSN 与环境变量。

- [ ] **Step 5: 跑门禁**

```bash
ruff format . && ruff check . --fix && pyright src/
```

Expected: ruff 无错；pyright 不新增 error（存量第三方误报不算）。

- [ ] **Step 6: 提交**

```bash
git add -A tests scripts
git commit -m "test(db): 存储侧测试与重置脚手架改为真实 PostgreSQL"
```

---

## Task 9: 退役 MySQL（服务、卷、init 脚本、依赖）

**Files:**
- Delete: `deploy/mysql/`
- Modify: `docker-compose.yml:3-25`（mysql 服务）、`:16`（`mysql_data` 挂载）、`:256-257`（卷声明）、app 的 `MYSQL_*` 环境变量与 `depends_on`
- Modify: `docker-compose.prod.yml:10-29`、`:19-20`、`:224-225`、app 的 `MYSQL_*`
- Modify: `pyproject.toml:31,33,34`（删 MySQL 驱动）、新增 `asyncpg` / `pgvector` / `sqlalchemy`
- Modify: `src/config/settings.py:145-149`（删 `MYSQL_*`）
- Test: `tests/config/test_no_mysql_leftovers.py`（新建）

**Interfaces:**
- Consumes: Task 8 全绿
- Produces: 仓库内不再有 MySQL 依赖与配置

- [ ] **Step 1: 写会失败的测试**

新建 `tests/config/test_no_mysql_leftovers.py`：

```python
"""退役 MySQL 的残留检查。"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# 只扫代码与配置，不扫 docs —— 变更文档里必然会引用 mysql+aiomysql 这个名字
SCAN_DIRS = ("src", "alembic", "tests", "scripts", "deploy")
SCAN_GLOBS = ("*.py", "*.ini", "*.yml", "*.yaml", "*.toml", "*.sql")
MYSQL_URL = re.compile(r"mysql\+(aio)?mysql|mysql\+pymysql")


def test_mysql_drivers_not_importable():
    """三个 MySQL 驱动都应从依赖里移除。"""
    for mod in ("aiomysql", "pymysql", "mysql.connector"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)


def test_no_mysql_settings():
    """settings 不应再导出 MYSQL_* 常量。"""
    settings = importlib.import_module("src.config.settings")
    assert not hasattr(settings, "MYSQL_HOST")


def test_deploy_mysql_dir_gone():
    assert not (REPO / "deploy" / "mysql").exists()


def test_no_mysql_url_left():
    """源码与配置里不得再有 mysql+aiomysql / mysql+pymysql 连接串。"""
    offenders = []
    for dirname in SCAN_DIRS:
        root = REPO / dirname
        if not root.exists():
            continue
        for pattern in SCAN_GLOBS:
            for path in root.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if MYSQL_URL.search(text):
                    offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"仍有 MySQL 连接串：{offenders}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/config/test_no_mysql_leftovers.py -v`
Expected: FAIL（驱动还在、`MYSQL_HOST` 还在、目录还在）。

- [ ] **Step 3: 删 MySQL 服务、卷与 init 脚本**

- `git rm -r deploy/mysql`
- `docker-compose.yml`：删 `mysql` 服务块（`:3-25`）、app 的 `mysql:` 依赖与 `MYSQL_HOST`/`MYSQL_PASSWORD` 环境变量、`volumes` 段里的 `mysql_data`（`:256-257`）。
- `docker-compose.prod.yml`：同上（`:10-29`、`:19-20`、`:224-225`、app 的 `MYSQL_*`）。

> ⚠ **先把 `deploy/postgres/init/` 挂载确认无误再删 `deploy/mysql/`**（Task 4 已完成）。删完立刻 `docker compose config` 校验。

- [ ] **Step 4: 删 `settings.py` 的 `MYSQL_*` 与 `deploy/mysql` 的残留引用**

```bash
grep -rn "MYSQL_\|deploy/mysql" --include="*.py" --include="*.yml" --include="*.ini" .
```

逐处清理。`scripts/rebuild_kb_data.py` 只 import repo，不受影响（P4 会整体删它）。

- [ ] **Step 5: 换依赖**

`pyproject.toml`：

- 删 `aiomysql>=0.2.0,<1.0.0`（`:34`）、`mysql-connector-python`（`:31`）、`pymysql`（`:33`）
- 加：

```toml
"asyncpg>=0.30.0,<1.0.0",
"pgvector>=0.3.6,<1.0.0",
"sqlalchemy[asyncio]>=2.0.36,<3.0.0",
```

> `sqlalchemy` 目前**未直接声明**（是传递依赖）—— 我们直接 import 它，必须显式声明。

- [ ] **Step 6: 重装依赖并跑测试**

```bash
uv sync --all-extras   # 或项目实际使用的安装方式
pytest tests/config/test_no_mysql_leftovers.py -v
```

Expected: PASS（4 条全绿）。

- [ ] **Step 7: 重建镜像并端到端验证**

```bash
docker compose build app && docker compose up -d --force-recreate app
docker compose logs --tail=80 app
```

Expected: 应用启动无报错；日志里没有 MySQL 连接失败。

- [ ] **Step 8: 跑全量测试与门禁**

```bash
pytest tests/ -v && ruff check . && pyright src/
```

Expected: 全绿。

- [ ] **Step 9: 提交**

```bash
git add -A
git commit -m "chore(db): 退役 MySQL（服务/卷/init 脚本/驱动/配置）"
```

---

## Task 10: 文档同步与收尾

**Files:**
- Modify: `docs/agents/code-map.md`、`docs/agents/data-flow.md`、`docs/agents/api_contract.md`、`docs/agents/glossary.md`
- Modify: `docs/agents/requirements_pool.md`（登记两项）
- Test: `tests/cli/test_doc_consistency.py`（已存在，跑通即可）

**Interfaces:**
- Consumes: 全部前序 Task
- Produces: 文档与代码一致

- [ ] **Step 1: 更新 `code-map.md`**

把关系型存储的描述从 MySQL 改为 PostgreSQL；写明：
- 引擎与 DSN 归属 `src/infra/db/engine.py`（DSN 由 `src/config/settings.py:build_postgres_dsn()` 提供）
- ORM 模型唯一来源 `src/infra/db/models/`
- alembic 唯一链在根 `alembic/`，baseline 文件 `alembic/versions/0001_pg_baseline.py`
- **`chunks` 表由 alembic baseline 建，但 ORM 里没有对应模型**（P2 会决定是否补）
- ⚠ 注明：`src/infra/db/mysql_db/` 这个**包名**在 P1 后已名不副实（里面是 PG repo），改名是独立事项（见 Step 3）

- [ ] **Step 2: 更新 `data-flow.md` 与 `api_contract.md`**

`data-flow.md`：把 MySQL 相关链路改为 PostgreSQL。
`api_contract.md`：P1 **不改任何 API 与公共方法签名**（repo 的签名与语义保持不变），因此只需确认无遗留引用；如发现文档里写着 MySQL 专有行为（如「靠唯一键冲突回滚」），改为 `ON CONFLICT` 的实际语义。

- [ ] **Step 3: 登记遗留项 L1–L4 到需求池**

在 `docs/agents/requirements_pool.md` 追加（编号沿用现有序列，勿覆盖）：

1. **（L1+L2）RDS 托管化切换**：RDS 侧的扩展清单与 `CREATE EXTENSION` 权限、RDS 上预建应用库与最小权限账号、`docker-compose.prod.yml` 指向 RDS、prod 安装与部署验证。背景：本轮（2026-09-19 用户决定）只做本地，不处理远程 RDS、不进行 prod 安装；本地已实测 `vector` 不是 trusted 扩展（应用账号会被拒），托管侧须先确认同项权限。
2. **（L3）`docker-compose.prod.yml` 的 `--workers 4` 与 `CLAUDE.md` 的「生产单 worker」规则冲突** —— 4 × (pool_size 10 + max_overflow 10) ≈ 80 连接，与 Langfuse 共享 RDS 的 `max_connections`。属独立变更（涉及流式生成状态在进程内这一前提），也是托管化时 RDS 规格的输入约束。
3. **（L4）`src/infra/db/mysql_db/` 包改名**（如 → `src/infra/db/repos/`）。理由：P1 之后该包内全是 PostgreSQL repo，包名会误导 `code-map.md` 与后续读者。P1 未做是因为它触及约 20 个 import 点，属纯命名变更、不在已评审的 change 范围内。

- [ ] **Step 4: 跑文档一致性测试**

Run: `pytest tests/cli/test_doc_consistency.py -v`
Expected: PASS。

- [ ] **Step 5: 跑最终门禁**

```bash
pytest tests/ -v
ruff format . && ruff check . --fix
pyright src/
grep -rn "print(" src/ | grep -v "\.pyc" || echo "no print"
grep -rn "TODO\|FIXME" src/ || echo "no todo"
```

Expected: 测试全绿、ruff 无错、pyright 不新增 error、无 `print()`、无 TODO/FIXME。

- [ ] **Step 6: 更新 change 的 tasks.md 索引与修正记录**

`docs/openspec/changes/postgres-storage-consolidation/tasks.md` 现在是**指针文件**（任务清单已迁到本计划），不要去找 checkbox 勾选。本步骤要做两件事：

1. 把顶部阶段表里 **P1 的状态从「待执行」改成「已完成」**（附本次收口的 commit 短 hash）。
2. 在「§2 实施期修正记录」表里补上执行期发现的新条目（本次已知至少有两条：`vector` 非 trusted 扩展 → 扩展改由超级用户创建、迁移只做断言；`kb_repo.get_or_create_kb` 保持异常兜底）。**若执行中还发现了别的事实偏差，一并登记**。

- [ ] **Step 7: 提交**

```bash
git add -A docs
git commit -m "docs: 同步 PostgreSQL 迁移后的代码结构与归属（P1 收尾）"
```

---

## 明确不在 P1 范围（避免越界）

- **不做远程 RDS 相关的一切**：不查 RDS 扩展清单、不在 RDS 上建库/建扩展/建账号、不改任何 RDS 配置（用户 2026-09-19 决定）
- **不做 prod 安装/部署/验证**：`docker-compose.prod.yml` 本轮只做与 dev 同构的**文件**调整，防止「依赖里删了 `aiomysql` 而 prod compose 还留 MySQL 服务」的不自洽状态
- **不改** `docker-compose.prod.yml` 指向 RDS（托管化是遗留项，另案）
- **不改** `src/infra/db/mysql_db/` 的包名（→ 需求池）
- **不改** `vector_store/`、`retrieval.py`、`bm25_index.py`、`document_service.py` 的检索/事务逻辑（→ P2/P3/P4）
- **不删** `chromadb` / `rank_bm25` / `data/chroma_persist` / `data/bm25_index`（→ P4）
- **不处置** `--workers 4` 与连接预算（→ 需求池 / 托管化遗留项）
- **不做** Chroma → PG 数据搬迁（→ P2）

## 遗留项（本轮明确不做，需登记）

| # | 遗留 | 归属 |
|---|---|---|
| L1 | RDS 侧扩展清单、`CREATE EXTENSION` 的账号与权限、RDS 上预建应用库/账号 | 托管化另案 |
| L2 | prod 安装与部署验证（含 `docker-compose.prod.yml` 指向 RDS） | 托管化另案 |
| L3 | prod 的 `--workers 4` 与 `CLAUDE.md`「生产单 worker」冲突；4 × (10+10) ≈ 80 连接 + Langfuse 对 RDS `max_connections` 的挤压 | 托管化另案（作为 RDS 规格输入） |
| L4 | `src/infra/db/mysql_db/` 包改名（P1 后包名名不副实） | 需求池 |

Task 10 要把 L1–L4 登记进 `docs/agents/requirements_pool.md`。
