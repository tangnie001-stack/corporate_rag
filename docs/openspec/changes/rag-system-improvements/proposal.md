## Why

RAG 系统在生产运行中发现三个独立问题需要优化：
1. LLM 调用入口分散，无法统一监控和后续切换后端
2. CLI 模式日志缺少 trace_id，无法串联全链路排查
3. 财务报表的大表格 chunk 超过 4000 字符，导致 RAGAS 评估 NLI prompt 超时

三个问题互不阻塞，但都有明确的收益场景。

## What Changes

- **`models.py` `get_llm()` 收口**：支持 `**kwargs` 透传，让 eval_ragas.py 也走统一入口
- **CLI trace_id 注入**：在 `logging.py` 的 patcher 中自动生成 trace_id，覆盖所有 CLI 入口
- **大表格行级切分**：去掉跨页合并的大小上限，新增按行切分 + 复制表头逻辑
- **残差短文本合并**：将 <200 字符的孤立文本粘到相邻表格上
- **两处 `LangchainLLMWrapper` 加 `bypass_n=True`**

## Capabilities

### New Capabilities

- `llm-client`: 统一的 LLM 实例化入口，支持参数透传和未来后端切换
- `cli-trace`: CLI 模式 trace_id 自动生成，串联全链路日志
- `table-chunking`: 大表格行级保护式切分 + 残差短文本合并

### Modified Capabilities

- 无（不改现有 spec 级别的行为）

## Impact

### Files modified
- `src/models.py` — `get_llm()` 加 `**kwargs`
- `src/core/logging.py` — `_setup_trace_id_patcher()` 加自动生成逻辑
- `src/cli/eval_ragas.py` — 替换 `ChatOpenAI` 为 `get_llm()`，加 `bypass_n=True`
- `src/cli/eval_ragas_generate.py` — 加 `bypass_n=True`
- `src/cli/check_retrieval.py` — `setup_logging(configure_trace_id=True)`
- `src/infra/chunking/strategies/table_preserving.py` — 三段式流水线（去上限、短文本粘附、行级切分）
- `src/config/settings.py` — 新增 `TABLE_ROW_CHUNK_CHARS`、`ORPHAN_THRESHOLD_CHARS`

### Dependencies
- 无外部依赖变更
