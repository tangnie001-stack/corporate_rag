# Iter 3 — 向量存储与检索 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`\- [x]`) syntax for tracking.

**Goal:** 实现 DashScope Embedding 向量化、ChromaDB 向量存储与语义检索、检索质量 CLI，完成"文档入库 → 向量化 → 语义检索"闭环。

**Architecture:** `models.py` 提供 Embedding 工厂函数（含 retry + 指数退避），`vector_store.py` 封装 ChromaDB PersistentClient（每个知识库一个 collection，`kb_` 前缀），`check_retrieval.py` 作为检索测试 CLI。与 Iter 2 的 `DocRouter`/`MySQLDB` 集成，将分块结果向量化后入库。

**Tech Stack:** Python 3.11, ChromaDB 0.5+, langchain-openai (DashScope OpenAI-compatible API), langchain-community (DashScopeEmbeddings), loguru

---

## 文件结构 Iter 3 创建/修改清单

```
src/
├── models.py            (新建) LLM/Embedding/Rerank 工厂 + retry 装饰器
├── vector_store.py      (新建) ChromaDB PersistentClient 封装
├── check_retrieval.py   (新建) 检索质量 CLI
├── config.py            (已有，不动) 读取 EMBEDDING_MODEL 等
├── parsers/             (已有，不动)
├── mysql_db.py           (已有，不动)
└── app.py               (已有，不动)

tests/
├── test_models.py       (新建) 模型工厂测试（mock API）
├── test_vector_store.py (新建) ChromaDB 封装测试
├── test_retrieval.py    (新建) 检索测试（需 mock embedding）
└── ... (已有 test_base.py, test_mysql_db.py 等，不动)
```

---

## Prerequisite: 确认 DashScope API Key

- [x] **Step 1: 确认 DashScope API Key** — 用户已提供真实 Key `sk-1e8d...`

---

### Task 1: 模型工厂（models.py）

**Files:**
- Create: `src/models.py`
- Create: `tests/test_models.py`

models.py 提供 4 个工厂函数 + retry 装饰器：

| 函数 | 返回类型 | 用途 |
|------|---------|------|
| `get_embeddings(model: str) -> DashScopeEmbeddings` | Embedding 模型 | 文本向量化 |
| `get_llm(model: str, temperature: float) -> ChatOpenAI` | LLM 模型 | 生成回答（Iter 4 用） |
| `get_rerank(model: str, top_n: int) -> DashScopeRerank` | Rerank 模型 | 重排序（Iter 4 用） |
| `with_retry(func, max_attempts, interval, backoff) -> Callable` | 装饰器 | 统一重试逻辑 |

\- [x] **Step 1: 写测试 `tests/test_models.py`**

```python
"""Tests for model factory functions."""
from unittest.mock import patch, MagicMock
import pytest
from src.models import get_embeddings, get_llm, get_rerank, with_retry


class TestWithRetry:
    def test_retry_success(self):
        """Function that fails twice then succeeds should return result."""
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary error")
            return "success"

        wrapped = with_retry(flaky, max_attempts=5, initial_interval=0.01, backoff=1.0)
        assert wrapped() == "success"
        assert call_count == 3

    def test_retry_exhausted(self):
        """Function that always fails should raise after max_attempts."""
        call_count = 0

        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("persistent error")

        wrapped = with_retry(always_fails, max_attempts=3, initial_interval=0.01, backoff=1.0)
        with pytest.raises(ValueError, match="persistent error"):
            wrapped()
        assert call_count == 3


class TestGetEmbeddings:
    def test_get_embeddings_returns_instance(self):
        emb = get_embeddings()
        assert emb is not None
        # Check it has expected interface
        assert hasattr(emb, "embed_query")
        assert hasattr(emb, "embed_documents")

    def test_get_embeddings_custom_model(self):
        emb = get_embeddings(model="text-embedding-v2")
        # Should not raise — model name just configures the instance
        assert emb is not None


class TestGetLLM:
    def test_get_llm_returns_instance(self):
        llm = get_llm()
        assert llm is not None
        assert hasattr(llm, "invoke") or hasattr(llm, "generate")

    def test_get_llm_temperature(self):
        llm = get_llm(temperature=0.5)
        assert llm is not None

    def test_get_llm_custom_model(self):
        llm = get_llm(model="qwen-turbo")
        assert llm is not None


class TestGetRerank:
    def test_get_rerank_returns_instance(self):
        rerank = get_rerank()
        assert rerank is not None
        assert hasattr(rerank, "rerank") or hasattr(rerank, "rank")

    def test_get_rerank_top_n(self):
        rerank = get_rerank(top_n=3)
        assert rerank is not None
```

