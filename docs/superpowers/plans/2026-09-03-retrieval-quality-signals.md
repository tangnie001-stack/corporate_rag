# retrieval-quality-signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在线采集 agent 检索行为信号（reretrieve / to_web / abstain_after_retrieve / unsupported / cited / empty_result）作为检索质量差的判据，并将 dedup 策略参数化支持多样性 A/B 实验。

**Architecture:** 信号埋点全部落在既有代码路径，经 `src/core/logging.py` 的 `retrieval_signal()` helper 输出（依赖 logging-convention-migration）。to_web 排除 verify 指派联网用 `RequestContext.web_guided` 标记（方案 A）。态 A（未绑 KB）不产缺陷信号。dedup 参数化默认 N=1 保持现状。

**Tech Stack:** Python 3.11+ / Loguru / pytest / ruff / pyright

## Global Constraints

- 依赖顺序：本 change 排在 agent-loop-hardening 之后实施（verify 已拆 `verify/` 包、`ctx.web_guided` 逻辑前置已就位）；logging-convention-migration 先行提供 `retrieval_signal()` helper
- 缺陷信号（reretrieve/to_web/abstain_after_retrieve/unsupported/empty_result）仅在 `kb_id` 非空时 emit；态 A 不产
- `to_web` 排除 verify 指派：`ctx.web_guided=True` 时 search_web 调用不产 to_web
- cited 对照基线两态都产（带 kind）
- `RETRIEVAL_MAX_PER_DOC` 默认 1，行为与现状一致
- `pytest tests/ -v` 全过；`ruff check .` 无错误；`pyright src/` 不新增 error

---

### Task 1: 埋点基础设施 — `web_guided` 标记 + cited/empty_result 信号

**Files:**
- Modify: `src/infra/llm/request_context.py`（加 `web_guided` 字段）
- Modify: `src/agents/graph/nodes.py`（format_node 埋 cited）
- Modify: `src/agents/tools/rag_tools.py`（retrieve_kb 埋 empty_result + reretrieve）
- Test: `tests/agents/graph/test_graph.py` / `tests/agents/tools/test_rag_tools.py`

**Interfaces:**
- Consumes: logging-convention-migration 的 `retrieval_signal(signal, query, iteration, **fields)`（在 `src.core.logging`）
- Produces:
  - `RequestContext.web_guided: bool = False`（verify 指派联网标记，Task 2 置位）
  - 每信号行格式：`retrieval_signal: signal={} query="{query[:40]}" iteration={} kb_id={} ...`

- [ ] **Step 1: request_context.py 加字段**

`src/infra/llm/request_context.py` RequestContext 加：

```python
    web_guided: bool = (
        False  # verify 注入联网指引驱动 search_web 的标记（来源：verify 节点注入指引时置位；用途：search_web 排除 to_web 误报 verify 指派联网）
    )
```

- [ ] **Step 2: format_node 埋 cited 信号**

`src/agents/graph/nodes.py` format_node 末尾（123 行 `return` 前）加：

```python
    # 对照基线：正常引用（kind 区分 kb/web），供检索质量诊断对照
    from src.core.logging import retrieval_signal
    from src.infra.llm.request_context import current_request_ctx

    _ctx = current_request_ctx.get()
    if _ctx is not None:
        query_text = _ctx.query if hasattr(_ctx, "query") else ""
        # RequestContext 无 query 字段时从 state 取——format_node 无 state.query，
        # 改经 capture 传递：此处取 tool_contexts 首个 kind 决定基线信号 kind
        kinds = {c.get("kind", "kb") for c in citations} or {"kb"}
        retrieval_signal(
            "cited",
            query_text or "",
            0,
            kb_id="",
            citation_count=len(citations),
            kind="|".join(sorted(kinds)),
        )
    return {"citations": citations}
```

**实现注意**：format_node 拿不到 `AgentState.query`（签名只有 state，含 query 字段——`state.query` 可用），改用 `state.query` 而非 ctx.query：

```python
    from src.core.logging import retrieval_signal

    query_text = state.query if hasattr(state, "query") else ""
    kinds = {c.get("kind", "kb") for c in citations} or {"kb"}
    retrieval_signal(
        "cited", query_text, 0, kb_id="", citation_count=len(citations), kind="|".join(sorted(kinds))
    )
```

- [ ] **Step 3: retrieve_kb 埋 empty_result + reretrieve**

