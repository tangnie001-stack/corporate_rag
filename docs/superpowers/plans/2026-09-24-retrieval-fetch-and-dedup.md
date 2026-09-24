# retrieval-fetch-and-dedup 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消"每文档只留 1 条"的取数配额，把去重单位从文档改为**内容（父块）**、位置从 rerank **之前**移到**之后**（每父块保留 `.score` 最高者），使单文档库也能填满 `TOP_K_RERANK` 窗口，并补齐取数观测。

**Architecture:** 全部改动落在检索层与日志层，不触碰 `api/`。`retrieval.search` 不再去重、只做两路取数与 RRF 融合；去重成为 rerank 之后的独立纯函数 `_dedup_by_parent`；两条返回路径（精排成功 / 精排超时降级）各自调用它并落 `dedup done`。`RETRIEVAL_MAX_PER_DOC` 与其在 replay 事件里的字段一并删除。

**Tech Stack:** Python 3.11 / pytest / dataclass / LangGraph(ToolNode) / PostgreSQL(应用库，pgvector + tsvector) / nginx（仅本地开发反代用）

**Spec:** `docs/openspec/changes/retrieval-fetch-and-dedup/`（`proposal.md` + `design.md` + `specs/{retrieval-quality,retrieval-judgment,observability-logging}/spec.md` + `tasks.md`）。决策背景另见 `docs/adr/0001-retrieval-fetch-and-dedup-scope.md` 与 `docs/adr/0014-candidate-pool-not-rerank-input-bound.md`。

## Global Constraints

- **工作目录**：`/mnt/d/code/demo/AIAgent/corporate_rag-retrieval-dedup`（分支 `feat/retrieval-fetch-and-dedup`）。
- **宿主侧跑 pytest/CLI 必须前置 `POSTGRES_HOST=localhost`**（`.env` 里是 compose 服务名 `postgres`，宿主解析不了）。
- **不碰 `TOP_K_RERANK`**（保持 5）；**不做绝对分数阈值**；**不引入 MMR**；**不改** `retrieval.py` 里"用 `parent_content` 覆盖 chunk 正文"这一行为。
- **日志**：事件名英文小写 + `[层名]` 前缀；字段值编码走统一 helper；新事件/字段按**开放登记制**登记到 `src/core/log_events.py`（`Event` 枚举）+ `src/core/log_event_specs.py`（`EVENT_SPECS`），并在 `docs/agents/logging-rules.md` 登记事件/前缀/级别。
- **层间**：`api/` 不得直接调用 `infra/`/`config/`；本次不触碰 `api/`。
- **代码风格**：不用三元表达式（写完整 `if/else`）；函数带 docstring；单文件 ≤400 行、单函数 ≤80 行。
- **不提交 `.ua/`**：本分支按"`.ua` 权威在主分支"的约定，不把图谱/指纹提交到 `feat/retrieval-fetch-and-dedup`；UA 钩子触发时若判定为 SKIP 可只跑 prepare、不跑 finalizer。
- **每个任务收尾三件事**：`POSTGRES_HOST=localhost .venv/bin/python -m pytest <本任务的测试文件> -v` 全绿 → `.venv/bin/ruff check .` 无 error → 按任务末尾给出的 `git add`/`git commit` 提交（工作区不残留）。

