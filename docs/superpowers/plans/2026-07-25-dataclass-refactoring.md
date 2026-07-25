# 数据层类型安全重构 + 大文件拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全面替换 `list[dict]` → typed dataclass，消除 None 静默传递问题；拆分 `mysql_db.py`(883行) 和 `vector_store.py`(582行)

**Architecture:** 三层失血模型实体体系：`entities/` 存放所有数据实体（MySQL + 检索），`mysql_db/` 包存放 Repo（连接池 + CRUD），`vector_store/` 包存放 ChromaDB 操作。Service 层做 Pydantic ←→ Entity 转换。

**Tech Stack:** Python 3.11+ / aiomysql + raw SQL / ChromaDB / BM25 / LangGraph / FastAPI

## Global Constraints

- 所有 dataclass 字段必须带注释
- 单文件不超过 400 行，超出必须拆包
- 不改 ChunkData (parsers/base.py)
- 不改 API 层的 Pydantic 模型（api/model/）
- API 层不直接调 infra（顺带修 api/documents.py）
- Entity 使用 `@dataclass(slots=True)`
- Entity 是纯失血模型（无业务方法）
- 每完成一个 Task 跑一次测试：`pytest tests/ -v`
- 每完成一个 Task commit 一次

---

## 文件结构总览

```
src/infra/db/
├── __init__.py                  # 导出 VectorStore + 所有 Repo
├── file_store.py                # 不动（MinIO）
│
├── entities/                    # Task 1: 所有数据实体
│   ├── __init__.py              #   导出所有实体
│   ├── search.py                #   ChunkResult, ChunkQueryResult
│   ├── kb.py                    #   KbEntity, KbListItem
│   ├── document.py              #   DocEntity
│   ├── chat.py                  #   SessionEntity, SessionListItem, MessageEntity
│   ├── user.py                  #   UserEntity
│   └── eval_report.py           #   EvalReportEntity
│
├── mysql_db/                    # Task 7: MySQL Repo 包
│   ├── __init__.py              #   导出 MySQLDB + 所有 Repo
│   ├── pool.py                  #   连接池管理
│   ├── kb_repo.py               #   KbRepo
│   ├── document_repo.py         #   DocumentRepo
│   ├── chat_repo.py             #   ChatRepo
│   ├── user_repo.py             #   UserRepo
│   └── eval_repo.py             #   EvalRepo
│
└── vector_store/                # Task 2: ChromaDB 包
    ├── __init__.py              #   导出 VectorStore
    ├── embedding.py             #   DashScopeEmbeddingFunction
    ├── client.py                #   连接 + collection 缓存
    ├── store.py                 #   写入/删除
    └── search.py                #   查询（返回 ChunkResult）
```

---

### Task 1: 创建 entities/ 目录和所有数据实体

**Files:**
- Create: `src/infra/db/entities/__init__.py`
- Create: `src/infra/db/entities/search.py`
- Create: `src/infra/db/entities/kb.py`
- Create: `src/infra/db/entities/document.py`
- Create: `src/infra/db/entities/chat.py`
- Create: `src/infra/db/entities/user.py`
- Create: `src/infra/db/entities/eval_report.py`

**Interfaces:**
- Produces: `ChunkResult`, `ChunkQueryResult`, `KbEntity`, `KbListItem`, `DocEntity`, `SessionEntity`, `SessionListItem`, `MessageEntity`, `UserEntity`, `EvalReportEntity`
- Produces: `entities/__init__.py` exports all types

- [ ] **Step 1: 创建 entities/__init__.py**

```python
"""数据实体 — 所有数据源（MySQL / ChromaDB / BM25）的实体类型。"""

from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
from src.infra.db.entities.kb import KbEntity, KbListItem
from src.infra.db.entities.document import DocEntity
from src.infra.db.entities.chat import SessionEntity, SessionListItem, MessageEntity
from src.infra.db.entities.user import UserEntity
from src.infra.db.entities.eval_report import EvalReportEntity

__all__ = [
    "ChunkResult", "ChunkQueryResult",
    "KbEntity", "KbListItem",
    "DocEntity",
    "SessionEntity", "SessionListItem", "MessageEntity",
    "UserEntity",
    "EvalReportEntity",
]
```

- [ ] **Step 2: 创建 entities/search.py**

```python
"""检索结果实体 — ChromaDB 语义检索和 BM25 词法检索的统一输出类型。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class ChunkResult:
    """检索结果统一类型。

    替代 similarity_search / BM25 search / RRF fusion / rerank 之间的 list[dict]。
    统一 ChromaDB 语义检索和 BM25 词法检索的输出格式。
    """

    id: str
    """分块 ID，格式为 {doc_id}:{chunk_index}（ChromaDB）或解析器生成（BM25）。"""
    content: str
    """分块的文本内容，由文档解析器生成，可能包含 Markdown 格式。"""
    metadata: dict = field(default_factory=dict)
    """元数据字典，包含 source（文件名）、page（页码）、doc_id（文档ID）等。"""
    distance: Optional[float] = None
    """余弦距离，仅语义检索时有值（越小越相似），BM25 检索和分页查询时为 None。"""
    bm25_score: Optional[float] = None
    """BM25 词法检索分数，仅 BM25 检索时有值，语义检索和分页查询时为 None。"""


@dataclass(slots=True)
class ChunkQueryResult:
    """分块分页查询结果（get_chunks_paginated 的返回类型）。"""

    items: list[ChunkResult]
    """当前页的分块列表。"""
    total: int
    """该文档的总分块数量。"""
    page: int
    """当前页码，从 1 开始。"""
    page_size: int
    """每页条数。"""
```

- [ ] **Step 3: 创建 entities/kb.py**

```python
"""知识库实体 — 对应 knowledge_base 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class KbEntity:
    """知识库实体，对应 knowledge_base 表一行记录。"""

    id: str
    """知识库 UUID。"""
    user_id: str = ""
    """所属用户 ID，空字符代表无用户场景。"""
    name: str = ""
    """知识库名称，同一用户下唯一。"""
    description: Optional[str] = None
    """知识库描述。"""
    status: str = "active"
    """状态：active / deleted。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后更新时间。"""


@dataclass(slots=True)
class KbListItem:
    """知识库列表项（含文档计数）。"""

    id: str
    """知识库 UUID。"""
    user_id: str
    """所属用户 ID。"""
    name: str
    """知识库名称。"""
    doc_count: int = 0
    """该知识库下的文档数量（LEFT JOIN document 计数）。"""
```

- [ ] **Step 4: 创建 entities/document.py**

```python
"""文档实体 — 对应 document 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class DocEntity:
    """文档实体，对应 document 表一行记录。"""

    id: str
    """文档 UUID。"""
    kb_id: str
    """所属知识库 ID（FK → knowledge_base.id）。"""
    filename: str
    """原始文件名。"""
    file_type: str = ""
    """文件类型：pdf / docx / txt。"""
    file_size: int = 0
    """文件大小（字节）。"""
    user_id: str = ""
    """上传用户 ID。"""
    status: str = "pending"
    """处理状态：pending / processing / ready / failed / deleted。"""
    file_path: Optional[str] = None
    """文件存储路径（MinIO 或本地路径）。"""
    hash: Optional[str] = None
    """文件 MD5 哈希。"""
    processing_state: Optional[str] = None
    """处理阶段：chunking / vectorizing / completed。"""
    processing_progress: int = 0
    """处理进度百分比（0-100）。"""
    processing_message: Optional[str] = None
    """处理状态描述消息。"""
    error_msg: Optional[str] = None
    """处理失败时的错误信息。"""
    chunk_strategy: str = "parent_child"
    """分块策略：parent_child / qa / table_preserving。"""
    chunk_count: int = 0
    """实际分块数量。"""
    meta_info: Optional[str] = None
    """JSON 格式的扩展元数据。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
```

- [ ] **Step 5: 创建 entities/chat.py**

