# RAG 引用落地校验与低相关度门控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SSE 回答 token 丢失 bug，实现引用来源落地校验（只展示被引用来源 + 编号徽标），并为低相关度增加 rerank 阈值门控与 abstention 出口。

**Architecture:** 三个独立层次：① `agent_service.py` 消费 LangGraph 事件时用 `metadata.langgraph_node` 匹配节点并捕获 Format/Generate 节点输出；② `format_node`/`rerank_results` 在 graph 内做引用过滤与阈值过滤；③ `generate_node` 区分 skip_retrieval（问候）、abstention（无达标 context）、正常三路。前端仅加编号徽标。

**Tech Stack:** Python 3.11 / FastAPI / LangGraph 1.2.9 / ChromaDB / SSE / 原生 JS（chat.html）

## Global Constraints

- 测试 mock 外部依赖，不发起真实网络调用
- 不用三元表达式，写完整 if/else 结构
- 类型不确定的值不用 `getattr(x, "attr", default)`，用 `x.attr if x is not None else default`
- 新增常量按用途归位 `src/config/`：settings.py（环境参数）、prompts.py（文案）、const.py（固定阈值/状态映射）
- 单文件超 400 行、单函数超 80 行必须拆分
- 所有函数写 docstring；dataclass 字段加行内注释
- 改完代码并验证通过后，先 commit，再输出 `git diff HEAD~1`

---

## 任务间依赖图

```
Task 1 (D1 metadata bug 修复)
Task 2 (D7 sse.py index 字段) ──────────────┐
Task 3 (D6 skip_retrieval 标记) ────────────┤
Task 4 (D3 rerank 阈值) ────────────────────┤
Task 5 (D2 format_node 引用过滤) ───────────┼──> Task 7 (D5 事件链修正)
Task 6 (D4 abstention 分支) ────────────────┘
Task 8 (前端徽标, 依赖 Task 2)
Task 9 (验证闭环)
```

---

### Task 1: D1 — 修复 SSE 回答 token 丢失 Bug

**Files:**
- Modify: `src/services/agent_service.py:117-130`
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: LangGraph 事件 dict（`event["metadata"]["langgraph_node"]`，已实测存在）
- Produces: `SSETokenEvent` 正常流出（generate 节点 token 不再被过滤）

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_agent_service.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_stream_chat_yields_generate_tokens_from_metadata():
    """generate 节点的 CHAT_MODEL_STREAM token 应通过 metadata.langgraph_node 识别并流出。"""
    from src.services.agent_service import AgentService
    from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode
    from src.utils.sse import SSETokenEvent
    from langchain_core.messages import AIMessageChunk

    service = AgentService.__new__(AgentService)
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    async def fake_astream(*args, **kwargs):
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAT_MODEL_STREAM,
            LangGraphKey.NAME: "ChatOpenAI",  # 事件 name 是模型类名，不是节点名
            "metadata": {"langgraph_node": LangGraphNode.Generate.NAME},
            LangGraphKey.DATA: {"chunk": AIMessageChunk(content="你好")},
        }

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events = []
    async for event in service.stream_chat("kb1", "session1", "阿里巴巴"):
        events.append(event)

    token_events = [e for e in events if isinstance(e, SSETokenEvent)]
    assert len(token_events) == 1
    assert token_events[0].token == "你好"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/test_agent_service.py::test_stream_chat_yields_generate_tokens_from_metadata -v`
Expected: FAIL（token_events 为空，因为当前按 name 过滤全部丢弃）

- [ ] **Step 3: 修复过滤逻辑**

修改 `src/services/agent_service.py` 的 `CHAT_MODEL_STREAM` 分支（约 117-130 行）：

```python
                    case LangGraphEvent.CHAT_MODEL_STREAM:
                        metadata = event.get("metadata", {}) or {}
                        node_name = metadata.get("langgraph_node", "")
                        chunk = event.get(LangGraphKey.DATA, {}).get(LangGraphKey.CHUNK)
                        content = chunk.content if chunk is not None else ""
                        if LangGraphNode.Generate.NAME not in node_name:
                            if content:
                                logger.info(
                                    "CHAT_MODEL_STREAM filtered: node={} content_prefix={!r:.50}",
                                    node_name, content,
                                )
                            continue
                        if content:
                            full_answer += content
                            yield SSETokenEvent(content)
```

注意：`name` 变量在循环顶部（107 行 `name = event.get(...)`）统一提取，`CHAIN_START` 分支（112 行 `if node in name`）依赖它做状态映射，**本分支不要覆盖 `name` 变量**，改用独立的 `node_name`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 3 个测试全部 PASS（原 2 个 + 新增 1 个）

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "fix: SSE answer tokens dropped - match generate node via metadata.langgraph_node"
```

---

### Task 2: D7 — SSECitationEvent 增加 index 字段

**Files:**
- Modify: `src/utils/sse.py:29-38, 113-139, 204-207`
- Test: `tests/utils/test_sse.py`

