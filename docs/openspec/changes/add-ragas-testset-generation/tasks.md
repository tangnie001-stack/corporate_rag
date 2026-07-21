## 1. 依赖与环境

- [ ] 1.1 更新 pyproject.toml：ragas 升至 0.4.3，新增 langchain-community 显式依赖
- [ ] 1.2 创建 langchain-community vertexai stub 文件，修复 import 兼容性
- [ ] 1.3 验证 ragas 0.4.3 所有 import 正常（evaluate / TestsetGenerator / metrics）

## 2. 配置

- [ ] 2.1 `src/config/settings.py` 新增 `RAGAS_TEST_SIZE: int = 20` 配置项
- [ ] 2.2 `src/config/__init__.py` 确保 `RAGAS_TEST_SIZE` 被重导出

## 3. 核心实现 — 测试集生成（新文件）

- [ ] 3.1 创建 `src/cli/eval_ragas_generate.py`，顶部放 vertexai stub 自动检查/安装逻辑
- [ ] 3.2 实现 `run_generate(kb_name, kb_id, size, model)` 入口函数
- [ ] 3.3 实现文档获取：从 MySQL 查文档列表 → MinIO 下载 → parser 解析 → 拼完整文本
- [ ] 3.4 实现 ragas TestsetGenerator 调用：`from_langchain()` → `generate_with_langchain_docs()`
- [ ] 3.5 实现测试集版本管理：扫描 `testset_{kb_id}_v*.json` → 取最大版本 → +1 → 写入新文件
- [ ] 3.6 写入时用 tmp 文件 + os.replace() 原子操作，防止半成品
- [ ] 3.6 确保 metadata 字段完整：kb_name、version、generated_at、llm_model、testset_size、ragas_version、doc_ids

## 4. 核心实现 — 评估流程改造（eval_ragas.py 瘦身）

- [ ] 4.1 `eval_ragas.py` 新增 `--generate` / `--size` / `--model` CLI 参数，路由到 `eval_ragas_generate.run_generate()`
- [ ] 4.2 实现 `_load_latest_testset()` 函数：按 kb_id 扫描 `testset_{kb_id}_v*.json`，正则提取版本号取最大
- [ ] 4.3 将 main() 中所有 `QUESTIONS`/`GROUND_TRUTH` 引用替换为从 JSON 加载的变量
- [ ] 4.4 删除 `src/config/ragas_pairs.py` 文件及其第 28 行的 import
- [ ] 4.5 删除 `--check` 参数及 `check_qa_count()` 函数
- [ ] 4.6 清理 docstring 中的运行示例，去掉 `--check` 示例

## 5. 验证

- [ ] 5.1 `ruff check .` 无错误
- [ ] 5.2 `pytest tests/ -v` 全部通过
- [ ] 5.3 `python -m src.cli.eval_ragas --kb-name "我的知识库" --generate --size 10` 正常生成
- [ ] 5.4 `python -m src.cli.eval_ragas --kb-name "我的知识库"` 加载生成后的测试集进行评估
- [ ] 5.5 无遗留 `print()`、`# TODO` 或调试代码