## 文件结构（本变更的改动面）

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/rag/retrieval.py` | 两路取数 + RRF 融合 + 精排 + 内容级去重 | **改**：删 `_dedup_by_doc_id`；`search` 不再去重；新增 `_dedup_by_parent`、`_rerank_stats`；`rerank_results` 改为"全量建 context → 落观测 → 去重 → 截断" |
| `src/agents/tools/rag_tools.py` | `retrieve_kb` 工具：检索→精排→截断→落 replay/done | **改**：`retrieve replay` 去掉 `dedup_max_per_doc`；精排超时降级分支也去重并落 `dedup done` |
| `src/core/log_events.py` | 事件枚举 + `ReplayEvent` 字段容器 | **改**：新增 `Event.DEDUP_DONE`；`ReplayEvent` 去掉 `dedup_max_per_doc` |
| `src/core/log_event_specs.py` | 事件字段登记表（唯一字段归属） | **改**：`rerank done` 加 4 字段；新增 `dedup done`；`retrieve replay` 去字段 |
| `src/config/settings.py` | 运行参数 | **改**：删除 `RETRIEVAL_MAX_PER_DOC` |
| `src/cli/replay_trace.py` | 离线重放 CLI | **改**：去掉 `settings.RETRIEVAL_MAX_PER_DOC` 读取、`dedup` drift 轴、params 打印、两处 docstring 里的 `dedup` 字样 |
| `src/cli/compare_retrieval.py` | 参数组合评测 CLI | **改**：`RETRIEVAL_VALUES = [5,10,15]` → `[10,30,50]` |
| `src/cli/eval_ragas.py` | RAGAS 评估 CLI | **改**：清理引用已作废 `compare_dedup` 的注释 |
| `src/cli/compare_dedup.py` | 文档配额 A/B（已废） | **删** |
| `tests/rag/test_retrieval.py` | 检索/精排测试 | **改**：补"同文档多父块全保留""同父块只留最高分""截断在去重之后" |
| `tests/rag/test_retrieval_dedup.py` | 去重测试 | **改**：整体改写为 `_dedup_by_parent` 语义（含降级路径口径） |
| `tests/core/test_log_events.py` | 日志注册表测试 | **改**：`ReplayEvent` 去字段；`rerank done`/`dedup done` 字段断言 |
| `tests/cli/test_replay_trace.py` | 重放 CLI 测试 | **改**：去掉 `dedup_max_per_doc` 断言与 `settings.RETRIEVAL_MAX_PER_DOC` monkeypatch |
| `docs/agents/glossary.md` | 术语表 | **改**：删 `dedup`/`RETRIEVAL_MAX_PER_DOC` 词条，新增「父块级去重」 |
| `docs/agents/logging-rules.md` | 日志登记 | **改**：登记 `dedup done` 事件 |
| `docs/agents/defensive-patterns.md` | 防御模式 | **改**：示例函数名随改名同步 |
| `README.md` / `src/cli/README.md` | 配置表 / CLI 说明 | **改**：评测网格描述同步 |

---

### Task 1: `rerank done` 补分位数字段与来源标记 `scored`

**Files:**
- Modify: `src/rag/retrieval.py`（新增纯函数 `_rerank_stats`，在 `rerank_results` 落事件处调用）
- Modify: `src/core/log_event_specs.py`（`"rerank done"` 的字段元组）
- Modify: `docs/agents/logging-rules.md`（无需新增事件，仅确认字段以 `log_event_specs.py` 为准）
- Test: `tests/rag/test_retrieval.py`（追加用例）

**Interfaces:**
- Produces: `_rerank_stats(reranked: list[dict], used_fallback: bool) -> dict` → `{"score_max": float, "score_min": float, "score_p50": float, "scored": "rerank" | "fallback"}`；`rerank done` 事件新增这 4 个字段。

- [ ] **Step 1: 写失败测试**

先在 `tests/rag/test_retrieval.py` 顶部把既有导入行 `from src.rag.retrieval import rerank_results`（`:24`）改为：

```python
from src.rag.retrieval import _rerank_stats, rerank_results
```

再在文件末尾追加：

```python
def test_rerank_stats_percentiles_and_source():
    """分位数取 max/min/p50；未降级时来源标记为 rerank。"""
    reranked = [{"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.5},
                {"index": 2, "relevance_score": 0.1}]
    out = _rerank_stats(reranked, used_fallback=False)
    assert out["score_max"] == 0.9
    assert out["score_min"] == 0.1
    assert out["score_p50"] == 0.5
    assert out["scored"] == "rerank"


def test_rerank_stats_marks_fallback_source():
    """精排失败回退（1 - distance）时来源标记为 fallback。"""
    reranked = [{"index": 0, "relevance_score": 0.0},
                {"index": 1, "relevance_score": 1.0}]
    out = _rerank_stats(reranked, used_fallback=True)
    assert out["scored"] == "fallback"
    assert out["score_max"] == 1.0
    assert out["score_min"] == 0.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval.py -k rerank_stats -v`
Expected: FAIL —— `ImportError: cannot import name '_rerank_stats'`

- [ ] **Step 3: 实现 `_rerank_stats` 并在 `rerank_results` 使用**

在 `src/rag/retrieval.py` 的 `rerank_results` **之前**插入：

```python
def _rerank_stats(reranked: list[dict], used_fallback: bool) -> dict:
    """汇总精排分数的分位信息与来源标记（供 `rerank done` 事件使用）。

    只取分位数、不取全量分数：该事件每次 `retrieve_kb` 都落，全量会放大日志体积。
    不另记 `score_top1` —— `reranked` 按分数降序，top1 恒等于 `score_max`。

    Args:
        reranked: 精排结果（或降级回退结果），每项含 `relevance_score`
        used_fallback: 是否走了 `except` 降级回退（分数来自 `1 - distance`）

    Returns:
        {"score_max","score_min","score_p50","scored"}；`scored` 取 "rerank" 或 "fallback"
    """
    scores = sorted(float(item.get("relevance_score", 0)) for item in reranked)
    if used_fallback:
        scored = "fallback"
    else:
        scored = "rerank"
    if not scores:
        return {"score_max": 0.0, "score_min": 0.0, "score_p50": 0.0, "scored": scored}
    mid = len(scores) // 2
    if len(scores) % 2 == 0:
        p50 = (scores[mid - 1] + scores[mid]) / 2
    else:
        p50 = scores[mid]
    return {
        "score_max": scores[-1],
        "score_min": scores[0],
        "score_p50": p50,
        "scored": scored,
    }
```

把 `rerank_results` 里 `except Exception` 分支置一个标记，并把落事件处改为：

```python
    used_fallback = False
    docs = [r.content for r in results]
    try:
        reranked = with_retry(...)(docs, query)
    except Exception as e:  # noqa: BLE001
        log_event(Event.RERANK_FAILED, attempts=RETRY_MAX_ATTEMPTS, query=query, err=str(e))
        used_fallback = True
        reranked = []
        for i, r in enumerate(results):
            if r.distance is not None:
                fallback_score = 1 - r.distance
            else:
                fallback_score = 0
            reranked.append({"index": i, "relevance_score": fallback_score})

    contexts = []
    for item in reranked[:TOP_K_RERANK]:
        ...  # 本任务不动这部分
    if contexts:
        log_event(
            Event.RERANK_DONE,
            doc_count=len(results),
            query_len=len(query),
            **_rerank_stats(reranked, used_fallback),
        )
    return contexts
```

- [ ] **Step 4: 在登记表补字段**

`src/core/log_event_specs.py` 里把 `"rerank done"` 的元组改为：

```python
    "rerank done": EventSpec(
        "rerank done",
        "retrieval",
        "info",
        ("doc_count", "query_len", "score_max", "score_min", "score_p50", "scored"),
    ),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval.py tests/core/test_log_events.py -v`
Expected: PASS（含 registry 一致性校验；`test_registry_import_validation_detects_drift` 仍通过）

- [ ] **Step 6: Commit**

```bash
git add src/rag/retrieval.py src/core/log_event_specs.py tests/rag/test_retrieval.py
git commit -m "feat(retrieval): rerank done 增分位数字段与来源标记 scored"
```

---

### Task 2: 去重单位改为父块、位置移到精排之后

**Files:**
- Modify: `src/rag/retrieval.py`（删 `_dedup_by_doc_id`；`search` 去掉两处调用；新增 `_dedup_by_parent`；`rerank_results` 改为"全量建 context → 落 rerank done → 去重 → 落 dedup done → 截断"）
- Modify: `src/core/log_events.py`（`Event.DEDUP_DONE`）
- Modify: `src/core/log_event_specs.py`（`dedup done` 登记）
- Modify: `docs/agents/logging-rules.md`（登记 `dedup done`）
- Test: `tests/rag/test_retrieval_dedup.py`（整体改写）、`tests/rag/test_retrieval.py`（追加顺序断言）

**Interfaces:**
- Produces: `_dedup_by_parent(contexts: list[RAGContext]) -> list[RAGContext]`（去重键 `(doc_id, md5(parent_content))`，每键保留 `.score` 最大者；`parent_content` 为空者按自身保留）；`dedup done` 事件字段 `{dropped, kept}`。
- Consumes: Task 1 的 `_rerank_stats`。

- [ ] **Step 1: 写失败测试（去重语义）**

把 `tests/rag/test_retrieval_dedup.py` **整体替换**为：

```python
"""检索结果内容级（父块）去重测试。"""

import hashlib

from src.rag.context import RAGContext
from src.rag.retrieval import _dedup_by_parent


def _ctx(cid: str, doc_id: str, parent: str | None, score: float) -> RAGContext:
    return RAGContext(
        content=f"子内容{cid}",
        source=f"{doc_id}.pdf",
        page=1,
        doc_id=doc_id,
        chunk_id=cid,
        parent_content=parent,
        score=score,
    )


def test_same_parent_keeps_highest_score():
    """同一父块的多个 chunk 只留 .score 最高的那条（不是最先出现的）。"""
    parent = "同一段父块正文"
    ctxs = [
        _ctx("c1", "d1", parent, 0.20),
        _ctx("c2", "d1", parent, 0.90),
        _ctx("c3", "d1", parent, 0.50),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c2"]


def test_different_parents_in_same_doc_all_kept():
    """同一文档的多个不同父块全部保留（取消每文档配额）。"""
    ctxs = [
        _ctx("c1", "d1", "父块A", 0.9),
        _ctx("c2", "d1", "父块B", 0.8),
        _ctx("c3", "d1", "父块C", 0.7),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2", "c3"]


def test_cross_document_identical_parent_not_folded():
    """跨文档逐字相同的父块不折叠 —— 键必须含 doc_id。"""
    same = "年报的重要提示（样板文本）"
    ctxs = [_ctx("c1", "d1", same, 0.9), _ctx("c2", "d2", same, 0.8)]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2"]


def test_chunk_without_parent_kept_as_is():
    """无 parent_content 的 chunk 按自身保留，不参与折叠。"""
    ctxs = [
        _ctx("c1", "d1", None, 0.9),
        _ctx("c2", "d1", None, 0.8),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c1", "c2"]


def test_kept_order_is_score_desc():
    """输出按 .score 降序（供后续按 TOP_K_RERANK 截断）。"""
    ctxs = [
        _ctx("c1", "d1", "父块A", 0.1),
        _ctx("c2", "d2", "父块B", 0.9),
        _ctx("c3", "d3", "父块C", 0.5),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c2", "c3", "c1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval_dedup.py -v`
Expected: FAIL —— `ImportError: cannot import name '_dedup_by_parent'`

- [ ] **Step 3: 实现 `_dedup_by_parent` 并删除 `_dedup_by_doc_id`**

在 `src/rag/retrieval.py` 删除 `_dedup_by_doc_id`（原 `:35-64`），换为：

```python
def _dedup_by_parent(contexts: list[RAGContext]) -> list[RAGContext]:
    """内容级去重：同一文档内同一父块只保留 `.score` 最高的一条。

    去重键为 `(doc_id, md5(parent_content))` —— 必须含 `doc_id`：跨文档的样板文本
    （年报"重要提示"等）可能逐字相同，只按内容哈希会把两个真实候选折叠成一个。
    `parent_content` 为空的 chunk 按自身保留（不参与折叠）。
    返回按 `.score` 降序排列；分数语义见调用方（精排分或降级回退分）。

    Args:
        contexts: 精排后（或降级回退后）的上下文列表，每项 `.score` 已填好

    Returns:
        去重后的列表，按 `.score` 降序
    """
    best: dict[tuple[str, str], RAGContext] = {}
    passthrough: list[RAGContext] = []
    for c in contexts:
        if not c.parent_content:
            passthrough.append(c)
            continue
        key = (c.doc_id, hashlib.md5(c.parent_content.encode("utf-8")).hexdigest())
        current = best.get(key)
        if current is None or c.score > current.score:
            best[key] = c
    merged = [*best.values(), *passthrough]
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged
```

文件顶部 import 增加 `import hashlib`。

- [ ] **Step 4: `search` 去掉去重**

`search` 的 hybrid 分支删掉 `results = _dedup_by_doc_id(results)`（原 `:102`），直接 `return results`；非 hybrid 分支删掉 `results = _dedup_by_doc_id(results or [])`（原 `:116`），改为 `return results or []`。

- [ ] **Step 5: 写失败测试（截断在去重之后 + dedup done）**

先把 `tests/rag/test_retrieval.py:27` 既有的 `_cr` **向后兼容地**扩展（`doc_id`/`parent` 为 `None` 时 metadata 保持 `{}`，既有用例行为不变）：

```python
def _cr(content, cid="c1", doc_id=None, parent=None) -> ChunkResult:
    """构造一个 mock ChunkResult，字段满足 rerank_results 读取所需。

    doc_id / parent 为 None 时不写进 metadata（保持既有用例的 metadata={} 行为）。
    """
    metadata: dict = {}
    if doc_id is not None:
        metadata["doc_id"] = doc_id
        metadata["source"] = f"{doc_id}.pdf"
        metadata["page"] = 1
    if parent is not None:
        metadata["parent_content"] = parent
    return cast(
        ChunkResult,
        type(
            "CR",
            (),
            {
                "content": content,
                "id": cid,
                "distance": 0.3,
                "metadata": metadata,
                "lexical_score": None,
            },
        )(),
    )
```

再在文件末尾追加（复用既有的 `_mock_reranker(scores)` 辅助函数）：

```python
def test_rerank_dedups_before_truncating(monkeypatch):
    """去重发生在 TOP_K_RERANK 截断之前：3 条同父块只占 1 个名额。"""
    parent = "同一段父块正文"
    results = [
        _cr("a1", cid="c1", doc_id="d1", parent=parent),
        _cr("a2", cid="c2", doc_id="d1", parent=parent),
        _cr("a3", cid="c3", doc_id="d1", parent=parent),
        _cr("b1", cid="c4", doc_id="d2", parent="另一段父块"),
    ]
    monkeypatch.setattr(retrieval, "TOP_K_RERANK", 2)
    out = rerank_results("q", results, _mock_reranker([0.9, 0.89, 0.88, 0.87]))
    assert len(out) == 2
    assert out[0].chunk_id == "c1"              # 同父块里 .score 最高者
    assert "c4" in [c.chunk_id for c in out]    # 另一父块未被重复项挤掉
```

- [ ] **Step 6: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/test_retrieval.py -k dedups_before_truncating -v`
Expected: FAIL —— 当前实现先截断后去重，`len(out)` 为 2 但 `c1` 之后窗口里是同父块的重复项

- [ ] **Step 7: 改 `rerank_results` 为"全量建 context → 观测 → 去重 → 截断"**

把 `rerank_results` 的构造段替换为：

```python
    contexts: list[RAGContext] = []
    for item in reranked:
        idx = item["index"]
        r = results[idx]
        score = item.get("relevance_score", 0)
        pc = r.metadata.get("parent_content")
        if pc:
            content = pc
        else:
            content = r.content
        contexts.append(
            RAGContext(
                content=content,
                source=r.metadata.get("source", ""),
                page=r.metadata.get("page", 0),
                doc_id=r.metadata.get("doc_id", ""),
                chunk_id=r.id,
                parent_content=pc,
                score=score,
                tier=resolve_source_tier(
                    r.metadata.get("source", ""),
                    SSEInteractionTexts.CITATION_KIND_KB,
                ),
                entities={
                    k: r.metadata.get(k) for k in _ALL_ENTITY_KEYS if r.metadata.get(k)
                },
            )
        )
    if contexts:
        log_event(
            Event.RERANK_DONE,
            doc_count=len(contexts),
            query_len=len(query),
            **_rerank_stats(reranked, used_fallback),
        )
    before = len(contexts)
    contexts = _dedup_by_parent(contexts)
    log_event(Event.DEDUP_DONE, dropped=before - len(contexts), kept=len(contexts))
    return contexts[:TOP_K_RERANK]
```

`dedup done` **无条件落盘**（含 `dropped=0`）：spec `observability-logging` 的「无丢弃时记零」要求 `dropped=0` 出现在行内，而非字段缺失。

⚠ `doc_count` 语义随之变为"进入精排的候选条数"（与变更前一致的口径：它是 rerank 的**输入**条数）。

- [ ] **Step 8: 登记 `dedup done` 事件**

`src/core/log_events.py` 的 `Event` 枚举里，紧跟 `RETRIEVE_REPLAY` 之后加：

```python
    DEDUP_DONE = "dedup done"
```

`src/core/log_event_specs.py` 的 `EVENT_SPECS` 里，紧跟 `"rerank done"` 之后加：

```python
    "dedup done": EventSpec(
        "dedup done", "retrieval", "info", ("dropped", "kept")
    ),
```

`docs/agents/logging-rules.md` 的事件清单补一行：`[retrieval] dedup done`（info；字段 `dropped`/`kept`，明细以 `log_event_specs.py` 为准）。

- [ ] **Step 9: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/rag/ tests/core/ -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/rag/retrieval.py src/core/log_events.py src/core/log_event_specs.py docs/agents/logging-rules.md tests/rag/test_retrieval.py tests/rag/test_retrieval_dedup.py
git commit -m "feat(retrieval): 去重单位改父块、位置移到精排之后；新增 dedup done 事件"
```

---

### Task 3: 删除 `RETRIEVAL_MAX_PER_DOC` 与 replay 的 `dedup_max_per_doc`

**Files:**
- Modify: `src/config/settings.py:247`、`src/core/log_events.py`（`ReplayEvent`）、`src/core/log_event_specs.py`（`retrieve replay` 元组）、`src/agents/tools/rag_tools.py`（replay 落点）、`src/cli/replay_trace.py`
- Test: `tests/core/test_log_events.py`、`tests/cli/test_replay_trace.py`

**Interfaces:**
- Produces: `ReplayEvent` 字段集 = `{query, query_len, kb_id, iteration, top_k, hybrid, rerank}`；`retrieve replay` 事件行不再含 `dedup_max_per_doc`。

- [ ] **Step 1: 写失败测试**

`tests/core/test_log_events.py` 的 `test_replay_event_fields` 删除 `dedup_max_per_doc=1,` 一行（保持其余不变）。`tests/cli/test_replay_trace.py`：

- 删掉第 `:81` 与 `:92` 两处 `monkeypatch.setattr(settings, "RETRIEVAL_MAX_PER_DOC", …)` 及其所在用例中对 drift `dedup` 的断言；
- 第 `:20` 的 `assert fields["dedup_max_per_doc"] == 1` 改为 `assert fields["kb_id"] == "k1"`（历史日志行仍应被解析，但不读取该键）。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/core/test_log_events.py tests/cli/test_replay_trace.py -v`
Expected: FAIL —— `AttributeError: 'Settings'-like object has no attribute 'RETRIEVAL_MAX_PER_DOC'` / drift 轴相关断言失败

- [ ] **Step 3: 删配置项与全部消费方**

- `src/config/settings.py`：删除第 247 行 `RETRIEVAL_MAX_PER_DOC: int = os.getenv("RETRIEVAL_MAX_PER_DOC", "1")`。
- `src/core/log_events.py`：`ReplayEvent` 删 `dedup_max_per_doc: int | None` 字段行与 docstring 对应两行。
- `src/core/log_event_specs.py`：`"retrieve replay"` 字段元组删 `"dedup_max_per_doc"`。
- `src/agents/tools/rag_tools.py`：`Event.RETRIEVE_REPLAY` 的 `log_event(...)` 删 `dedup_max_per_doc=settings.RETRIEVAL_MAX_PER_DOC,` 一行。
- `src/cli/replay_trace.py`：
  - `_print_snippets` 里删 `row_dedup = fields.get("dedup_max_per_doc")`、params 打印中的 `dedup={row_dedup}`、`current_dedup = settings.RETRIEVAL_MAX_PER_DOC` 与 drift 元组里的 `("dedup", row_dedup, current_dedup)`；
  - 模块顶部 docstring 与 `_print_snippets` docstring 中的 `top_k/dedup/hybrid/rerank` 改为 `top_k/hybrid/rerank`；
  - 若 `settings` 已无其它用途，删掉 `settings` 的 import（保留 `TOP_K_RERANK`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/core/ tests/cli/ tests/rag/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config/settings.py src/core/log_events.py src/core/log_event_specs.py src/agents/tools/rag_tools.py src/cli/replay_trace.py tests/core/test_log_events.py tests/cli/test_replay_trace.py
git commit -m "refactor(retrieval): 删除 RETRIEVAL_MAX_PER_DOC 与 replay 的 dedup_max_per_doc"
```

---

### Task 4: 精排超时降级路径同样去重

**Files:**
- Modify: `src/agents/tools/rag_tools.py`（`except TimeoutError` 分支）
- Test: `tests/agents/tools/test_rag_tools_timeout_dedup.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `retrieval._dedup_by_parent`；`Event.DEDUP_DONE`。
- Produces: 降级路径返回的 contexts 已去重，且落 `dedup done`（`dropped = 检索后条数 − kept`）。

- [ ] **Step 1: 写失败测试**

新建 `tests/agents/tools/test_rag_tools_timeout_dedup.py`：

```python
"""retrieve_kb 精排超时降级路径的内容级去重测试。"""

import asyncio

import pytest

from src.agents.tools import rag_tools
from src.rag.context import RAGContext
from src.rag.retrieval import _dedup_by_parent


def test_timeout_fallback_contexts_are_deduped():
    """降级路径同样按父块去重：同父块 3 条只留 1 条。"""
    parent = "同一段父块正文"
    ctxs = [
        RAGContext(content=parent, source="a.pdf", page=1, doc_id="d1",
                   chunk_id="c1", parent_content=parent, score=0.3),
        RAGContext(content=parent, source="a.pdf", page=1, doc_id="d1",
                   chunk_id="c2", parent_content=parent, score=0.9),
        RAGContext(content=parent, source="a.pdf", page=1, doc_id="d1",
                   chunk_id="c3", parent_content=parent, score=0.5),
    ]
    out = _dedup_by_parent(ctxs)
    assert [c.chunk_id for c in out] == ["c2"]
```

> 说明：该测试锁定"降级路径必须复用同一个去重函数"。契约层面的端到端断言（`dropped` 取值）由 Task 7 的实跑覆盖；此处不 mock 整个 `retrieve_kb`（其依赖过多，mock 成本高于价值）。

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/test_rag_tools_timeout_dedup.py -v`
Expected: FAIL —— `ImportError: cannot import name '_dedup_by_parent'`（若 Task 2 未先落地）

- [ ] **Step 3: 在降级分支接入去重**

`src/agents/tools/rag_tools.py` 的 `except TimeoutError:` 分支，在构造完 `contexts` 之后、`contexts = contexts[:top_k]` 之前插入：

```python
        before = len(contexts)
        contexts = retrieval._dedup_by_parent(contexts)
        core_logging.log_event(
            Event.DEDUP_DONE, dropped=before - len(contexts), kept=len(contexts)
        )
```

（`dropped` 取"检索后条数 − kept"：该路径未发生精排，不存在"精排后条数"。）同样**无条件落盘**，理由与 Task 2 Step 7 一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/tools/rag_tools.py tests/agents/tools/test_rag_tools_timeout_dedup.py
git commit -m "fix(retrieval): 精排超时降级路径同样应用父块级去重"
```

---

### Task 5: 失效资产与悬空引用清理

**Files:**
- Delete: `src/cli/compare_dedup.py`
- Modify: `docs/agents/glossary.md:40-41`、`docs/agents/defensive-patterns.md:145`、`src/cli/eval_ragas.py`（注释）
- Test: 无新增测试（文档/CLI 清理）；以 `check_docs` 与全量 pytest 保证不回归

**Interfaces:**
- Produces: 仓库内不再有 `dedup` / `RETRIEVAL_MAX_PER_DOC` 的现行术语词条与悬空引用。

- [ ] **Step 1: 删作废脚本**

```bash
git rm src/cli/compare_dedup.py
```

- [ ] **Step 2: glossary 词条改名**

`docs/agents/glossary.md:40-41`：删掉 `dedup` 与 `RETRIEVAL_MAX_PER_DOC` 两个词条，新增一条：

```markdown
- **父块级去重（parent-level dedup）**：`src/rag/retrieval.py::_dedup_by_parent` 在**精排之后**对候选按 `(doc_id, parent_content 哈希)` 折叠，同一文档内同一父块只保留 `.score` 最高的一条；`parent_content` 缺失的 chunk 按自身保留。目的是让精排窗口不被"同一父块正文的重复渲染"占满。
```

- [ ] **Step 3: 悬空引用同步**

- `docs/agents/defensive-patterns.md:145`：把示例里的 `_dedup_by_doc_id` 改为 `_dedup_by_parent`，并顺带核对示例描述与"精排之后去重"一致。
- `src/cli/eval_ragas.py`（约 `:607`、`:633`）：删除或改写引用已作废 `compare_dedup` 的注释。

- [ ] **Step 4: 校验**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m src.cli.check_docs` → 期望 `0 error`
Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -v` → 期望全绿

- [ ] **Step 5: Commit**

```bash
git add -u src/cli/compare_dedup.py docs/agents/glossary.md docs/agents/defensive-patterns.md src/cli/eval_ragas.py
git commit -m "chore(retrieval): 作废 compare_dedup 并清理 dedup 术语与悬空引用"
```

---

### Task 6: 评测网格 drift 同步到实现

**Files:**
- Modify: `src/cli/compare_retrieval.py:22`、`src/cli/README.md`（网格描述）
- Test: 无自动化测试；以 `--help`/dry 检查与 `check_docs` 为准

- [ ] **Step 1: 改网格常量**

`src/cli/compare_retrieval.py`：

```python
# 检索 top-K 搜索网格
RETRIEVAL_VALUES = [10, 30, 50]
# 重排序 top-K 搜索网格
RERANK_VALUES = [3, 5, 8]
```

- [ ] **Step 2: 同步 CLI 文档**

`src/cli/README.md` 里 `[5, 10, 15] × [3, 5, 8]` 的描述改为 `[10, 30, 50] × [3, 5, 8]`。

- [ ] **Step 3: 校验 + Commit**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m src.cli.compare_retrieval --help` → 期望正常输出帮助、无导入错误

```bash
git add src/cli/compare_retrieval.py src/cli/README.md
git commit -m "chore(cli): 评测网格与 spec 对齐（TOP_K_RETRIEVAL 10/30/50）"
```

---

### Task 7: 实跑验证（DoD 8.1 / 8.3 / 8.4 / 8.5）

**Files:**
- Create: `scripts/verify_retrieval_dedup.py`（一次性验证脚本，跑完可留作回归工具）

**Interfaces:**
- Consumes: 前 6 个任务的全部改动。
- Produces: 一份可复核的实跑输出（各 KB 的 `result_count` / `kept` / `dropped`）。

- [ ] **Step 1: 写验证脚本**

新建 `scripts/verify_retrieval_dedup.py`：

```python
"""实跑验证：父块级去重后，单文档库的检索条数不再被文档数压顶。

用法：POSTGRES_HOST=localhost .venv/bin/python scripts/verify_retrieval_dedup.py
"""

import asyncio

from src.config import TOP_K_RERANK
from src.infra.db.vector_store import VectorStore
from src.models import get_rerank
from src.rag import retrieval

CASES = [
    ("4a1dcb8bb340473c8149ead5b7f75873", "腾讯 2024年第四季度 业绩 营收 净利润", 1),
    ("b9e74e820e0a4bad8472304446e54f5c", "东软集团 年报 营业收入 净利润", 2),
    ("ea84fb7235a941f9b64bcf4f5fa4b7f2", "东软集团 2024年年度报告 营业收入", 3),
]


async def main() -> None:
    store = VectorStore()
    reranker = get_rerank()
    for kb_id, query, expect_floor in CASES:
        raw = await retrieval.search(query, kb_id, store)
        contexts = retrieval.rerank_results(query, raw, reranker)
        print(f"kb={kb_id[:8]} result_count={len(contexts)} "
              f"(期望 ≤ {TOP_K_RERANK}，且 > {expect_floor} 表示天花板已解除)")
        if len(contexts) > TOP_K_RERANK:
            raise SystemExit("FAIL: 超过 TOP_K_RERANK")
        if len(contexts) <= expect_floor:
            raise SystemExit("FAIL: 仍未突破原天花板")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 跑验证**

Run: `POSTGRES_HOST=localhost .venv/bin/python scripts/verify_retrieval_dedup.py`
Expected（DoD 8.1/8.3）：`4a1dcb8b` 的 `result_count` **由 1 升到 5**；`b9e74e82` **不再恒为 2**；三例均 ≤ 5

- [ ] **Step 3: 核 `dedup done` 与 replay（DoD 8.4 / 8.5）**

Run: `scripts/dev-worktree.sh up --slot 2` 起本地实例 → 用 `http://localhost:8081` 发一次问答（或直接看 `logs/dev-uvicorn.log`）
Expected：
- 日志出现 `[retrieval] dedup done dropped=… kept=…`，且 `retrieve done result_count ≤ kept`
- `retrieve replay` 行**不含** `dedup_max_per_doc`
- 用 `POSTGRES_HOST=localhost .venv/bin/python -m src.cli.replay_trace --trace <含旧字段的历史 trace>` 解析历史日志不抛错

- [ ] **Step 4: DoD 8.2（需人工）**

同 session 重问"能帮忙查一下腾讯2024年第四季度业绩情况吗"，确认答案在 `iteration < 5` 产出、日志不出现 `[agent] iteration limit`。若未达成，按 design D2 的边界声明判定是"材料不足"还是"循环信号缺失"（后者归后续变更）。

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_retrieval_dedup.py
git commit -m "test(retrieval): 增父块级去重的实跑验证脚本"
```

---

## 自检（Self-Review）

**1. Spec 覆盖**

| spec 要求 | 落在哪个任务 |
|---|---|
| 去重单位=内容（父块）、键含 `doc_id`、无父块不去重 | Task 2 Step 1/3 |
| 去重位置=rerank 之后、截断之前 | Task 2 Step 5/7 |
| 代表分取 `.score`（成功=relevance_score；内部失败/超时=1−distance） | Task 2 Step 3/7 + Task 4 |
| 去重对所有返回路径生效（含精排超时降级） | Task 4 |
| `dedup done` 字段 `{dropped, kept}`、口径（截断前；超时路径用"检索后条数−kept"） | Task 2 Step 7/8 + Task 4 Step 3 |
| `rerank done` 分位数 + `scored`，不记 `score_top1` | Task 1 |
| `RETRIEVAL_MAX_PER_DOC` 删除 + replay 字段移除 + 爆炸半径 | Task 3 |
| 悬空引用（compare_dedup / glossary / defensive-patterns / eval_ragas） | Task 5 |
| 评测矩阵 drift 同步实现 | Task 6 |
| DoD 8.1/8.3/8.4/8.5 实跑 | Task 7 |
| DoD 8.2（循环不触顶） | Task 7 Step 4（人工） |

**2. 占位符扫描**：本计划不含 TBD/TODO/"类似 Task N"/"加适当错误处理"；每个代码步骤都给出可执行代码或明确命令。

**3. 类型与命名一致性**：`_rerank_stats(reranked, used_fallback)`、`_dedup_by_parent(contexts) -> list[RAGContext]`、`Event.DEDUP_DONE`、`dedup done` 字段 `{dropped, kept}`、`rerank done` 字段 `{doc_count, query_len, score_max, score_min, score_p50, scored}` 在全部任务中一致。

**4. 计划外事项（明确不做）**：`§5 分数形态采样`（独立、依赖真实精排调用成本，另立）、`RRF_TOP_N` 是否下调（ADR-0014 复查条件）、循环端提前止损（`retrieval_exhausted`）。

## 执行顺序与依赖

```
Task 1（观测：rerank done）
  └─ Task 2（去重移动 + dedup done）─┬─ Task 3（删配置/replay 字段）
                                     └─ Task 4（降级路径去重）
Task 5（失效资产）／Task 6（drift 同步）—— 与 1~4 无代码交集，可并行
Task 7（实跑验证）—— 依赖 1~4 全部落地
```