`src/agents/tools/rag_tools.py` retrieve_kb 内，在结果确定后（130 行 `results = []` 与 170 行 contexts 截断之间）插埋点。需要先在 retrieve_kb 入口读 `ctx` 与调用计数：

```python
    ctx = current_request_ctx.get()
    if ctx is None:
        ctx = _NullCtx()  # 保持既有容忍
    # 同 turn 检索调用计数（reretrieve 判定）
    call_seq = getattr(ctx, "retrieve_call_seq", 0) + 1
    setattr(ctx, "retrieve_call_seq", call_seq)
```

然后检索结果确定后：

```python
        # 检索完成，埋行为信号（态 B 专属）
        from src.core.logging import retrieval_signal

        if kb_id:
            kb_state = kb_id
            if not results:
                retrieval_signal("empty_result", query, iteration, kb_id=kb_state, result_count=0)
            if call_seq >= 2:
                retrieval_signal("reretrieve", query, iteration, kb_id=kb_state, call_seq=call_seq, result_count=len(results))
```

**实现注意**：`ctx.retrieve_call_seq` 不是 RequestContext 既有字段——在 `RequestContext` 加字段更规范（Task 1 Step 1 一并加）：

```python
    retrieve_call_seq: int = (
        0  # 同 turn retrieve_kb 调用次数（来源：retrieve_kb 自增；用途：reretrieve 换词信号判定）
    )
```

然后 retrieve_kb 内用 `ctx.retrieve_call_seq += 1`。

- [ ] **Step 4: 测试（empty_result / reretrieve / 态 A 不产）**

`tests/agents/tools/test_rag_tools.py` 追加（复用既有 `_new_state` / mock search 模式；`make_rag_tools` 签名按 agent-loop-hardening C1-1 落地后 `(vector_store, bm25, reranker, prompt_manager)`）：

```python
async def test_retrieve_kb_emits_empty_result_signal(monkeypatch):
    """态 B 检索空 → empty_result 信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        captured["signal"] = signal
        captured["query"] = query
        captured["iteration"] = iteration
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)

    async def fake_search(query, kb_id, vector_store, bm25):
        return []  # 检索空

    monkeypatch.setattr(retrieval, "search", fake_search)
    tool = make_rag_tools(
        cast(VectorStore, None), None, None, cast(Any, None)
    )  # search 已 mock，依赖不会被真实调用
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        out = await tool.ainvoke({"query": "毛利率", "state": _new_state()})
        assert out == ""  # 空检索返回空串
        assert captured.get("signal") == "empty_result"
        assert captured.get("iteration") == 0
        assert captured["fields"]["kb_id"] == "kb1"
    finally:
        current_request_ctx.reset(token)


async def test_retrieve_kb_no_signal_unbound(monkeypatch):
    """态 A（未绑定 KB）检索 → 不产 empty_result/reretrieve（设计行为非缺陷）。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        captured["signal"] = signal

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)

    async def fake_search(query, kb_id, vector_store, bm25):
        return []

    monkeypatch.setattr(retrieval, "search", fake_search)
    tool = make_rag_tools(cast(VectorStore, None), None, None, cast(Any, None))
    ctx = RequestContext(session_id="s1")
    token = current_request_ctx.set(ctx)
    try:
        state = AgentState.make_initial_state("s1", "", "财务年报毛利率多少", [])
        await tool.ainvoke({"query": "毛利率", "state": state})  # kb_id="" → 态 A
        assert "signal" not in captured  # 未产缺陷信号
    finally:
        current_request_ctx.reset(token)
```

