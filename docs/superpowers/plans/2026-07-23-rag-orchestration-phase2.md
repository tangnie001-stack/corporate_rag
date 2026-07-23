# RAG Orchestration Upgrade Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the imperative RAG pipeline orchestration (chain.py if/else + api/chat.py manual stages) with a LangGraph StateGraph featuring three-tier routing, rule-based quality grading, and graceful degradation.

**Architecture:** New files under `src/agents/graph/` define the LangGraph state, nodes, and workflow. `src/services/agent_service.py` manages the graph lifecycle and SSE event conversion. The existing `rag/` code (retrieval.py, stream.py, prompt.py) remains unchanged as the implementation layer called by graph nodes. A new `src/tools/` directory provides the tool abstraction framework shell.

**Tech Stack:** Python 3.11+, LangGraph 1.2.9, LangChain 1.3.11, FastAPI 0.138, Loguru

## Global Constraints

- All new node functions MUST include `[trace_id] nodename action: detail` format INFO logs at entry and exit
- RAG core capabilities (ChromaDB hybrid search, BM25, DashScopeRerank, query rewriting) remain in `rag/` unchanged
- `pyproject.toml` dependency pinning: `"langgraph==1.2.9"`
- `src/rag/chain.py` stays unchanged (only used by eval_ragas.py)
- `src/services/chat_service.py` stays unchanged
- No Checkpointer (MemorySaver) — Phase 3
- All existing tests must continue to pass
- Code style: `ruff check .` zero errors, no TODO/print debug

---

## File Structure

```
src/agents/                        # NEW — Agent definitions
├── __init__.py                    # Empty
├── graph/                         # LangGraph graph definition
│   ├── __init__.py                # Empty
│   ├── state.py                   # AgentState TypedDict
│   ├── workflow.py                # StateGraph assembly + compile
│   └── nodes.py                   # 7 node functions
└── grader.py                      # RetrievalGrader (rule-based)

src/tools/                         # NEW — Tool framework shell
├── __init__.py                    # Empty
└── base.py                        # ToolBase abstract class (empty)

src/services/
├── agent_service.py               # NEW — Graph lifecycle manager
└── app_service.py                 # MODIFY — Mount agent_service

src/api/
└── chat.py                        # MODIFY ��� Call agent_service

tests/
├── api/
│   └── test_chat.py               # MODIFY — Mock agent_service
└── agents/
    └── graph/
        ├── test_graph.py          # NEW — Node unit tests
        └── test_grader.py         # NEW — Grader tests
```

---

### Task 1: Add langgraph dependency and create directory structure

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agents/__init__.py`
- Create: `src/agents/graph/__init__.py`
- Create: `src/agents/grader.py`
- Create: `src/tools/__init__.py`
- Create: `src/tools/base.py`

**Interfaces:**
- Consumes: nothing
- Produces: directory structure used by Task 2

- [ ] **Step 1: Add langgraph dependency**

Open `pyproject.toml` and add `"langgraph==1.2.9",` after the existing langchain dependencies block (around line 20).

Verify with:

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag
pip install langgraph==1.2.9
pip show langgraph
```

Expected output: `Version: 1.2.9`

- [ ] **Step 2: Create empty `src/agents/` directory structure**

```bash
mkdir -p src/agents/graph
touch src/agents/__init__.py
touch src/agents/graph/__init__.py
```

- [ ] **Step 3: Create empty `src/agents/grader.py`**

```python
# src/agents/grader.py
"""检索质量评分器（规则版，Phase 2 实现关键词覆盖度评分）。"""
```

- [ ] **Step 4: Create `src/tools/` directory structure**

```bash
mkdir -p src/tools
touch src/tools/__init__.py
touch src/tools/base.py
```

- [ ] **Step 5: Create empty `src/tools/base.py`**

```python
# src/tools/base.py
"""工具抽象基类（预留，Phase 3 填充）。"""
```

- [ ] **Step 6: Verify existing tests still pass**

```bash
pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/agents/ src/tools/
git commit -m "chore: add langgraph dependency and scaffold agents/tools directories"
```

---

### Task 2: Implement AgentState

**Files:**
- Create: `src/agents/graph/state.py`

**Interfaces:**
- Produces: `AgentState` TypedDict — the central state container used by all 7 node functions in Task 3

- [ ] **Step 1: Write the test file**

Create `tests/agents/graph/test_state.py`:

```python
"""Tests for AgentState definition."""
from src.agents.graph.state import AgentState


def test_agent_state_defaults():
    """AgentState SHALL allow partial initialization (total=False)."""
    state: AgentState = {"query": "2024年营收多少", "kb_id": "kb-1"}
    assert state["query"] == "2024年营收多少"
    assert state["kb_id"] == "kb-1"


def test_agent_state_with_contexts():
    """AgentState SHALL hold RAGContextItem list."""
    from src.rag.context import RAGContext

    ctx = RAGContext(content="净利润100亿", source="财报.pdf", page=5,
                     doc_id="doc-1", chunk_id="chunk-1", score=0.95)
    state: AgentState = {
        "query": "净利润多少",
        "contexts": [{"content": ctx.content, "source": ctx.source,
                      "page": ctx.page, "doc_id": ctx.doc_id,
                      "chunk_id": ctx.chunk_id, "score": ctx.score}],
    }
    assert len(state["contexts"]) == 1
    assert state["contexts"][0]["content"] == "净利润100亿"


def test_agent_state_downgrade_fields():
    """AgentState SHALL support downgrade tracking."""
    state: AgentState = {"query": "test", "downgraded": True,
                         "downgrade_reason": "rerank_empty"}
    assert state["downgraded"] is True
    assert state["downgrade_reason"] == "rerank_empty"


def test_agent_state_trace_id():
    """AgentState SHALL carry trace_id."""
    state: AgentState = {"query": "test", "trace_id": "trace_abc123"}
    assert state["trace_id"] == "trace_abc123"
```

- [ ] **Step 2: Create `src/agents/graph/state.py`**

```python
# src/agents/graph/state.py
"""AgentState — LangGraph 图状态定义。

包含 RAG 流水线完整状态（输入/中间态/输出）和图控制字段。
"""

from typing import TypedDict, Optional, List


class RAGQueryIntent(TypedDict, total=False):
    """查询意图分类结果。"""
    route: str  # "simple" | "medium" | "complex"
    rewritten: bool


class RAGContextItem(TypedDict, total=False):
    """检索结果上下文项（对应 rag/context.py 中的 RAGContext）。"""
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    score: float


class AgentState(TypedDict, total=False):
    """LangGraph 图执行状态。

    既是图的输入/输出容器，也是节点间传递的共享状态。
    """
    # ── 输入 ─────────────────────────────────────
    session_id: str
    kb_id: str
    query: str

    # ── 中间态 ───────────────────────────────────
    intent: RAGQueryIntent                    # classify 节点输出
    rewritten_query: Optional[str]            # rewrite 节点输出
    retrieval_results: List[dict]             # retrieve 节点输出（原始检索结果）
    contexts: List[RAGContextItem]            # rerank 节点输出（精排后上下文）
    grader_score: Optional[float]             # grader 节点输出
    retrieval_retries: int                    # 重检次数（防死循环）

    # ── 输出 ─────────────────────────────────────
    answer: str
    citations: List[dict]

    # ── 可观测 ──────────────────────────────────
    trace_id: str
    timings: dict                             # 各阶段耗时

    # ── 降级控制 ────────────────────────────────
    downgraded: bool
    downgrade_reason: str
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/agents/graph/test_state.py -v
```

Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add src/agents/graph/state.py tests/agents/graph/test_state.py
git commit -m "feat: add AgentState TypedDict for LangGraph state management"
```

---

### Task 3: Update classify_query() and rewrite_query() in rag/retrieval.py

**Files:**
- Modify: `src/rag/retrieval.py:118-174`

**Interfaces:**
- Produces: `classify_query()` returns `"simple" | "medium" | "complex"` (was `"clear" | "fuzzy_short" | "colloquial" | "compound"`)
- Produces: `rewrite_query()` updated mapping for new classification

- [ ] **Step 1: Update `classify_query()` and `rewrite_query()`**

Replace lines 118-174 in `src/rag/retrieval.py`:

```python
def classify_query(query: str) -> str:
    """对用户查询进行三级分类。

    Returns:
        "simple":  单事实检索（如数字、年份+指标），走 Naive RAG
        "medium":  需要 2-3 个事实关联或分析，走 Enhanced RAG
        "complex": 多跳推理、跨文档对比，走 Agentic RAG
    """
    cleaned = query.strip()
    if not cleaned:
        return "simple"

    # 对比/比较类 → complex
    if any(w in cleaned for w in ["对比", "比较", "差异", "versus", "vs"]):
        return "complex"

    # 分析/推理类 → medium (need retrieval but not complex enough for grader)
    if any(w in cleaned for w in ["分析", "解释", "说明", "为什么", "原因"]):
        return "medium"

    # 模糊短查询 → medium (needs context expansion)
    if len(cleaned) < 10:
        return "medium"

    # 单事实数字查询 → simple
    if re.search(r"\d{4}年", cleaned) or re.search(r"(营收|利润|收入|成本|资产|负债|现金流|净利润)", cleaned):
        return "simple"

    return "medium"  # default fallback