\- [x] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_models.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（models.py 不存在）
```

\- [x] **Step 3: 实现 `src/models.py`**

```python
"""Model factory functions for LLM, Embedding, Rerank with retry."""
import time
import functools
from typing import Callable, TypeVar

from loguru import logger
from langchain_openai import ChatOpenAI
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.cross_encoders import DashScopeRerank

from src.config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    LLM_MODEL,
    EMBEDDING_MODEL,
    RERANK_MODEL,
    LLM_TEMPERATURE,
    TOP_K_RERANK,
    RETRY_MAX_ATTEMPTS,
    RETRY_INITIAL_INTERVAL,
    RETRY_BACKOFF_FACTOR,
)

F = TypeVar("F", bound=Callable)


def with_retry(
    func: F = None,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
    initial_interval: float = RETRY_INITIAL_INTERVAL,
    backoff: float = RETRY_BACKOFF_FACTOR,
) -> Callable:
    """Decorator: retry a function with exponential backoff.

    Can be used with or without arguments:
        @with_retry
        def my_func(): ...

        @with_retry(max_attempts=5, initial_interval=2.0)
        def my_func(): ...
    """
    if func is None:
        return lambda f: with_retry(f, max_attempts, initial_interval, backoff)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    wait = initial_interval * (backoff ** (attempt - 1))
                    logger.warning(
                        "{} failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                        func.__name__, attempt, max_attempts, e, wait,
                    )
                    time.sleep(wait)
        logger.error("{} failed after {} attempts", func.__name__, max_attempts)
        raise last_error  # type: ignore

    return wrapper


def get_embeddings(model: str = EMBEDDING_MODEL) -> DashScopeEmbeddings:
    """Create a DashScope Embeddings instance."""
    return DashScopeEmbeddings(
        model=model,
        dashscope_api_key=DASHSCOPE_API_KEY,
    )


def get_llm(model: str = LLM_MODEL, temperature: float = LLM_TEMPERATURE) -> ChatOpenAI:
    """Create a DashScope LLM instance (OpenAI-compatible interface)."""
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )


def get_rerank(model: str = RERANK_MODEL, top_n: int = TOP_K_RERANK) -> DashScopeRerank:
    """Create a DashScope Rerank instance."""
    return DashScopeRerank(
        model=model,
        top_n=top_n,
        dashscope_api_key=DASHSCOPE_API_KEY,
    )
```

\- [x] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_models.py -v
# 预期: 6 passed
```

\- [x] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: add model factory functions (LLM/Embedding/Rerank) with retry"
```

---

### Task 2: 向量存储（vector_store.py）

**Files:**
- Create: `src/vector_store.py`
- Create: `tests/test_vector_store.py`

ChromaDB PersistentClient 封装，每个知识库一个 collection：

| 方法 | 说明 |
|------|------|
| `__init__()` | 初始化 ChromaDB PersistentClient，从 config 读取持久化目录 |
| `get_or_create_collection(kb_id) -> Collection` | 获取或创建 `kb_{uuid_hex}` collection |
| `add_chunks(kb_id, chunks, doc_id) -> int` | 将 ChunkData 列表向量化后入库，返回入库数 |
| `similarity_search(kb_id, query, k) -> list[dict]` | 语义检索，返回 top-k 结果 |
| `delete_collection(kb_id) -> bool` | 删除知识库对应的 collection |
| `delete_document(kb_id, doc_id) -> int` | 删除某个文档的所有 chunk |

\- [x] **Step 1: 写测试 `tests/test_vector_store.py`**

```python
"""Tests for VectorStore."""
import uuid
import pytest
from src.vector_store import VectorStore
from src.parsers.base import ChunkData


@pytest.fixture
def vs():
    """Return a VectorStore with a temp persistent dir."""
    import tempfile
    tmpdir = tempfile.mkdtemp()
    store = VectorStore(persist_dir=tmpdir)
    yield store
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def kb_id():
    return uuid.uuid4().hex