```python
"""会话/消息实体 — 对应 sessions 和 conversation_history 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class SessionEntity:
    """会话实体，对应 sessions 表一行记录。"""

    id: str
    """会话 UUID。"""
    title: str = ""
    """会话标题（截取首条消息前 20 字）。"""
    kb_id: str = ""
    """关联的知识库 ID（空字符代表所有知识库）。"""
    user_id: str = ""
    """所属用户 ID。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后活跃时间。"""


@dataclass(slots=True)
class SessionListItem:
    """会话列表项（含知识库名称和消息数量）。"""

    id: str
    """会话 UUID。"""
    title: str
    """会话标题。"""
    kb_id: str
    """关联的知识库 ID。"""
    kb_name: str
    """知识库名称（LEFT JOIN 结果）。"""
    message_count: int
    """该会话的消息数量。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后活跃时间。"""


@dataclass(slots=True)
class MessageEntity:
    """消息实体，对应 conversation_history 表一行记录。"""

    session_id: str
    """所属会话 ID。"""
    role: str
    """角色：user / assistant。"""
    content: str
    """消息内容。"""
    kb_id: str = ""
    """关联的知识库 ID。"""
    sources: Optional[str] = None
    """来源引用 JSON 字符串。"""
    prompt_tokens: int = 0
    """提示 token 数。"""
    completion_tokens: int = 0
    """补全 token 数。"""
    total_tokens: int = 0
    """总 token 数。"""
    model_name: str = ""
    """模型名称（如 qwen-plus）。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
```

- [ ] **Step 6: 创建 entities/user.py**

```python
"""用户实体 — 对应 users 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class UserEntity:
    """用户实体，对应 users 表一行记录。"""

    id: str
    """用户 UUID。"""
    account: str
    """登录账号。"""
    password: str
    """密码的 bcrypt 哈希值。"""
    token: Optional[str] = None
    """当前登录 token。"""
    created_at: Optional[datetime] = None
    """注册时间。"""
```

- [ ] **Step 7: 创建 entities/eval_report.py**

```python
"""评估报告实体 — 对应 eval_report 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class EvalReportEntity:
    """评估报告实体，对应 eval_report 表一行记录。"""

    id: str
    """报告 UUID。"""
    kb_id: str
    """关联的知识库 ID。"""
    run_type: str = "manual"
    """运行类型：manual / auto。"""
    qa_count: int = 0
    """QA 对数量。"""
    faithfulness: Optional[float] = None
    """忠实度评分。"""
    answer_relevancy: Optional[float] = None
    """答案相关性评分。"""
    context_precision: Optional[float] = None
    """上下文精确度。"""
    context_recall: Optional[float] = None
    """上下文召回率。"""
    overall_score: Optional[float] = None
    """综合评分。"""
    passed: bool = False
    """是否通过评估。"""
    report_path: Optional[str] = None
    """报告文件路径。"""
    triggered_by: Optional[str] = None
    """触发者（用户 ID）。"""
    detail_json: Optional[dict] = None
    """详细评估数据（JSON 可序列化）。"""
    eval_date: Optional[datetime] = None
    """评估日期。"""
```

- [ ] **Step 8: 验证**

```bash
python3 -c "
from src.infra.db.entities import (
    ChunkResult, ChunkQueryResult,
    KbEntity, KbListItem,
    DocEntity,
    SessionEntity, SessionListItem, MessageEntity,
    UserEntity,
    EvalReportEntity,
)
# 验证构造和属性访问
cr = ChunkResult(id='a', content='hello')
assert cr.distance is None
assert cr.bm25_score is None
print(f'All {len(__all__) if hasattr(__all__, \"__len__\") else 10} entities OK')
# slots 验证
import traceback
for cls in [ChunkResult, KbEntity, DocEntity, SessionEntity, UserEntity]:
    try:
        obj = cls.__new__(cls)
        obj.unknown_field = 'test'
    except AttributeError:
        pass  # slots 正确阻止了动态属性
print('slots validation OK')
"
```

- [ ] **Step 9: Commit**

```bash
git add src/infra/db/entities/
git commit -m "feat: add entities/ dir with all data entity dataclasses"
```

---

### Task 2: vector_store 拆包 + 返回 ChunkResult

**Files:**
- Create: `src/infra/db/vector_store/__init__.py`
- Create: `src/infra/db/vector_store/embedding.py`
- Create: `src/infra/db/vector_store/client.py`
- Create: `src/infra/db/vector_store/store.py`
- Create: `src/infra/db/vector_store/search.py`
- Delete: `src/infra/db/vector_store.py`

**Interfaces:**
- Consumes: `ChunkResult`, `ChunkQueryResult` from `entities/`
- Produces: `VectorStore` class (same method signatures, return types changed)
- Produces: `VectorStore.similarity_search(kb_id, query, k=5) -> list[ChunkResult]`
- Produces: `VectorStore.similarity_search_all(query, k) -> list[ChunkResult]`
- Produces: `VectorStore.get_chunks_by_doc_id(doc_id, kb_id) -> list[ChunkResult]`
- Produces: `VectorStore.get_chunks_paginated(doc_id, kb_id, page, page_size) -> ChunkQueryResult`

- [ ] **Step 1: 创建 vector_store/embedding.py**

```python
"""DashScope Embedding 适配器。"""

from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from src.models import FixedDimDashScopeEmbeddings
from src.config import EMBEDDING_MODEL, DASHSCOPE_API_KEY


class DashScopeEmbeddingFunction(EmbeddingFunction):
    """DashScope 云端 Embedding 适配器，符合 ChromaDB 0.5+ 接口规范。"""

    def __init__(self, model: str = EMBEDDING_MODEL, api_key: str = DASHSCOPE_API_KEY):
        self._embedding = FixedDimDashScopeEmbeddings(
            model=model, dashscope_api_key=api_key
        )

    def __call__(self, input: Documents) -> Embeddings:
        return self._embedding.embed_documents(list(input))

    def embed_query(self, text: str) -> list[float]:
        return self._embedding.embed_query(text)
```

- [ ] **Step 2: 创建 vector_store/client.py**

```python
"""ChromaDB 连接管理和 collection 缓存。"""

from typing import Optional
import chromadb
from chromadb.config import Settings
from loguru import logger
from src.config import CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR
from src.infra.db.vector_store.embedding import DashScopeEmbeddingFunction
from src.config import EMBEDDING_MODEL


class ChromaClient:
    """ChromaDB 连接管理 + collection 缓存（单例模式）。"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or CHROMA_PERSIST_DIR
        self._client: Optional[chromadb.ClientAPI] = None
        self._collection_cache: dict[str, chromadb.Collection] = {}
        self._embed_fn = DashScopeEmbeddingFunction()

    def _get_client(self) -> chromadb.ClientAPI:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=self._persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info(
                "ChromaDB PersistentClient created: persist_dir={} model={}",
                self._persist_dir, EMBEDDING_MODEL,
            )
        return self._client

    @staticmethod
    def _collection_name(kb_id: str) -> str:
        clean_id = kb_id.replace("-", "")
        return f"{CHROMA_COLLECTION_PREFIX}{clean_id}"

    def get_or_create_collection(self, kb_id: str) -> chromadb.Collection:
        cache_key = kb_id
        if cache_key in self._collection_cache:
            return self._collection_cache[cache_key]
        name = self._collection_name(kb_id)
        client = self._get_client()
        collection = client.get_or_create_collection(
            name=name,
            embedding_function=self._embed_fn,
            metadata={
                "hnsw:space": "cosine",
                "hnsw:M": 8,
                "hnsw:construction_ef": 64,
            },
        )
        logger.debug("Got or created collection '{}' for kb_id={}", name, kb_id)
        self._collection_cache[cache_key] = collection
        return collection

    def delete_collection_cache(self, kb_id: str) -> None:
        self._collection_cache.pop(kb_id, None)

    def list_collection_names(self) -> list[str]:
        client = self._get_client()
        names = client.list_collections()
        return [n.name for n in names if n.name.startswith(CHROMA_COLLECTION_PREFIX)]
```

