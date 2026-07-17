# eval_ragas.py 精简重构设计

## 背景

当前 `eval_ragas.py` 包含两个运行模式：标准模式（对已有知识库评估）和 Benchmark 模式（自动创建临时 KB 跑评估）。
项目已全面转向父子分块（Parent-Child Chunking），分块策略由 `ChunkRouter` 自动路由，单参数 `--chunk-size`
已无法反映实际分块行为。Benchmark 模式失去意义，需清理。

同时，根据 RAGAS 评估最佳实践（调研 5 篇业界文章），新增以下改进：
- 评估 LLM 独立配置（temperature=0 保证结果确定性）
- `--kb-name` 改为必填，新增 `--list-kbs` 辅助参数
- QA 对数检查阈值从 20 提高到 50

## 改动范围

仅限于 `src/cli/eval_ragas.py` 和 `src/config/settings.py` 两个文件。
`src/cli/compare_retrieval.py` 不受影响（已传 `--kb-name` 参数）。

## 删除内容

### 删除的参数

- `--chunk-size`：Benchmark 模式专用，不再需要

### 删除的函数

- `setup_benchmark_kb()`：创建临时知识库并上传 `test_docs/sample.txt`
- `cleanup_benchmark_kb()`：清理临时知识库

### 删除的逻辑分支

- Benchmark 模式所有代码（`temp_kb_name`、`original_chunk_size` 相关分支）
- `_save_eval_report()` 中的 `chunk_size` 参数
- Markdown 报告中 `chunk_size` 配置行

## 新增内容

### 评估 LLM 独立配置

`src/config/settings.py` 新增：

```python
# RAGAS 评估专用模型（独立于生产 LLM，temperature 固定为 0）
RAGAS_LLM_MODEL: str = os.getenv("RAGAS_LLM_MODEL", "")
```

为空时回退到 `LLM_MODEL`。

`eval_ragas.py` 中不再复用 `get_llm()`，改为独立创建评估 LLM：

```python
llm = ChatOpenAI(
    model=settings.RAGAS_LLM_MODEL or settings.LLM_MODEL,
    temperature=0,
    api_key=settings.DASHSCOPE_API_KEY,
    base_url=settings.DASHSCOPE_BASE_URL,
)
```

temperature=0 保证多次运行结果一致，这是 RAGAS 官方推荐的最佳实践。

### `--list-kbs` 参数

新增参数，列出 MySQL 中所有知识库的名称和文档数，帮助用户选择 `--kb-name`：

```
python -m src.cli.eval_ragas --list-kbs

Available knowledge bases:
  rag_eval          (12 documents)
  我的知识库        (5 documents)
```

实现上查询 `knowledge_base` 表获取名称列表，对每个 KB 统计 `document` 表中的文档数。
列完后直接退出，不执行评估。

## 参数调整

### `--kb-name` 改为必填

去掉 `default="rag_eval"`，不传时 argparse 自动报错：

```
usage: eval_ragas.py [-h] --kb-name KB_NAME
eval_ragas.py: error: the following arguments are required: --kb-name
```

### `--check` 阈值提高到 50

两种行为模式：

1. **独立模式**（仅传 `--check`，不执行评估）：只检查 QA 对数，不达标则 `exit(1)` 并输出引导
2. **伴随模式**（`--check` 与其他参数一起传）：检查后继续执行评估，不达标时打 warning 不阻断

阈值从 20 提高到 50。引导信息示例：

```
QA pairs only N (< 50). Add more questions and ground_truth to src/config/ragas_pairs.py.
建议覆盖以下类型：事实查询、推理查询、多上下文查询、边界案例。
```

## 保留不变的部分

| 参数/功能 | 说明 |
|-----------|------|
| `--output` | CSV 输出路径 |
| `--session-id` | 评估会话 ID |
| `--gate` | 多维度质量门禁（每个指标独立阈值，符合业界实践） |
| `--check` | QA 对数检查（阈值改为 50） |
| CSV 输出 | 含每行详细分数 |
| Markdown 报告 | 含平均值摘要 |
| MySQL `eval_report` 表写入 | 保留，`overall_score` 照常写入 |

## compare_retrieval.py 兼容性

`compare_retrieval.py` 已传 `--kb-name` 参数，不受本次改动影响。无需任何修改。