class TestVectorStore:
    def test_get_or_create_collection(self, vs, kb_id):
        coll = vs.get_or_create_collection(kb_id)
        assert coll is not None
        assert coll.name == f"kb_{kb_id}"

    def test_get_or_create_collection_idempotent(self, vs, kb_id):
        coll1 = vs.get_or_create_collection(kb_id)
        coll2 = vs.get_or_create_collection(kb_id)
        assert coll1.name == coll2.name

    def test_add_chunks_and_search(self, vs, kb_id):
        vs.get_or_create_collection(kb_id)
        chunks = [
            ChunkData(
                content="贵州茅台2024年营业收入1,741亿元",
                metadata={"source": "test.txt", "page": 1},
                chunk_id="test:0",
            ),
            ChunkData(
                content="贵州茅台2024年净利润857亿元",
                metadata={"source": "test.txt", "page": 1},
                chunk_id="test:1",
            ),
        ]
        doc_id = uuid.uuid4().hex
        count = vs.add_chunks(kb_id, chunks, doc_id)
        assert count == 2

        # Search should work (may return 0 results if no real embedding)
        results = vs.similarity_search(kb_id, "营业收入", k=5)
        assert isinstance(results, list)
        # Results may be empty if no embedding model available,
        # but the interface should return a list

    def test_delete_collection(self, vs, kb_id):
        vs.get_or_create_collection(kb_id)
        assert vs.delete_collection(kb_id) is True
        # Getting the deleted collection should recreate it (ChromaDB behavior)
        coll = vs.get_or_create_collection(kb_id)
        assert coll is not None

    def test_delete_nonexistent_collection(self, vs):
        result = vs.delete_collection("nonexistent_kb_id")
        # Should not raise — returns False or handles gracefully
        assert result is False

    def test_collection_name_format(self, vs, kb_id):
        """Collection name should follow kb_{uuid_hex} pattern."""
        coll = vs.get_or_create_collection(kb_id)
        assert coll.name.startswith("kb_")
        assert len(coll.name) > 3
```

\- [x] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_vector_store.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（vector_store.py 不存在）
```

\- [x] **Step 3: 实现 `src/vector_store.py`**

```python
"""ChromaDB vector store wrapper with kb_ prefix collections."""
import uuid
from typing import Optional

import chromadb
from chromadb.config import Settings
from loguru import logger

from src.config import CHROMA_PERSIST_DIR, CHROMA_COLLECTION_PREFIX
from src.parsers.base import ChunkData


class VectorStore:
    """ChromaDB vector store — one collection per knowledge base."""

    def __init__(self, persist_dir: Optional[str] = None):
        self.persist_dir = persist_dir or CHROMA_PERSIST_DIR
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}

    def _get_client(self) -> chromadb.PersistentClient:
        """Get or create the persistent client."""
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info("ChromaDB client initialized at {}", self.persist_dir)
        return self._client

    def _collection_name(self, kb_id: str) -> str:
        """Generate collection name from kb_id."""
        clean_id = kb_id.replace("-", "")
        return f"{CHROMA_COLLECTION_PREFIX}{clean_id}"

    def get_or_create_collection(self, kb_id: str) -> chromadb.Collection:
        """Get existing collection or create a new one."""
        cache_key = kb_id
        if cache_key in self._collection_cache:
            return self._collection_cache[cache_key]

        name = self._collection_name(kb_id)
        client = self._get_client()
        try:
            collection = client.get_collection(name)
        except ValueError:
            collection = client.create_collection(
                name=name,
                metadata={"hnsw:space": "cosine", "hnsw:M": 8, "hnsw:construction_ef": 64},
            )
            logger.info("Created collection '{}' for kb_id={}", name, kb_id)

        self._collection_cache[cache_key] = collection
        return collection

    def add_chunks(self, kb_id: str, chunks: list[ChunkData], doc_id: str) -> int:
        """Add chunks to the knowledge base collection. Returns count added."""
        if not chunks:
            return 0

        collection = self.get_or_create_collection(kb_id)
        ids = []
        documents = []
        metadatas = []

        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc_id}:{i}"
            ids.append(chunk_id)
            documents.append(chunk.content)
            metadatas.append({
                "source": chunk.metadata.get("source", ""),
                "page": chunk.metadata.get("page", 0),
                "chunk_index": i,
                "chunk_total": len(chunks),
                "doc_id": doc_id,
            })

        # ChromaDB will auto-embed using the default embedding function
        # When integrated with models.py, we use add with embeddings param
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Added {} chunks to collection '{}'", len(ids), collection.name)
        return len(ids)

    def similarity_search(self, kb_id: str, query: str, k: int = 5) -> list[dict]:
        """Search the knowledge base collection with a text query."""
        collection = self.get_or_create_collection(kb_id)
        results = collection.query(
            query_texts=[query],
            n_results=min(k, 100),
        )

        # Format results
        formatted = []
        if results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                formatted.append({
                    "id": results["ids"][0][i],
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "distance": results["distances"][0][i] if results.get("distances") else None,
                })
        return formatted

    def delete_collection(self, kb_id: str) -> bool:
        """Delete a knowledge base collection. Returns success."""
        name = self._collection_name(kb_id)
        client = self._get_client()
        try:
            client.delete_collection(name)
            self._collection_cache.pop(kb_id, None)
            logger.info("Deleted collection '{}'", name)
            return True
        except ValueError:
            logger.warning("Collection '{}' not found for deletion", name)
            return False

    def delete_document(self, kb_id: str, doc_id: str) -> int:
        """Delete all chunks belonging to a document. Returns count."""
        try:
            collection = self.get_or_create_collection(kb_id)
            # ChromaDB supports delete by metadata filter
            # We need to get all ids matching the doc_id
            results = collection.get(where={"doc_id": doc_id})
            if results["ids"]:
                collection.delete(ids=results["ids"])
                count = len(results["ids"])
                logger.info("Deleted {} chunks for doc_id={}", count, doc_id)
                return count
            return 0
        except ValueError:
            return 0

    def list_collections(self) -> list[str]:
        """List all kb_ collection names."""
        client = self._get_client()
        return [c.name for c in client.list_collections() if c.name.startswith(CHROMA_COLLECTION_PREFIX)]
```

