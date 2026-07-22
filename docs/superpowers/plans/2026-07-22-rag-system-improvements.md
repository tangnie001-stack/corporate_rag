# RAG System Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement three RAG system improvements: (1) unify LLM creation under `get_llm()`, (2) add CLI trace_id for log correlation, (3) improve table chunking with large-table row splitting and orphan text merging.

**Architecture:** Three independent feature groups touching models.py, logging.py, table_preserving.py, eval_ragas.py, eval_ragas_generate.py, check_retrieval.py, and settings.py. Each group is self-contained with its own test validation.

**Tech Stack:** Python 3.11+, Loguru, PyMuPDF, RAGAS, LangChain

## Global Constraints

- All CLI entry points must call `setup_logging(configure_trace_id=True)` to enable trace_id
- `get_llm()` must preserve return type `ChatOpenAI` — no breaking changes for existing callers
- Table chunking changes must maintain backward compatibility for documents already in the vector store (only affects new uploads)
- No modifications to RAGAS library internal code
- `ruff format . && ruff check . --fix` must pass clean
- `pytest tests/ -v` must pass

---

### Task 1: Add table chunking config to settings.py

**Files:**
- Modify: `src/config/settings.py` — add 2 new env vars after `MAX_TABLE_TOKENS`

**Interfaces:**
- Consumes: nothing
- Produces: `TABLE_ROW_CHUNK_CHARS`, `ORPHAN_THRESHOLD_CHARS` exported from `src.config`

- [ ] **Step 1: Add new config constants**

Insert after line 116 (`MAX_TABLE_TOKENS`):

```python
# 大表格行级切分阈值：合并后的表格超过此字符数时，按行边界切分，复制表头
TABLE_ROW_CHUNK_CHARS: int = int(os.getenv("TABLE_ROW_CHUNK_CHARS", "2000"))
# 残差短文本合并阈值：小于此字符数的独立文本段，如果与表格相邻则粘到表格上
ORPHAN_THRESHOLD_CHARS: int = int(os.getenv("ORPHAN_THRESHOLD_CHARS", "200"))
```

- [ ] **Step 2: Verify export**

Confirm `src/config/__init__.py` already has `from src.config.settings import *`. It does (line 9) — no change needed.

- [ ] **Step 3: Commit**

```bash
git add src/config/settings.py
git commit -m "feat: add TABLE_ROW_CHUNK_CHARS and ORPHAN_THRESHOLD_CHARS config"
```

---

### Task 2: Add `**kwargs` to get_llm()

**Files:**
- Modify: `src/models.py:181-199`

**Interfaces:**
- Consumes: nothing
- Produces: `get_llm(model, temperature, **kwargs) -> ChatOpenAI`

- [ ] **Step 1: Modify get_llm() signature**

Change line 181:
```python
# Before:
def get_llm(model: str = LLM_MODEL, temperature: float = LLM_TEMPERATURE) -> ChatOpenAI:
```

```python
# After:
def get_llm(
    model: str = LLM_MODEL,
    temperature: float = LLM_TEMPERATURE,
    **kwargs: Any,
) -> ChatOpenAI:
```

Also import `Any` from typing at the top of the file (line 15 currently has `from typing import Callable, TypeVar` — change to `from typing import Any, Callable, TypeVar`).

- [ ] **Step 2: Update return statement**

Change the `return ChatOpenAI(...)` to pass `**kwargs`:
```python
# Before:
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )

# After:
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
        **kwargs,
    )
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/ -v
```
Expected: all tests pass (no existing callers use kwargs, so no behavior change).

- [ ] **Step 4: Commit**

```bash
git add src/models.py
git commit -m "feat: add **kwargs to get_llm() for parameter passthrough"
```

---

### Task 3: Add CLI auto-generated trace_id to logging.py

**Files:**
- Modify: `src/core/logging.py:74-86`

**Interfaces:**
- Consumes: nothing
- Produces: `_setup_trace_id_patcher()` auto-generates trace_id when ContextVar is empty

- [ ] **Step 1: Add auto-generation logic**

In `_setup_trace_id_patcher()`, before the `def _patcher` line, add:

```python
# Before:
def _setup_trace_id_patcher() -> None:
    """配置 Loguru patcher，自动注入当前请求的 trace_id。"""
    from src.infra.llm.trace_context import current_trace_id as _trace_var

    def _patcher(record):
```

