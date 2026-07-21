# 文档上传链路日志补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) for tracking.

**Goal:** 在文档上传→解析→入库的链路关键节点补全 logger.info，支持通过 INFO 日志追踪数据在每个环节的状态和耗时

**Architecture:** 纯加法改动，不改变任何业务逻辑。解析器概要日志和阶段耗时放在 `api/documents.py` 的 `_process_document_task` 主协程中（避免线程池丢失 trace_id）；分块策略详情日志放在各 strategy 的 `chunk()` return 前

**Tech Stack:** Loguru (logger), time.perf_counter

**Global Constraints:**
- 所有改动只加 logger.info，不得修改业务逻辑
- 解析器日志放 `await svc.router.parse()` 之后的主协程中，不放线程池内
- 计时用 `time.perf_counter()` 在主协程打点，不放线程内
- table_preserving 的合并计数器通过修改私有静态方法 `_split_by_table_boundary` 的返回类型实现（`list[str]` → `tuple[list[str], int]`）

---

### Task 1: 解析器概要 + 阶段耗时日志

**Files:**
- Modify: `src/api/documents.py:255-389` (`_process_document_task` 函数)
- Test: `tests/api/test_documents.py`（已有测试，只需验证通过）

**Interfaces:**
- Consumes: `svc.router.parse()` 返回的 `ParseResult`（字段: file_type, total_pages, total_chars, is_scanned, encoding）
- Produces: 无外部接口变化，仅在代码内新增 `import time`

- [ ] **Step 1: 在 `_process_document_task` 中加 import time**

在 `src/api/documents.py` 顶部添加：

```python
import time
```

放在第 12 行 `import tempfile` 之后（保持字母序）。

- [ ] **Step 2: 在 parse_result 后添加解析器概要日志**

找到第 296 行 `parse_result = await asyncio.to_thread(svc.router.parse, tmp_path)`，在其后（第 297 行之前）插入：

```python
            logger.info(
                "Parser result: {} -> type={} pages={} chars={} scanned={} encoding={}",
                filename,
                parse_result.file_type,
                parse_result.total_pages,
                parse_result.total_chars,
                parse_result.is_scanned,
                parse_result.encoding,
            )
```

注意保持与周围代码一致的缩进（12 空格 = 3 层缩进）。

- [ ] **Step 3: 添加三段计时打点**

在 `_process_document_task` 的 try 块内，找到以下三个关键点并添加计时变量：

**3a. 解析前**（第 295 行注释 `# 解析` 之前）：

```python
            t0 = time.perf_counter()
```

放在第 295 行之前（与 `# 解析 — CPU + 文件 I/O` 注释对齐）。

**3b. 解析后、分块前**（第 303 行 `# 分块` 之前，在 scan 检测之后）：

```python
            t1 = time.perf_counter()
```

放在第 303 行（`# 分块 — CPU，to_thread` 注释之前）。

**3c. 分块后、入库前**（第 317 行 `# ChromaDB` 之前）：

```python
            t2 = time.perf_counter()
```

放在第 357 行（`# ChromaDB — 同步库，to_thread` 注释之前）。

**3d. 入库后**（第 362 行 `# DB 更新` 之前）：

```python
            t3 = time.perf_counter()
```

放在第 362 行（`# DB 更新 — 异步` 注释之前）。

- [ ] **Step 4: 修改最终日志，追加耗时**

找到第 372 行：

```python
            logger.info(
                "Document processed: {} -> {} chunks (strategy={})",
                filename,
                count,
                strategy,
            )
```

改为：

```python
            logger.info(
                "Document processed: {} -> {} chunks (strategy={}) | "
                "parse={:.1f}s chunk={:.1f}s store={:.1f}s total={:.1f}s",
                filename,
                count,
                strategy,
                t1 - t0,
                t2 - t1,
                t3 - t2,
                t3 - t0,
            )
```

- [ ] **Step 5: 运行已有测试确认不破坏功能**

```bash
pytest tests/api/test_documents.py -v
```

预期：全部 PASS。如果 `test_upload_document` 失敗，验证 mock 是否正确处理新增代码（`_process_document_task` 是后台任务，不会阻塞上传接口的返回）。

- [ ] **Step 6: Commit**

```bash
git add src/api/documents.py
git commit -m "feat: add parser summary and phase timing logs to document pipeline"
```

---

### Task 2: 分块策略日志

**Files:**
- Modify: `src/infra/chunking/strategies/parent_child.py`（+4 行: import logger + log call）
- Modify: `src/infra/chunking/strategies/qa.py`（+4 行: import logger + log call）
- Modify: `src/infra/chunking/strategies/table_preserving.py`（+8 行: import logger + merge counter + log call）
- Test: `tests/infra/chunking/test_chunking.py`

**Interfaces:**
- Produces: 每个 strategy 的 `chunk()` 方法在 return 前输出一行 `logger.info`

- [ ] **Step 1: parent_child.py — 添加日志**

在 `src/infra/chunking/strategies/parent_child.py` 开头（第 1 行）添加 import：

```python
from loguru import logger
```