def rewrite_query(query: str, history: list[dict]) -> str | list[str]:
    """根据三级分类执行相应的改写策略。

    Returns:
        str:      simple / medium 路径返回改写后的单条查询
        list[str]: complex 路径返回分解后的多条子查询
    """
    t = classify_query(query)
    if t == "simple":
        return query
    if t == "medium":
        # 模糊短查询 → 用历史上下文扩展；口语化查询 → 精简
        if len(query.strip()) < 10:
            return expand_query(query, history)
        if any(w in query for w in ["分析", "解释", "说明", "为什么"]):
            return condense_query(query)
        return query
    if t == "complex":
        return decompose_query(query)
    return query
```

Also add the `import re` at the top of the file if not already present:

```python
import re
```

- [ ] **Step 2: Update existing tests**

Run the existing retrieval tests to ensure they pass:

```bash
pytest tests/rag/test_retrieval.py -v
```

Fix any assertions that expect the old classification values.

- [ ] **Step 3: Commit**

```bash
git add src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "refactor: change classify_query to three-tier (simple/medium/complex)"
```

---

### Task 4: Implement RetrievalGrader

**Files:**
- Create: `src/agents/grader.py`
- Test: `tests/agents/graph/test_grader.py`

**Interfaces:**
- Consumes: `TOP_K_RERANK` from `src/config` (default 5)
- Produces: `RetrievalGrader.grade(query, results, reranked) -> float`

- [ ] **Step 1: Write the failing test**

Create `tests/agents/graph/test_grader.py`:

```python
"""Tests for RetrievalGrader."""
from src.agents.grader import RetrievalGrader


def test_grader_high_coverage():
    """所有关键词在 Top-N 结果中 → score >= 0.5。"""
    grader = RetrievalGrader()
    query = "2024年营业收入净利润"
    results = [{"content": "...", "distance": 0.9}]
    reranked = [
        {"content": "2024年公司营业收入达到100亿元"},
        {"content": "净利润同比增长20%"},
    ]
    score = grader.grade(query, results, reranked)
    assert score >= 0.5, f"Expected >= 0.5, got {score}"


def test_grader_low_coverage():
    """关键词不在 Top-N 结果中 → score < 0.5。"""
    grader = RetrievalGrader()
    query = "2024年营业收入净利润"
    results = [{"content": "...", "distance": 0.3}]
    reranked = [
        {"content": "公司总部位于北京"},
        {"content": "员工人数5000人"},
    ]
    score = grader.grade(query, results, reranked)
    assert score < 0.5, f"Expected < 0.5, got {score}"


def test_grader_no_keywords():
    """无可提取关键词 → 默认通过 0.8。"""
    grader = RetrievalGrader()
    query = "是的"
    score = grader.grade(query, [], [{"content": "test"}])
    assert score == 0.8


def test_grader_empty_reranked():
    """精排结果为空 → 返回 0.0。"""
    grader = RetrievalGrader()
    score = grader.grade("test", [], [])
    assert score == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agents/graph/test_grader.py -v
```

Expected: ModuleNotFoundError or ImportError.

- [ ] **Step 3: Implement RetrievalGrader**

Write `src/agents/grader.py`:

```python
# src/agents/grader.py
"""检索质量评分器（规则版）。

使用关键词覆盖度评估检索结果质量。
分数范围 0~1，阈值 0.5。
"""

from loguru import logger
import jieba

from src.config import TOP_K_RERANK