**说明**：`make_rag_tools` 在 agent-loop-hardening 落地后签名去掉 `embed_fn`（位置参数 5→4）。若两 change 并行实施，测试构造以最终签名为准（见 plan Global Constraints 依赖顺序）。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/agents/tools/test_rag_tools.py tests/agents/graph/test_graph.py -v`
Expected: 全过（新增 2 用例通过；信号格式本身已由 logging change 单测覆盖）

- [ ] **Step 6: Commit**

```bash
git add src/infra/llm/request_context.py src/agents/graph/nodes.py src/agents/tools/rag_tools.py
git commit -m "feat: 检索行为信号埋点（cited/empty_result/reretrieve + web_guided 字段）"
```

---

### Task 2: to_web 排除 verify 指派（web_guided 置位 + search_web 排除）

**Files:**
- Modify: `src/agents/graph/verify/node.py`（或 verify 包注入指引处——Task 1 决策化注入点）
- Modify: `src/agents/tools/web_tools.py`（search_web 读 web_guided 排除）
- Test: `tests/agents/graph/test_verify_node.py` / `tests/agents/tools/test_web_search.py`

**Interfaces:**
- Consumes: Task 1 的 `RequestContext.web_guided`
- Produces: verify 注入联网指引时置 `ctx.web_guided=True`；search_web 在 `web_guided=True` 时不产 to_web 缺陷信号（正常执行联网）

- [ ] **Step 1: verify 注入指引处置位**

在 `src/agents/graph/verify/`（agent-loop-hardening 后 verify 包结构）注入联网指引 SystemMessage 处（含 `VERIFY_GUIDANCE_MARKER` 的分支）加：

```python
        if ctx is not None:
            ctx.web_guided = True  # 标记 verify 指派联网（search_web 据此排除 to_web 误报）
```

（同步态 A 的 web_citation_guard 注入处如也驱动 search_web 补充，同样置位——按 agent-loop-hardening 最终结构确认。）

- [ ] **Step 2: search_web 排除 to_web**

`src/agents/tools/web_tools.py` search_web 内（校验 ctx 后、实际搜索前）加排除标记：

```python
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    # to_web 缺陷信号排除：verify 指派联网（补数据）非检索质量缺陷
    is_verify_guided = ctx.web_guided
```

在 search_web 正常返回块（原 "judge: queries=..." 日志处）替换为信号输出：

```python
    from src.core.logging import retrieval_signal
    from src.config import settings as _settings

    # 态 B（绑 KB）且非 verify 指派 → to_web 检索降级缺陷信号；态 A 纯对话不产
    if ctx.web_guided is False and getattr(ctx, "kb_bound", False):
        retrieval_signal(
            "to_web",
            ", ".join(queries),
            0,  # iteration 非关键：search_web 无 InjectedState 拿不到，传 0
            kb_id=getattr(ctx, "kb_id", ""),
            result_count=len(blocks),
            latency_ms=f"{(time.monotonic() - start) * 1000:.0f}",
        )
```

**实现注意**：search_web 需知道"是否态 B 绑 KB"——RequestContext 加 `kb_id: str = ""` 与 `kb_bound: bool = False`，由请求入口（`_run_generation` 创建 ctx 处）写入：

```python
    # agent_service 创建 RequestContext 时：ctx.kb_id = kb_id; ctx.kb_bound = bool(kb_id)
