# Iter 4 — RAG 问答链路 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现检索→重排序→Prompt→流式生成→引用的完整 RAG 问答链路，端到端可问答，并支持 Redis 降级容错。

**Architecture:** `ChatManager` 封装 Redis 对话缓存（InMemory 降级），`RAGChain` 编排完整链路：MySQL 解析 kb_name → VectorStore 语义检索 → DashScope Rerank 重排序 → 金融约束 Prompt → Qwen-max 流式生成 → 引用提取。

**Tech Stack:** Python 3.11, LangChain, DashScope API (qwen-max, gte-rerank-v2), Redis 7, loguru

**关联 Spec:** [rag-generation](/openspec/changes/financial-qa-mvp/specs/rag-generation/spec.md)

---

## 文件结构

```
src/
├── chat_manager.py   (新建) Redis 对话缓存 + InMemory 降级
├── rag_chain.py      (新建) RAG 问答链核心
├── models.py         (已有, 不动)
├── vector_store.py   (已有, 不动)
├── mysql_db.py       (已有, 不动)
├── config.py         (已有, 不动)
└── ...

tests/
├── test_chat_manager.py  (新建)
├── test_rag_chain.py     (新建)
└── ...
```

---

### Task 1: Chat Manager（chat_manager.py）

**Files:**
- Create: `src/chat_manager.py`
- Create: `tests/test_chat_manager.py`

对话会话管理器，负责：
- Redis 缓存对话历史（每个 session_id 一个 list，TTL 7 天）
- Redis 不可用时自动降级到 InMemory dict（`logger.warning` 记录）
- 按 `MEMORY_WINDOW` 截取最近 N 轮对话

- [ ] **Step 1: 写测试 `tests/test_chat_manager.py`**

```python
"""Tests for ChatManager with Redis and InMemory fallback."""
import uuid
import pytest
from src.chat_manager import ChatManager
from src.config import MEMORY_WINDOW


class TestChatManagerInMemory:
    """Test ChatManager with Redis unavailable (InMemory fallback)."""

    @pytest.fixture
    def cm(self):
        """Create ChatManager with fake Redis URL to force InMemory fallback."""
        return ChatManager(redis_url="redis://localhost:16379/0")  # nonexistent port

    def test_init_fallback_to_inmemory(self, cm):
        """Should fall back to InMemory when Redis is unreachable."""
        assert cm._in_memory is True

    def test_add_and_get_history(self, cm):
        session_id = "test_session_1"
        cm.add_message(session_id, "user", "你好")
        cm.add_message(session_id, "assistant", "你好！有什么可以帮助你的？")

        history = cm.get_history(session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "你好"
        assert history[1]["role"] == "assistant"

    def test_get_window_limits(self, cm):
        """Should only return the last N messages."""
        session_id = "test_window"
        for i in range(10):
            cm.add_message(session_id, "user", f"msg_{i}")

        window = cm.get_window(session_id, window_size=3)
        assert len(window) == 3
        assert window[-1]["content"] == "msg_9"

    def test_clear_history(self, cm):
        session_id = "test_clear"
        cm.add_message(session_id, "user", "hello")
        cm.clear_history(session_id)
        assert cm.get_history(session_id) == []

    def test_get_window_default_memory_window(self, cm):
        """Should use MEMORY_WINDOW from config when not specified."""
        session_id = "test_default_window"
        for i in range(MEMORY_WINDOW + 5):
            cm.add_message(session_id, "user", f"msg_{i}")

        window = cm.get_window(session_id)  # no explicit window_size
        assert len(window) <= MEMORY_WINDOW

    def test_get_history_empty_session(self, cm):
        assert cm.get_history("nonexistent_session") == []

    def test_inmemory_store_isolation(self, cm):
        """Two sessions should not interfere."""
        cm.add_message("session_a", "user", "from_a")
        cm.add_message("session_b", "user", "from_b")
        hist_a = cm.get_history("session_a")
        hist_b = cm.get_history("session_b")
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["content"] == "from_a"
        assert hist_b[0]["content"] == "from_b"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_chat_manager.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（chat_manager.py 不存在）
```

- [ ] **Step 3: 实现 `src/chat_manager.py`**

