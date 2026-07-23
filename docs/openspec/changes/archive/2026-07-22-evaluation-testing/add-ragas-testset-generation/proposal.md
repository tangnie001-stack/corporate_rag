## Why

当前手工维护的 `ragas_pairs.py` 中 50 条 QA 对全部是单文档检索问题，无法覆盖跨文档推理和多跳检索场景，评估覆盖面不足。同时手工维护 QA 对随文档增多不可持续。

## What Changes

- **新增** `--generate` CLI 模式：从 MinIO 取文档 → ragas TestsetGenerator → 自动生成 QA 对，保存到 `data/ragas/testset_{kb_id}_vN.json`（按已有版本自增）
- **修改** 评估流程：无 `--generate` 时自动加载 `data/ragas/` 下最新版本测试集进行评估
- **删除** `src/config/ragas_pairs.py`（手工 QA 对）
- **删除** `--check` 参数
- **升级** ragas 从 0.3.1 到 0.4.3
- **新增** `langchain-community` 显式依赖 + vertexai stub 修复

## Capabilities

### New Capabilities
- `testset-generation`: 从知识库文档自动生成多跳 QA 测试集，含版本管理和 metadata

### Modified Capabilities
<!-- 无现有 spec 需要修改 -->

## Impact

- `src/cli/eval_ragas.py` — 保留 CLI 入口+评估逻辑，新增 `--generate`/`--size`/`--model` 参数自动触发路由
- `src/cli/eval_ragas_generate.py` — **新增**，测试集生成逻辑（MinIO 下载、解析、TestsetGenerator、JSON 写入）
- `src/config/settings.py` — 新增 `RAGAS_TEST_SIZE` 配置
- `pyproject.toml` — ragas 版本锁定、新增 langchain-community 依赖
- `data/ragas/` — 新目录，存放生成的测试集 JSON
- `src/config/ragas_pairs.py` — 删除
- `.venv` — 新增 vertexai stub 文件（修复 langchain-community 0.4 兼容性）