- [ ] **Step 3: 创建 vector_store/store.py**

```python
"""ChromaDB 写入和删除操作。"""

from loguru import logger
from src.parsers.base import ChunkData


def add_chunks(
    collection, kb_id: str, chunks: list[ChunkData], doc_id: str
) -> int:
    """批量写入分块到 collection。"""
    if not chunks:
        return 0

    ids = []
    documents = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{doc_id}:{i}"
        ids.append(chunk_id)
        documents.append(chunk.content)
        meta = dict(chunk.metadata)
        meta.update({"chunk_index": i, "chunk_total": len(chunks), "doc_id": doc_id})
        meta.setdefault("source", "")
        meta.setdefault("page", 0)
        metadatas.append(meta)

    try:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
    except Exception as e:
        logger.exception("ChromaDB add_chunks failed: kb_id={} doc_id={} error={}", kb_id, doc_id, e)
        raise
    logger.info("ChromaDB add_chunks success: kb_id={} doc_id={} count={}", kb_id, doc_id, len(ids))
    return len(ids)


def delete_document(collection, doc_id: str) -> int:
    """删除指定文档的所有分块。"""
    try:
        results = collection.get(where={"doc_id": doc_id})
        if results["ids"]:
            collection.delete(ids=results["ids"])
            count = len(results["ids"])
            logger.info("ChromaDB delete_document: doc_id={} deleted={}", doc_id, count)
            return count
        return 0
    except NotFoundError:
        return 0


def delete_collection(chroma_client, name: str, cache_key: str, cache: dict) -> bool:
    """删除整个 collection。"""
    try:
        chroma_client.delete_collection(name)
        cache.pop(cache_key, None)
        logger.info("Deleted collection '{}'", name)
        return True
    except (NotFoundError, ValueError):
        logger.warning("Collection '{}' not found for deletion", name)
        return False
```

- [ ] **Step 4: 创建 vector_store/search.py**

```python
"""ChromaDB 查询操作，返回类型化的 ChunkResult/ChunkQueryResult。"""

from loguru import logger
from src.core.logging import LOG_MAX_BODY
from src.config import TOP_K_RETRIEVAL, EMBEDDING_MODEL
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult


def similarity_search(collection, embed_fn, kb_id, query, k=5) -> list[ChunkResult]:
    """语义相似度检索，返回 list[ChunkResult]。"""
    query_vec = embed_fn.embed_query(query)
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=min(k, 100),
    )

    formatted = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            formatted.append(ChunkResult(
                id=results["ids"][0][i],
                content=results["documents"][0][i] if results["documents"] else "",
                metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                distance=results["distances"][0][i] if results.get("distances") else None,
            ))
    logger.info("ChromaDB search: kb_id={} query_len={} results={} model={}",
                kb_id, len(query), len(formatted), EMBEDDING_MODEL)
    logger.debug("[CHROMA] method=similarity_search | kb_id={} | rows={} | data={}",
                 kb_id, len(formatted),
                 str(formatted)[:LOG_MAX_BODY] if formatted else "[]")
    return formatted


def similarity_search_all(
    collections_dict, embed_fn, query, k=TOP_K_RETRIEVAL
) -> list[ChunkResult]:
    """在所有 collection 中进行语义搜索，合并后排序取 top-k。"""
    all_results: list[ChunkResult] = []
    for kb_id in collections_dict:
        try:
            col = collections_dict[kb_id]
            results = similarity_search(col, embed_fn, kb_id, query, k=k)
            all_results.extend(results)
        except Exception as e:
            logger.warning("搜索 collection '{}' 失败: {}", kb_id, e)
            continue

    all_results.sort(key=lambda r: r.distance if r.distance is not None else float("inf"))
    result = all_results[:k]
    logger.info("ChromaDB search_all: collections={} query_len={} results={}",
                len(collections_dict), len(query), len(result))
    return result


def get_chunks_by_doc_id(collection, doc_id: str) -> list[ChunkResult]:
    """查询指定文档的所有分块。"""
    try:
        results = collection.get(where={"doc_id": doc_id})
        if not results["ids"]:
            return []
        chunks = []
        for i in range(len(results["ids"])):
            chunks.append(ChunkResult(
                id=results["ids"][i],
                content=results["documents"][i] if results["documents"] else "",
                metadata=results["metadatas"][i] if results["metadatas"] else {},
            ))
        logger.info("[CHROMA] method=get_chunks_by_doc_id | doc_id={} | rows={}", doc_id, len(chunks))
        return chunks
    except Exception as e:
        logger.warning("Failed to get chunks for doc_id={}: {}", doc_id, e)
        return []


def get_chunks_paginated(collection, doc_id: str, page: int = 1, page_size: int = 50) -> ChunkQueryResult:
    """分页查询指定文档的分块。"""
    try:
        all_ids = collection.get(where={"doc_id": doc_id}, include=[])
        total = len(all_ids["ids"]) if all_ids.get("ids") else 0
        if total == 0:
            return ChunkQueryResult(items=[], total=0, page=page, page_size=page_size)

        offset = (page - 1) * page_size
        results = collection.get(
            where={"doc_id": doc_id},
            limit=page_size,
            offset=offset,
            include=["documents", "metadatas"],
        )
        items = []
        for i in range(len(results["ids"])):
            items.append(ChunkResult(
                id=results["ids"][i],
                content=results["documents"][i] if results["documents"] else "",
                metadata=results["metadatas"][i] if results["metadatas"] else {},
            ))
        logger.info("[CHROMA] method=get_chunks_paginated | doc_id={} | page={} | total={}",
                    doc_id, page, total)
        return ChunkQueryResult(items=items, total=total, page=page, page_size=page_size)
    except Exception as e:
        logger.warning("Failed to get paginated chunks for doc_id={}: {}", doc_id, e)
        return ChunkQueryResult(items=[], total=0, page=page, page_size=page_size)
```

- [ ] **Step 5: 创建 vector_store/__init__.py**（整合 VectorStore 类）

```python
"""向量存储模块 — 封装 ChromaDB 的增删查操作，返回类型化对象。

对外暴露的 VectorStore 类保持方法签名不变，仅返回类型从 list[dict] 改为
list[ChunkResult] / ChunkQueryResult。
"""

from typing import Optional
from loguru import logger
from src.config import CHROMA_COLLECTION_PREFIX
from src.infra.db.vector_store.client import ChromaClient
from src.infra.db.vector_store import store as _store
from src.infra.db.vector_store import search as _search
from src.infra.db.entities.search import ChunkResult, ChunkQueryResult
from src.parsers.base import ChunkData


class VectorStore:
    """ChromaDB 向量存储封装 — 每个知识库对应一个独立 collection。"""

    def __init__(self, persist_dir: Optional[str] = None):
        self._chroma = ChromaClient(persist_dir)

    @property
    def _embed_fn(self):
        return self._chroma._embed_fn

    def get_or_create_collection(self, kb_id: str):
        return self._chroma.get_or_create_collection(kb_id)

    def add_chunks(self, kb_id: str, chunks: list[ChunkData], doc_id: str) -> int:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _store.add_chunks(self._chroma, collection, kb_id, chunks, doc_id)

    def similarity_search(self, kb_id: str, query: str, k: int = 5) -> list[ChunkResult]:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.similarity_search(collection, self._embed_fn, kb_id, query, k)

    def similarity_search_all(self, query: str, k: int = 10) -> list[ChunkResult]:
        names = self._chroma.list_collection_names()
        if not names:
            return []
        collections = {}
        for name in names:
            kb_id = name.removeprefix(CHROMA_COLLECTION_PREFIX)
            try:
                collections[kb_id] = self._chroma.get_or_create_collection(kb_id)
            except Exception:
                continue
        return _search.similarity_search_all(collections, self._embed_fn, query, k)

    def get_chunks_by_doc_id(self, doc_id: str, kb_id: str) -> list[ChunkResult]:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.get_chunks_by_doc_id(collection, doc_id)

    def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ) -> ChunkQueryResult:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _search.get_chunks_paginated(collection, doc_id, page, page_size)

    def delete_collection(self, kb_id: str) -> bool:
        name = self._chroma._collection_name(kb_id)
        return _store.delete_collection(
            self._chroma._get_client(), name, kb_id, self._chroma._collection_cache
        )

    def delete_document(self, kb_id: str, doc_id: str) -> int:
        collection = self._chroma.get_or_create_collection(kb_id)
        return _store.delete_document(collection, doc_id)

    def list_collections(self) -> list[str]:
        return self._chroma.list_collection_names()


__all__ = ["VectorStore", "ChunkResult", "ChunkQueryResult"]
```