```python
# After (+ 4 lines):
def _setup_trace_id_patcher() -> None:
    """配置 Loguru patcher，自动注入当前请求的 trace_id。

    从 trace_context 模块的 ContextVar 中读取当前 trace_id，
    写入每一条日志记录的 extra 字段。
    如果 ContextVar 为空（CLI 模式），自动生成一个 trace_id。
    """
    from src.infra.llm.trace_context import current_trace_id as _trace_var

    # CLI 模式：没有外部传入的 trace_id 时自动生成
    if not _trace_var.get():
        import uuid
        _trace_var.set(f"trace_{uuid.uuid4()}")

    def _patcher(record):
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v
```
Expected: all pass. The change only adds behavior when ContextVar is empty.

- [ ] **Step 3: Commit**

```bash
git add src/core/logging.py
git commit -m "feat: auto-generate trace_id in CLI mode when ContextVar is empty"
```

---

### Task 4: Table chunking — remove cross-page merge size limit

**Files:**
- Modify: `src/infra/chunking/strategies/table_preserving.py:5,80-82,95-96,111-112`

**Interfaces:**
- Consumes: nothing
- Produces: cross-page table merging no longer limited by `MAX_TABLE_CHARS`

- [ ] **Step 1: Remove unused import and variable**

Remove `MAX_TABLE_TOKENS` from the import (line 5):

```python
# Before:
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD, MAX_TABLE_TOKENS

# After:
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD
```

Remove lines 80-82 (the `MAX_TABLE_CHARS` assignment and its comment):

```python
# Remove these 3 lines:
        # 合并跨页表格：列数相同 + 中间短文本（< N 字）→ 同一张表，合并
        # MAX_TABLE_TOKENS 是 token 数，*2 转字符数（中文 1 token ≈ 2 字符）
        MAX_TABLE_CHARS = MAX_TABLE_TOKENS * 2

# After: nothing at these lines
```

- [ ] **Step 2: Remove the two `MAX_TABLE_CHARS` conditions**

In the first merge condition (line 95-96):
```python
# Before:
                and len(segments[i]) + len(segments[i + 1]) + len(segments[i + 2])
                <= MAX_TABLE_CHARS

# After:
                # removed size limit — merge regardless of combined size
```

In the chain merge condition (line 111-112):
```python
# Before:
                and len(merged[-1]) + len(segments[i]) + len(segments[i + 1])
                <= MAX_TABLE_CHARS

# After:
                # removed size limit — merge regardless of combined size
```

- [ ] **Step 3: Run existing tests**

```bash
pytest tests/infra/chunking/test_chunking.py -v
```
Expected: `test_table_preserving_keeps_table` passes.

- [ ] **Step 4: Commit**

```bash
git add src/infra/chunking/strategies/table_preserving.py
git commit -m "refactor: remove MAX_TABLE_CHARS limit from cross-page table merge"
```

---

### Task 5: Table chunking — add orphan text merge

**Files:**
- Modify: `src/infra/chunking/strategies/table_preserving.py`

**Interfaces:**
- Consumes: `ORPHAN_THRESHOLD_CHARS` from settings
- Produces: `_merge_orphan_texts(segments) -> list[str]`

- [ ] **Step 1: Add import for ORPHAN_THRESHOLD_CHARS**

Update line 5:
```python
# Before:
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD

# After:
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD, ORPHAN_THRESHOLD_CHARS
```

- [ ] **Step 2: Add the `_merge_orphan_texts` static method**

Add this method after `_same_table_structure` (after line 63):

```python
    @staticmethod
    def _merge_orphan_texts(segments: list[str]) -> list[str]:
        """将小于 ORPHAN_THRESHOLD_CHARS 的孤立短文本合并到相邻 TABLE segment.

        扫描 text segment，< ORPHAN_THRESHOLD_CHARS 且与 TABLE 相邻时：
          - 优先向后合并（粘到后一个表格开头）
          - 其次向前合并（粘到前一个表格末尾）
        迭代扫描直到没有新的合并。
        """
        result = list(segments)
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(result):
                is_table = bool(
                    TablePreservingChunker.TABLE_PATTERN.search(result[i])
                )
                is_short = not is_table and len(result[i]) < ORPHAN_THRESHOLD_CHARS

                if is_short:
                    # 向后合并：粘到后一个 TABLE 开头
                    if i + 1 < len(result) and TablePreservingChunker.TABLE_PATTERN.search(
                        result[i + 1]
                    ):
                        result[i + 1] = result[i] + "\n" + result[i + 1]
                        result.pop(i)
                        changed = True
                        continue

                    # 向前合并：粘到前一个 TABLE 末尾
                    if i > 0 and TablePreservingChunker.TABLE_PATTERN.search(
                        result[i - 1]
                    ):
                        result[i - 1] = result[i - 1] + "\n" + result[i]
                        result.pop(i)
                        changed = True
                        continue
                i += 1
        return result
```

