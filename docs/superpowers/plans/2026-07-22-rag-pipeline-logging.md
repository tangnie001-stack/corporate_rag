# RAG 问答链路日志补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 RAG 问答链路（用户提问 → 检索 → 重排序 → 生成）的每个环节补全 logger.info，支持通过 INFO 日志追溯请求状态、数据和耗时

**Architecture:** 纯加法改动，不改变任何业务逻辑。计时用 `time.perf_counter()` 在主协程/主线程打点。`api/chat.py` 的 SSE 路径和 `rag/chain.py` 的同步路径各自独立加计时和完成日志；底层 `rag/retrieval.py` 和 `rag/stream.py` 的日志两条路径共享

**Tech Stack:** Loguru (logger), time.perf_counter

**Global Constraints:**
- 所有改动只加 `logger.info` 和 `import time`，不得修改业务逻辑
- 计时用 `time.perf_counter()` 在主协程打点，不在线程池或子函数内部埋点
- 查询文本原文记录，不截断不脱敏
- SSE status 事件（推给前端）与服务端日志（量化数据）各自独立，不冲突
- `_stream_rag_response`（SSE）和 `chat_with_citations`（同步）两条路径各自独立加入口/计时/完成日志

---

### Task 1: SSE 路径入口日志 + 阶段计时

**Files:**
- Modify: `src/api/chat.py:144-244`（`_stream_rag_response` 函数）
- Test: `tests/api/test_chat.py`

**Interfaces:**
- Consumes: `svc.rag_chain.search()`、`svc.rag_chain.rerank()`、`svc.rag_chain.stream_answer()`、`svc.rag_chain._last_token_usage`
- Produces: 无外部接口变化，仅在代码内新增 `import time`

- [ ] **Step 1: 添加 import time**

在 `src/api/chat.py` 顶部，第 3 行 `import os` 之后（保持字母序）添加：

```python
import time
```

- [ ] **Step 2: 在 try 块开始处添加入口日志**

找到第 161 行 `try:`，在其后、第 163 行之前插入：

```python
        logger.info(
            "Chat stream start: session_id={} kb_id={} query_len={} query={}",
            session_id, kb_id, len(query), query,
        )
```

注意缩进为 8 空格（2 层缩进）。

- [ ] **Step 3: 添加四阶段计时**

在 try 块内找到以下四个位置添加 `time.perf_counter()`：

**3a. 检索前**（第 170 行之后，`yield sse_status("retrieving")` 之后、`results = await...` 之前）：

```python
        t0 = time.perf_counter()
```

**3b. 检索后、重排序前**（第 172 行 `results = await...` 之后、第 175 行 yield sse_status("reranking") 之前）：

```python
        t1 = time.perf_counter()
```

**3c. 重排序后、生成前**（第 176 行 `contexts = svc.rag_chain.rerank(...)` 之后、第 179 行 yield sse_status("generating") 之前）：

```python
        t2 = time.perf_counter()
```

**3d. 生成后、citations 前**（在 for 循环 `for token in ... stream_answer(...)` 之后、第 188 行 tracer.end_trace 之前）：

```python
        t3 = time.perf_counter()
```

- [ ] **Step 4: 添加完成日志**

在 `t3` 之后、第 188 行 `tracer.end_trace(...)` 之前插入：

```python
        tu = getattr(svc.rag_chain, "_last_token_usage", {})
        logger.info(
            "Chat stream completed: session_id={} | "
            "search={:.1f}s rerank={:.1f}s generate={:.1f}s total={:.1f}s "
            "| tokens: prompt={} completion={} total={} | citations={}",
            session_id,
            t1 - t0,
            t2 - t1,
            t3 - t2,
            t3 - t0,
            tu.get("prompt_tokens", 0),
            tu.get("completion_tokens", 0),
            tu.get("total_tokens", 0),
            len(seen),
        )
```

注意 `len(seen)` 的 `seen` 变量在第 192 行才定义。需要把这段日志移到第 211 行（citations 循环之后、第 213 行 `# Save assistant response` 之前）。或者更好的做法：完成日志放在 citations 循环之后、`# Save assistant response` 之前。

确认位置：第 211 行 `await asyncio.sleep(0)`（citations 循环末尾）之后、第 213 行注释之前。

- [ ] **Step 5: 在 `_persist_conversation` 成功后添加确认日志**