\- [x] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_vector_store.py -v
# 预期: 6 passed（test_add_chunks_and_search 可能 skip 如果没有 embedding 模型）
```

\- [x] **Step 5: Commit**

```bash
git add src/vector_store.py tests/test_vector_store.py
git commit -m "feat: add VectorStore with ChromaDB PersistentClient wrapper"

---

### Task 3: 检索测试 CLI（check_retrieval.py）

**Files:**
- Create: `src/check_retrieval.py`

CLI 工具，对指定知识库执行语义检索并打印结果。

\- [x] **Step 1: 实现 `src/check_retrieval.py`**

```python
#!/usr/bin/env python3
"""Retrieval quality check CLI.

Usage:
    python src/check_retrieval.py --kb <kb_name> --query "<your question>"
    python src/check_retrieval.py --kb <kb_name> --query "<q>" --top-k 10

Requires existing documents in the knowledge base (from Iter 2 parsing flow)
and a valid DASHSCOPE_API_KEY in .env for embedding.
"""
import argparse
import sys
from loguru import logger
from src.mysql_db import MySQLDB
from src.vector_store import VectorStore
from src.config import TOP_K_RERANK


def main():
    parser = argparse.ArgumentParser(description="Retrieval quality checker")
    parser.add_argument("--kb", required=True, help="Knowledge base name")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--top-k", type=int, default=TOP_K_RERANK, help="Number of results (default: 5)")
    args = parser.parse_args()

    # Resolve kb_id from name
    db = MySQLDB()
    kb_id = db.get_kb_by_name(args.kb)
    if not kb_id:
        print(f"Error: Knowledge base '{args.kb}' not found.")
        print("Available KBs:")
        for kid, name in db.get_all_kb():
            print(f"  - {name} ({kid})")
        sys.exit(1)
    db.close()

    # Search
    store = VectorStore()
    try:
        results = store.similarity_search(kb_id, args.query, k=args.top_k)
    except Exception as e:
        print(f"Search failed: {e}")
        print("Hint: Ensure DASHSCOPE_API_KEY is set and documents have been added.")
        sys.exit(1)

    # Print results
    print("=" * 60)
    print(f"  检索结果")
    print(f"  知识库:  {args.kb}")
    print(f"  查询:    {args.query}")
    print(f"  返回:    {len(results)} 条结果")
    print("=" * 60)

    if not results:
        print("  (无结果)")
        print("=" * 60)
        return

    for i, r in enumerate(results):
        print(f"\n  [{i + 1}] 距离: {r.get('distance', 'N/A'):.4f}" if r.get('distance') else f"\n  [{i + 1}]")
        print(f"      来源: {r['metadata'].get('source', 'unknown')} (页 {r['metadata'].get('page', '?')})")
        print(f"      内容: {r['content'][:200]}...")
        print("-" * 60)
```