class RetrievalGrader:
    """规则版检索质量评分器。

    策略：
    1. 提取查询中有意义的关键词（长度 >= 2 的非停用词）
    2. 检查关键词在精排后 Top-N 结果中的出现比例
    3. 返回覆盖度作为质量分数
    """

    KEYWORD_MIN_LEN = 2
    DEFAULT_PASS = 0.8

    def grade(self, query: str, results: list[dict], reranked: list[dict]) -> float:
        """返回质量分数 0~1。

        Args:
            query: 用户查询
            results: 检索原始结果（当前未使用，保留接口签名）
            reranked: 精排后的结果列表

        Returns:
            float: 质量分数，< 0.5 认为不合格
        """
        tokens = jieba.lcut(query)
        keywords = [t for t in tokens if len(t) >= self.KEYWORD_MIN_LEN]

        if not keywords:
            return self.DEFAULT_PASS

        top_contents = [c.get("content", "") for c in reranked[:TOP_K_RERANK]]
        if not top_contents:
            return 0.0

        covered = sum(
            1 for kw in keywords
            if any(kw in content for content in top_contents)
        )
        coverage = covered / len(keywords)
        logger.debug("RetrievalGrader: coverage={:.2f} keywords={}", coverage, keywords)
        return coverage
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/agents/graph/test_grader.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/grader.py tests/agents/graph/test_grader.py
git commit -m "feat: add rule-based RetrievalGrader with keyword coverage scoring"
```

---

### Task 5: Implement graph nodes

**Files:**
- Create: `src/agents/graph/nodes.py`
- Modify: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: `AgentState` (Task 2), `RetrievalGrader` (Task 4), `rag/retrieval.py` search/rerank_results/rewrite_query (Task 3), `rag/stream.py` stream_answer, `rag/prompt.py` build_prompt/build_simple_prompt
- Produces: 7 node functions called by `workflow.py` (Task 6)

All nodes have the signature: `def node_name(state: AgentState) -> AgentState`

- [ ] **Step 1: Write the failing test for classify node**

Create `tests/agents/graph/test_graph.py`:

```python
"""Tests for LangGraph node functions."""
from src.agents.graph.state import AgentState
from src.agents.graph.nodes import classify_node


def test_classify_simple():
    """单事实查询 → simple"""
    state: AgentState = {"query": "2024年营收多少"}
    result = classify_node(state)
    assert result["intent"]["route"] == "simple"


def test_classify_medium():
    """分析类查询 → medium"""
    state: AgentState = {"query": "分析近三年营收变化趋势"}
    result = classify_node(state)
    assert result["intent"]["route"] == "medium"


def test_classify_complex():
    """对比类查询 → complex"""
    state: AgentState = {"query": "对比A公司和B公司的偿债能力差异"}
    result = classify_node(state)
    assert result["intent"]["route"] == "complex"


def test_classify_vague_maps_to_medium():
    """QueryRouter 返回 "vague" → classify_node 映射为 "medium" """
    state: AgentState = {"query": "帮我看一下"}
    # 注意：classify_node 内部使用 QueryRouter.route() + 映射
    from unittest.mock import patch
    with patch("src.agents.graph.nodes.QueryRouter") as mock_router:
        mock_router.return_value = mock_router
        mock_router.route.return_value = "vague"
        result = classify_node(state)
        assert result["intent"]["route"] == "medium"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/agents/graph/test_graph.py::test_classify_simple -v
```

Expected: ModuleNotFoundError or ImportError.

- [ ] **Step 3: Implement node functions**

Create `src/agents/graph/nodes.py`:

```python
# src/agents/graph/nodes.py
"""LangGraph 图节点函数。

每个节点函数接收 AgentState 并返回 AgentState 子集。
所有节点包含 trace_id 出入日志。
"""

import time
import asyncio
from typing import Any, Callable
from loguru import logger
from src.config import TOP_K_RERANK
from src.infra.search.query_router import QueryRouter
from src.rag.retrieval import search, rerank_results, rewrite_query
from src.rag.stream import stream_answer, estimate_usage
from src.rag.prompt import build_prompt, build_simple_prompt, format_context
from src.agents.grader import RetrievalGrader
from src.agents.graph.state import AgentState


def _tid(state: AgentState) -> str:
    return state.get("trace_id", "unknown")


def classify_node(state: AgentState) -> dict:
    """查询分类节点：基于 QueryRouter 输出三级路由。"""
    tid = _tid(state)
    logger.info("[{}] classify_node start: query={}", tid, state.get("query", "")[:50])

    router = QueryRouter()
    raw_route = router.route(state.get("query", ""))

    # 映射 vague → medium
    route_map = {"simple": "simple", "vague": "medium", "medium": "medium", "complex": "complex"}
    route = route_map.get(raw_route, "medium")

    logger.info("[{}] classify_node done: raw={} mapped={}", tid, raw_route, route)
    return {"intent": {"route": route, "rewritten": False}}


def rewrite_node(state: AgentState) -> dict:
    """查询改写节点：对非 simple 路径的查询进行改写。"""
    tid = _tid(state)
    query = state.get("query", "")
    rewritten = rewrite_query(query, state.get("_history", []))

    if isinstance(rewritten, list):
        rewritten = " ".join(rewritten)

    result = {"rewritten_query": rewritten}
    if rewritten != query:
        result["intent"] = {"route": state.get("intent", {}).get("route", "medium"), "rewritten": True}
        logger.info("[{}] rewrite_node: {} -> {}", tid, query[:30], rewritten[:30])
    else:
        logger.info("[{}] rewrite_node: no rewrite", tid)
    return result


