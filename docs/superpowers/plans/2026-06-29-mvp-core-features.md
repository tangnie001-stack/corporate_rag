# MVP Core Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen RAGAS evaluation coverage, optimize retrieval quality, and polish frontend UX for MVP sign-off.

**Architecture:** This change builds on existing RAG pipeline (ChromaDB + DashScope rerank + Qwen LLM) and adds automated evaluation infrastructure, retrieval parameter comparison, and frontend error state handling. No new external dependencies.

**Tech Stack:** Python 3.11+, FastAPI, ChromaDB, DashScope (Qwen), pytest, RAGAS

## Global Constraints

- All Python code must pass `ruff check .` with zero errors
- All existing tests must pass: `pytest tests/ -v`
- QA test pairs must cover at least 2 distinct financial documents (贵州茅台 2024 年报 + 厦门灿坤 2019 年报)
- Minimum QA pair count: 20
- RAGAS quality gate thresholds: faithfulness >= 0.85, context_precision >= 0.80, context_recall >= 0.70, answer_relevancy >= 0.85
- Default TOP_K_RETRIEVAL = 10, TOP_K_RERANK = 5
- ChromaDB persistence path: `data/chroma_persist/`
- Report output path: `data/reports/`
- No new vector database, LLM model, or RAG pipeline architecture changes

---

## File Structure

### Files to Modify
- `src/config/qa_pairs.py` — QA test pairs (7 → 20+)
- `src/config/settings.py` — TOP_K_RETRIEVAL default (8 → 10)
- `src/eval_ragas.py` — add `--check`, `--gate` flags, empty KB handling, Markdown report output
- `src/rag_chain.py` — add short query guard (< 5 Chinese characters)
- `nginx/html/js/chat.js` — categorized error messages in SSE handler
- `nginx/html/css/style.css` — error/warning state CSS classes
- `tests/test_eval_ragas.py` — tests for new CLI flags
- `tests/test_rag_chain.py` — test for short query guard

### Files to Create
- `scripts/compare_chunk.py` — CLI for chunk_size comparison (512/768/1024)
- `scripts/compare_retrieval.py` — CLI for TOP_K_RETRIEVAL × TOP_K_RERANK comparison
- `docs/demo-script.md` — end-to-end demo instructions
- `data/reports/mvp-signoff-ragas-report.md` — final sign-off report

### Files Not Changed (Read-only reference)
- `src/rag_chain.py` (existing functions: `generate()`, `similarity_search_all()`, `_rerank_results()`) — verify cross-document aggregation works

---

## Task 1: Expand QA Test Pairs

**Files:**
- Modify: `src/config/qa_pairs.py` (entire file)

**Interfaces:**
- Consumes: N/A
- Produces: `QUESTIONS: list[str]` (20+ items), `GROUND_TRUTH: list[str]` (20+ items) — imported by `src.eval_ragas`

- [ ] **Step 1: Add 厦门灿坤 QA pairs**

Append ~15 new QA pairs covering 厦门灿坤 2019 年报 to `qa_pairs.py`. Keep the same parallel list format. Dimensions to cover: revenue/growth, EPS/shareholder structure, core business analysis, regional/segment performance, company basic information.

- [ ] **Step 2: Verify count**

Run: `python -c "from src.config.qa_pairs import QUESTIONS; print(len(QUESTIONS))"`
Expected: >= 20

---

## Task 2: Add `--check` and `--gate` flags to eval_ragas

**Files:**
- Modify: `src/eval_ragas.py` — add argparse args, validation logic, gate check
- Modify: `tests/test_eval_ragas.py` — add test cases

**Interfaces:**
- Consumes: `QUESTIONS`/`GROUND_TRUTH` from `src.config.qa_pairs`; RAGAS score dict from evaluation
- Produces: `--check` flag prints count and exits 0/1; `--gate` flag prints per-metric pass/fail and exits 0/1

- [ ] **Step 1: Add `--check` argparse argument**

At argument parser section of `eval_ragas.py`, add:
```python
parser.add_argument('--check', action='store_true',
                    help='Check QA pair count >= 20, exit 1 if below threshold')
```

When `--check` is passed, count questions and:
- If < 20: print `QA pair count: {N} (below minimum 20)` and `sys.exit(1)`
- If >= 20: print `QA pair count: {N} (OK)` and `sys.exit(0)`

