## Why

当前 `eval_ragas.py` 包含 Benchmark 模式（自动创建临时 KB 跑评估），但项目已全面转向父子分块（Parent-Child Chunking），分块策略由 `ChunkRouter` 自动路由，单参数 `--chunk-size` 已无法反映实际分块行为。同时，根据 RAGAS 评估最佳实践，需要新增评估 LLM 独立配置（temperature=0）和更好的使用体验。

## What Changes

- 删除 `--chunk-size` 参数和 Benchmark 模式（`setup_benchmark_kb` / `cleanup_benchmark_kb`）
- `--kb-name` 改为必填，新增 `--list-kbs` 列出可用知识库
- 新增 `RAGAS_LLM_MODEL` 配置项，评估 LLM 独立创建（temperature=0）
- `--check` 阈值从 20 提高到 50，不达标时 warning 但不阻断评估

## Capabilities

### New Capabilities
- `ragas-evaluation`: 对指定知识库运行 RAGAS 四指标评估（faithfulness / answer_relevancy / context_precision / context_recall）

### Modified Capabilities
- `evaluation-pipeline`: 移除 `--chunk-size` 参数和 Benchmark 模式；`--kb-name` 改为必填；`--check` 阈值从 20 提高到 50

## Impact

- `src/cli/eval_ragas.py`：删除 Benchmark 模式相关代码，调整参数
- `src/config/settings.py`：新增 `RAGAS_LLM_MODEL` 配置项
- `compare_retrieval.py`：不受影响（已传 `--kb-name`）
