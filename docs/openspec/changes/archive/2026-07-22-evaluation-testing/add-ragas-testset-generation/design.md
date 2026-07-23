## Context

当前 RAGAS 评估使用 `ragas_pairs.py` 中手工编写的 50 条 QA 对，全部为单文档检索问题。数据流为：手工 QA 对 → 通过 `RAGChain.chat_with_citations()` 生成回答 → `ragas.evaluate()` 评估四指标。

现有依赖 `ragas==0.3.1`，`langchain-community==0.4.2` 已移除 `vertexai` 子模块导致 import 崩溃，需通过 stub 修复。

## Goals / Non-Goals

**Goals:**
- 从知识库文档自动生成多跳 QA 测试集，覆盖单文档和多文档推理场景
- 测试集带版本管理、metadata（生成参数、时间、文档列表）
- 支持按知识库查找和使用对应测试集
- 删除手工维护的 `ragas_pairs.py`

**Non-Goals:**
- 不实现图表理解（视觉元素不在 scope 内）
- 不实现测试集的手工标注/审核 UI
- 不修改文档上传管道（通过 MinIO 按需重解析即可）

## Decisions

### D1: 文档来源 — 从 MinIO 下载重解析
**选择**：方案 A，从 MinIO 下载原始文件 → parser 解析 → 拼完整文本 → 传给 `TestsetGenerator.generate_with_langchain_docs()`
**理由**：`TestsetGenerator` 需要完整文档文本构建知识图谱。从 ChromaDB 取分块碎片会损失跨文档节点关系。
**代价**：每次生成需重解析（~几秒/文档），但对 CLI 脚本可接受。

### D2: 测试集格式 — 单 JSON 文件（含 metadata）
**选择**：`data/ragas/testset_{kb_id}_vN.json`，顶层分 metadata 和 samples 两部分。版本号自增，评估时按 kb_id 扫描 `_v*.json` 取最新。
**理由**：JSONL 不支持顶层 metadata。单 JSON 文件，用 UUID 做文件名无需 sanitize 且不重名。版本号保留历史可回退。

### D3: ragas 版本 — 升级到 0.4.3
**选择**：0.4.3，并新增 `langchain-community` 显式依赖
**理由**：0.4.3 的 `TestsetGenerator` API 稳定，`evaluate()` 依然兼容（虽有 deprecation warning）。
**修复**：`vertexai` 子模块缺失问题通过 stub 文件解决。

### D4: 生成和评估分离
**选择**：`--generate` flag 仅生成测试集，不自动评估；评估用无 flag 模式，自动加载最新测试集
**理由**：生成和评估是不同频率的操作（生成一次，评估多次），分离更灵活。

### D5: 文件拆分 — 生成与评估分为两个文件
**选择**：
- `src/cli/eval_ragas.py` — 保留为 CLI 入口 + 评估逻辑（删减后控制在 400 行内）
- `src/cli/eval_ragas_generate.py` — 新增文件，放测试集生成相关逻辑（MinIO 下载、解析、TestsetGenerator 调用、JSON 写入）
**理由**：`eval_ragas.py` 当前 584 行已超项目 400 行红线，新增 generate 功能后会到 700+。拆分后各自职责清晰，`eval_ragas.py` 通过 `from .eval_ragas_generate import run_generate()` 调用。

### D6: vertexai stub 放在 generate 模块内
**选择**：stub 的自动检查和安装逻辑放在 `eval_ragas_generate.py` 顶部，不放入 `src/infra/`
**理由**：只有 TestsetGenerator 的导入链路触发 `ChatVertexAI` 硬导入，评估流程不需要。放在 generate 文件内更精确，不污染全局 infra。

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 生成 QA 对的 LLM 调用成本 | `settings.RAGAS_TEST_SIZE` 默认 20 可调，首次可 `--size 10` 试水 |
| 生成质量不稳定 | 版本管理保留历史，可回退到旧版本；手工 QA 对作为 v0 baseline 的备份 |
| langchain-community vertexai 后续继续变 | stub 文件只在 `langchain_community.chat_models` 路径下，未来 ragas 升级后可能不再需要 |
| 知识图谱构建结果不可复现 | Testset 本身含生成参数 metadata，保证可追溯 |