- [ ] **Step 6: 删除原 vector_store.py**

```bash
git rm src/infra/db/vector_store.py
```

- [ ] **Step 7: 更新所有 import 路径**

修改以下文件中 `from src.infra.db.vector_store import VectorStore` 路径不变（因为 `__init__.py` 保持了同一个导入路径）：

实际上 `__init__.py` 中保留了 `VectorStore` 类，所以 import 路径 `from src.infra.db.vector_store import VectorStore` 仍然有效。只需确认每个文件能正常导入。

检查以下文件（grep 确认 import 有效）：
- `src/services/agent_service.py`
- `src/services/app_service.py`
- `src/services/document_service.py`
- `src/rag/retrieval.py`
- `src/agents/graph/workflow.py`
- `src/api/documents.py`
- `src/cli/check_retrieval.py`
- `src/cli/eval_ragas.py`
- `src/cli/eval_ragas_generate.py`

```bash
python3 -c "from src.infra.db.vector_store import VectorStore; print('VectorStore import OK')"
python3 -c "from src.infra.db.vector_store import ChunkResult; print('ChunkResult import OK')"
```

- [ ] **Step 8: 验证**

```bash
python3 -c "
from src.infra.db.vector_store import VectorStore, ChunkResult, ChunkQueryResult
from src.infra.db.entities import ChunkResult as CR
assert ChunkResult is CR  # 同一个类
print('All imports OK')
"
```

- [ ] **Step 9: Commit**

```bash
git add src/infra/db/vector_store/
git add -u
git commit -m "refactor: split vector_store.py into vector_store/ package with typed returns"
```

---

### Task 3: BM25 + RRF fusion 返回类型化

**Files:**
- Modify: `src/infra/search/bm25_index.py`

**Interfaces:**
- Consumes: `ChunkResult`, `ChunkData` from entities/ and parsers/
- Consumes: VectorStore 已拆包
- Produces: `BM25Index.search(kb_id, query, k=150) -> list[ChunkResult]`
- Produces: `rrf_fusion(dense, bm25_res, k=60, top_n=50) -> list[ChunkResult]`
- Produces: `BM25Index.build_index(kb_id, chunks: list[ChunkData])`

- [ ] **Step 1: 修改 bm25_index.py**

```python
"""BM25 词法检索引擎 — 基于 BM25Okapi 的稀疏检索+RRF 融合函数。"""

import pickle
from pathlib import Path
from rank_bm25 import BM25Okapi
from src.infra.db.entities import ChunkResult
from src.parsers.base import ChunkData


class BM25Index:
    """基于 BM25Okapi 的词法检索引擎。"""

    def __init__(self, index_dir: str = "data/bm25_index"):
        self.index_dir = Path(index_dir)

    def build_index(self, kb_id: str, chunks: list[ChunkData]) -> None:
        kb_dir = self.index_dir / kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        corpus = [list(chunk.content) for chunk in chunks]
        bm25 = BM25Okapi(corpus)
        with open(kb_dir / "bm25.pkl", "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)

    def search(self, kb_id: str, query: str, k: int = 150) -> list[ChunkResult]:
        kb_dir = self.index_dir / kb_id
        if not (kb_dir / "bm25.pkl").exists():
            return []
        with open(kb_dir / "bm25.pkl", "rb") as f:
            data = pickle.load(f)
        bm25, chunks = data["bm25"], data["chunks"]
        tokenized = list(query)
        scores = bm25.get_scores(tokenized)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        results = []
        for idx in ranked:
            chunk = chunks[idx]
            # chunks 可能是 dict（旧格式）或 ChunkData（新格式）
            if isinstance(chunk, dict):
                results.append(ChunkResult(
                    id=chunk.get("id", chunk.get("chunk_id", "")),
                    content=chunk.get("content", ""),
                    metadata=chunk.get("metadata", {}),
                    bm25_score=float(scores[idx]),
                ))
            else:
                results.append(ChunkResult(
                    id=chunk.chunk_id,
                    content=chunk.content,
                    metadata=chunk.metadata,
                    bm25_score=float(scores[idx]),
                ))
        return results


def rrf_fusion(
    dense: list[ChunkResult], bm25_res: list[ChunkResult],
    k: int = 60, top_n: int = 50,
) -> list[ChunkResult]:
    """RRF 融合 Dense 语义检索和 BM25 词法检索结果。"""
    scores: dict[str, float] = {}
    data: dict[str, ChunkResult] = {}

    for rank, doc in enumerate(dense):
        doc_id = doc.id
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        data[doc_id] = doc

    for rank, doc in enumerate(bm25_res):
        doc_id = doc.id
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        if doc_id not in data:
            data[doc_id] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [data[doc_id] for doc_id, _ in ranked[:top_n]]
```

- [ ] **Step 2: 验证**

```bash
python3 -c "
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.infra.db.entities import ChunkResult
# 验证 rrf_fusion 类型标注
import inspect
sig = inspect.signature(rrf_fusion)
assert 'list[ChunkResult]' in str(sig.return_annotation), str(sig.return_annotation)
print('BM25 + RRF types OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/infra/search/bm25_index.py
git commit -m "refactor: BM25 search and RRF fusion return list[ChunkResult]"
```

---

### Task 4: 检索链路适配

**Files:**
- Modify: `src/rag/retrieval.py`

**Interfaces:**
- Consumes: `ChunkResult` from entities/
- Consumes: `VectorStore.similarity_search() -> list[ChunkResult]`
- Consumes: `BM25Index.search() -> list[ChunkResult]`
- Produces: `search(query, kb_id, vector_store, bm25) -> list[ChunkResult]`
- Produces: `rerank_results(query, results: list[ChunkResult], reranker) -> list[RAGContext]`

- [ ] **Step 1: 修改 retrieval.py**