def make_retrieve_node(vector_store, bm25) -> Callable:
    """创建检索节点工厂函数。"""
    async def _search(query, kb_id):
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


def grader_node(state: AgentState) -> dict:
    """质量评分节点：关键字覆盖度评分。"""
    tid = _tid(state)
    query = state.get("rewritten_query") or state.get("query", "")
    results = state.get("retrieval_results", [])
    grader = RetrievalGrader()
    score = grader.grade(query, results, results)
    logger.info("[{}] grader_node: score={:.2f}", tid, score)
    return {"grader_score": score}


def make_rerank_node(reranker) -> Callable:
    """创建精排节点工厂函数。"""
    def rerank_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.get("rewritten_query") or state.get("query", "")
        results = state.get("retrieval_results", [])
        if not results:
            return {"contexts": []}
        contexts = rerank_results(query, results, reranker)
        ctx_list = [
            {"content": c.content, "source": c.source, "page": c.page,
             "doc_id": c.doc_id, "chunk_id": c.chunk_id, "score": c.score}
            for c in contexts
        ]
        logger.info("[{}] rerank_node: contexts={}", tid, len(ctx_list))
        return {"contexts": ctx_list}
    return rerank_node


def make_generate_node(llm, prompt_manager, tracer) -> Callable:
    """创建生成节点工厂函数。"""
    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.get("rewritten_query") or state.get("query", "")
        contexts = state.get("contexts", [])
        downgraded = state.get("downgraded", False)

        if not contexts:
            # 降级到 Naive RAG
            logger.info("[{}] generate_node: empty contexts, Naive RAG fallback", tid)
            prompt = build_simple_prompt(query, state.get("_history", []), prompt_manager)
        else:
            # TypedDict → RAGContext 转换
            from src.rag.context import RAGContext
            rag_ctx_list = [RAGContext(**c) for c in contexts]
            context_str = format_context(rag_ctx_list)
            prompt = build_prompt(query, context_str, state.get("_history", []), prompt_manager)

        # 收集所有 token，组装完整文本
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
    return generate_node


def format_node(state: AgentState) -> dict:
    """格式化节点：去重后的引用列表。"""
    tid = _tid(state)
    contexts = state.get("contexts", [])
    seen = set()
    citations = []
    for ctx in contexts:
        key = (ctx.get("source", ""), ctx.get("page", 0))
        if key in seen:
            continue
        seen.add(key)
        citations.append({
            "source": ctx.get("source", ""),
            "page": ctx.get("page", 0),
            "snippet": ctx.get("content", "")[:200],
            "score": ctx.get("score", 0),
        })
    logger.info("[{}] format_node: citations={}", tid, len(citations))
    return {"citations": citations}
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/agents/graph/test_graph.py::test_classify_simple \
       tests/agents/graph/test_graph.py::test_classify_medium \
       tests/agents/graph/test_graph.py::test_classify_complex -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: implement 7 LangGraph node functions with trace_id logging"
```

---

### Task 6: Assemble and compile StateGraph (workflow.py)

**Files:**
- Create: `src/agents/graph/workflow.py`

**Interfaces:**
- Consumes: node functions from Task 5, `RetrievalGrader` from Task 4
- Produces: `build_graph(vector_store, bm25, llm, reranker, prompt_manager) -> CompiledStateGraph` — the compiled graph that agent_service calls in Task 7

- [ ] **Step 1: Create `src/agents/graph/workflow.py`**

```python
# src/agents/graph/workflow.py
"""StateGraph 组装 — 节点注册、条件边连接、图编译。"""

from langgraph.graph import StateGraph, END
from loguru import logger

from src.agents.graph.state import AgentState
from src.agents.graph.nodes import (
    classify_node, rewrite_node, grader_node, format_node,
    make_retrieve_node, make_rerank_node, make_generate_node,
)
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index


def route_by_intent(state: AgentState) -> str:
    intent = state.get("intent", {})
    return intent.get("route", "medium")


def route_by_grader(state: AgentState) -> str:
    score = state.get("grader_score", 0)
    retries = state.get("retrieval_retries", 0)
    if score is not None and score >= 0.5:
        return "pass"
    if retries < 2:
        # route_by_grader 内部增加重试计数
        state["retrieval_retries"] = retries + 1  # type: ignore
        return "rewrite"
    # 重试用尽，降级到 Enhanced RAG
    state["downgraded"] = True  # type: ignore
    state["downgrade_reason"] = "grader_retries_exhausted"  # type: ignore
    return "pass"