```python
"""Chat session manager with Redis backing and InMemory fallback."""
import json
from typing import Optional

from loguru import logger

from src.config import MEMORY_WINDOW, REDIS_URL, REDIS_TTL


class ChatManager:
    """Manages conversation history with Redis, falling back to in-memory.

    On construction, attempts to connect to Redis. If Redis is unreachable,
    falls back silently to InMemory dict storage (per session_id).
    """

    def __init__(self, redis_url: Optional[str] = None, ttl: int = REDIS_TTL):
        self.ttl = ttl
        self._redis = None
        self._in_memory: bool = False
        self._memory_store: dict[str, list[dict]] = {}
        self._init_redis(redis_url or REDIS_URL)

    def _init_redis(self, redis_url: str) -> None:
        """Try connecting to Redis; fall back to InMemory on failure."""
        try:
            import redis

            self._redis = redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            logger.info("ChatManager: Redis connected at {}", redis_url)
        except Exception as e:
            self._redis = None
            self._in_memory = True
            logger.warning(
                "ChatManager: Redis unavailable ({}), using InMemory fallback", e,
            )

    def _session_key(self, session_id: str) -> str:
        return f"chat_history:{session_id}"

    def get_history(self, session_id: str) -> list[dict]:
        """Get all messages for a session."""
        if self._in_memory:
            return list(self._memory_store.get(session_id, []))

        key = self._session_key(session_id)
        try:
            raw = self._redis.lrange(key, 0, -1)
            return [json.loads(m) for m in raw]
        except Exception as e:
            logger.error("ChatManager: Redis get_history failed: {}", e)
            return []

    def add_message(
        self, session_id: str, role: str, content: str, sources: Optional[list] = None,
    ) -> None:
        """Add a message to the session history."""
        msg = {"role": role, "content": content}
        if sources:
            msg["sources"] = sources

        if self._in_memory:
            if session_id not in self._memory_store:
                self._memory_store[session_id] = []
            self._memory_store[session_id].append(msg)
            return

        key = self._session_key(session_id)
        try:
            self._redis.rpush(key, json.dumps(msg, ensure_ascii=False))
            self._redis.expire(key, self.ttl)
        except Exception as e:
            logger.error("ChatManager: Redis add_message failed: {}", e)

    def get_window(self, session_id: str, window_size: int = MEMORY_WINDOW) -> list[dict]:
        """Get the last `window_size` messages from the session."""
        history = self.get_history(session_id)
        return history[-window_size:] if len(history) > window_size else history

    def clear_history(self, session_id: str) -> None:
        """Clear all messages for a session."""
        if self._in_memory:
            self._memory_store.pop(session_id, None)
            return

        key = self._session_key(session_id)
        try:
            self._redis.delete(key)
        except Exception as e:
            logger.error("ChatManager: Redis clear_history failed: {}", e)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_chat_manager.py -v
# 预期: 7 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/chat_manager.py tests/test_chat_manager.py
git commit -m "feat: add ChatManager with Redis backing and InMemory fallback"
```

---

### Task 2: RAG Chain（rag_chain.py）

**Files:**
- Create: `src/rag_chain.py`
- Create: `tests/test_rag_chain.py`

RAGChain 核心逻辑：
1. `kb_name` → MySQL 查 `kb_id`
2. 用户查询 → Embedding → VectorStore 检索 top_k=8
3. 检索结果 → gte-rerank-v2 重排序 → top_n=5
4. 重排序结果 + 对话历史 → 金融约束 Prompt
5. Qwen-max 流式生成
6. 从检索结果提取引用信息

**系统提示词（金融约束）：**
```
你是一个金融文档问答助手。请严格遵循以下规则：
1. 仅根据提供的文档内容回答，不要计算文档中没有直接给出的比率或汇总数据
2. 回答中必须标注数据对应的年份/报告期
3. 如果文档中找不到相关信息，明确说明"未在文档中找到相关数据"
4. 回答语言与用户提问语言一致
```

- [ ] **Step 1: 写测试 `tests/test_rag_chain.py`**