```

**iteration 简化（YAGNI）**：不引入 `agent_iteration` 字段——search_web 工具无 InjectedState 拿不到 state._agent_iterations，信号行 iteration 传 0（诊断价值在 query + kb_id + 信号类型，iteration 非必要）。latency_ms 提供性能旁证。

- [ ] **Step 3: 测试（verify 指派不产 to_web / 自主降级产）**

`tests/agents/tools/test_web_search.py` 追加：

```python
@pytest.mark.asyncio
async def test_search_web_verify_guided_no_to_web_signal(monkeypatch, ctx):
    """verify 指派联网（web_guided=True）→ 不产 to_web 缺陷信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        captured["signal"] = signal

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    ctx.web_guided = True  # verify 已指派联网

    out = await search_web.ainvoke({"queries": ["腾讯2023年报"]})

    assert out.startswith("[1] 来源: https://a.com")
    assert "signal" not in captured  # verify 指派联网不产 to_web


@pytest.mark.asyncio
async def test_search_web_autonomous_emits_to_web_signal(monkeypatch, ctx):
    """agent 自主降级调 search_web（web_guided=False）→ 产 to_web 缺陷信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        captured["signal"] = signal
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    monkeypatch.setattr(web_tools, "tavily_search", _fake_tavily_search)
    monkeypatch.setattr(web_tools, "tavily_extract", _fake_tavily_extract)
    # ctx.web_guided 默认 False

    await search_web.ainvoke({"queries": ["腾讯2023年报"]})

    assert captured.get("signal") == "to_web"
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/agents/tools/test_web_search.py tests/agents/graph/test_verify_node.py -v`
Expected: 全过（新增 2 用例通过）

- [ ] **Step 5: Commit**

```bash
git add src/agents/graph/verify/ src/agents/tools/web_tools.py src/infra/llm/request_context.py src/agents/graph/agent_node.py
git commit -m "feat: to_web 信号排除 verify 指派联网（ctx.web_guided 标记）"
```

---

### Task 3: abstain_after_retrieve + unsupported 信号

**Files:**
- Modify: `src/services/agent_service.py`（abstain_after_retrieve）
- Modify: `src/agents/graph/verify/`（faithfulness 后埋 unsupported）
- Test: `tests/services/test_agent_service.py` / `tests/agents/graph/test_verify_node.py`

**Interfaces:**
- Consumes: Task 1-2 的 retrieval_signal helper 与 ctx 字段
- Produces: abstain_after_retrieve（检索过且拒答）；unsupported（judge 打标后）

- [ ] **Step 1: agent_service 埋 abstain_after_retrieve**

`src/services/agent_service.py` `_run_generation` 收尾（448-461 行）。注意 `final_state` 是临时构造的（仅 answer/tool_contexts），**不含 kb_id/query**——信号数据源从 `_run_generation` 的参数 `kb_id` / `query` 取。改造后完整块：

```python
    # 收尾：复刻旧 stream_chat 语义，按捕获结果补发 abstention / model_info
    # 事件（capture 在循环内经 _convert_event 填充 model_used / final_answer）
    if capture.final_answer is not None:
        final_state = AgentState(
            answer=capture.final_answer,
            tool_contexts=capture.final_contexts,
        )
        if _is_abstention(final_state):
            # abstain_after_retrieve 行为信号：绑 KB 且检索过却拒答 → 检索质量缺陷
            has_kb_retrieved = bool(capture.final_contexts) and bool(kb_id)
            if has_kb_retrieved:
                from src.core.logging import retrieval_signal

                retrieval_signal(
                    "abstain_after_retrieve",
                    query,
                    0,  # iteration 非关键：capture 未存迭代数，传 0（YAGNI 不做 capture 改造）
                    kb_id=kb_id,
                    tool_context_count=len(capture.final_contexts or []),
                )
            abstention_event = SSEAbstentionEvent()
            manager.add_event(
                session_id,
                abstention_event.type,
                abstention_event.payload_for_buffer(),
            )
    if capture.model_used:
        model_info_event = SSEModelInfoEvent(
            model=capture.model_used, is_fallback=False
        )
        manager.add_event(
            session_id,
            model_info_event.type,
            model_info_event.payload_for_buffer(),
        )
    return full_answer
```

**实现注意**：`kb_id` / `query` 是 `_run_generation` 的参数（签名已有）；`_StreamCapture` 不加 final_iteration 字段——abstain 信号 iteration 传 0（诊断价值在 query + kb_id + 检索过却拒答的事实，iteration 非必要）。若未来需要精确 iteration，另行评估。

- [ ] **Step 2: faithfulness 后埋 unsupported**

在 verify 包 faithfulness_check 返回 `_unsupported` 非空处（verify node 或校验器收尾）加：

```python
            if unsupported:
                from src.core.logging import retrieval_signal

                if state.kb_id:
                    retrieval_signal(
                        "unsupported",
                        state.query,
                        state._agent_iterations,
                        kb_id=state.kb_id,
                        unsupported_count=len(unsupported),
                    )
                return {
                    "answer": answer,
                    "_unsupported": unsupported,
                    "_needs_regenerate": False,
                }
```

- [ ] **Step 3: 测试（unsupported 信号）**

`tests/agents/graph/test_verify_node.py` 追加（复用既有 `_make_contexts` / monkeypatch faithfulness 模式）：

```python
@pytest.mark.asyncio
async def test_verify_unsupported_signal_emitted(monkeypatch):
    """态 B judge 返回 unsupported → 产 unsupported 行为信号。"""
    captured: dict = {}

    def _fake_signal(signal, query, iteration, **fields):
        captured["signal"] = signal
        captured["fields"] = fields

    monkeypatch.setattr("src.core.logging.retrieval_signal", _fake_signal)
    # faithfulness_check 已被 monkeypatch 返回 unsupported 列表（复用既有 fake）
    # 构造绑 KB 的 state + ctx，跑 verify_node
    state = AgentState(
        answer="2024年营收3943亿",
        kb_id="kb1",
        query="腾讯2024营收",
        tool_contexts=_make_contexts(),
    )
    ctx = RequestContext(session_id="s1", tool_contexts=_make_contexts())
    token = current_request_ctx.set(ctx)
    try:
        result = await verify_node(state)
        assert result["_unsupported"] == ["句X"]
        assert captured.get("signal") == "unsupported"
        assert captured["fields"]["kb_id"] == "kb1"
    finally:
        current_request_ctx.reset(token)
