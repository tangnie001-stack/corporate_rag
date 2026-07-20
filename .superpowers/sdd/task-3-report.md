# Task 3 Report — 测试集版本扫描与 JSON Schema

## 1. 概要

在 `src/cli/eval_ragas_generate.py` 中追加了两个函数：

- **`_find_next_version(kb_id) -> int`**：扫描 `data/ragas/testset_{kb_id}_v*.json` 文件，返回最大版本号 + 1（从 1 开始）；目录不存在时优雅处理（创建空目录逻辑由调用方完成）。
- **`_load_latest_testset(kb_id) -> tuple[list[str], list[str]]`**：加载指定知识库的最新版本测试集，返回 `(questions, ground_truth)` 元组；无文件时抛出 `FileNotFoundError`。

同时添加了 `RAGAS_DATA_DIR = "data/ragas"` 常量和 JSON schema 注释。

## 2. 提交记录

Commit `f8e4330474287a49e110f3defb9a89054c8a8d72`:
`feat: add version scan and testset loader`

修改文件：`src/cli/eval_ragas_generate.py`（+86 行）

## 3. 验证结果

```text
Import OK
_find_next_version("test") = 1
RAGAS_DATA_DIR = data/ragas
FileNotFoundError raised as expected: No testset found for kb_id=test. 请先运行 --generate 生成测试集 ...
```

- 导入无错误
- `_find_next_version` 在无文件时正确返回 1
- `_load_latest_testset` 在无文件时正确抛出 `FileNotFoundError`
- 没有违反层间调用规则（仅依赖标准库 + loguru）

## 4. 注意事项

无。`data/ragas/` 已在 `.gitignore` 中，代码仅通过 `Path.exists()` 判断目录是否存在，不会创建空文件。
