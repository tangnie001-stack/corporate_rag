## Context

`eval_ragas.py` 包含两种模式：标准模式（对已有知识库评估）和 Benchmark 模式（自动创建临时 KB 跑评估）。项目转向父子分块后，Benchmark 模式因使用单 `--chunk-size` 参数已无法反映实际分块策略，失去意义。

同时，根据 RAGAS 评估最佳实践（调研 5 篇业界文章）：评估 LLM 的 temperature 必须为 0 以保证结果确定性；建议评估 LLM 与生产 LLM 分离。

## Goals / Non-Goals

**Goals:**
- 删除 Benchmark 模式（`--chunk-size`、`setup_benchmark_kb`、`cleanup_benchmark_kb`）
- `--kb-name` 改为必填，新增 `--list-kbs` 列出可用知识库
- 新增 `RAGAS_LLM_MODEL` 配置项，评估 LLM 独立创建（temperature=0）
- `--check` 阈值从 20 提高到 50

**Non-Goals:**
- 不改变 `compare_retrieval.py`（已传 `--kb-name` 参数，不受影响）
- 不改变 CSV/Markdown 报告格式
- `overall_score` 照常写入 eval_report 表

## Decisions

1. **评估 LLM 独立创建**：在 `eval_ragas.py` 中用 `ChatOpenAI(model=..., temperature=0)` 创建，不修改 `get_llm()` 全局行为。`RAGAS_LLM_MODEL` 为空时回退到 `LLM_MODEL`。

2. **`--kb-name` 必填**：去掉 `default="rag_eval"`，argparse 自动报错。`--list-kbs` 列完直接退出。

3. **`--check` 双模式**：独立模式（仅传 `--check`）不达标时 `exit(1)` 并输出引导；伴随模式（与其他参数一起传）不达标时 warning 不阻断。

## Risks / Trade-offs

- `RAGAS_LLM_MODEL` 默认回退到生产模型，可能产生评估者 self-bias。用户可手动设为更强或不同族的模型来避免。

## 参考

完整设计文档见 `docs/superpowers/specs/2026-07-18-eval-ragas-refactor-design.md`