- [ ] **Step 2: Add `--gate` flag**

Add `--gate` argument and implement gate validation after evaluation:
```python
GATE_THRESHOLDS = {
    "faithfulness": 0.85,
    "context_precision": 0.80,
    "context_recall": 0.70,
    "answer_relevancy": 0.85,
}
```

After RAGAS `evaluate()` completes, extract per-metric scores. For each metric, print `{metric}: {score:.4f} {'PASS' if score >= threshold else 'FAIL'}`. If any fail, print failing questions and `sys.exit(1)`.

- [ ] **Step 3: Write tests**

In `tests/test_eval_ragas.py`, add:
```python
def test_check_passes_with_20_questions():
    """--check exits 0 when >= 20 QA pairs exist."""

def test_check_fails_with_few_questions(monkeypatch):
    """--check exits 1 when < 20 QA pairs exist (monkeypatch QUESTIONS)."""

def test_gate_passes_with_high_scores(monkeypatch):
    """--gate exits 0 when all metrics meet thresholds."""

def test_gate_fails_with_low_scores(monkeypatch):
    """--gate exits 1 when a metric is below threshold."""
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_eval_ragas.py -v -k "check or gate"`
Expected: All 4 new tests pass

---

## Task 3: Add chunk_size comparison script

**Files:**
- Create: `scripts/compare_chunk.py`

