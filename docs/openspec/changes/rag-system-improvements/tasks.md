## 1. get_llm() 收口

- [ ] 1.1 `src/models.py`: `get_llm()` 加入 `**kwargs` 参数，透传给 `ChatOpenAI`
- [ ] 1.2 `src/cli/eval_ragas.py`: 替换 `ChatOpenAI(...)` 为 `get_llm(model=eval_model, temperature=0)`，去掉 `ChatOpenAI` import，去掉 `DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL` import，改为 `from src.models import get_llm, get_embeddings`
- [ ] 1.3 `src/cli/eval_ragas_generate.py`: `_LLMWrapper(_langchain_llm, cache=_cache)` 加 `bypass_n=True`

## 2. CLI 日志 trace_id

- [ ] 2.1 `src/core/logging.py`: `_setup_trace_id_patcher()` 中，在安装 patcher 前检测 `contextvar` 是否为空，为空则自动生成 `trace_<uuid>`
- [ ] 2.2 `src/cli/eval_ragas.py`: `setup_logging()` 改为 `setup_logging(configure_trace_id=True)`
- [ ] 2.3 `src/cli/check_retrieval.py`: `setup_logging()` 改为 `setup_logging(configure_trace_id=True)`

## 3. 表格三段式流水线

- [ ] 3.1 `src/config/settings.py`: 新增 `TABLE_ROW_CHUNK_CHARS=2000`、`ORPHAN_THRESHOLD_CHARS=200`
- [ ] 3.2 `src/infra/chunking/strategies/table_preserving.py`: 跨页合并去掉两处 `MAX_TABLE_CHARS` 上限检查（line 95-96, 111-112），改为不限大小
- [ ] 3.3 `src/infra/chunking/strategies/table_preserving.py`: 新增 `_merge_orphan_texts()` 方法，将 <200 字符的孤立短文本粘到相邻 TABLE segment
- [ ] 3.4 `src/infra/chunking/strategies/table_preserving.py`: 新增 `_split_large_tables()` 方法，将 >2000 字符的 TABLE segment 按行边界切分、复制表头
- [ ] 3.5 `src/infra/chunking/strategies/table_preserving.py`: `chunk()` 方法中按顺序调用：`_split_by_table_boundary()` → `_merge_orphan_texts()` → `_split_large_tables()`

## 4. 验证

- [ ] 4.1 `ruff format . && ruff check . --fix` 无错误
- [ ] 4.2 `pytest tests/ -v` 全部通过
- [ ] 4.3 运行 `python -m src.cli.eval_ragas --kb-id ... --testset-version 7` 验证评估可跑通，日志含 trace_id
- [ ] 4.4 检查表格切分日志（`[table_preserving] split large table`）确认大表格被正确切分