```python
"""检索与查询改写 — 向量检索、Reranker 精排、查询分类与改写。"""

import asyncio
import re
from typing import Optional
from loguru import logger
from src.config import TOP_K_RETRIEVAL, TOP_K_RERANK, HYBRID_SEARCH_ENABLED
from src.infra.search.bm25_index import BM25Index, rrf_fusion
from src.infra.db.vector_store import VectorStore
from src.infra.db.entities import ChunkResult
from src.models import with_retry
from src.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_INTERVAL, RETRY_BACKOFF_FACTOR
from src.rag.context import RAGContext


async def search(
    query: str,
    kb_id: str,
    vector_store: VectorStore,
    bm25: Optional[BM25Index] = None,
) -> list[ChunkResult]:
    """执行语义检索（混合模式可选）。"""
    if HYBRID_SEARCH_ENABLED and bm25 and kb_id:
        logger.info("RAG search starting hybrid: kb_id={}", kb_id)
        dense_t = asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, TOP_K_RETRIEVAL
        )
        bm25_t = asyncio.to_thread(bm25.search, kb_id, query, TOP_K_RETRIEVAL)
        d, b = await asyncio.gather(dense_t, bm25_t)
        results = rrf_fusion(d or [], b or [])
        logger.info("RAG search: kb_id={} query_len={} results={} mode=hybrid",
                    kb_id, len(query), len(results))
        return results

    if not kb_id:
        results = await asyncio.to_thread(
            vector_store.similarity_search_all, query, k=TOP_K_RETRIEVAL
        )
    else:
        results = await asyncio.to_thread(
            vector_store.similarity_search, kb_id, query, k=TOP_K_RETRIEVAL
        )
    logger.info("RAG search: kb_id={} query_len={} results={} mode={}",
                kb_id, len(query), len(results) if results else 0,
                "search_all" if not kb_id else "dense")
    return results or []


def rerank_results(
    query: str,
    results: list[ChunkResult],
    reranker,
) -> list[RAGContext]:
    """Reranker 精排，返回 top-N 的 RAGContext 列表。"""
    if not results:
        return []

    docs = [r.content for r in results]
    try:
        reranked = with_retry(
            reranker.rerank,
            max_attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )(query, docs)
    except Exception as e:
        logger.warning("Rerank failed after {} attempts (using raw order): {}",
                       RETRY_MAX_ATTEMPTS, e)
        reranked = [
            {"index": i, "relevance_score": r.distance or 0}
            for i, r in enumerate(results)
        ]

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        idx = item["index"]
        r = results[idx]
        pc = r.metadata.get("parent_content")
        score = item.get("relevance_score", 0)
        contexts.append(RAGContext(
            content=pc if pc else r.content,
            source=r.metadata.get("source", ""),
            page=r.metadata.get("page", 0),
            doc_id=r.metadata.get("doc_id", ""),
            chunk_id=r.id,
            parent_content=pc,
            score=score,
        ))
    if contexts:
        logger.info("Rerank completed: {} -> {} contexts, top_score={:.4f}",
                    len(results), len(contexts), contexts[0].score)
    return contexts


# ═══ 以下函数不变 ═══
# classify_query, expand_query, condense_query, decompose_query, rewrite_query
# （以上函数不涉及 dict 访问，不需要修改）
```

注意：`rerank_results` 中 fallback 路径 `r.distance or 0` 处理了 ChunkResult 属性访问，BM25 结果 distance 为 None 时使用 0。

- [ ] **Step 2: 验证**

```bash
python3 -c "
from src.rag.retrieval import search, rerank_results
from src.infra.db.entities import ChunkResult
import inspect
sig = inspect.signature(search)
print('search return:', sig.return_annotation)
sig2 = inspect.signature(rerank_results)
print('rerank_results results param:', list(sig2.parameters.keys()))
print('retrieval types OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/rag/retrieval.py
git commit -m "refactor: retrieval.py uses ChunkResult types in search and rerank_results"
```

---

### Task 5: AgentState + Graph 节点适配

**Files:**
- Modify: `src/rag/context.py`
- Modify: `src/agents/graph/state.py`
- Modify: `src/agents/graph/nodes.py`

**Interfaces:**
- Consumes: `ChunkResult`, `RAGContext` from entities/ and context.py
- Produces: `AgentState.retrieval_results: List[ChunkResult]`
- Produces: `AgentState.contexts: List[RAGContext]`

- [ ] **Step 1: 修改 context.py — 启用 slots**

将 `@dataclass` 改为 `@dataclass(slots=True)`：

```python
from dataclasses import dataclass


@dataclass(slots=True)
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。"""
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0

    def to_citation(self) -> str:
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"
```

- [ ] **Step 2: 修改 state.py — 更新类型注解**

```python
# src/agents/graph/state.py
from typing import TypedDict, Optional, List
from src.infra.db.entities import ChunkResult
from src.rag.context import RAGContext


class RAGQueryIntent(TypedDict, total=False):
    route: str
    rewritten: bool


class AgentState(TypedDict, total=False):
    # ── 输入 ─────
    session_id: str
    kb_id: str
    query: str
    # ── 中间态 ───
    intent: RAGQueryIntent
    rewritten_query: Optional[str]
    retrieval_results: List[ChunkResult]      # 改为类型化的 ChunkResult
    contexts: List[RAGContext]                 # 改为直接存 RAGContext
    grader_score: Optional[float]
    retrieval_retries: int
    # ── 输出 ─────
    answer: str
    citations: List[dict]
    # ── 可观测 ───
    trace_id: str
    timings: dict
    # ── 降级控制 ─
    downgraded: bool
    downgrade_reason: str
    # ── 内部 ─────
    _history: list
    _token_usage: dict
```

- [ ] **Step 3: 修改 nodes.py — 适配属性访问**

```python
# src/agents/graph/nodes.py — 只改涉及 retrieval_results 和 contexts 的部分

# grader_node 中：
# 改前：results = state.get("retrieval_results", [])
# 改后：results = state.get("retrieval_results", [])  # 类型变为 list[ChunkResult]
# grader.grade(query, results, results)  # 内部用 r.content 访问，不需要改 signature

# rerank_node 中：
# 改前：contexts = rerank_results(query, results, reranker)  # 返回 list[RAGContext]
#        ctx_list = [{"content": c.content, ...} for c in contexts]  # 转 dict 存 state
# 改后：contexts = rerank_results(query, results, reranker)
#        return {"contexts": contexts}  # 直接存 RAGContext 列表

# format_node 中：contexts = state.get("contexts", [])
# 改前：ctx.get("source", "")
# 改后：ctx.source

def make_rerank_node(reranker):
    def rerank_node(state: AgentState) -> dict:
        query = state.get("rewritten_query") or state.get("query", "")
        results = state.get("retrieval_results", [])
        if not results:
            return {"contexts": []}
        contexts = rerank_results(query, results, reranker)
        # 直接存 RAGContext 列表，不做 dict 转换
        logger.info("[{}] rerank_node: contexts={}", _tid(state), len(contexts))
        return {"contexts": contexts}
    return rerank_node


def format_node(state: AgentState) -> dict:
    contexts = state.get("contexts", [])
    seen = set()
    citations = []
    for ctx in contexts:
        key = (ctx.source, ctx.page)
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "source": ctx.source,
            "page": ctx.page,
            "snippet": ctx.content[:200],
            "score": ctx.score,
        })
    logger.info("[{}] format_node: citations={}", _tid(state), len(citations))
    return {"citations": citations}


def make_generate_node(llm, prompt_manager, tracer):
    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.get("rewritten_query") or state.get("query", "")
        contexts = state.get("contexts", [])

        if not contexts:
            logger.info("[{}] generate_node: empty contexts, Naive RAG fallback", tid)
            prompt = build_simple_prompt(query, state.get("_history", []), prompt_manager)
        else:
            # contexts 已经是 list[RAGContext]，不需要 RAGContext(**c) 转换
            context_str = format_context(contexts)
            prompt = build_prompt(query, context_str, state.get("_history", []), prompt_manager)

        full_text = ""
        for token in stream_answer(prompt, llm, tracer, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result = {"answer": full_text, "_token_usage": usage}
        if not contexts:
            result["downgraded"] = True
            result["downgrade_reason"] = "rerank_empty"
        logger.info("[{}] generate_node done: answer_len={} tokens={}",
                    tid, len(full_text), usage.get("total", 0))
        return result
```

- [ ] **Step 4: 验证**

```bash
python3 -c "
from src.rag.context import RAGContext
# 验证 slots
try:
    c = RAGContext.__new__(RAGContext)
    c.unknown = 'test'
    assert False, 'slots should prevent this'
except AttributeError:
    print('RAGContext slots OK')
from src.agents.graph.state import AgentState
print('AgentState type annotations OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/rag/context.py src/agents/graph/state.py src/agents/graph/nodes.py
git commit -m "refactor: AgentState stores dataclass directly, remove dict conversions"
```

---

### Task 6: CLI 文件适配