def build_graph(vector_store: VectorStore, bm25: BM25Index | None,
                llm, reranker, prompt_manager, tracer) -> StateGraph:
    """构建并编译 StateGraph。"""
    builder = StateGraph(AgentState)

    # ── 用工厂函数创建带依赖的节点 ────────────────
    builder.add_node("classify", classify_node)
    builder.add_node("rewrite", rewrite_node)
    builder.add_node("retrieve", make_retrieve_node(vector_store, bm25))
    builder.add_node("grader", grader_node)
    builder.add_node("rerank", make_rerank_node(reranker))
    builder.add_node("generate", make_generate_node(llm, prompt_manager, tracer))
    builder.add_node("format", format_node)

    # ── 条件边：三级路由 ──────────────────────────
    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify", route_by_intent, {
            "simple":  "generate",    # Naive RAG：无检索
            "medium":  "rewrite",     # Enhanced RAG
            "complex": "rewrite",     # Agentic RAG
        }
    )

    # medium + complex → rewrite → retrieve
    builder.add_edge("rewrite", "retrieve")

    # complex 路径：retrieve → grader（medium 路径不走 grader）
    builder.add_edge("retrieve", "grader")

    # grader 条件边：通过 → rerank，不通过（+ retry < 2）→ rewrite
    builder.add_conditional_edges(
        "grader", route_by_grader, {
            "pass":    "rerank",
            "rewrite": "rewrite",
        }
    )

    builder.add_edge("rerank", "generate")
    builder.add_edge("generate", "format")
    builder.add_edge("format", END)

    graph = builder.compile()
    logger.info("LangGraph StateGraph compiled: 7 nodes, 3-tier routing")
    return graph
```

- [ ] **Step 2: Run a quick smoke test**

```python
from src.agents.graph.workflow import build_graph
from src.models import get_llm, get_embeddings, get_rerank
from src.infra.db.vector_store import VectorStore
from src.infra.llm.prompt_manager import PromptManager
from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.infra.search.bm25_index import BM25Index

llm = get_llm()
reranker = get_rerank()
prompt_manager = PromptManager()
vector_store = VectorStore()
bm25 = BM25Index(index_dir=BM25_INDEX_DIR) if HYBRID_SEARCH_ENABLED else None

graph = build_graph(vector_store, bm25, llm, reranker, prompt_manager)
print("Graph compiled:", graph)
```

Expected: "Graph compiled: <langgraph.graph.state.CompiledStateGraph>"

- [ ] **Step 3: Commit**

```bash
git add src/agents/graph/workflow.py
git commit -m "feat: add StateGraph assembly with three-tier conditional routing"
```

---

### Task 7: Implement AgentService

**Files:**
- Create: `src/services/agent_service.py`

**Interfaces:**
- Consumes: compiled graph from Task 6, `sse_utils.py` functions
- Produces: `AgentService` class with `stream_chat(kb_id, session_id, query) -> AsyncGenerator[str, None]`

- [ ] **Step 1: Create `src/services/agent_service.py`**

```python
# src/services/agent_service.py
"""Agent 服务 — LangGraph 图生命周期管理。

职责：
1. 初始化并编译 StateGraph
2. 调用 graph.astream_events() 执行
3. 将 LangGraph 事件转换为 SSE 事件
4. 异常边界处理和三降级策略
"""

import os
import time
import uuid
from typing import AsyncGenerator

from loguru import logger

from src.api.sse_utils import sse_status, sse_token, sse_citation, sse_done, sse_error
from src.agents.graph.workflow import build_graph
from src.agents.graph.state import AgentState
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index
from src.infra.llm.langfuse_tracing import LangfuseTracer
from src.infra.llm.prompt_manager import PromptManager
from src.chat.manager import ChatManager