```

（abstain_after_retrieve 信号在 agent_service 集成层，随既有 `_run_generation` 测试扩展 mock 断言——若现有测试结构不便覆盖，标注由 Task 5 手动端到端验证。）

- [ ] **Step 4: 运行测试**

Run: `pytest tests/services/test_agent_service.py tests/agents/graph/test_verify_node.py -v`
Expected: 全过

- [ ] **Step 5: Commit**

```bash
git add src/services/agent_service.py src/agents/graph/verify/
git commit -m "feat: abstain_after_retrieve + unsupported 行为信号埋点"
```

---

### Task 4: dedup 参数化 + A/B 实验

**Files:**
- Modify: `src/config/settings.py`（加 `RETRIEVAL_MAX_PER_DOC`）
- Modify: `src/rag/retrieval.py`（`_dedup_by_doc_id` 参数化）
- Test: `tests/rag/test_retrieval_dedup.py`

**Interfaces:**
- Consumes: 无
- Produces: `_dedup_by_doc_id(results, max_per_doc: int | None = None)`——None 时读 settings

- [ ] **Step 1: settings 加配置**

`src/config/settings.py` 加：

```python
    # 检索结果按 doc_id 去重的每文档保留条数（1 = 现状；>1 供多样性 A/B 实验）
    RETRIEVAL_MAX_PER_DOC: int = 1
```

- [ ] **Step 2: dedup 参数化**

`src/rag/retrieval.py` `_dedup_by_doc_id` 改为每文档最多 N 条：

```python
def _dedup_by_doc_id(
    results: list[ChunkResult], max_per_doc: int | None = None
) -> list[ChunkResult]:
    """按 doc_id 去重检索结果，每个文档最多保留 max_per_doc 条。

    默认（None）读 settings.RETRIEVAL_MAX_PER_DOC（=1 保持现状）；
    无 doc_id 的项按自身保留，不计入配额。

    Args:
        results: 检索结果列表（RRF 融合后）
        max_per_doc: 每文档保留条数上限，None 时读 settings

    Returns:
        去重后的结果列表
    """
    if max_per_doc is None:
        max_per_doc = settings.RETRIEVAL_MAX_PER_DOC
    seen_count: dict[str, int] = {}
    deduped: list[ChunkResult] = []
    for r in results:
        doc_id = r.metadata.get("doc_id")
        if doc_id is None:
            deduped.append(r)
            continue
        n = seen_count.get(doc_id, 0)
        if n >= max_per_doc:
            continue
        seen_count[doc_id] = n + 1
        deduped.append(r)
    return deduped
```

（函数加 `from src.config import settings` import——retrieval.py 顶部已 `from src.config import (...)`，补 `RETRIEVAL_MAX_PER_DOC` 从 settings 读，注意名字冲突用完整 `settings.RETRIEVAL_MAX_PER_DOC`。）

- [ ] **Step 3: 测试**

`tests/rag/test_retrieval_dedup.py` 追加（复用既有 `_chunk` 辅助）：

```python
def test_dedup_default_one_per_doc_keeps_existing_behavior():
    """默认（max_per_doc 未传）每文档 1 条 — 现状不回归。"""
    results = [
        _chunk("a1", "d1"),
        _chunk("a2", "d1"),
        _chunk("b1", "d2"),
        _chunk("a3", "d1"),
    ]
    out = _dedup_by_doc_id(results)
    assert [c.id for c in out] == ["a1", "b1"]


def test_dedup_max_per_doc_two():
    """max_per_doc=2 时每文档保留前 2 条。"""
    results = [
        _chunk("a1", "d1"),
        _chunk("a2", "d1"),
        _chunk("a3", "d1"),
        _chunk("b1", "d2"),
    ]
    out = _dedup_by_doc_id(results, max_per_doc=2)
    assert [c.id for c in out] == ["a1", "a2", "b1"]