```python
"""Tests for RAGChain."""
from unittest.mock import MagicMock, patch
import pytest
from src.rag_chain import RAGChain, RAGContext


class TestRAGContext:
    """RAGContext dataclass tests."""

    def test_create_context(self):
        ctx = RAGContext(
            content="test content",
            source="年报2023.pdf",
            page=5,
            doc_id="doc123",
            chunk_id="doc123:0",
            score=0.95,
        )
        assert ctx.content == "test content"
        assert ctx.source == "年报2023.pdf"
        assert ctx.page == 5

    def test_to_citation(self):
        ctx = RAGContext(
            content="贵州茅台2024年营收1,741亿元",
            source="年报2024.pdf",
            page=3,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.9,
        )
        citation = ctx.to_citation()
        assert "年报2024.pdf" in citation
        assert "第3页" in citation or "page 3" in citation.lower()


class TestRAGChainInit:
    """RAGChain initialization tests."""

    @patch("src.rag_chain.get_rerank")
    @patch("src.rag_chain.get_llm")
    @patch("src.rag_chain.get_embeddings")
    def test_init_defaults(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """Should create with default factory calls."""
        chain = RAGChain()
        assert chain.llm is not None
        assert chain.embeddings is not None
        assert chain.reranker is not None
        assert chain.vector_store is not None
        assert chain.db is not None
        assert chain.chat_manager is not None

    @patch("src.rag_chain.get_rerank")
    @patch("src.rag_chain.get_llm")
    @patch("src.rag_chain.get_embeddings")
    def test_init_custom_deps(self, mock_get_emb, mock_get_llm, mock_get_rerank):
        """Should accept injected dependencies."""
        vs = MagicMock()
        db = MagicMock()
        cm = MagicMock()

        chain = RAGChain(
            vector_store=vs,
            mysql_db=db,
            chat_manager=cm,
        )
        assert chain.vector_store is vs
        assert chain.db is db
        assert chain.chat_manager is cm
        # Should not call the factory functions
        mock_get_emb.assert_not_called()
        mock_get_llm.assert_not_called()
        mock_get_rerank.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_rag_chain.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（rag_chain.py 不存在）
```

- [ ] **Step 3: 实现 `src/rag_chain.py`**