class AgentService:
    """图生命周期管理服务。"""

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

        # 编译图（实例化一次，共享给所有请求）
        self._graph = build_graph(
            vector_store, bm25, llm, reranker, self._prompt_manager, self._tracer
        )
        logger.info("AgentService initialized with compiled graph")

    async def stream_chat(
        self,
        kb_id: str,
        session_id: str,
        query: str,
    ) -> AsyncGenerator[str, None]:
        """执行图并流式返回 SSE 事件。"""
        trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        tracer_trace_id = self._tracer.start_trace(
            "chat_stream_agent",
            {"kb_id": kb_id, "session_id": session_id, "query": query},
            session_id=session_id,
        )

        # 加载对话历史
        history = await self._chat_manager.get_history_async(session_id) or []
        self._chat_manager.add_message(session_id, "user", query)

        initial_state: AgentState = {
            "session_id": session_id,
            "kb_id": kb_id,
            "query": query,
            "trace_id": trace_id,
            "retrieval_retries": 0,
            "downgraded": False,
            "downgrade_reason": "",
            "_history": history,
        }

        full_answer = ""

        try:
            t0 = time.perf_counter()
            async for event in self._graph.astream_events(
                initial_state,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")

                if kind == "on_chain_start":
                    if "classify" in name:
                        yield sse_status("classifying", "正在分析查询类型...")
                    elif "rewrite" in name:
                        yield sse_status("rewriting", "正在优化查询...")
                    elif "retrieve" in name or "retrieval" in name:
                        yield sse_status("retrieving", "正在检索相关文档...")
                    elif "rerank" in name:
                        yield sse_status("reranking", "正在精排结果...")
                    elif "generate" in name:
                        yield sse_status("generating", "正在生成回答...")

                # LLM 流式 token — on_chat_model_stream 由 LangGraph 自动发出
                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk", {})
                    content = getattr(chunk, "content", "") or ""
                    if content:
                        full_answer += content
                        yield sse_token(content)

            # 从最终状态提取 citations
            final_state = await self._graph.ainvoke(initial_state)
            contexts = final_state.get("contexts", [])
            seen = set()
            for ctx in contexts:
                key = (ctx.get("source", ""), ctx.get("page", 0))
                if key in seen:
                    continue
                seen.add(key)
                yield sse_citation(
                    ctx.get("source", ""),
                    ctx.get("page", 0),
                    (ctx.get("content", "")[:200]),
                    ctx.get("score", 0),
                )

            # 持久化对话
            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )

            t1 = time.perf_counter()
            logger.info(
                "[{}] AgentService stream_chat completed: total={:.1f}s "
                "downgraded={} reason={} contexts={}",
                trace_id, t1 - t0,
                final_state.get("downgraded"),
                final_state.get("downgrade_reason"),
                len(contexts),
            )

        except Exception as e:
            logger.exception("[{}] AgentService stream_chat failed: {}", trace_id, e)
            yield sse_error(f"暂时无法回答：{str(e)[:100]}")
        finally:
            self._tracer.end_trace(tracer_trace_id)
            yield sse_done()
```

- [ ] **Step 2: Commit**

```bash
git add src/services/agent_service.py
git commit -m "feat: add AgentService with graph lifecycle and SSE event conversion"
```

---

### Task 8: Wire AgentService into AppService and update api/chat.py

**Files:**
- Modify: `src/services/app_service.py`
- Modify: `src/api/chat.py`

- [ ] **Step 1: Modify `src/services/app_service.py`**

Add `agent_service` parameter to `__init__`:

```python
from typing import Optional
from src.services.agent_service import AgentService

class AppService:
    def __init__(
        self,
        mysql_db: Optional[MySQLDB] = None,
        vector_store: Optional[VectorStore] = None,
        router: Optional[DocRouter] = None,
        rag_chain: Optional[RAGChain] = None,
        agent_service: Optional[AgentService] = None,
    ) -> None:
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
```

- [ ] **Step 2: Rewrite `src/api/chat.py` SSE entry**

Replace `_stream_rag_response` to delegate to agent_service:

```python
async def _stream_rag_response(
    svc: AppService,
    kb_id: str,
    session_id: str,
    query: str,
) -> AsyncGenerator[str, None]:
    """以 SSE 事件流推送 RAG 响应 — 委托给 agent_service。"""
    try:
        async for event in svc.agent_service.stream_chat(kb_id, session_id, query):
            yield event
    except Exception as e:
        logger.exception("Chat stream unhandled error: {}", str(e))
        yield sse_error(str(e))
        yield sse_done()
```

Remove imports that are no longer needed: `jieba`, `get_query_biased_snippet`, `_build_highlighted_snippet`, `_persist_conversation` (persistence responsibility now lives in agent_service).

- [ ] **Step 3: Update `tests/api/test_chat.py`**

Replace the mock setup:

```python
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
from src.api.dependencies import get_app_service
from src.main import app

client = TestClient(app)


