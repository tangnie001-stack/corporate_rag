## 1. 配置新增

- [ ] 1.1 `src/config/settings.py` 新增 `RAGAS_LLM_MODEL` 配置项

## 2. eval_ragas.py 精简

- [ ] 2.1 删除 `--chunk-size` 参数及相关处理逻辑
- [ ] 2.2 删除 `setup_benchmark_kb()` 和 `cleanup_benchmark_kb()` 函数
- [ ] 2.3 删除 Benchmark 模式所有分支代码（`temp_kb_name`、`original_chunk_size`）
- [ ] 2.4 `--kb-name` 改为必填（去掉 `default`），新增 `--list-kbs` 参数
- [ ] 2.5 实现 `--list-kbs` 逻辑：查询 knowledge_base 表并打印名称+文档数
- [ ] 2.6 评估 LLM 独立创建：用 `ChatOpenAI(temperature=0, model=RAGAS_LLM_MODEL or LLM_MODEL)` 替代 `get_llm()`
- [ ] 2.7 `--check` 阈值从 20 提高到 50，处理独立/伴随两种行为模式
- [ ] 2.8 更新 `_save_eval_report()`：去掉 `chunk_size` 参数
- [ ] 2.9 更新 Markdown 报告：移除 `chunk_size` 配置行
- [ ] 2.10 更新文件头部 docstring 和 CLI 使用说明

## 3. 验证

- [ ] 3.1 运行 `ruff check .` 确认无错误
- [ ] 3.2 运行 `python -m src.cli.eval_ragas --list-kbs` 验证列出知识库
- [ ] 3.3 运行 `python -m src.cli.eval_ragas`（不传 `--kb-name`）验证必填报错