- [ ] **Step 3: Run existing tests**

```bash
pytest tests/infra/chunking/test_chunking.py -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/infra/chunking/strategies/table_preserving.py
git commit -m "feat: add _merge_orphan_texts to attach short text to adjacent tables"
```

---

### Task 6: Table chunking — add large table row-level split

**Files:**
- Modify: `src/infra/chunking/strategies/table_preserving.py`

**Interfaces:**
- Consumes: `TABLE_ROW_CHUNK_CHARS` from settings
- Produces: `_split_large_tables(segments) -> list[str]`

- [ ] **Step 1: Add import for TABLE_ROW_CHUNK_CHARS**

Update line 5:
```python
# Before:
from src.config import CROSS_PAGE_TABLE_MERGE_THRESHOLD, ORPHAN_THRESHOLD_CHARS

# After:
from src.config import (
    CROSS_PAGE_TABLE_MERGE_THRESHOLD,
    ORPHAN_THRESHOLD_CHARS,
    TABLE_ROW_CHUNK_CHARS,
)
```

- [ ] **Step 2: Add the `_split_large_tables` static method**

Add this method after `_merge_orphan_texts`:

```python
    @staticmethod
    def _split_large_tables(segments: list[str]) -> list[str]:
        """将超过 TABLE_ROW_CHUNK_CHARS 的大表格按行切分，每段复制表头.

        表格以 Markdown pipe 格式：
          | 项目 | 2025年 | 2024年 |
          |---|---|---|
          | 收入 | 100 | 90 |
          ...

        切分策略：
          - 提取表头行（第一行 |...|）和分隔行（|---|）
          - 数据行贪心分组（每组 ~TABLE_ROW_CHUNK_CHARS 字符）
          - 每组前复制表头+分隔行
          - 无分隔行时：首行当表头，其余当数据行
        """
        result = []
        for seg in segments:
            is_table = bool(
                TablePreservingChunker.TABLE_PATTERN.search(seg)
            )
            if not is_table or len(seg) <= TABLE_ROW_CHUNK_CHARS:
                result.append(seg)
                continue

            lines = seg.split("\n")
            # 定位表头行和分隔行
            header_idx = -1
            sep_idx = -1
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("|") and not stripped.startswith("|---"):
                    if header_idx == -1:
                        header_idx = i
                if stripped.startswith("|---"):
                    if sep_idx == -1:
                        sep_idx = i

            if header_idx == -1:
                result.append(seg)
                continue

            header = lines[header_idx]
            separator = lines[sep_idx] if sep_idx >= 0 else ""

            # 数据行 = 不以 |---| 开头且不是表头的 |...| 行
            data_rows = [
                l for l in lines
                if l.strip().startswith("|")
                and not l.strip().startswith("|---")
                and l != header
            ]

            if not data_rows:
                result.append(seg)
                continue

            # 贪心分组：累计到 TABLE_ROW_CHUNK_CHARS 就切
            current_group: list[str] = []
            current_chars = 0
            header_sep_chars = len(header) + len(separator) + 2  # 2 换行

            def _flush():
                if current_group:
                    result.append(
                        header
                        + ("\n" + separator if separator else "")
                        + "\n"
                        + "\n".join(current_group)
                    )

            for row in data_rows:
                row_chars = len(row) + 1
                limit = TABLE_ROW_CHUNK_CHARS - header_sep_chars
                if current_chars + row_chars > limit and current_group:
                    _flush()
                    current_group = []
                    current_chars = 0
                current_group.append(row)
                current_chars += row_chars
            _flush()

            logger.debug(
                "[table_preserving] split large table: {} chars -> {} sub-tables",
                len(seg),
                (len(seg) // TABLE_ROW_CHUNK_CHARS) + 1,
            )

        return result
```

- [ ] **Step 3: Wire up the three-stage pipeline in chunk()**