def test_chat_stream_returns_sse():
    """GET /api/chat/stream returns SSE event stream."""
    mock_svc = MagicMock()
    mock_agent = mock_svc.agent_service

    # Mock async generator for stream_chat
    async def fake_stream(*args, **kwargs):
        yield "event: status\ndata: {\"stage\":\"generating\"}\n\n"
        yield "data: 净利润\n\n"
        yield "data: 为100亿元\n\n"
        yield "event: done\ndata: {}\n\n"

    mock_agent.stream_chat = fake_stream

    app.dependency_overrides[get_app_service] = lambda: mock_svc
    try:
        response = client.get(
            "/api/chat/stream?session_id=s1&kb_id=kb-1&query=净利润多少"
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
    finally:
        app.dependency_overrides.pop(get_app_service, None)
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/ -v
```

Expected: All existing tests pass (including API tests with updated mocks).

- [ ] **Step 5: Commit**

```bash
git add src/services/app_service.py src/api/chat.py tests/api/test_chat.py
git commit -m "feat: wire AgentService into AppService and update SSE entry point"
```

---

### Task 9: Add generate_node downgrade test and clean up docs

**Files:**
- Modify: `docs/upgrade_plan.md`
- Modify: `docs/phase2_structure.md`
- Modify: `docs/financial_rag_architecture_analysis.md`

- [ ] **Step 1: Clean up docs — remove `rag/pipeline/` and `rag/state/` references**

In `docs/upgrade_plan.md`:
- Remove `rag/state/rag_state.py` from milestone P2.1 description
- Change Phase 3 `rag/pipeline/faithfulness.py` → `agents/faithfulness.py`
- Change Phase 3 `rag/pipeline/reflection.py` → `agents/reflection.py`

In `docs/phase2_structure.md`:
- Remove `pipeline/` and `state/` directories from the directory tree
- Update call rules to remove `pipeline/` references
- Update change summary table

In `docs/financial_rag_architecture_analysis.md`:
- Update the old Phase 1 migration recommendation to remove `rag/pipeline/` and `rag/state/`

- [ ] **Step 2: Run RAGAS evaluation comparison**

```bash
python -m src.cli.eval_ragas --kb-name <kb-name>
```

Compare faithfulness / context_precision scores before and after the Phase 2 changes (baseline from main branch).

- [ ] **Step 3: Run final test sweep**

```bash
pytest tests/ -v
ruff check src/
```

Expected: All tests pass, ruff zero errors, no print/TODO.

- [ ] **Step 4: Commit**

```bash
git add docs/upgrade_plan.md docs/phase2_structure.md docs/financial_rag_architecture_analysis.md
git commit -m "docs: clean up rag/pipeline/ and rag/state/ references from planning docs"
```

---

### Task 10: Integration test — verify three-tier routing and degradation

**Files:**
- Test: manual verification (no new source files)

- [ ] **Step 1: Verify simple routing**

Send a simple query via the SSE endpoint:

```bash
curl -s "http://localhost:8000/api/chat/stream?session_id=test1&kb_id=kb-1&query=2024年营收多少"
```

Expected: `sse_status("classifying")` → `sse_status("generating")` → tokens → `sse_done()`. No `sse_status("retrieving")` or `sse_status("reranking")` events should appear.

- [ ] **Step 2: Verify medium routing**

```bash
curl -s "http://localhost:8000/api/chat/stream?session_id=test2&kb_id=kb-1&query=分析近三年营收变化趋势"
```

Expected: `sse_status("classifying")` → `sse_status("rewriting")` → `sse_status("retrieving")` → `sse_status("reranking")` → `sse_status("generating")` → tokens → citation → `sse_done()`.
No `sse_status("downgrading")` should appear.

- [ ] **Step 3: Verify complex routing**

```bash
curl -s "http://localhost:8000/api/chat/stream?session_id=test3&kb_id=kb-1&query=对比A公司和B公司的偿债能力差异并分析原因"
```

Expected: Same as medium + possibly `sse_status("downgrading")` if grader fails.

- [ ] **Step 4: Verify degradation chain**

Simulate grader retry exhaustion by setting a very high threshold in test config. Verify the downgrade event appears.

- [ ] **Step 5: Run full test suite one final time**

```bash
pytest tests/ -v
ruff check src/
```

- [ ] **Step 6: Final commit**

```bash
git commit -m "chore: final integration and tests pass"
```

---

## Spec Coverage Check

| Spec Requirement | Task |
|:---|:---|
| StateGraph with 7 nodes | Task 5 + Task 6 |
| AgentState TypedDict | Task 2 |
| Three-tier routing (simple/medium/complex) | Task 4 (classify_query) + Task 6 (conditional edges) |
| RetrievalGrader rule-based | Task 4 |
| SSE event conversion with classifying/rewriting/downgrading events | Task 7 |
| Graceful degradation (3 levels) | Task 5 (generate_node) + Task 6 (route_by_grader) |
| Node trace_id logging | Task 5 (all nodes) |
| AgentService with error boundary | Task 7 |
| AppService wire-up | Task 8 |
| Tests: node unit tests, grader tests | Task 4, Task 5 |
| Tests: API mock update | Task 8 |
| Docs cleanup | Task 9 |
| Integration verification | Task 10 |