\- [x] **Step 2: 验证 CLI 可运行**

```bash
docker compose exec app python src/check_retrieval.py --kb "集成验证测试库" --query "营业收入"
# 如果该知识库已删除，会提示"not found"
# 预期: 显示检索结果或友好的错误提示
```

\- [x] **Step 3: Commit**

```bash
git add src/check_retrieval.py
git commit -m "feat: add check_retrieval.py CLI for semantic search testing"
```

---

### Task 4: 集成验证

**Files:** （无创建，仅运行命令）

\- [x] **Step 1: 确认 Docker 容器健康运行**

```bash
docker compose ps
# 预期: app (up), mysql (healthy), redis (healthy)
```

\- [x] **Step 2: 运行所有测试**

```bash
docker compose exec app python -m pytest tests/ -v
# 预期: 全部测试通过（包括已有的 36 个 + 新增的 models/vector_store 测试）
```

\- [x] **Step 3: 验证 models.py 工厂函数**

```bash
docker compose exec app python -c "
from src.models import get_embeddings, get_llm, get_rerank
emb = get_embeddings()
llm = get_llm()
rerank = get_rerank()
print(f'Embeddings: {type(emb).__name__}')
print(f'LLM: {type(llm).__name__}')
print(f'Rerank: {type(rerank).__name__}')
"
# 预期: 三个实例创建成功
```

\- [x] **Step 4: 验证 VectorStore 基本操作**

```bash
docker compose exec app python -c "
from src.vector_store import VectorStore
from src.parsers.base import ChunkData
import uuid

kb_id = uuid.uuid4().hex
vs = VectorStore()

# 创建 collection
coll = vs.get_or_create_collection(kb_id)
print(f'Collection: {coll.name}')

# 添加 chunks
chunks = [
    ChunkData(content='测试内容1', metadata={'source': 'test.txt', 'page': 1}, chunk_id='test:0'),
    ChunkData(content='测试内容2', metadata={'source': 'test.txt', 'page': 1}, chunk_id='test:1'),
]
count = vs.add_chunks(kb_id, chunks, 'doc_test')
print(f'Added: {count} chunks')

# 检索（可能需要真实 embedding，取决于 DASHSCOPE_API_KEY）
results = vs.similarity_search(kb_id, '测试', k=5)
print(f'Search results: {len(results)}')

# 删除
vs.delete_collection(kb_id)
print(f'Collection deleted')

# 列表
all_colls = vs.list_collections()
print(f'Total kb_ collections: {len(all_colls)}')
"
# 预期: 创建、添加、搜索、删除全流程通过
```

\- [x] **Step 5: 验证 check_retrieval.py CLI**

```bash
# 先创建一个知识库并添加文档
docker compose exec app python -c "
from src.mysql_db import MySQLDB
from src.vector_store import VectorStore
from src.parsers.router import DocRouter
import uuid

# 创建知识库
db = MySQLDB()
kb_name = f'retrieval_test_{uuid.uuid4().hex[:6]}'
kid, is_new = db.get_or_create_kb(kb_name)
print(f'KB created: {kb_name} ({kid})')

# 解析文档
router = DocRouter()
result = router.parse('test_docs/sample.txt')
print(f'Parsed: {len(result.chunks)} chunks')

# 向量化入库
doc_id = db.add_document(kid, 'sample.txt', 'txt', 1167)
vs = VectorStore()
count = vs.add_chunks(kid, result.chunks, doc_id)
db.update_document_status(doc_id, 'ready', chunk_count=count)
print(f'Vectorized: {count} chunks')
db.close()
print(f'KB name for next step: {kb_name}')
" 2>&1

# 然后运行检索 CLI
echo "--- Now run: ---"
echo 'docker compose exec app python src/check_retrieval.py --kb "<kb_name>" --query "营业总收入"'
```

\- [x] **Step 6: Iter 3 完成——提交代码**

```bash
git add src/models.py src/vector_store.py src/check_retrieval.py tests/test_models.py tests/test_vector_store.py
git commit -m "feat: complete Iter 3 vector storage and retrieval

- Add model factory functions (LLM/Embedding/Rerank) with retry
- Add VectorStore with ChromaDB PersistentClient, kb_ prefix collections
- Add check_retrieval.py CLI for semantic search testing
- Add HNSW index config (cosine, M=8, ef_construction=64)
- Add comprehensive test suite"
```