**Interfaces:**
- Produces: `SSECitationEvent(source, page, snippet, score=0.0, highlighted_snippet=None, index=0)`，`to_sse()` 支持 index 字段
- Consumes by: Task 5（format_node）、Task 7（agent_service 发送）、Task 8（前端）

- [ ] **Step 1: 写失败测试**

在 `tests/utils/test_sse.py` 中确认现有测试结构后，追加：

```python
def test_sse_citation_with_index():
    """sse_citation 应序列化 index 字段。"""
    from src.utils.sse import sse_citation, SSECitationEvent, to_sse

    event = SSECitationEvent(source="a.pdf", page=3, snippet="内容", index=2)
    text = to_sse(event)
    assert '"index": 2' in text
    assert '"source": "a.pdf"' in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/utils/test_sse.py::test_sse_citation_with_index -v`
Expected: FAIL（AttributeError: SSECitationEvent has no attribute 'index'）

- [ ] **Step 3: 实现 index 字段**

修改 `src/utils/sse.py`：

```python
@dataclass
class SSECitationEvent:
    """引用来源事件。"""

    source: str  # 文档来源名称
    page: int  # 页码
    snippet: str  # 内容摘要（前 200 字）
    score: float = 0.0  # Reranker 分数
    highlighted_snippet: str | None = None  # 高亮 HTML 片段
    index: int = 0  # 原文档编号（对应 format_context 的 [n]），0 表示兜底无编号
```

修改 `sse_citation()`：

```python
def sse_citation(
    source: str,
    page: int,
    snippet: str,
    score: float = 0.0,
    highlighted_snippet: str | None = None,
    index: int = 0,
) -> str:
    """构建 SSE citation 事件。

    Args:
        source: 文档来源名称
        page: 页码
        snippet: 内容摘要
        score: Reranker 分数
        highlighted_snippet: 高亮 HTML 片段
        index: 原文档编号（对应 format_context 的 [n]），0 表示无编号

    Returns:
        SSE 格式的文本行
    """
    data = {
        "source": source,
        "page": page,
        "snippet": snippet,
        "score": score,
        "highlighted_snippet": highlighted_snippet,
        "index": index,
    }
    return f"event: citation\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

修改 `to_sse()` 的 SSECitationEvent 匹配：

```python
        case SSECitationEvent(
            source=s, page=p, snippet=snippet, score=score,
            highlighted_snippet=hs, index=idx,
        ):
            return sse_citation(s, p, snippet, score, hs, idx)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/utils/test_sse.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/sse.py tests/utils/test_sse.py
git commit -m "feat: add index field to SSECitationEvent for citation badge mapping"
```

---

### Task 3: D6 — skip_retrieval 标记（QueryRouter + AgentState + classify_node）

**Files:**
- Modify: `src/infra/search/query_router.py:15-84`
- Modify: `src/agents/graph/state.py`（AgentState 加字段）
- Modify: `src/agents/graph/nodes.py:64-80`（classify_node 透出）
- Test: `tests/infra/search/test_query_router.py`、`tests/agents/graph/test_state.py`

**Interfaces:**
- Produces: `AgentState.skip_retrieval: bool`；QueryRouter.route() 返回 dict 增加 `"skip_retrieval"` 键
- Consumes by: Task 6（generate_node 三分支）

- [ ] **Step 1: 写失败测试（AgentState）**

在 `tests/agents/graph/test_state.py` 追加：

```python
def test_agent_state_skip_retrieval_default_false():
    """skip_retrieval 默认应为 False。"""
    from src.agents.graph.state import AgentState

    state = AgentState.make_initial_state("s1", "kb1", "营收多少", [])
    assert state.skip_retrieval is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/graph/test_state.py -v`
Expected: FAIL（AttributeError: skip_retrieval）

- [ ] **Step 3: AgentState 加字段**

在 `src/agents/graph/state.py` 的 `classification_confidence` 字段后追加：

```python
    classification_confidence: float = 0.0  # LLM 置信度（LLM 输出 key="confidence"）
    skip_retrieval: bool = False  # 问候/闲聊标记：跳过检索直接回答（由 classify_node 设置）
```

- [ ] **Step 4: 写失败测试（QueryRouter）**

在 `tests/infra/search/test_query_router.py` 追加：

```python
def test_greeting_sets_skip_retrieval() -> None:
    """问候查询应设置 skip_retrieval=True。"""
    from src.infra.search.query_router import QueryRouter
    from unittest.mock import Mock

    router = QueryRouter(llm=Mock())
    result = router.route("你好", [])
    assert result["skip_retrieval"] is True


def test_normal_query_not_skip_retrieval() -> None:
    """普通查询应设置 skip_retrieval=False。"""
    from src.infra.search.query_router import QueryRouter

    router = QueryRouter(llm=None)
    result = router.route("2024年营收多少", [])
    assert result["skip_retrieval"] is False