**Files:**
- Modify: `src/cli/check_retrieval.py`
- Modify: `src/cli/eval_ragas_generate.py`
- Modify: `src/cli/eval_ragas.py`

**Interfaces:**
- Consumes: `VectorStore` from `vector_store/` package
- Consumes: `ChunkResult` from entities/

- [ ] **Step 1: 修改 check_retrieval.py**

```python
# 改前：r.get("content", "")[:100]
# 改后：r.content[:100]

# 查找 .get(" 和 [" 的 dict 访问模式，改为属性访问
# 主要改动：
for r in results:
    print(f"  [{i}] dist={r.distance:.4f} | {r.content[:100]}...")
```

- [ ] **Step 2: 修改 eval_ragas_generate.py**

```python
# 查找 chunks_data 的 dict 访问模式
# 改前：chunk["content"]
# 改后：chunk.content
```

- [ ] **Step 3: 修改 eval_ragas.py**

```python
# 只需更新 import 路径
# from src.infra.db.vector_store import VectorStore  # 路径不变
# 因为 vector_store/__init__.py 保持了同一导入路径
```

- [ ] **Step 4: 验证**

```bash
python3 -c "
from src.infra.db.vector_store import VectorStore
print('VectorStore import from CLI OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/cli/
git commit -m "refactor: update CLI files for typed vector_store returns"
```

---

### Task 7: MySQL Repo 拆分 — mysql_db/ 包

**Files:**
- Create: `src/infra/db/mysql_db/__init__.py`
- Create: `src/infra/db/mysql_db/pool.py`
- Create: `src/infra/db/mysql_db/kb_repo.py`
- Create: `src/infra/db/mysql_db/document_repo.py`
- Create: `src/infra/db/mysql_db/chat_repo.py`
- Create: `src/infra/db/mysql_db/user_repo.py`
- Create: `src/infra/db/mysql_db/eval_repo.py`
- Delete: `src/infra/db/mysql_db.py`

**Interfaces:**
- Consumes: All entity types from `entities/`
- Consumes: `MySQLDB` connection pool from `pool.py`
- Produces: `KbRepo`, `DocumentRepo`, `ChatRepo`, `UserRepo`, `EvalRepo`
- Produces: `MySQLDB` (thin class with pool + init_db + close)

- [ ] **Step 1: 创建 pool.py**

```python
"""MySQL 连接池管理。"""

import asyncio
import aiomysql
from loguru import logger
from src.config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
from src.config.queries import (
    CREATE_TABLE_USERS, CREATE_TABLE_KNOWLEDGE_BASE, CREATE_TABLE_DOCUMENT,
    CREATE_TABLE_CONVERSATION_HISTORY, CREATE_TABLE_SESSIONS, DROP_CONVERSATION_HISTORY_FK,
)


class MySQLDB:
    """MySQL 连接池封装 — 仅管理连接池和表初始化。"""

    def __init__(self):
        self._pool: aiomysql.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> aiomysql.Pool:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            self._pool = await aiomysql.create_pool(
                host=MYSQL_HOST, port=MYSQL_PORT,
                user=MYSQL_USER, password=MYSQL_PASSWORD,
                db=MYSQL_DATABASE, charset="utf8mb4",
                cursorclass=aiomysql.DictCursor, autocommit=True,
                minsize=2, maxsize=10, connect_timeout=10, pool_recycle=3600,
            )
            logger.info("MySQL connection pool created (minsize=2, maxsize=10)")
            return self._pool

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("MySQL connection pool closed")

    async def init_db(self) -> None:
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

- [ ] **Step 2: 创建 kb_repo.py**

```python
"""知识库 Repo — knowledge_base 表 CRUD。"""

import uuid
from typing import Optional
import aiomysql
from loguru import logger
from src.core.logging import log_sql_result
from src.config.queries import (
    INSERT_KNOWLEDGE_BASE, SELECT_KNOWLEDGE_BASE_ID_BY_NAME, SELECT_KB_NAME_BY_ID,
    SELECT_ALL_KNOWLEDGE_BASES, DELETE_KNOWLEDGE_BASE_BY_ID, SOFT_DELETE_KNOWLEDGE_BASE_BY_ID,
)
from src.infra.db.entities import KbListItem


class KbRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def get_or_create_kb(self, user_id: str, name: str, description: str = "") -> tuple[str, bool]:
        pool = await self._pool_getter()
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
                    raise RuntimeError(f"IntegrityError on '{name}' but get_kb_by_name returned None")
                return existing_id, False

    async def get_kb_by_name(self, user_id: str, name: str) -> Optional[str]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KNOWLEDGE_BASE_ID_BY_NAME, (user_id, name))
                row = await cursor.fetchone()
        return row["id"] if row else None

    async def get_kb_name_by_id(self, kb_id: str) -> Optional[str]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_KB_NAME_BY_ID, (kb_id,))
                row = await cursor.fetchone()
        return row["name"] if row else None

    async def get_all_kb(self, user_id: str = "") -> list[KbListItem]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_ALL_KNOWLEDGE_BASES, (user_id,))
                result = [
                    KbListItem(
                        id=row["id"],
                        user_id=row["user_id"],
                        name=row["name"],
                        doc_count=row["doc_count"],
                    )
                    for row in await cursor.fetchall()
                ]
            await conn.commit()
        return result

    async def delete_kb(self, kb_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_kb(self, kb_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_KNOWLEDGE_BASE_BY_ID, (kb_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0
```

- [ ] **Step 3: 创建 document_repo.py**

```python
"""文档 Repo — document 表 CRUD。"""

from typing import Optional
from loguru import logger
from src.core.logging import log_sql_result
from src.config.queries import (
    INSERT_DOCUMENT, SELECT_DOCUMENTS_BY_KB_ID, SELECT_DOC_NAMES_BY_IDS,
    UPDATE_DOCUMENT_STATUS, SOFT_DELETE_DOCUMENT_BY_ID, SOFT_DELETE_DOCUMENTS_BY_KB_ID,
)
from src.infra.db.entities import DocEntity

class DocumentRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def add_document(self, doc: DocEntity) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_DOCUMENT, (
                    doc.id, doc.kb_id, doc.user_id, doc.filename, doc.file_type,
                    doc.file_size, doc.status, doc.file_path, doc.hash,
                    doc.processing_state, doc.processing_progress, doc.processing_message,
                    doc.chunk_strategy, doc.meta_info,
                ))
            await conn.commit()

    async def get_documents(self, kb_id: str) -> list[DocEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_DOCUMENTS_BY_KB_ID, (kb_id,))
                rows = await cursor.fetchall()
        return [DocEntity(**row) for row in rows]

    async def get_doc_names(self, doc_ids: list[str]) -> dict[str, str]:
        if not doc_ids:
            return {}
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(doc_ids))
                sql = SELECT_DOC_NAMES_BY_IDS.format(placeholders)
                await cursor.execute(sql, doc_ids)
                rows = await cursor.fetchall()
        return {row["id"]: row["filename"] for row in rows}

    async def update_document_status(self, doc_id: str, **kwargs) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_DOCUMENT_STATUS, (
                    kwargs.get("status", "ready"),
                    kwargs.get("chunk_count", 0),
                    kwargs.get("error_msg", ""),
                    kwargs.get("processing_state"),
                    kwargs.get("processing_progress", 0),
                    kwargs.get("processing_message"),
                    kwargs.get("chunk_strategy"),
                    doc_id,
                ))
            await conn.commit()

    async def soft_delete_document(self, doc_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENT_BY_ID, (doc_id,))
                deleted = cursor.rowcount
            await conn.commit()
        return deleted > 0

    async def soft_delete_documents_by_kb(self, kb_id: str) -> int:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SOFT_DELETE_DOCUMENTS_BY_KB_ID, (kb_id,))
                count = cursor.rowcount
            await conn.commit()
        return count
```

- [ ] **Step 4: 创建 chat_repo.py**

```python
"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