```python
"""RAG answer generation chain with retrieval, rerank, streaming, and citations."""
from dataclasses import dataclass, field
from typing import Generator, Optional

from loguru import logger
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import TOP_K_RETRIEVAL, TOP_K_RERANK, LLM_TEMPERATURE
from src.models import get_embeddings, get_llm, get_rerank
from src.vector_store import VectorStore
from src.mysql_db import MySQLDB
from src.chat_manager import ChatManager


FINANCIAL_SYSTEM_PROMPT = """你是一个金融文档问答助手。请严格遵循以下规则：

1. 仅根据提供的文档内容回答，不要计算文档中没有直接给出的比率或汇总数据
2. 回答中必须标注数据对应的年份/报告期
3. 如果文档中找不到相关信息，明确说明"未在文档中找到相关数据"
4. 回答语言与用户提问语言一致"""


@dataclass
class RAGContext:
    """A single retrieved context chunk with metadata."""
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    score: float = 0.0

    def to_citation(self) -> str:
        """Format as markdown citation block."""
        snippet = self.content[:200].replace("\n", " ")
        return (
            f"> **来源:** {self.source} (第{self.page}页)\n"
            f"> {snippet}\n"
        )


class RAGChain:
    """RAG answer generation chain.

    Orchestrates the full pipeline:
    kb_name → kb_id → retrieve → rerank → prompt → stream → citations
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        mysql_db: Optional[MySQLDB] = None,
        chat_manager: Optional[ChatManager] = None,
        llm=None,
        embeddings=None,
        reranker=None,
    ):
        self.vector_store = vector_store or VectorStore()
        self.db = mysql_db or MySQLDB()
        self.chat_manager = chat_manager or ChatManager()
        self.llm = llm or get_llm()
        self.embeddings = embeddings or get_embeddings()
        self.reranker = reranker or get_rerank()

    def chat_with_citations(
        self, kb_name: str, session_id: str, query: str,
    ) -> tuple[Generator[str, None, None], list[RAGContext]]:
        """Generate a streaming answer with citations.

        Returns:
            Tuple of (token_generator, citations_list).
        """
        # Step 1: Resolve knowledge base
        kb_id = self.db.get_kb_by_name(kb_name)
        if not kb_id:
            logger.warning("Knowledge base '{}' not found", kb_name)
            citations: list[RAGContext] = []

            def _not_found_gen():
                yield f"知识库 '{kb_name}' 不存在，请先创建知识库并上传文档。"

            return _not_found_gen(), citations

        # Step 2: Retrieve relevant chunks
        try:
            results = self.vector_store.similarity_search(
                kb_id, query, k=TOP_K_RETRIEVAL,
            )
        except Exception as e:
            logger.error("Vector search failed for kb={}: {}", kb_name, e)
            citations = []

            def _search_err_gen():
                yield f"检索失败: {e}"

            return _search_err_gen(), citations

        if not results:
            logger.info("No results found for query in kb='{}'", kb_name)
            citations = []

            def _no_result_gen():
                yield "未在文档中找到相关数据。"

            return _no_result_gen(), citations

        # Step 3: Rerank results
        rag_contexts = self._rerank_results(query, results)

        # Step 4: Build context and prompt
        context_str = self._format_context(rag_contexts)
        history = self.chat_manager.get_window(session_id)
        prompt = self._build_prompt(query, context_str, history)

        # Step 5: Generate streaming answer
        token_generator = self._stream_answer(prompt)

        # Save user query to chat history
        self.chat_manager.add_message(session_id, "user", query)

        return token_generator, rag_contexts

    def _rerank_results(self, query: str, results: list[dict]) -> list[RAGContext]:
        """Apply reranker to retrieved results, return top N contexts."""
        if not results:
            return []

        docs = [r["content"] for r in results]
        try:
            reranked = self.reranker.rerank(
                query, docs,
            )
        except Exception as e:
            logger.warning("Rerank failed (using raw order): {}", e)
            # Fallback: use raw order with distance scores
            reranked = [
                {"index": i, "relevance_score": r.get("distance", 0)}
                for i, r in enumerate(results)
            ]

        contexts = []
        for item in reranked[:TOP_K_RERANK]:
            idx = item["index"]
            r = results[idx]
            score = item.get("relevance_score", 0)
            contexts.append(RAGContext(
                content=r["content"],
                source=r["metadata"].get("source", ""),
                page=r["metadata"].get("page", 0),
                doc_id=r["metadata"].get("doc_id", ""),
                chunk_id=r["id"],
                score=score,
            ))
        return contexts

    @staticmethod
    def _format_context(contexts: list[RAGContext]) -> str:
        """Format retrieved contexts into a single prompt block."""
        blocks = []
        for i, ctx in enumerate(contexts):
            blocks.append(
                f"[{i + 1}] 来源: {ctx.source} (第{ctx.page}页)\n"
                f"内容: {ctx.content}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _build_prompt(
        query: str, context: str, history: list[dict],
    ) -> list[dict]:
        """Build the full message list (system + history + user)."""
        messages = [SystemMessage(content=FINANCIAL_SYSTEM_PROMPT)]

        # Add conversation history (last N turns as alternating user/assistant)
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                from langchain_core.messages import AIMessage
                messages.append(AIMessage(content=msg["content"]))

        # Build the user message with context
        user_content = f"""请根据以下文档内容回答问题。

【参考文档】
{context}

【问题】
{query}

请基于以上文档内容回答。如果文档中没有相关信息，请说明"未在文档中找到相关数据"。
"""
        messages.append(HumanMessage(content=user_content))
        return messages

    def _stream_answer(self, messages: list) -> Generator[str, None, None]:
        """Stream LLM response token by token."""
        try:
            stream = self.llm.stream(messages)
            for chunk in stream:
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    yield content
        except Exception as e:
            logger.error("LLM streaming failed: {}", e)
            yield f"生成回答失败: {e}"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_rag_chain.py -v
# 预期: 4 passed（基础测试，不需要 API Key）
```

- [ ] **Step 5: Commit**

```bash
git add src/rag_chain.py tests/test_rag_chain.py
git commit -m "feat: add RAGChain with retrieval, rerank, streaming, and citations"
```

---

### Task 3: 集成验证

**Files:** （无创建，仅运行命令）

- [ ] **Step 1: 确认 Docker 容器健康运行**

```bash
docker compose ps
# 预期: app (up), mysql (healthy), redis (healthy)
```

- [ ] **Step 2: 运行所有测试**

```bash
docker compose exec app python -m pytest tests/ -v
# 预期: 全部通过（含原有的 36+ 和新增的 chat_manager + rag_chain 测试）
```

- [ ] **Step 3: 准备测试环境——创建知识库并入库文档**

```bash
docker compose exec app python -c "
from src.mysql_db import MySQLDB
from src.vector_store import VectorStore
from src.parsers.router import DocRouter
import uuid

# 创建知识库
db = MySQLDB()
kb_name = f'rag_test_{uuid.uuid4().hex[:6]}'
kid, is_new = db.get_or_create_kb(kb_name)
print(f'KB: {kb_name} ({kid})')

# 解析 sample.txt
router = DocRouter()
result = router.parse('test_docs/sample.txt')
print(f'Parsed: {len(result.chunks)} chunks')

# 入库
doc_id = db.add_document(kid, 'sample.txt', 'txt', 1167)
vs = VectorStore()
count = vs.add_chunks(kid, result.chunks, doc_id)
db.update_document_status(doc_id, 'ready', chunk_count=count)
print(f'Vectorized: {count} chunks')
db.close()
print(f'KB_NAME={kb_name}')
"
```