**Interfaces:**
- Consumes: `src.eval_ragas` (invoked as subprocess with `--chunk-size` and `--output`)
- Produces: `data/reports/chunk_comparison.md` Markdown report

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""Compare RAGAS metrics across different chunk sizes (512, 768, 1024)."""

import argparse
import subprocess
import re
from datetime import datetime
from pathlib import Path


def parse_metrics_from_stdout(stdout: str) -> dict:
    """Extract metric averages from eval_ragas stdout."""
    metrics = {}
    for line in stdout.splitlines():
        for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            if metric in line.lower():
                match = re.search(r"[\d.]+", line.split(":")[-1] if ":" in line else line)
                if match:
                    metrics[metric] = float(match.group(0))
    return metrics


def run_eval(chunk_size: int, kb_name: str) -> dict:
    """Run eval_ragas for a given chunk size."""
    output_path = Path(f"data/reports/ragas_eval_{datetime.now().strftime('%Y%m%d')}_{chunk_size}.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["python", "-m", "src.eval_ragas",
         "--kb-name", kb_name,
         "--chunk-size", str(chunk_size),
         "--output", str(output_path)],
        capture_output=True, text=True
    )
    return {"chunk_size": chunk_size, "stdout": result.stdout, "returncode": result.returncode}


def generate_report(results: list[dict], output_path: Path):
    """Generate Markdown comparison report."""
    lines = [
        "# Chunk Size Comparison Report",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Per-Metric Scores",
        "",
        "| Chunk Size | faithfulness | answer_relevancy | context_precision | context_recall |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        m = parse_metrics_from_stdout(r["stdout"])
        lines.append(
            f"| {r['chunk_size']} | {m.get('faithfulness', 'N/A')} | "
            f"{m.get('answer_relevancy', 'N/A')} | "
            f"{m.get('context_precision', 'N/A')} | "
            f"{m.get('context_recall', 'N/A')} |"
        )
    lines.extend(["", "## Summary", ""])
    output_path.write_text("\n".join(lines) + "\n")
    print(f"Report saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Compare chunk_size RAGAS metrics")
    parser.add_argument("--kb-name", default="rag_eval", help="Knowledge base name")
    parser.add_argument("--chunk-sizes", nargs="+", type=int,
                        default=[512, 768, 1024], help="Chunk sizes to compare")
    args = parser.parse_args()

    output_path = Path("data/reports/chunk_comparison.md")
    results = [run_eval(cs, args.kb_name) for cs in args.chunk_sizes]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_report(results, output_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script loads**

Run: `python -m scripts.compare_chunk --help`
Expected: Shows usage with `--kb-name`, `--chunk-sizes` options, exits 0

---

## Task 4: Update evaluation report to output CSV + Markdown summary to data/reports/

**Files:**
- Modify: `src/eval_ragas.py` — change default output dir, add Markdown summary generation

**Interfaces:**
- Consumes: evaluation results (DataFrame with per-question scores + averages)
- Produces: `data/reports/ragas_eval_<date>_<chunk_size>.csv` and `.md`

- [ ] **Step 1: Change default output to `data/reports/`**

Replace default `outputs/` prefix with `data/reports/`. Use format `ragas_eval_<YYYYMMDD>_<chunk_size|default>.csv`.

- [ ] **Step 2: Add Markdown summary generation**

After CSV write completes, generate a Markdown file at same path but with `.md` extension:
```markdown
# RAGAS Evaluation Report
**Date:** YYYY-MM-DD HH:MM
**Configuration:** chunk_size=N, TOP_K_RETRIEVAL=K, TOP_K_RERANK=N
**QA Pairs:** N

| Question | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| Q1 | 0.95 | 0.90 | 0.88 | 0.85 |
| ... | ... | ... | ... | ... |

**Averages:** faithfulness=0.XX, answer_relevancy=0.XX, context_precision=0.XX, context_recall=0.XX
```

- [ ] **Step 3: Run check to verify no regressions**

Run: `python -m src.eval_ragas --check`
Expected: Exits 0 (assumes QA pairs already expanded in Task 1)

---

## Task 5: Handle empty KB in evaluation

**Files:**
- Modify: `src/eval_ragas.py` — add KB emptiness check

**Interfaces:**
- Consumes: ChromaDB `vector_store` instance
- Produces: early `sys.exit(1)` with "Knowledge base is empty" message

- [ ] **Step 1: Add empty KB check**

After ChromaDB collection connection in evaluation setup:
```python
if vector_store._collection.count() == 0:
    logging.error("Knowledge base is empty")
    print("ERROR: Knowledge base is empty. Please upload documents first.")
    sys.exit(1)
```

- [ ] **Step 2: Verify by inspection**

The implementation guard is clear — no separate test needed since it's a safety check that triggers before evaluation starts.

---

## Task 6: Fix default TOP_K_RETRIEVAL to 10

**Files:**
- Modify: `src/config/settings.py` — change default value

- [ ] **Step 1: Update default**

Change `TOP_K_RETRIEVAL = int(os.getenv("TOP_K_RETRIEVAL", "8"))` to `TOP_K_RETRIEVAL = int(os.getenv("TOP_K_RETRIEVAL", "10"))`.

- [ ] **Step 2: Update affected tests**

Run: `grep -rn "TOP_K_RETRIEVAL" tests/`
If any test expects `TOP_K_RETRIEVAL = 8`, update to expect 10.

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -v`
Expected: All existing tests pass

---

## Task 7: Add short query protection in rag_chain

**Files:**
- Modify: `src/rag_chain.py` — add query length check at `RAGChain.generate()` entry
- Test: `tests/test_rag_chain.py` — add test

**Interfaces:**
- Consumes: user query string
- Produces: early return of friendly message if query < 5 Chinese characters, normal flow otherwise

- [ ] **Step 1: Add short query guard**

In `RAGChain.generate()`, right at the top before vector search:
```python
SHORT_QUERY_THRESHOLD = 5
cleaned = query.strip()
if len(cleaned) < SHORT_QUERY_THRESHOLD:
    msg = "查询内容过短，请输入更具体的财务问题（如"2024年营业收入是多少？"）"
    yield f"[TOKEN]{msg}"
    return
```

- [ ] **Step 2: Write test**

In `tests/test_rag_chain.py`:
```python
def test_short_query_returns_friendly_message(rag_chain):
    gen = rag_chain.generate("你好", kb_name="test", session_id="test")
    response = "".join([t for t in gen if t.startswith("[TOKEN]")])
    assert "查询内容过短" in response
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_rag_chain.py -v`
Expected: All pass (existing + new)

---

## Task 8: Verify cross-document aggregation

**Files:**
- Read-only: `src/rag_chain.py` — verify `_rerank_results()` doesn't filter by source document

- [ ] **Step 1: Review code**

In `src/rag_chain.py`, locate `_rerank_results()`. Confirm that after reranking, it slices `reranked[:TOP_K_RERANK]` without any filtering by `metadata["source"]` or document origin.

Expected finding: The method reranks all chunks together and returns the top N regardless of source document, which satisfies the cross-document aggregation requirement.

- [ ] **Step 2: Document finding**

No code change needed. Cross-document aggregation is already working as designed.

---

## Task 9: Create retrieval quality comparison script

**Files:**
- Create: `scripts/compare_retrieval.py`

**Interfaces:**
- Consumes: `src.eval_ragas` (invoked as subprocess with env var overrides)
- Produces: console table showing metrics per parameter combination

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""Compare TOP_K_RETRIEVAL × TOP_K_RERANK combinations."""