from typing import Optional
import json
from loguru import logger
from src.config.queries import (
    INSERT_SESSION, SELECT_SESSIONS, SELECT_SESSION_BY_ID,
    SELECT_MESSAGES_BY_SESSION, INSERT_MESSAGE,
    DELETE_SESSION, DELETE_MESSAGES_BY_SESSION,
)
from src.infra.db.entities import SessionEntity, SessionListItem, MessageEntity

class ChatRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def create_session(self, session: SessionEntity) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_SESSION,
                    (session.id, session.user_id, session.title, session.kb_id))
            await conn.commit()

    async def get_sessions(self) -> list[SessionListItem]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSIONS)
                rows = await cursor.fetchall()
        return [SessionListItem(
            id=r["id"], title=r["title"], kb_id=r["kb_id"],
            kb_name=r["kb_name"], message_count=r["message_count"],
            created_at=r.get("created_at"), updated_at=r.get("updated_at"),
        ) for r in rows]

    async def get_session_by_id(self, session_id: str) -> Optional[SessionEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_SESSION_BY_ID, (session_id,))
                row = await cursor.fetchone()
        if not row:
            return None
        return SessionEntity(**row)

    async def get_messages(self, session_id: str) -> list[MessageEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_MESSAGES_BY_SESSION, (session_id,))
                rows = await cursor.fetchall()
        return [MessageEntity(**r) for r in rows]

    async def save_message(self, msg: MessageEntity) -> None:
        pool = await self._pool_getter()
        sources_json = json.dumps(msg.sources, ensure_ascii=False) if msg.sources else None
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_MESSAGE, (
                    msg.session_id, msg.kb_id, msg.role, msg.content, sources_json,
                    msg.prompt_tokens, msg.completion_tokens, msg.total_tokens, msg.model_name,
                ))
            await conn.commit()

    async def delete_session_and_messages(self, session_id: str) -> bool:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DELETE_MESSAGES_BY_SESSION, (session_id,))
                await cursor.execute(DELETE_SESSION, (session_id,))
                deleted = cursor.rowcount > 0
            await conn.commit()
        return deleted
```

- [ ] **Step 5: 创建 user_repo.py**

```python
"""用户 Repo — users 表 CRUD。"""

from typing import Optional
from src.config.queries import INSERT_USER, SELECT_USER_BY_ACCOUNT, UPDATE_USER_TOKEN, SELECT_USER_BY_TOKEN
from src.infra.db.entities import UserEntity

class UserRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def add_user(self, user_id: str, account: str, password_hash: str) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_USER, (user_id, account, password_hash))
            await conn.commit()

    async def get_user_by_account(self, account: str) -> Optional[UserEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_ACCOUNT, (account,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)

    async def update_user_token(self, user_id: str, token: str) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(UPDATE_USER_TOKEN, (token, user_id))
            await conn.commit()

    async def get_user_by_token(self, token: str) -> Optional[UserEntity]:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_USER_BY_TOKEN, (token,))
                row = await cursor.fetchone()
        if not row:
            return None
        return UserEntity(**row)
```

- [ ] **Step 6: 创建 eval_repo.py**

```python
"""评估报告 Repo — eval_report 表 CRUD。"""

import uuid
import json
from typing import Optional
from loguru import logger
from src.config.queries import CREATE_EVAL_REPORT_TABLE, INSERT_EVAL_REPORT, SELECT_LATEST_EVAL_REPORT
from src.infra.db.entities import EvalReportEntity

class EvalRepo:
    def __init__(self, mysql_db):
        self._pool_getter = mysql_db._get_pool

    async def ensure_table(self) -> None:
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(CREATE_EVAL_REPORT_TABLE)
            await conn.commit()

    async def insert_report(self, report: EvalReportEntity) -> None:
        await self.ensure_table()
        pool = await self._pool_getter()
        detail_str = json.dumps(report.detail_json, ensure_ascii=False) if report.detail_json else None
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(INSERT_EVAL_REPORT, (
                    str(uuid.uuid4()), report.kb_id, report.run_type, report.qa_count,
                    report.faithfulness, report.answer_relevancy,
                    report.context_precision, report.context_recall,
                    report.overall_score, 1 if report.passed else 0,
                    report.report_path, report.triggered_by, detail_str,
                ))
            await conn.commit()

    async def get_latest_report(self, kb_id: str) -> Optional[EvalReportEntity]:
        await self.ensure_table()
        pool = await self._pool_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(SELECT_LATEST_EVAL_REPORT, (kb_id,))
                row = await cursor.fetchone()
        if not row:
            return None
        detail = json.loads(row["detail_json"]) if row.get("detail_json") else None
        return EvalReportEntity(
            id=row["id"], kb_id=row["kb_id"], run_type=row["run_type"],
            qa_count=row["qa_count"], faithfulness=row["faithfulness"],
            answer_relevancy=row["answer_relevancy"],
            context_precision=row["context_precision"],
            context_recall=row["context_recall"],
            overall_score=row["overall_score"], passed=bool(row["passed"]),
            report_path=row["report_path"], triggered_by=row["triggered_by"],
            detail_json=detail, eval_date=row["eval_date"],
        )
```

- [ ] **Step 7: 创建 mysql_db/__init__.py**

```python
"""MySQL 包入口 — 导出 MySQLDB 和所有 Repo。"""

from src.infra.db.mysql_db.pool import MySQLDB
from src.infra.db.mysql_db.kb_repo import KbRepo
from src.infra.db.mysql_db.document_repo import DocumentRepo
from src.infra.db.mysql_db.chat_repo import ChatRepo
from src.infra.db.mysql_db.user_repo import UserRepo
from src.infra.db.mysql_db.eval_repo import EvalRepo

__all__ = ["MySQLDB", "KbRepo", "DocumentRepo", "ChatRepo", "UserRepo", "EvalRepo"]
```

- [ ] **Step 8: 删除原 mysql_db.py**

```bash
git rm src/infra/db/mysql_db.py
```

- [ ] **Step 9: 验证**

```bash
python3 -c "
from src.infra.db.mysql_db import MySQLDB, KbRepo, DocumentRepo, ChatRepo, UserRepo, EvalRepo
print('All mysql_db/ imports OK')
"
```

- [ ] **Step 10: Commit**

```bash
git add src/infra/db/mysql_db/
git commit -m "refactor: split mysql_db into mysql_db/ package with domain repos"
```

---

### Task 8: Service 层改用 Repo

**Files:**
- Modify: `src/services/kb_service.py`
- Modify: `src/services/document_service.py`
- Modify: `src/services/auth_service.py`
- Modify: `src/services/app_service.py`
- Modify: `src/chat/persistence.py`
- Modify: `src/chat/manager.py`

**Interfaces:**
- Consumes: All Repos from `mysql_db/`
- Produces: Service classes use typed Repos instead of raw MySQLDB

- [ ] **Step 1: 修改 kb_service.py**

```python
from src.infra.db.mysql_db import KbRepo

class KbService:
    def __init__(self, mysql_db=None, kb_repo=None):
        self.repo = kb_repo or KbRepo(mysql_db)

    async def list_kb(self, user_id):
        return await self.repo.get_all_kb(user_id)

    async def get_or_create(self, user_id, name, description=""):
        return await self.repo.get_or_create_kb(user_id, name, description)

    async def delete(self, kb_id, soft=True):
        if soft:
            return await self.repo.soft_delete_kb(kb_id)
        return await self.repo.delete_kb(kb_id)
```

- [ ] **Step 2: 修改 document_service.py**

```python
from src.infra.db.mysql_db import DocumentRepo

class DocumentService:
    def __init__(self, mysql_db=None, vector_store=None, doc_repo=None):
        self.repo = doc_repo or DocumentRepo(mysql_db)

    async def list_docs(self, kb_id):
        return await self.repo.get_documents(kb_id)

    async def add_document_record(self, doc):
        await self.repo.add_document(doc)

    async def update_status(self, doc_id, **kwargs):
        await self.repo.update_document_status(doc_id, **kwargs)