找到 `_persist_conversation` 函数末尾（第 296 行），在两个 `await retry(...)` 之后添加：

```python
    logger.info(
        "Conversation persisted: session_id={} kb_id={} sources={}",
        session_id, kb_id, len(sources),
    )
```

- [ ] **Step 6: 运行测试确认**

```bash
pytest tests/api/test_chat.py -v
```

预期：全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/api/chat.py
git commit -m "feat: add SSE stream lifecycle and timing logs (api/chat.py)"
```

---

### Task 2: 同步路径入口日志 + 阶段计时

**Files:**
- Modify: `src/rag/chain.py:101-145`（`chat_with_citations` 方法）
- Test: `tests/rag/test_rag_chain_tracing.py`

**Interfaces:**
- Consumes: `self.router.route()` 返回的 route 值、`self._rewrite_if_needed()`、`search()`、`rerank_results()`、`self.stream_answer()`、`self._last_token_usage`
- Produces: 无外部接口变化

- [ ] **Step 1: 在 `chat_with_citations` 入口添加入口日志**

在第 110 行 `route = self.router.route(query)` 之后、第 111 行 `history = ...` 之前插入：

```python
        logger.info(
            "Chat with citations: route={} query_len={} query={}",
            route, len(query), query,
        )
```

注意缩进为 8 空格。

- [ ] **Step 2: 在 rewrite 后添加重写日志**

在第 119 行 `query = self._rewrite_if_needed(query, history)` 之后插入：

```python
        if route in ("vague", "complex"):
            query_rewritten = self._rewrite_if_needed(query, history)
            if query_rewritten != query:
                logger.info(
                    "Query rewritten: \"{}\" -> \"{}\"",
                    query, query_rewritten,
                )
                query = query_rewritten
```

注意：原有的第 118-119 行：
```python
        if route in ("vague", "complex"):
            query = self._rewrite_if_needed(query, history)
```

需要修改为上面的版本，改造写逻辑以支持重写前后的对比日志。

- [ ] **Step 3: 添加 import time**

在 `src/rag/chain.py` 顶部，第 3 行 `from typing import Generator, Optional` 之后添加：

```python
import time
```

- [ ] **Step 4: 添加四阶段计时**

在 try 块内的 `search` 调用前、后及 `rerank_results` 后、`stream_answer` 后添加计时：

`t0` — 第 127 行 `try:` 之后、`import asyncio` 之前：

```python
        t0 = time.perf_counter()
```

`t1` — 第 133 行 `loop.close()` 之后、第 137 行 `if not results:` 之前：

```python
        t1 = time.perf_counter()
```

`t2` — 第 141 行 `rag_contexts = rerank_results(...)` 之后、第 142 行 `history = ...` 之前：

```python
        t2 = time.perf_counter()
```

`t3` — 第 143 行 `token_generator = self.stream_answer(...)` 之后、第 144 行 `self.chat_manager.add_message(...)` 之前：

```python
        t3 = time.perf_counter()
```

- [ ] **Step 5: 在 return 前添加完成日志**

在第 144 行 `self.chat_manager.add_message(...)` 之后、第 145 行 `return token_generator, rag_contexts` 之前插入：

```python
        tu = getattr(self, "_last_token_usage", {})
        logger.info(
            "Chat with citations completed: "
            "search={:.1f}s rerank={:.1f}s generate={:.1f}s total={:.1f}s "
            "| results={} contexts={} "
            "| tokens: prompt={} completion={} total={}",
            t1 - t0,
            t2 - t1,
            t3 - t2,
            t3 - t0,
            len(results),
            len(rag_contexts),
            tu.get("prompt_tokens", 0),
            tu.get("completion_tokens", 0),
            tu.get("total_tokens", 0),
        )
```

- [ ] **Step 6: 运行测试确认**

```bash
pytest tests/rag/test_rag_chain_tracing.py -v
```

预期：全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add src/rag/chain.py
git commit -m "feat: add sync path lifecycle and timing logs (rag/chain.py)"
```

---

### Task 3: 检索和重排序日志

**Files:**
- Modify: `src/rag/retrieval.py:34-52`（search 函数的两条日志消息）和 `src/rag/retrieval.py:99-100`（rerank_results 的 return 前）
- Test: `tests/rag/test_retrieval.py`