- [ ] **Step 4: 验证端到端 RAG 问答**

```bash
# 用上一步输出的 KB_NAME 替换
docker compose exec app python -c "
from src.rag_chain import RAGChain
rc = RAGChain()
answer_gen, citations = rc.chat_with_citations(
    '<KB_NAME>', 'test_session_001', '贵州茅台2024年营业收入是多少?'
)
full = ''.join([t for t in answer_gen])
print(f'Answer: {full}')
print(f'Citations: {len(citations)}')
for c in citations:
    print(f'  - {c.source} (p{c.page}): {c.content[:80]}...')
"
# 预期: 生成包含营收数据的回答 + 1条以上引用
```

- [ ] **Step 5: 验证 Redis 降级**

```bash
# 停止 Redis
docker stop financial-qa-redis-1

# 问答应仍可工作（InMemory 降级）
docker compose exec app python -c "
from src.rag_chain import RAGChain
rc = RAGChain()
answer_gen, citations = rc.chat_with_citations(
    '<KB_NAME>', 'test_session_002', '贵州茅台主营业务是什么?'
)
full = ''.join([t for t in answer_gen])
print(f'Answer (degraded): {full}')
"
# 预期: 仍有回答输出，日志显示 InMemory 降级警告

# 重启 Redis
docker start financial-qa-redis-1
```

- [ ] **Step 6: Iter 4 完成——提交代码**

```bash
git add src/rag_chain.py src/chat_manager.py tests/test_rag_chain.py tests/test_chat_manager.py
git status  # 确认只有 Iter 4 相关文件
git commit -m "feat: complete Iter 4 RAG Q&A chain

- Add ChatManager with Redis backing and InMemory fallback
- Add RAGChain orchestrating retrieval→rerank→prompt→stream→citations
- Add financial-specific system prompt with constraints
- Add gte-rerank-v2 reranking for improved precision
- Add comprehensive test suite for both modules"
```

---

### 执行记录与计划差异（2026-06-16）

实际执行中与原始计划的差异：

| # | 差异点 | 计划 | 实际 | 原因 |
|---|--------|------|------|------|
| 1 | **Rerank 降级触发** | 预期正常 rerank | DashScopeRerank.rerank() 返回 None，3次重试后退回原始排序 | gte-rerank-v2 在 langchain-community 的 DashScopeRerank 接口存在问题（返回值结构不匹配） |
| 2 | **测试用 KB 名称** | rag_test_<UUID> | rag_test_4672d3（自动生成） | 集成测试时动态创建避免冲突 |
| 3 | **AIMessage 导入位置** | 计划在 `_build_prompt` 方法内导入 | 改为模块级导入 `from langchain_core.messages import AIMessage` | 代码质量审查要求 PEP 8 合规，避免重复 import 的性能开销 |
| 4 | **`__init__` 返回注解** | 未明确要求 | 增加 `-> None` 返回类型注解 | 代码质量审查发现不符合"函数强制类型注解"规则 |
| 5 | **工厂函数懒加载** | 计划方案中 LLM/Embedding/Rerank 在 `__init__` 中直接调用工厂 | 改用 property 懒加载，仅首次访问时才调用工厂函数 | 保证 `test_init_custom_deps` 测试中注入依赖时不会触发不必要的工厂调用 |
| 6 | **LLM stream 重试** | 未在计划中具体实现 | `_stream_answer` 添加 3 次指数退避重试（1s, 2s interval） | Spec review 发现缺少"API 调用重试"的 SHALL 要求 |
| 7 | **Rerank 重试** | 计划中无 rerank 重试 | `_rerank_results` 添加 3 次指数退避重试 | Spec review 发现缺少"API 调用重试"的 SHALL 要求 |
| 8 | **测试数量** | 计划预期：基础测试（无 API Key） | 实际实现：18 个测试（含完整 mock 的 pipeline 测试） | 子代理实现了更多边缘用例覆盖 |
| 9 | **git commit 未执行** | 计划含 git commit 步骤 | 未执行 git commit | 项目规范要求手动审核后提交 |