```

注意：普通查询走 `_fallback_route`（无 llm 时），需确认返回值含 `skip_retrieval`。

- [ ] **Step 5: 运行测试确认失败**

Run: `pytest tests/infra/search/test_query_router.py -v`
Expected: FAIL（KeyError: 'skip_retrieval'）

- [ ] **Step 6: QueryRouter 实现**

修改 `src/infra/search/query_router.py`：

`_simple_result()` 增加 `"skip_retrieval": True`：

```python
    def _simple_result(self) -> dict[str, Any]:
        return {
            "intent": RAGQueryIntent(route="simple"),
            "extracted_entities": [],
            "missing_entities": [],
            "classification_confidence": 1.0,
            "skip_retrieval": True,
        }
```

`route()` 的正常路径 result 增加 `"skip_retrieval": False`：

```python
        result = {
            "intent": RAGQueryIntent(route=llm_result["route"]),
            "extracted_entities": entities_dict,
            "missing_entities": llm_result.get("missing_entities", []),
            "classification_confidence": llm_result.get("confidence", 0.0),
            "skip_retrieval": False,
        }
```

同时确认 `_llm_classify()` 和 `_fallback_route()` 的返回 dict 不需要 skip_retrieval（它们在 route() 里被解包成 llm_result，只需 route/confidence 等键）。

- [ ] **Step 7: classify_node 透出**

修改 `src/agents/graph/nodes.py` 的 `classify_node`（约 73-78 行）：

```python
        return {
            "intent": result["intent"],
            "extracted_entities": result["extracted_entities"],
            "missing_entities": result["missing_entities"],
            "classification_confidence": result["classification_confidence"],
            "skip_retrieval": result.get("skip_retrieval", False),
        }
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/infra/search/test_query_router.py tests/agents/graph/test_state.py -v`
Expected: 全部 PASS

- [ ] **Step 9: 提交**

```bash
git add src/infra/search/query_router.py src/agents/graph/state.py src/agents/graph/nodes.py tests/infra/search/test_query_router.py tests/agents/graph/test_state.py
git commit -m "feat: add skip_retrieval flag for greeting/short-query direct-answer path"
```

---

### Task 4: D3 — Rerank 分数阈值 RERANK_MIN_SCORE

**Files:**
- Modify: `src/config/settings.py`（新增 RERANK_MIN_SCORE）
- Modify: `src/rag/retrieval.py:75-140`
- Test: `tests/rag/test_retrieval.py`

**Interfaces:**
- Produces: `RERANK_MIN_SCORE: float`（默认 0.3，环境变量可覆盖）；`rerank_results()` 过滤低于阈值的 context，**rerank 失败 fallback 不应用阈值**
- Consumes by: Task 6（generate_node 判断 contexts 是否为空）

- [ ] **Step 1: 写失败测试**

在 `tests/rag/test_retrieval.py` 的 `TestRerank` 类中追加：

```python
    def test_rerank_filters_below_threshold(self):
        """低于 RERANK_MIN_SCORE 的 context 应被过滤。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.9},
            {"index": 1, "relevance_score": 0.1},
        ]
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(2)
        ]
        with patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
        assert contexts[0].source == "a0.pdf"

    def test_rerank_all_below_threshold_returns_empty(self):
        """全部低于阈值应返回空列表。"""
        reranker = MagicMock()
        reranker.rerank.return_value = [
            {"index": 0, "relevance_score": 0.1},
            {"index": 1, "relevance_score": 0.05},
        ]
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(2)
        ]
        with patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3):
            contexts = retrieval.rerank_results("query", results, reranker)
        assert contexts == []

    def test_rerank_fallback_skips_threshold(self):
        """rerank 失败 fallback（1-distance 分数）不应用阈值。"""
        reranker = MagicMock()
        reranker.rerank.side_effect = RuntimeError("rerank down")
        results = [
            ChunkResult(
                id=f"d1:{i}",
                content=f"content {i}",
                distance=0.5,  # 1-0.5=0.5，若应用阈值 0.3 会保留；用 0.9 距离=0.1 分验证不过滤
                metadata={"source": f"a{i}.pdf", "page": 1, "doc_id": "d1"},
            )
            for i in range(1)
        ]
        # 距离 0.9 → fallback 分数 0.1 < 0.3，若应用阈值会被过滤；不应被过滤
        results[0].distance = 0.9
        with patch("src.rag.retrieval.RERANK_MIN_SCORE", 0.3):
            with patch("src.rag.retrieval.with_retry", side_effect=lambda f, **kw: f):
                contexts = retrieval.rerank_results("query", results, reranker)
        assert len(contexts) == 1
```

注意：需确认 `ChunkResult` 是否有 `distance` 字段（用于 fallback 分支 `r.distance`）。若没有，改用 `with patch.object(results[0], 'distance', 0.9, create=True)` 或直接设置 metadata。实现时检查 `src/infra/db/vector_store/types.py`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: FAIL（当前无过滤逻辑，`test_rerank_filters_below_threshold` 断言 len==1 实为 2）

- [ ] **Step 3: 配置项**

在 `src/config/settings.py` 的 `TOP_K_RERANK` 之后追加：

```python
# 重排序后保留的最低分数阈值：低于此分数的 context 不送入 LLM（rerank 失败 fallback 不应用）
RERANK_MIN_SCORE: float = float(os.getenv("RERANK_MIN_SCORE", "0.3"))
```

- [ ] **Step 4: rerank_results 实现**

修改 `src/rag/retrieval.py`：

导入处追加：`from src.config import TOP_K_RETRIEVAL, TOP_K_RERANK, HYBRID_SEARCH_ENABLED, RERANK_MIN_SCORE`

`rerank_results` 中：

```python
    docs = [r.content for r in results]
    # rerank 成功与否影响是否应用阈值：失败 fallback 分数量纲不同（1-distance），不应用阈值
    apply_threshold = True
    try:
        reranked = with_retry(
            reranker.rerank,
            max_attempts=RETRY_MAX_ATTEMPTS,
            initial_interval=RETRY_INITIAL_INTERVAL,
            backoff=RETRY_BACKOFF_FACTOR,
        )(docs, query)
    except Exception as e:
        logger.warning(
            "Rerank failed after {} attempts (using raw order): {}",
            RETRY_MAX_ATTEMPTS,
            e,
        )
        apply_threshold = False
        reranked = [
            {
                "index": i,
                "relevance_score": 1 - r.distance if r.distance is not None else 0,
            }
            for i, r in enumerate(results)
        ]

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        idx = item["index"]
        r = results[idx]
        score = item.get("relevance_score", 0)
        if apply_threshold and score < RERANK_MIN_SCORE:
            logger.info(
                "Rerank filter: idx={} score={:.4f} < RERANK_MIN_SCORE={}",
                idx, score, RERANK_MIN_SCORE,
            )
            continue
        pc = r.metadata.get("parent_content")
        contexts.append(
            RAGContext(
                content=pc if pc else r.content,
                source=r.metadata.get("source", ""),
                page=r.metadata.get("page", 0),
                doc_id=r.metadata.get("doc_id", ""),
                chunk_id=r.id,
                parent_content=pc,
                score=score,
            )
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: 全部 PASS（新增 3 个 + 原有）

- [ ] **Step 6: 提交**

```bash
git add src/config/settings.py src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "feat: add RERANK_MIN_SCORE threshold, skip on rerank failure fallback"
```

---

### Task 5: D2 — format_node 引用落地过滤

**Files:**
- Modify: `src/config/prompts.py`（拒答关键词常量）
- Modify: `src/agents/graph/nodes.py:221-240`（format_node）
- Test: `tests/agents/graph/test_graph.py`（新增 format_node 测试）

**Interfaces:**
- Consumes: `state.answer`、`state.contexts`、拒答关键词常量
- Produces: `format_node(state) -> {"citations": [{"index", "source", "page", "snippet", "score"}]}`；只保留回答实际引用的来源；拒答时返回空
- Consumed by: Task 7（agent_service 捕获 Format CHAIN_END）

- [ ] **Step 1: 写失败测试**

在 `tests/agents/graph/test_graph.py` 追加（先确认该文件的 import 结构）：

```python
def test_format_node_only_keeps_cited_sources():
    """format_node 应只保留回答中实际引用的来源，并带原始编号。"""
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

    state = AgentState(
        answer="腾讯2024年营收3943亿元[1]，灿坤2019年营收见[3]",
        contexts=[
            RAGContext(content="腾讯2024年报内容", source="腾讯.pdf", page=5,
                       doc_id="d1", chunk_id="c1", score=0.9),
            RAGContext(content="灿坤内容A", source="灿坤.pdf", page=1,
                       doc_id="d2", chunk_id="c2", score=0.8),
            RAGContext(content="灿坤2019年报内容", source="灿坤.pdf", page=10,
                       doc_id="d2", chunk_id="c3", score=0.7),
            RAGContext(content="无关内容", source="其他.pdf", page=1,
                       doc_id="d3", chunk_id="c4", score=0.6),
        ],
    )
    result = format_node(state)
    citations = result["citations"]
    assert len(citations) == 2  # [1] 腾讯.pdf:5 和 [3] 灿坤.pdf:10
    assert citations[0]["index"] == 1
    assert citations[0]["source"] == "腾讯.pdf"
    assert citations[1]["index"] == 3
    assert citations[1]["source"] == "灿坤.pdf"


def test_format_node_ignores_invalid_index():
    """超出范围的引用编号应被忽略。"""
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

    state = AgentState(
        answer="内容[9]",  # 只有 1 个 context，编号 9 非法
        contexts=[
            RAGContext(content="内容", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="c1", score=0.9),
        ],
    )
    result = format_node(state)
    assert result["citations"] == []


def test_format_node_empty_when_abstention():
    """回答含拒答语时 citations 应为空。"""
    from src.agents.graph.nodes import format_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext

    state = AgentState(
        answer="未在文档中找到相关数据。",
        contexts=[
            RAGContext(content="内容", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="c1", score=0.5),
        ],
    )
    result = format_node(state)
    assert result["citations"] == []
```

注意：`RAGContext` 构造参数（content/source/page/doc_id/chunk_id/score），以 `src/rag/context.py` 实际签名为准。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: FAIL（当前 format_node 返回全部去重 context，无过滤）

- [ ] **Step 3: 拒答关键词常量**

在 `src/config/prompts.py` 末尾追加：

```python
# ====== Abstention / 拒答 ======

# 拒答语检测关键词：回答命中任一关键词时，format_node 不输出引用
ABSTENTION_MARKERS: tuple[str, ...] = ("未在文档中找到",)
```

- [ ] **Step 4: format_node 实现**

修改 `src/agents/graph/nodes.py`：

导入处追加：`import re` 和 `from src.config.prompts import ABSTENTION_MARKERS`

```python
def format_node(state: AgentState) -> dict:
    """格式化节点：只保留回答中实际引用的来源，去重并带原始编号。"""
    answer = state.answer or ""
    contexts = state.contexts or []

    # 拒答检测：回答明确表示未找到数据时，不输出引用
    if any(marker in answer for marker in ABSTENTION_MARKERS):
        logger.info("format_node: answer is abstention, citations=[]")
        return {"citations": []}

    # 提取回答中引用的编号 [n]，非法编号（超出 context 范围）忽略
    cited_numbers = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    valid_numbers = {n for n in cited_numbers if 1 <= n <= len(contexts)}
    if not valid_numbers:
        logger.info("format_node: no valid citation markers, citations=[]")
        return {"citations": []}

    # 按编号升序取对应 context，按 (source, page) 去重
    seen = set()
    citations = []
    for n in sorted(valid_numbers):
        ctx = contexts[n - 1]
        key = (ctx.source, ctx.page)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "index": n,
                "source": ctx.source,
                "page": ctx.page,
                "snippet": ctx.content[:200],
                "score": ctx.score,
            }
        )
    logger.info("format_node: citations={}", len(citations))
    return {"citations": citations}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/config/prompts.py src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: filter citations to only sources actually cited in answer"
```

---

### Task 6: D4 — generate_node 三分支（skip_retrieval / abstention / 正常）

**Files:**
- Modify: `src/config/prompts.py`（ABSTENTION_TEXT）
- Modify: `src/agents/graph/nodes.py:178-216`（generate_node）
- Test: `tests/agents/graph/test_graph.py`

**Interfaces:**
- Consumes: `state.skip_retrieval`（Task 3）、`state.contexts`（Task 4 过滤后）
- Produces: `generate_node(state) -> dict`；abstention 分支返回 `{"answer": ABSTENTION_TEXT, "model_used": "", "is_fallback": False, ...}`；skip_retrieval 分支走 `build_simple_prompt`
- Consumed by: Task 7（agent_service 捕获 answer）

- [ ] **Step 1: 写失败测试**

在 `tests/agents/graph/test_graph.py` 追加：

```python
def test_generate_node_abstention_when_no_contexts():
    """无 contexts 且非 skip_retrieval 时，generate_node 应返回 abstention 静态文案。"""
    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.config.prompts import ABSTENTION_TEXT
    from unittest.mock import Mock

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="阿里巴巴",
        rewritten_query="阿里巴巴",
        contexts=[],
        skip_retrieval=False,
    )
    result = node(state)
    assert result["answer"] == ABSTENTION_TEXT
    assert result["model_used"] == ""
    assert result["is_fallback"] is False


def test_generate_node_skip_retrieval_uses_simple_prompt():
    """skip_retrieval=True 时即使有 contexts 也走 build_simple_prompt。"""
    from src.agents.graph.nodes import make_generate_node
    from src.agents.graph.state import AgentState
    from src.rag.context import RAGContext
    from unittest.mock import Mock, patch

    node = make_generate_node(llm=Mock(), prompt_manager=Mock())
    state = AgentState(
        query="你好",
        rewritten_query="你好",
        contexts=[
            RAGContext(content="随机内容", source="a.pdf", page=1,
                       doc_id="d1", chunk_id="c1", score=0.8),
        ],
        skip_retrieval=True,
    )
    with patch("src.agents.graph.nodes.build_simple_prompt", return_value=[]) as m:
        with patch("src.agents.graph.nodes.stream_answer", return_value=iter(["你好！"])):
            result = node(state)
    m.assert_called_once()
    assert "你好" in result["answer"]
```

注意：`make_generate_node` 签名 `(llm, prompt_manager)`；`stream_answer` 返回生成器，patch 时用 `iter([...])`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: FAIL（abstention 测试当前会走 build_simple_prompt 调 LLM，skip_retrieval 测试无 skip_retrieval 概念）

- [ ] **Step 3: ABSTENTION_TEXT 常量**

在 `src/config/prompts.py` 的 ABSTENTION_MARKERS 后追加：

```python
# abstention 出口的回答文案：检索无达标 context 时直接返回，不回 LLM
ABSTENTION_TEXT: str = "未在文档中找到相关数据。请尝试更换问题表述或补充更多文档。"
```

- [ ] **Step 4: generate_node 实现**

修改 `src/agents/graph/nodes.py` 的 `make_generate_node`：

```python
    def generate_node(state: AgentState) -> dict:
        tid = _tid(state)
        query = state.rewritten_query or state.query
        contexts = state.contexts or []

        # ① 问候/闲聊：免检索直接回答（无视 contexts）
        if state.skip_retrieval:
            logger.info("generate_node: skip_retrieval, simple prompt")
            prompt = build_simple_prompt(query, state._history or [], prompt_manager)
            full_text = ""
            for token in stream_answer(prompt, llm, tid):
                full_text += token
            usage = estimate_usage(prompt, full_text)
            result: dict = {"answer": full_text, "_token_usage": usage}
            model_name = getattr(llm, "model", LLM_MODEL) or ""
            if model_name:
                result["model_used"] = model_name
            logger.info(
                "generate_node done (skip_retrieval): answer_len={} tokens={}",
                len(full_text), usage.total_tokens,
            )
            return result

        # ② 检索无达标 context：abstention 静态文案，不回 LLM
        if not contexts:
            logger.info("generate_node: empty contexts, abstention")
            usage = estimate_usage([], ABSTENTION_TEXT)
            logger.info(
                "generate_node done (abstention): answer_len={} tokens={}",
                len(ABSTENTION_TEXT), usage.total_tokens,
            )
            return {
                "answer": ABSTENTION_TEXT,
                "_token_usage": usage,
                "model_used": "",
                "is_fallback": False,
            }

        # ③ 正常 RAG 生成
        context_str = format_context(contexts)
        prompt = build_prompt(query, context_str, state._history or [], prompt_manager)
        full_text = ""
        for token in stream_answer(prompt, llm, tid):
            full_text += token
        usage = estimate_usage(prompt, full_text)

        result = {"answer": full_text, "_token_usage": usage}
        model_name = getattr(llm, "model", LLM_MODEL) or ""
        if model_name:
            result["model_used"] = model_name
        logger.info(
            "generate_node done: answer_len={} tokens={}",
            len(full_text), usage.total_tokens,
        )
        return result
```

注意：删除了原 `if not contexts: build_simple_prompt` 的 Naive RAG 降级（原 186-190 行）和原 208-210 行的 `downgraded=True/reason="rerank_empty"`。

行为变更说明：原 empty-contexts 时设置的 `downgraded=True/reason="rerank_empty"` 标记已删除。agent_service 只在 Grader `CHAIN_END` 读 downgraded（`agent_service.py:151-156`），Generate `CHAIN_END` 不读，故删除不影响 agent_service 行为；abstention 路径的状态透出改由 Task 7 的 `SSEStatusEvent`（`ABSTENTION_STATUS_MSG`）承担，graph 内不再有 `rerank_empty` 降级标记。此变更需在 commit message 注明。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/agents/graph/test_graph.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/config/prompts.py src/agents/graph/nodes.py tests/agents/graph/test_graph.py
git commit -m "feat: abstention branch for empty contexts, keep simple prompt for greetings"
```

---

### Task 7: D5 — agent_service 事件链修正

**Files:**
- Modify: `src/services/agent_service.py`（Generate CHAIN_END 捕获 answer、Format CHAIN_END 捕获 citations、abstention token 兜底、abstention 状态提示）
- Modify: `src/config/const.py`（abstention 状态文案）
- Test: `tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: `output["answer"]`（Task 6）、`output["citations"]`（Task 5）、`SSECitationEvent.index`（Task 2）
- Produces: abstention 文本以 `SSETokenEvent` 送达前端；citations 优先用 format_node 过滤结果；abstention 时发状态事件

- [ ] **Step 1: 写失败测试**

在 `tests/services/test_agent_service.py` 追加：

```python
@pytest.mark.asyncio
async def test_stream_chat_yields_abstention_answer_as_token():
    """generate 返回静态 answer 时（abstention），应作为 token 送达且 citations 为空。"""
    from src.services.agent_service import AgentService
    from src.config.const import LangGraphEvent, LangGraphKey, LangGraphNode
    from src.utils.sse import SSETokenEvent, SSECitationEvent, SSEStatusEvent
    from src.config.prompts import ABSTENTION_TEXT

    service = AgentService.__new__(AgentService)
    service._llm = Mock()
    service._chat_manager = AsyncMock()
    service._chat_manager.get_history_async.return_value = []
    service._chat_manager.add_message_async = AsyncMock()
    service._prompt_manager = Mock()
    service._tracer = Mock()

    async def fake_astream(*args, **kwargs):
        # rerank 产出空 contexts → generate 走 abstention 静态文案
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Rerank.NAME,
            LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"contexts": []}},
        }
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Generate.NAME,
            LangGraphKey.DATA: {
                LangGraphKey.OUTPUT: {
                    "answer": ABSTENTION_TEXT,
                    "model_used": "",
                    "is_fallback": False,
                }
            },
        }
        yield {
            LangGraphKey.EVENT: LangGraphEvent.CHAIN_END,
            LangGraphKey.NAME: LangGraphNode.Format.NAME,
            LangGraphKey.DATA: {LangGraphKey.OUTPUT: {"citations": []}},
        }

    service._graph = Mock()
    service._graph.astream_events = fake_astream

    events = []
    async for event in service.stream_chat("kb1", "session1", "阿里巴巴"):
        events.append(event)

    tokens = [e for e in events if isinstance(e, SSETokenEvent)]
    citations = [e for e in events if isinstance(e, SSECitationEvent)]
    statuses = [e for e in events if isinstance(e, SSEStatusEvent)]
    assert any(t.token == ABSTENTION_TEXT for t in tokens)
    assert citations == []
    assert len(statuses) >= 1  # abstention 状态提示
    # 持久化到 chat_manager（开头还有一次 user 消息调用，用 assert_any_call 匹配 assistant 那次）
    service._chat_manager.add_message_async.assert_any_call(
        "session1", "assistant", ABSTENTION_TEXT
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/test_agent_service.py::test_stream_chat_yields_abstention_answer_as_token -v`
Expected: FAIL（当前 Generate CHAIN_END 不捕获 answer，无 token 流出）

- [ ] **Step 3: abstention 状态文案常量**

在 `src/config/const.py` 的 `SSE_STATUS` 后追加：

```python
# abstention 分支的状态提示文案（agent_service 在拒答时发送）
ABSTENTION_STATUS_MSG: str = "未找到相关文档，已直接答复"
```

- [ ] **Step 4: agent_service 实现**

修改 `src/services/agent_service.py`：

在 `stream_chat` 方法开头附近追加变量：

```python
        full_answer = ""
        answer = ""
        formatted_citations = None  # None=Format 节点未捕获（兜底用）；[]=明确无引用
        model_used = ""
        is_fallback = False
```

`CHAIN_END` 分支修改（157-159 行附近）：

```python
                            elif LangGraphNode.Generate.NAME in name:
                                answer = output.get("answer", "") or answer
                                model_used = output.get("model_used", model_used)
                                is_fallback = output.get("is_fallback", is_fallback)
                            elif LangGraphNode.Format.NAME in name:
                                formatted_citations = output.get("citations", []) or []
```

建议：在 `src/config/const.py` 的 `LangGraphNode.Format` 类中加：

```python
    class Format:
        NAME: str = "format"  # 引用格式化
        CITATIONS: str = "citations"  # 引用列表输出字段
```

然后 agent_service 写 `output.get(LangGraphNode.Format.CITATIONS, []) or []`。

引用事件输出部分修改（约 181-192 行）：

```python
            # abstention 兜底：generate 返回静态 answer 且无流式 token 时，作为 token 送达
            if not full_answer and answer:
                full_answer = answer
                if answer == ABSTENTION_TEXT:
                    yield SSEStatusEvent("generate", ABSTENTION_STATUS_MSG)
                yield SSETokenEvent(answer)

            # 引用事件：优先用 format_node 过滤后的 citations
            # formatted_citations 为 None 表示 Format 节点未捕获（异常路径），兜底遍历 contexts
            # formatted_citations 为 [] 表示明确无引用（拒答/未引用任何文档），直接不发
            if formatted_citations is None:
                seen = set()
                citations_to_send = []
                for ctx in contexts:
                    key = (ctx.source, ctx.page or 0)
                    if key in seen:
                        continue
                    seen.add(key)
                    citations_to_send.append(
                        {
                            "source": ctx.source or "",
                            "page": ctx.page or 0,
                            "snippet": (ctx.content or "")[:200],
                            "score": ctx.score or 0,
                            "index": 0,
                        }
                    )
            else:
                citations_to_send = formatted_citations
            for c in citations_to_send:
                yield SSECitationEvent(
                    source=c.get("source", ""),
                    page=c.get("page", 0),
                    snippet=c.get("snippet", ""),
                    score=c.get("score", 0.0),
                    index=c.get("index", 0),
                )

            if full_answer:
                await self._chat_manager.add_message_async(
                    session_id, "assistant", full_answer
                )
```

同时确认导入：`from src.config.prompts import ABSTENTION_TEXT`、`from src.config.const import ABSTENTION_STATUS_MSG`（或从 `src.config` 导入）。

语义边界：`formatted_citations` 初始为 `None`（Format 节点未执行/事件丢失 → 兜底发 contexts）；Format 节点正常执行后为 `[]`（拒答或未引用任何文档 → 明确不发）或非空列表（只发被引用来源）。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/services/test_agent_service.py -v`
Expected: 全部 PASS（原 2 个 + Task 1 的 1 个 + 新增 1 个）

- [ ] **Step 6: 提交**

```bash
git add src/config/const.py src/services/agent_service.py tests/services/test_agent_service.py
git commit -m "feat: capture generate answer & format citations in agent_service, abstention SSE"
```

---

### Task 8: 前端引用编号徽标

**Files:**
- Modify: `frontend/chat.html`（renderCitation 函数 + SSE citation 监听 + CSS）

**Interfaces:**
- Consumes: SSE citation 事件的 `index` 字段（Task 2）
- Produces: 引用条目显示 `[n]` 徽标

- [ ] **Step 1: 修改 renderCitation**

`frontend/chat.html` 中 `renderCitation(source, page, snippet)`（约 646-655 行）改为接收 index：

```javascript
function renderCitation(source, page, snippet, index) {
  let list = chatContainer.querySelector('.citation-list');
  if (!list) {
    list = document.createElement('div');
    list.className = 'citation-list';
    chatContainer.appendChild(list);
  }
  const item = document.createElement('div');
  item.className = 'citation-item';
  const badge = (index && index > 0) ? `<span class="citation-index">[${index}]</span>` : '';
  item.innerHTML = `${badge}<span class="citation-source">📄 ${escapeHtml(source)}</span> · 第${page}页<br>${escapeHtml(snippet)}`;
  list.appendChild(item);
  scrollToBottom();
}
```

- [ ] **Step 2: 修改 SSE 监听**

`frontend/chat.html` 中 `source.addEventListener('citation', ...)`（约 789-798 行）：

```javascript
  source.addEventListener('citation', (e) => {
    try {
      const data = JSON.parse(e.data);
      renderCitation(data.source, data.page, data.snippet || '', data.index || 0);
    } catch (err) { /* ignore */ }
  });
```

- [ ] **Step 3: 增加 CSS**

在 `.citation-source` 样式附近追加：

```css
  .citation-index {
    display: inline-block;
    background: #e8f0fe;
    border-radius: 4px;
    color: #1a56db;
    font-weight: 600;
    margin-right: 4px;
    padding: 1px 5px;
    font-size: 12px;
  }
```

- [ ] **Step 4: 手动验证**

用 playwright-cli（或浏览器打开 `frontend/chat.html`）验证：模拟收到带 index 的 citation 事件，条目显示 `[2] 📄 xxx.pdf · 第x页`；无 index 时（index=0）不显示徽标。

- [ ] **Step 5: 提交**

```bash
git add frontend/chat.html
git commit -m "feat: show citation index badge in frontend citation list"
```

---

### Task 9: 验证闭环

**Files:**
- 无代码修改，仅验证

**Interfaces:**
- Consumes: Task 1-8 全部改动

- [ ] **Step 1: 全量测试**

Run: `pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 2: lint 检查**

Run: `ruff check .`
Expected: 无错误
（如失败：`ruff format . && ruff check . --fix`）

- [ ] **Step 3: 复测 trace 场景（拒答）**

启动服务，query="阿里巴巴"，复现 trace 场景。预期：
- 回答文本正常流式输出（不再是 filtered 日志）
- 回答为"未在文档中找到相关数据…"（abstention）
- 无 citation 事件，前端引用栏为空
- 有 abstention 状态提示
- `add_message_async` 持久化了 abstention 文本

- [ ] **Step 4: 复测正常场景（高相关）**

query=高相关财务问题（如"腾讯2024年营收"）。预期：
- 回答含 `[n]` 标记
- 引用列表只含被引用来源，且条目带 `[n]` 徽标
- 回答流式正常

- [ ] **Step 5: 复测问候场景**

query="你好"。预期：
- 免检索直接回答（skip_retrieval），不受 abstention 影响
- 不检索（或检索但不进 RAG 生成）

- [ ] **Step 6: 提交（如测试中发现修复）**

```bash
git add -A
git commit -m "fix: verification fixes"
```

---

## Self-Review 记录

**Spec 覆盖：**
- 问题 1（token 丢失）→ Task 1 ✓
- 问题 2（引用失真）→ Task 5（format_node 过滤）+ Task 7（agent_service 消费）+ Task 8（前端徽标）✓
- 问题 3（低相关门控）→ Task 4（阈值）+ Task 6（abstention）+ Task 7（事件链）✓
- skip_retrieval 问候路径 → Task 3 + Task 6 ✓
- 硬编码归位 → 各 Task 内明确常量去向（settings.py / prompts.py / const.py）✓

**关键类型一致性：**
- `SSECitationEvent.index: int = 0`（Task 2）→ Task 7 发送 `index=c.get("index", 0)` ✓ → Task 8 读取 `data.index` ✓
- `AgentState.skip_retrieval: bool`（Task 3）→ Task 6 `if state.skip_retrieval:` ✓
- `ABSTENTION_TEXT`（Task 6）→ Task 7 导入使用 ✓
- `formatted_citations` 初始化为 `None`，Format CHAIN_END 赋 `[]` 或列表 ✓
- `RERANK_MIN_SCORE`（Task 4）→ retrieval.py 导入 ✓

**Grilling 决议落实情况：**
- 拷问 1（`add_message_async` 断言）→ Task 7 测试已改为 `assert_any_call("session1", "assistant", ABSTENTION_TEXT)` ✓
- 拷问 2（LLM 自述拒答不显示状态提示）→ 状态提示仅在 `not full_answer` 时发，LLM 自述场景不发 ✓
- 拷问 3（fallback 测试 mock）→ 已实证 `with_retry(func=None, ...)` 签名 + `retrieval.py:11` import 可 patch ✓
- 拷问 4（Naive RAG 删除副作用）→ Task 6 补充行为变更说明（`rerank_empty` 标记删除，状态透出由 Task 7 承担）✓
- `formatted_citations` 三态语义（None=未捕获/[]=明确无引用/list=过滤后）在 Task 7 代码中统一 ✓