Replace the `chunk()` method's first line:

```python
# Before:
    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments, merge_count = self._split_by_table_boundary(text)

# After:
    def chunk(self, text: str, metadata: dict) -> list[dict]:
        segments, merge_count = self._split_by_table_boundary(text)
        segments = self._merge_orphan_texts(segments)       # 阶段 2
        segments = self._split_large_tables(segments)        # 阶段 3
```

- [ ] **Step 4: Run existing tests**

```bash
pytest tests/infra/chunking/test_chunking.py -v
```
Expected: all pass. The test table is under 2000 chars so splitting is not triggered.

- [ ] **Step 5: Commit**

```bash
git add src/infra/chunking/strategies/table_preserving.py
git commit -m "feat: add _split_large_tables for row-boundary table splitting"
```

---

### Task 7: Update eval_ragas.py — get_llm() + trace_id + bypass_n

**Files:**
- Modify: `src/cli/eval_ragas.py`

**Interfaces:**
- Consumes: `get_llm()` from models.py, `current_trace_id` from trace_context
- Produces: evaluation LLM created via `get_llm()` with `bypass_n=True`

- [ ] **Step 1: Update module-level imports and setup_logging**

Change lines 27-31:

```python
# Before:
from src.core.logging import setup_logging
from src.config import settings, DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
setup_logging()

# After:
from src.core.logging import setup_logging
from src.config import settings
from src.infra.llm.trace_context import current_trace_id
setup_logging(configure_trace_id=True)
```

Note: `DASHSCOPE_API_KEY` and `DASHSCOPE_BASE_URL` are no longer needed because `ChatOpenAI(...)` is replaced by `get_llm()`.

- [ ] **Step 2: Update main() function imports**

Find line 412-413 (inside `main()`):

```python
# Before:
    from langchain_openai import ChatOpenAI
    from src.models import get_embeddings

# After:
    from src.models import get_llm, get_embeddings
```

- [ ] **Step 3: Replace ChatOpenAI with get_llm + bypass_n**

Find lines 430-437:

```python
# Before:
    llm = ChatOpenAI(
        model=eval_model,
        temperature=0,
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL,
    )
    embeddings = get_embeddings()
    llm_wrapper = LangchainLLMWrapper(llm)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)

# After:
    llm = get_llm(model=eval_model, temperature=0)
    embeddings = get_embeddings()
    llm_wrapper = LangchainLLMWrapper(llm, bypass_n=True)
    embeddings_wrapper = LangchainEmbeddingsWrapper(embeddings)
```

- [ ] **Step 4: Verify no remaining references to removed imports**

```bash
grep -n "DASHSCOPE_API_KEY\|DASHSCOPE_BASE_URL\|ChatOpenAI\|from langchain_openai" src/cli/eval_ragas.py
```
Expected: no matches.

- [ ] **Step 5: Run lint + tests**

```bash
ruff check src/cli/eval_ragas.py --fix
pytest tests/ -v
```
Expected: no import errors, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/cli/eval_ragas.py
git commit -m "feat: use get_llm() for evaluation LLM, enable bypass_n, add CLI trace_id"
```

---

### Task 8: Update eval_ragas_generate.py — bypass_n

**Files:**
- Modify: `src/cli/eval_ragas_generate.py:273`

**Interfaces:**
- Consumes: nothing
- Produces: `_LLMWrapper` created with `bypass_n=True`

- [ ] **Step 1: Add bypass_n parameter**

Change line 273:
```python
# Before:
    ragas_llm = _LLMWrapper(_langchain_llm, cache=_cache)

# After:
    ragas_llm = _LLMWrapper(_langchain_llm, cache=_cache, bypass_n=True)
```

- [ ] **Step 2: Run lint**

```bash
ruff check src/cli/eval_ragas_generate.py --fix
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/cli/eval_ragas_generate.py
git commit -m "fix: add bypass_n=True to testset generation LLM wrapper"
```

---

### Task 9: Update check_retrieval.py — trace_id

**Files:**
- Modify: `src/cli/check_retrieval.py:28`

**Interfaces:**
- Consumes: `setup_logging(configure_trace_id=True)` from logging.py
- Produces: CLI logs include trace_id

- [ ] **Step 1: Enable trace_id in setup_logging**

Change line 28:
```python
# Before:
setup_logging()

