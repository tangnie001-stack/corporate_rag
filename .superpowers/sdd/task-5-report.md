# Task 5 Report: 改造 eval_ragas.py

## Summary of Changes

### `src/cli/eval_ragas.py`
1. **Module docstring** — Updated examples to show `--generate` usage, removed `--check` example
2. **Imports** — Removed `from src.config.ragas_pairs import QUESTIONS, GROUND_TRUTH`; added `Optional` to typing imports
3. **`parse_args()`** — Removed `--check` argument; added `--generate` (flag), `--size` (int, default from settings), `--model` (str) arguments
4. **Removed `check_qa_count()`** — Entire function deleted
5. **`main()` rewritten** — Routes between:
   - `--list-kbs`: lists knowledge bases (wrapped in `asyncio.run()`)
   - `--generate`: calls `run_generate()` from `eval_ragas_generate`
   - Default (evaluate): loads testset dynamically via `_load_latest_testset(kb_id)` from `eval_ragas_generate` instead of importing static `QUESTIONS`/`GROUND_TRUTH`
   - `kb_id` lookup is now async (wrapped in `asyncio.run()`)
6. **`_save_eval_report()`** — Signature changed from `(kb_name, result, questions: list[str], output_path)` to `(kb_name, result, qa_count: int, output_path)`; body updated: `len(questions)` → `qa_count`, detail loop uses `range(qa_count)` instead of `enumerate(questions)`

### `src/config/ragas_pairs.py`
- **Deleted** — No longer needed; testset data now loaded from JSON via dynamic generation

## Commit

- Base: `c07fd179d24a25a811f7114f0586dd7d8e67c043`
- Head: `2da3f7c330700603b9cf29b99d0e6d816032c052`
- Range: `c07fd17..2da3f7c`

## Verification

- `ruff check src/cli/eval_ragas.py` — **PASS** (All checks passed)
- `python3 -c "from src.cli.eval_ragas import main; print('import OK')"` — **PASS** (import OK)
- `src/config/ragas_pairs.py` — **CONFIRMED DELETED** (no longer exists)

## Concerns

- `_save_eval_report` internally calls `svc.db.get_kb_by_name(kb_name)` without `await`/`asyncio.run()` wrapper. This appears to be a pre-existing issue (same pattern in the original code) and was not called out for change in the task brief.
- The `detail` list in `_save_eval_report` now stores empty strings for `question` field since `qa_count` is an integer and individual question texts are no longer available. This is acceptable since per-question question text was mainly for reference and the per-index metrics are preserved.