```

- [ ] **Step 3: 修改 auth_service.py**

```python
from src.infra.db.mysql_db import UserRepo

class AuthService:
    def __init__(self, mysql_db=None, user_repo=None):
        self.repo = user_repo or UserRepo(mysql_db)

    async def get_user_by_account(self, account):
        return await self.repo.get_user_by_account(account)

    async def get_user_by_token(self, token):
        return await self.repo.get_user_by_token(token)
```

- [ ] **Step 4: 修改 app_service.py**

```python
from src.infra.db.mysql_db import ChatRepo, EvalRepo

class AppService:
    def __init__(self, mysql_db=None, vector_store=None, chat_repo=None, eval_repo=None):
        self.chat_repo = chat_repo or ChatRepo(mysql_db)
        self.eval_repo = eval_repo or EvalRepo(mysql_db)

    async def get_sessions(self):
        return await self.chat_repo.get_sessions()

    async def get_session_by_id(self, session_id):
        return await self.chat_repo.get_session_by_id(session_id)

    async def get_messages(self, session_id):
        return await self.chat_repo.get_messages(session_id)

    async def delete_session_and_messages(self, session_id):
        return await self.chat_repo.delete_session_and_messages(session_id)

    async def get_latest_eval_report(self, kb_id):
        return await self.eval_repo.get_latest_report(kb_id)

    async def get_chunks_paginated(self, doc_id, kb_id, page=1, page_size=50):
        import asyncio
        return await asyncio.to_thread(
            self.vector_store.get_chunks_paginated, doc_id, kb_id, page, page_size
        )
```

- [ ] **Step 5: 修改 persistence.py**

```python
from src.infra.db.mysql_db import ChatRepo
from src.infra.db.entities import SessionEntity, MessageEntity

class PersistenceService:
    def __init__(self, chat_repo: ChatRepo):
        self._chat_repo = chat_repo

    async def save_session(self, session_id, title, kb_id, user_id=""):
        await self._chat_repo.create_session(
            SessionEntity(id=session_id, title=title, kb_id=kb_id, user_id=user_id)
        )

    async def save_message(self, session_id, kb_id, role, content, sources=None,
                          prompt_tokens=0, completion_tokens=0, total_tokens=0, model_name=""):
        await self._chat_repo.save_message(
            MessageEntity(
                session_id=session_id, kb_id=kb_id, role=role, content=content,
                sources=sources, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
                model_name=model_name,
            )
        )
```

- [ ] **Step 6: 修改 manager.py**

```python
from src.infra.db.mysql_db import ChatRepo

class ChatManager:
    def set_chat_repo(self, chat_repo: ChatRepo) -> None:
        self._persistence = PersistenceService(chat_repo)
```

- [ ] **Step 7: 验证**

```bash
# 验证 import
python3 -c "
from src.services.kb_service import KbService
from src.services.document_service import DocumentService
from src.services.auth_service import AuthService
from src.services.app_service import AppService
print('Service imports OK')
"
```

- [ ] **Step 8: Commit**

```bash
git add src/services/ src/chat/
git commit -m "refactor: services use typed Repos instead of raw MySQLDB"
```

---

### Task 9: API 层适配

**Files:**
- Modify: `src/api/documents.py`

**Interfaces:**
- Consumes: `AppService.get_chunks_paginated()` 
- Consumes: `ChunkResult` (属性访问)

- [ ] **Step 1: 修改 api/documents.py — 走 service + 属性访问**

```python
# 改前：svc.vector_store.get_chunks_paginated(...)
#        result["items"], c["id"], c.get("metadata", {})
# 改后：await svc.get_chunks_paginated(...)  # 通过 AppService
#        result.items, c.id, c.metadata

# 具体改动：
# 1. import 中移除 vector_store 的引用（如果单独导入的话）
# 2. get_document_chunks 函数内：
result = await svc.get_chunks_paginated(body.doc_id, body.kb_id, body.page, body.page_size)
items = [
    ChunkItem(
        chunk_id=c.id,
        content=c.content[: svc.settings.MAX_TABLE_TOKENS * 2],
        page=c.metadata.get("page", 1),
        tokens=c.metadata.get("tokens", 0),
        char_count=len(c.content),
        block_type=c.metadata.get("block_type", "text"),
        parent_content=c.metadata.get("parent_content"),
    )
    for c in result.items
]
return ChunksResponse(
    items=items,
    total=result.total,
    page=result.page,
    page_size=result.page_size,
    parent_map=parent_map,
)
```

- [ ] **Step 2: 在 app_service.py 中添加 get_chunks_paginated 方法**

```python
# 在 AppService 类中：
async def get_chunks_paginated(self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50):
    return await asyncio.to_thread(
        self.vector_store.get_chunks_paginated, doc_id, kb_id, page, page_size
    )
```

- [ ] **Step 3: 验证**

```bash
python3 -c "
from src.api.documents import router
print('API documents import OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/api/documents.py src/services/app_service.py
git commit -m "refactor: api/documents.py goes through service, uses typed ChunkResult"
```

---

### Task 10: 测试适配 + 收尾验证

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/infra/db/test_mysql_db.py`
- Modify: `tests/services/test_app_service.py`
- Modify: `tests/reset_data.py`

- [ ] **Step 1: 更新 conftest.py 中的 mysql_db fixture**

```python
# 更新 import 路径：
from src.infra.db.mysql_db import MySQLDB
# 路径不变（mysql_db/__init__.py 导出了 MySQLDB）
```

- [ ] **Step 2: 更新 test_mysql_db.py**

```python
# 将 MySQLDB import 改为：
from src.infra.db.mysql_db import MySQLDB
# assert 语句改为属性比较，例如：
# 改前：assert row["id"] == doc_id
# 改后：assert doc.id == doc_id
```

- [ ] **Step 3: 更新 test_app_service.py 的 @patch 路径**

```python
# @patch("src.services.app_service.MySQLDB")  # 路径不变
```

- [ ] **Step 4: 更新 reset_data.py**

```python
# 使用 Repo 替代 MySQLDB 直接 SQL
```

- [ ] **Step 5: 全量验证**

```bash
pytest tests/ -v 2>&1 | tail -100
```

- [ ] **Step 6: lint**

```bash
ruff check . --fix && ruff format .
```

- [ ] **Step 7: 检查遗留问题**

```bash
# 确认无 print() TODO
grep -rn "print(" src/infra/db/ --include="*.py" | grep -v "logger\|#"
grep -rn "TODO\|FIXME" src/ --include="*.py"
```

- [ ] **Step 8: 最终验证**

```bash
pytest tests/ -v && ruff check .
```

- [ ] **Step 9: Commit**

```bash
git add tests/
git commit -m "test: update tests for typed entities and repos"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|---|---|
| 检索结果统一类型（ChunkResult） | Task 1.2 + Task 2.5 |
| BM25 search 返回 ChunkResult | Task 3.1 |
| RRF fusion 输入/输出 ChunkResult | Task 3.2 |
| rerank_results 输入 ChunkResult | Task 4.2 |
| similarity_search_all 返回 ChunkResult | Task 2.5（自动） |
| get_chunks_by_doc_id 返回 ChunkResult | Task 2.5 |
| get_chunks_paginated 返回 ChunkQueryResult | Task 2.5 |
| RAGContext 启用 slots | Task 5.1 |
| MySQL 实体类型（KbListItem/DocEntity/SessionEntity/MessageEntity/UserEntity/EvalReportEntity） | Task 1 |
| AgentState 存放 dataclass | Task 5.2 + Task 5.3 |
| vector_store.py 拆为模块包 | Task 2 |
| mysql_db.py 拆为 Repo | Task 7 |
| api/documents.py 走 service | Task 9 |
| ChatManager 改用 ChatRepo | Task 8.5 + Task 8.6 |