在 `chunk()` 方法的 `return result` 之前（第 43 行之前）添加：

```python
        logger.info(
            "[parent_child] chunks={} parents={} children={} tokens={}",
            len(result),
            len(parent_docs),
            len(result),
            sum(c["metadata"]["tokens"] for c in result),
        )
```

- [ ] **Step 2: qa.py — 添加日志**

在 `src/infra/chunking/strategies/qa.py` 开头添加 import：

```python
from loguru import logger
```

在 `chunk()` 方法的 `return result` 之前添加：

```python
        logger.info(
            "[qa] chunks={} qa_pairs={} tokens={}",
            len(result),
            len(qa_pairs),
            sum(c["metadata"]["tokens"] for c in result),
        )
```

- [ ] **Step 3: table_preserving.py — 添加合并计数器 + 日志**

**3a.** 在 `src/infra/chunking/strategies/table_preserving.py` 开头添加 import：

```python
from loguru import logger
```

**3b.** 修改 `_split_by_table_boundary` 的返回类型和签名，第 49 行：

```python
    @staticmethod
    def _split_by_table_boundary(text: str) -> tuple[list[str], int]:
```

**3c.** 在方法内第 66 行（`merged = []` 之后）添加计数器：

```python
        merge_count = 0
```

**3d.** 在第 78 行（首次合并的 `merged.append(...)` 之前）添加计数：

```python
                merge_count += 1
```

**3e.** 在第 93 行（链式合并的 `merged[-1] += ...` 之后）也添加计数（链式合并每次追加也计为一次合并操作）：

```python
                    merge_count += 1
```

**3f.** 修改第 98 行的 return：

```python
        return merged, merge_count
```

**3g.** 在 `chunk()` 方法中修改调用（第 12 行）：

```python
        segments, merge_count = self._split_by_table_boundary(text)
```

**3h.** 在 `chunk()` 方法的 `return result` 之前添加日志：

```python
        table_segments = sum(1 for s in segments if self.TABLE_PATTERN.search(s))
        text_segments = len(segments) - table_segments
        logger.info(
            "[table_preserving] chunks={} (table={} text={}) "
            "segments={} tables={} texts={} merges={} tokens={}",
            len(result),
            sum(1 for c in result if c["metadata"].get("block_type") == "table"),
            sum(1 for c in result if c["metadata"].get("block_type") != "table"),
            len(segments),
            table_segments,
            text_segments,
            merge_count,
            sum(c["metadata"]["tokens"] for c in result),
        )
```

- [ ] **Step 4: 运行分块测试确认不破坏功能**

```bash
pytest tests/infra/chunking/ -v
```

预期：全部 PASS。日志是纯加法改动，不应影响任何测试结果。

- [ ] **Step 5: 运行全量测试**

```bash
pytest tests/ -v
```

预期：全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/infra/chunking/strategies/parent_child.py \
       src/infra/chunking/strategies/qa.py \
       src/infra/chunking/strategies/table_preserving.py
git commit -m "feat: add chunk strategy detail logs (parent_child/qa/table_preserving)"
```

---

### Task 3: 运行 ruff 格式化 + lint 检查

- [ ] **Step 1: 格式化 + lint**

```bash
ruff format src/api/documents.py src/infra/chunking/strategies/
ruff check src/api/documents.py src/infra/chunking/strategies/ --fix
```

预期：无错误输出。

- [ ] **Step 2: 最终全量测试**

```bash
pytest tests/ -v
```

预期：全部 PASS。

- [ ] **Step 3: 最终 commit（如有 lint 修复）**

```bash
git add -A
git commit -m "style: format and lint document pipeline logging changes"
```

---

### 验证方式

上线后观察容器日志 `/data/logs/app_*.log`，用 trace_id 串联排查：

```
# 一条完整的上传链路日志示例（trace_id 已折叠）：
2026-07-21 10:00:00.123 | INFO    | ... | Upload request: filename=年报.pdf size=524288 kb_id=kb-1 user_id=user_...
2026-07-21 10:00:00.456 | INFO    | ... | Upload success: doc_id=doc-xxx filename=年报.pdf kb_id=kb-1 size=524288
2026-07-21 10:00:00.789 | INFO    | ... | Parser result: 年报.pdf -> type=pdf pages=42 chars=158320 scanned=false encoding=utf-8
2026-07-21 10:00:01.234 | INFO    | ... | Detected chunk strategy 'parent_child' for document: 年报.pdf
2026-07-21 10:00:01.456 | INFO    | ... | [parent_child] chunks=42 parents=12 children=42 tokens=18432
2026-07-21 10:00:03.456 | INFO    | ... | ChromaDB add_chunks success: kb_id=kb-1 doc_id=doc-xxx count=42 model=...
2026-07-21 10:00:03.789 | INFO    | ... | Document processed: 年报.pdf -> 42 chunks (strategy=parent_child) | parse=2.3s chunk=0.8s store=4.1s total=7.2s
```

可观测的链路节点从 4 个（上传请求 → 上传成功 → 策略选型 → 处理完成）扩展到 7 个（+ 解析概要 → 策略产出详情 → 阶段耗时）。