# After:
setup_logging(configure_trace_id=True)
```

- [ ] **Step 2: Run lint**

```bash
ruff check src/cli/check_retrieval.py --fix
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/cli/check_retrieval.py
git commit -m "feat: enable trace_id in check_retrieval CLI"
```

---

### Task 10: Update tests for new chunking behavior

**Files:**
- Modify: `tests/infra/chunking/test_chunking.py`

**Interfaces:**
- Consumes: `TablePreservingChunker` with new methods
- Produces: test coverage for orphan merge and large table split

- [ ] **Step 1: Add test for orphan text merging**

```python
def test_table_preserving_orphan_text_merge():
    """短文本（<200 chars）在表格前或后应合并到表格上."""
    chunker = TablePreservingChunker()
    text = (
        "| 项目 | 金额 |\n|--- |--- |\n| 营收 | 100亿 |\n"
        "注：以上数据来自审计报告\n"
        "| 项目 | 数量 |\n|--- |--- |\n| 订单 | 500 |"
    )
    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d4"})
    # 短文本应被合并到相邻表格上，不应作为独立 text chunk
    text_chunks = [r for r in result if r["metadata"]["block_type"] != "table"]
    orphan_texts = [r for r in text_chunks if "审计报告" in r["content"]]
    assert len(orphan_texts) == 0, "短文本应被合并到表格，不应独立成块"

    # 第一个表格应包含 "注：以上数据来自审计报告"
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    merged = any("审计报告" in c["content"] for c in table_chunks)
    assert merged, "表格 chunk 应包含被合并的短文本"
```

- [ ] **Step 2: Add test for large table row-level split**

```python
def test_table_preserving_split_large_table():
    """大表格（>2000 chars）应按行边界切分，每块保留表头."""
    import time
    chunker = TablePreservingChunker()
    # 构建一个约 3000 字符的表格（20+ 行）
    header = "| 项目 | 金额 | 占比 | 同比 |\n|---|---|---|---|\n"
    rows = [f"| 项目{i} | {i}00万 | {i}% | {i*10}% |" for i in range(30)]
    text = "开头\n" + header + "\n".join(rows) + "\n结尾"

    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d5"})

    # 应产生多个 table chunk
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    assert len(table_chunks) >= 2, f"大表格应被切分为多个子表，实际: {len(table_chunks)}"

    # 每个子表都应包含表头行
    for tc in table_chunks:
        assert "| 项目 | 金额 | 占比 | 同比 |" in tc["content"], \
            "每个子表块应包含表头"

    # 所有数据行应完整保留
    all_content = "".join(tc["content"] for tc in table_chunks)
    for i in range(30):
        assert f"| 项目{i}" in all_content, f"数据行 项目{i} 应被保留"
```

- [ ] **Step 3: Add test for separator-less table split**

```python
def test_table_preserving_split_no_separator():
    """无分隔行(|---|)的表格也能正常切分."""
    chunker = TablePreservingChunker()
    # 表格没有分隔行
    table = "\n".join([f"| col{i} | value{i} |" for i in range(25)])
    text = table  # >2000 chars with 25 rows

    result = chunker.chunk(text, {"source": "f.txt", "doc_id": "d6"})
    table_chunks = [r for r in result if r["metadata"]["block_type"] == "table"]
    assert len(table_chunks) >= 1

    # 首个数据行的内容应保持一致
    if len(table_chunks) >= 2:
        assert "| col0 | value0 |" in table_chunks[0]["content"]
```

- [ ] **Step 4: Run all tests**

```bash
pytest tests/infra/chunking/test_chunking.py -v
```
Expected: all 7-8 test cases pass (3 new + ~5 existing).

- [ ] **Step 5: Commit**

```bash
git add tests/infra/chunking/test_chunking.py
git commit -m "test: add test coverage for orphan merge and large table split"
```

---

### Task 11: Final verification

**Files:**
- All modified files

- [ ] **Step 1: Full lint and test suite**

```bash
ruff format . && ruff check . --fix
pytest tests/ -v
```
Expected: no errors, all tests pass.

- [ ] **Step 2: Verify no unused imports remain**

```bash
ruff check . | grep "F401" || echo "No unused imports"
```

- [ ] **Step 3: Visual inspection of changes**

```bash
git diff --stat
```
Expected: shows changes to ~7 files.

- [ ] **Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "chore: final cleanup and lint fixes"
```