```

**说明**：`_dedup_by_doc_id` 改为接受 `max_per_doc` 位置参数；现有两用例（`test_dedup_keeps_first_per_doc` / `test_dedup_keeps_items_without_doc_id`）未传该参数 → 读 settings 默认 1，行为不变，无需改动。

- [ ] **Step 4: 运行测试 + 回归**

Run: `pytest tests/rag/test_retrieval_dedup.py tests/ -v`
Expected: 全过

- [ ] **Step 5: Commit**

```bash
git add src/config/settings.py src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "feat: 检索去重参数化（RETRIEVAL_MAX_PER_DOC，默认 1 不回归）"
```

---

### Task 5: 离线 A/B 实验 + detail 下钻（覆盖 change 2.3-2.5 / 3.1-3.2）

**Files:**
- Create: `src/cli/compare_dedup.py`（A/B 实验脚本，复用 compare_retrieval.py 的子进程覆盖模式）
- Modify: `src/cli/eval_ragas.py`（输出 detail_json 增补每 query 检索明细）
- Test: 无（实验脚本 + 手动下钻验证；eval_ragas 改动走既有测试回归）

**Interfaces:**
- Consumes: Task 4 的 `RETRIEVAL_MAX_PER_DOC` 环境变量；行为信号标记的"真实差 query 清单"（Task 5 手动收集 ≥10 条）
- Produces: ① A/B 对照结论（N=1 vs N=2/3 的 RAGAS 指标）；② detail_json 含每 query 的 dense/bm25/dedup/rerank 明细，供低分下钻

- [ ] **Step 1: 收集真实差 query 测试集**

从行为信号日志（`grep retrieval_signal /data/logs/app_*.log` 的 to_web / empty_result / abstain_after_retrieve）人工收集 ≥10 条绑 KB 的"疑似检索差"query，存为 `data/retrieval_ab_tests.json`：

```json
{
  "kb_id": "<实际 kb id>",
  "queries": [
    {"query": "腾讯2023年营收", "ground_truth": "需人工标注期望命中"}
  ]
}
```

（ground_truth 由人工为每条 query 标注"KB 里应该有哪份文档/哪段数据"，作为 context_recall 判定基线。）

- [ ] **Step 2: 新建 compare_dedup.py（A/B 骨架）**

复用 `compare_retrieval.py` 的子进程覆盖 env 模式，但覆盖 `RETRIEVAL_MAX_PER_DOC`：

```python
"""对比 RETRIEVAL_MAX_PER_DOC 取值的 RAGAS 指标（dedup 多样性 A/B）。

遍历 max_per_doc ∈ {1, 2, 3}，在子进程覆盖 RETRIEVAL_MAX_PER_DOC 后
运行 eval_ragas，解析指标均值，打印并排对照。

Usage:
    python -m src.cli.compare_dedup --kb-name rag_eval
"""

import argparse
import os
import subprocess
import sys

# 复用 compare_retrieval 的 _resolve_kb_id / parse_metrics / run_eval 骨架
from src.cli.compare_retrieval import _resolve_kb_id, parse_metrics

CANDIDATES = [1, 2, 3]


def run_eval_with_dedup(max_per_doc: int, kb_id: str) -> dict:
    """通过环境变量覆盖 RETRIEVAL_MAX_PER_DOC 后运行 eval_ragas。

    Args:
        max_per_doc: 每文档保留条数（本轮实验值）
        kb_id: 目标知识库 id

    Returns:
        解析后的 RAGAS 指标均值 dict
    """
    env = os.environ.copy()
    env["RETRIEVAL_MAX_PER_DOC"] = str(max_per_doc)
    cmd = [sys.executable, "-m", "src.cli.eval_ragas", "--kb-id", kb_id]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1200)
    return parse_metrics(result.stdout)


def main() -> None:
    """跑 N=1/2/3 对照并打印表格。"""
    parser = argparse.ArgumentParser(description="dedup 多样性 A/B")
    parser.add_argument("--kb-name", required=True)
    args = parser.parse_args()
    kb_id = _resolve_kb_id(args.kb_name)
    print(f"{'max_per_doc':<12} {'context_recall':<14} {'context_precision':<18} {'faithfulness':<14}")
    for n in CANDIDATES:
        m = run_eval_with_dedup(n, kb_id)
        print(
            f"{n:<12} {m.get('context_recall', float('nan')):<14.4f} "
            f"{m.get('context_precision', float('nan')):<18.4f} "
            f"{m.get('faithfulness', float('nan')):<14.4f}"
        )


if __name__ == "__main__":
    main()