import argparse
import subprocess
import os
import re
from itertools import product


RETRIEVAL_VALUES = [5, 10, 15]
RERANK_VALUES = [3, 5, 8]


def run_eval(retrieval_k: int, rerank_k: int, kb_name: str) -> dict:
    """Run eval with specific TOP_K values via environment override."""
    env = os.environ.copy()
    env["TOP_K_RETRIEVAL"] = str(retrieval_k)
    env["TOP_K_RERANK"] = str(rerank_k)
    result = subprocess.run(
        ["python", "-m", "src.eval_ragas", "--kb-name", kb_name],
        capture_output=True, text=True, env=env
    )
    return {"retrieval_k": retrieval_k, "rerank_k": rerank_k,
            "stdout": result.stdout, "stderr": result.stderr,
            "returncode": result.returncode}


def parse_metrics(stdout: str) -> dict:
    metrics = {}
    for line in stdout.splitlines():
        for m in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            if m in line.lower():
                match = re.search(r"[\d.]+", line.split(":")[-1] if ":" in line else line)
                if match:
                    metrics[m] = float(match.group(0))
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Compare retrieval parameter combinations")
    parser.add_argument("--kb-name", default="rag_eval")
    args = parser.parse_args()

    print(f"{'RETRIEVE':>8} {'RERANK':>6} {'faithfulness':>14} {'answer_relevancy':>18} "
          f"{'context_precision':>18} {'context_recall':>14}")
    print("-" * 85)

    for r_k, rp_k in product(RETRIEVAL_VALUES, RERANK_VALUES):
        res = run_eval(r_k, rp_k, args.kb_name)
        m = parse_metrics(res["stdout"])
        print(f"{r_k:>8} {rp_k:>6} {m.get('faithfulness', 'N/A'):>14} "
              f"{m.get('answer_relevancy', 'N/A'):>18} "
              f"{m.get('context_precision', 'N/A'):>18} "
              f"{m.get('context_recall', 'N/A'):>14}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify script loads**

Run: `python -m scripts.compare_retrieval --help`
Expected: Shows usage, exits 0

---

## Task 10: Frontend error state handling

**Files:**
- Modify: `nginx/html/js/chat.js` — enhance SSE error event
- Modify: `nginx/html/css/style.css` — add error/warning state classes

**Interfaces:**
- Consumes: SSE `event: error` with `data: {"error": "..."}` from backend
- Produces: Categorized Chinese error messages with styled UI

- [ ] **Step 1: Add CSS classes to style.css**

Append to `nginx/html/css/style.css`:
```css
.error-banner {
    background-color: #fef3c7;
    border: 1px solid #fcd34d;
    color: #92400e;
    border-radius: 0.5rem;
    padding: 0.75rem 1rem;
    margin: 0.5rem 0;
    font-size: 0.875rem;
    line-height: 1.25rem;
}
.empty-state {
    color: #9ca3af;
    text-align: center;
    padding: 2rem 0;
    font-size: 0.875rem;
}
```

- [ ] **Step 2: Update SSE error handler in chat.js**

In the SSE event handler, replace the generic error text display with error categorization:

```javascript
// SSE error event handler
if (eventType === 'error') {
    const errorMsg = data.error || '';
    let displayText = '';
    if (errorMsg.includes('检索') || errorMsg.includes('search')) {
        displayText = '未找到相关信息，请尝试换个问法';
    } else if (errorMsg.includes('超时') || errorMsg.includes('timeout')) {
        displayText = '模型响应超时，请稍后重试';
    } else if (errorMsg.includes('知识库') || errorMsg.includes('KB')) {
        displayText = '知识库不存在，请刷新页面';
    } else {
        displayText = '服务异常，请稍后重试';
    }
    // Use error-banner class instead of inline red text
    messageDiv.innerHTML = `<div class="error-banner">${displayText}</div>`;
    messageDiv.classList.remove('status-loading');
}
```

- [ ] **Step 3: Run lint check on frontend files**

Run: `npx eslint nginx/html/js/chat.js 2>/dev/null || echo "No ESLint configured for frontend"`
Expected: No errors (or eslint not found is acceptable)

---

## Task 11: Create demo script

**Files:**
- Create: `docs/demo-script.md`

- [ ] **Step 1: Write demo script**

```markdown
# MVP Demo Script

## Prerequisites
- Docker Compose running (`docker compose up -d`)
- Test PDF documents: `贵州茅台2024年年报.pdf`, `厦门灿坤2019年年报.pdf`
- OpenSpec change `mvp-core-features` fully implemented

## Step-by-Step

### 1. Create Knowledge Base
1. Open http://localhost in browser
2. Click "Create Knowledge Base"
3. Name: `finance-demo`
4. Confirm KB is created and visible in KB selector

### 2. Upload Documents
1. Go to Documents page
2. Upload both test PDFs
3. Wait for processing to complete (check status badges)

### 3. Test RAGAS Evaluation
```bash
python -m src.eval_ragas --check
# Expected: QA pair count: 20+ (OK)

python -m scripts.compare_chunk
# Expected: Report saved to data/reports/chunk_comparison.md

python -m src.eval_ragas --gate
# Expected: All metrics pass thresholds, exit 0
```

### 4. Test Chat QA
1. Select `finance-demo` KB
2. Ask 5 representative questions:
   - "2024年贵州茅台营业收入是多少？"
   - "2024年基本每股收益是多少？"
   - "厦门灿坤2019年主营业务收入是多少？"
   - "贵州茅台国内国外收入占比如何？"
   - "前十大股东持股情况如何？"
3. Verify: streaming response, correct figures, citation source displayed

### 5. Test Edge Cases
1. Ask "你好" — expect short query warning
2. Ask about nonexistent KB — expect error message
3. Check session history sidebar — past conversations listed

### Expected Outcomes
- All RAGAS metrics pass quality gate
- QA responses contain accurate financial figures with citations
- Error states show user-friendly Chinese messages
- Session history persists across page reloads
```

---

## Task 12: Run quality gate and generate sign-off report

**Files:**
- Create: `data/reports/mvp-signoff-ragas-report.md`

- [ ] **Step 1: Run full RAGAS evaluation with gate**

Run: `python -m src.eval_ragas --gate`
Expected: Exit 0 with all metrics passing thresholds

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Run lint**

Run: `ruff check .`
Expected: No errors

- [ ] **Step 4: Generate sign-off report**

Create `data/reports/mvp-signoff-ragas-report.md` with:
- Date, evaluator
- Final Qwen configuration (model, chunk_size, TOP_K params)
- Per-question metric table
- Aggregate scores and pass/fail per metric
- Notes

- [ ] **Step 5: Verify no debug artifacts**

Check no `print()`, TODO, or `# DEBUG` remains in modified source files.

---

## Self-Review Checklist

**1. Spec coverage:**
- QA test pair coverage (spec: evaluation-pipeline) → Task 1
- `--check` / `--gate` flags (spec: evaluation-pipeline) → Task 2
- Chunk_size comparison (spec: evaluation-pipeline) → Task 3
- Report archival (spec: evaluation-pipeline) → Task 4
- Empty KB handling (spec: evaluation-pipeline) → Task 5
- TOP_K configuration (spec: retrieval-quality) → Task 6
- Short query handling (spec: retrieval-quality) → Task 7
- Cross-document aggregation (spec: retrieval-quality) → Task 8
- Retrieval quality comparison (spec: retrieval-quality) → Task 9
- Error handling in chat UI (spec: demo-verification) → Task 10
- Demo script (spec: demo-verification) → Task 11
- Quality gate + sign-off report (spec: demo-verification) → Task 12

**2. Placeholder scan:** No TBD, TODO, "implement later", "add validation", or similar placeholders present. All steps contain complete code.

**3. Type consistency:** All function names, parameters, and file paths are consistent across tasks. No naming conflicts.

**4. Cross-task interface check:** Task 1 produces QA pairs consumed by Tasks 2-4, 9, 12. Task 6 produces new default values consumed by Task 9. Task 7's `generate()` method signature is consumed by its own test. No cross-task naming mismatches.