**Interfaces:**
- Consumes: `len(query)`、`len(results)`、`contexts[0].score`
- Produces: 无外部接口变化

- [ ] **Step 1: 修改 hybrid 分支的日志，追加 mode=hybrid**

找到第 34-37 行：

```python
        logger.info(
            "RAG search: kb_id={} query_len={} results={}",
            kb_id, len(query), len(results),
        )
```

改为：

```python
        logger.info(
            "RAG search: kb_id={} query_len={} results={} mode=hybrid",
            kb_id, len(query), len(results),
        )
```

- [ ] **Step 2: 修改默认分支的日志，追加 mode=dense**

找到第 48-51 行：

```python
    logger.info(
        "RAG search: kb_id={} query_len={} results={}",
        kb_id, len(query), len(results),
    )
```

改为：

```python
    logger.info(
        "RAG search: kb_id={} query_len={} results={} mode=dense",
        kb_id, len(query), len(results),
    )
```

- [ ] **Step 3: 在 rerank_results 正常路径添加完成日志**

在第 99 行 `)` 之后、第 100 行 `return contexts` 之前插入：

```python
    if contexts:
        logger.info(
            "Rerank completed: {} -> {} contexts, top_score={:.4f}",
            len(results), len(contexts), contexts[0].score,
        )
```

注意缩进为 4 空格。

- [ ] **Step 4: 运行测试确认**

```bash
pytest tests/rag/test_retrieval.py -v
```

预期：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/rag/retrieval.py
git commit -m "feat: add search mode and rerank completion logs (rag/retrieval.py)"
```

---

### Task 4: 生成完成日志

**Files:**
- Modify: `src/rag/stream.py:74-76`（stream_answer 的 return 前）
- No dedicated test file for stream.py；run full suite

**Interfaces:**
- Consumes: `time.monotonic()` 的 `_stream_start`（已有）、`full_output`、`last_token_usage`
- Produces: 无外部接口变化

- [ ] **Step 1: 在 return 前添加生成完成日志**

找到第 73-76 行：

```python
            tracer.end_generation(
                gen_id, trace_id, output=full_output, usage=last_token_usage,
            )
            return
```

在其之后、`return` 之前插入（即在 `end_generation` 调用之后、`return` 之前）：

```python
            _gen_latency = (time.monotonic() - _stream_start) * 1000
            logger.info(
                "Generation completed: chars={} latency={:.0f}ms "
                "| tokens: prompt={} completion={} total={}",
                len(full_output),
                _gen_latency,
                last_token_usage.get("prompt_tokens", 0),
                last_token_usage.get("completion_tokens", 0),
                last_token_usage.get("total_tokens", 0),
            )
```

最终代码（第 73-76 行后变为）：

```python
            tracer.end_generation(
                gen_id, trace_id, output=full_output, usage=last_token_usage,
            )
            _gen_latency = (time.monotonic() - _stream_start) * 1000
            logger.info(
                "Generation completed: chars={} latency={:.0f}ms "
                "| tokens: prompt={} completion={} total={}",
                len(full_output),
                _gen_latency,
                last_token_usage.get("prompt_tokens", 0),
                last_token_usage.get("completion_tokens", 0),
                last_token_usage.get("total_tokens", 0),
            )
            return
```

- [ ] **Step 2: 运行测试确认**

```bash
pytest tests/ -v -x
```

预期：全部 PASS。

- [ ] **Step 3: Commit**

```bash
git add src/rag/stream.py
git commit -m "feat: add generation completion logs with token usage (rag/stream.py)"
```

---

### Task 5: 格式化与最终验证

- [ ] **Step 1: 格式化**

```bash
ruff format src/api/chat.py src/rag/chain.py src/rag/retrieval.py src/rag/stream.py
```

预期：可能重排缩进，无错误。

- [ ] **Step 2: Lint 检查**

```bash
ruff check src/api/chat.py src/rag/chain.py src/rag/retrieval.py src/rag/stream.py --fix
```

预期：All checks passed。

- [ ] **Step 3: 全量测试**

```bash
pytest tests/ -v
```

预期：与之前结果一致（可能预存在少量失败）。

- [ ] **Step 4: 如有 lint 修复则提交**

```bash
git add -A
git commit -m "style: format and lint RAG pipeline logging changes"
```