```

**说明**：`parse_metrics` 返回键名以 eval_ragas 实际输出为准（context_recall/context_precision/faithfulness）；若与 compare_retrieval 现有键不一致，对照其 metric_names 调整。

- [ ] **Step 3: eval_ragas detail_json 增补检索明细**

`src/cli/eval_ragas.py` `generate_answers_and_contexts` 内（154 行累积 `ctx_list` 处）同步累积结构化检索明细，并在函数 return 时作为第 4 元组返回：

```python
            # 提取上下文字段列表（用于 context_recall / context_precision 评估）
            ctx_list = [
                c.to_prompt_text() for c in final_state.get("tool_contexts", [])
            ]
            contexts.append(ctx_list)
            # 结构化检索明细（detail_json 下钻用：rerank 后来源+分数+类型）
            retrieval_details.append(
                [
                    {
                        "source": getattr(c, "source", ""),
                        "score": getattr(c, "score", 0.0),
                        "kind": getattr(c, "kind", "kb"),
                    }
                    for c in final_state.get("tool_contexts", [])
                ]
            )
```

函数签名改 `-> tuple[list[str], list[list[str]], list[str], list[list[dict]]]`，顶部初始化 `retrieval_details: list[list[dict]] = []`，异常分支 append `[]`，return 加 `retrieval_details`。

**同步调用处**（唯一调用在 main，529 行附近）：
```python
    answers, contexts, trace_ids, retrieval_details = asyncio.run(
        generate_answers_and_contexts(...)
    )
```
并把 `retrieval_details` 传给 `_save_eval_report`（在 detail_json 字段中 JSON 序列化）——`_save_eval_report(kb_id, result, len(questions), output_path, retrieval_details)`，内部 `detail_json` 存 `{"retrieval_details": retrieval_details}`（或并入既有 detail 结构）。

**说明**：dense/bm25 各自命中切分需检索层单独埋点（本 change 超范围）；detail 先给 rerank 后的 source+score+kind，满足"看低分 query 检索/排序了啥"的下钻。dense/bm25 分路命中记录为后续增强。

- [ ] **Step 4: 运行 A/B + 下钻验证**

Run: `python -m src.cli.compare_dedup --kb-name rag_eval`
Expected: 打印 N=1/2/3 三行指标对照；依据 context_recall 变化判断 N 是否改善召回多样性

下钻验证：挑一条标记 query，跑 eval_ragas 看 detail_json——能看出该 query 检索到了哪些 source、分数排序如何（哪一环问题）。

- [ ] **Step 5: 定 N 并写结论**

依据 Step 4 结果：
- 若 N=2/3 的 context_recall 明显高于 N=1 且 faithfulness 不降 → 设 `RETRIEVAL_MAX_PER_DOC=2`（或 3）
- 否则维持 1（现状）——保留参数化能力但默认不变
结论写入 `docs/openspec/changes/retrieval-quality-signals/tasks.md` 2.5 备注。

- [ ] **Step 6: Commit**

```bash
git add src/cli/compare_dedup.py src/cli/eval_ragas.py data/retrieval_ab_tests.json
git commit -m "feat: dedup 多样性 A/B 实验 + eval detail 下钻明细"
```

---

### Task 6: 收尾质量门禁

**Files:**
- Modify: `docs/openspec/changes/retrieval-quality-signals/tasks.md`（勾选全部）

**Interfaces:**
- Consumes: Task 1-5 产物

- [ ] **Step 1: 全量回归 + 门禁**

Run: `pytest tests/ -v && ruff check . && pyright src/`
Expected: 全过 / 无 error / 不新增 pyright error

- [ ] **Step 2: 手动端到端验证**

- 绑 KB 检索空 → 日志 `retrieval_signal: signal=empty_result query=... iteration=... kb_id=...`
- 绑 KB 检索不足转 web（自主）→ `signal=to_web`；verify 指派联网（用户确认后）→ 无 to_web
- 拒答 → `signal=abstain_after_retrieve`；judge 无支撑 → `signal=unsupported`
- 正常引用 → `signal=cited kind=kb|web`
- 态 A 纯对话调 search_web → 不产缺陷信号

- [ ] **Step 3: 勾选 change tasks + Commit**

修改 `docs/openspec/changes/retrieval-quality-signals/tasks.md` 全勾（含 2.3-2.5 A/B 结论、3.1-3.2 detail 下钻，已完成于 Task 5），然后：

```bash
git add docs/openspec/changes/retrieval-quality-signals/
git commit -m "docs: retrieval-quality-signals tasks 完成"
```
