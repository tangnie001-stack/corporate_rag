## Context

Phase 2 基础设施重构已实现完整的 RAG 流水线（文档解析 → 向量入库 → 语义检索 → Rerank 精排 → LLM 流式生成 → 引用溯源），且配套了 RAGAS 评估框架、CLI 检查工具、全面的测试覆盖。

当前项目中，RAGAS 评估已具备基础能力（faithfulness/answer_relevancy/context_precision/context_recall 四项指标），但 QA 测试对仅 7 组（覆盖贵州茅台 2024 年报），缺乏多策略对比实验（如不同 chunk_size）和系统性的质量门禁机制。

本 change 的定位是对已有能力进行"验收级"强化——补齐评估覆盖度、调优检索参数、形成可复现的演示流程。

## Goals / Non-Goals

**Goals:**
- 扩展 QA 测试对至 20+ 组，覆盖至少两份测试文档（茅台 + 灿坤）
- 实现 chunk_size（512/768/1024）对比自动化脚本
- RAGAS 评估结果自动存档（data/reports/）
- 检索质量参数调优（TOP_K_RETRIEVAL、TOP_K_RERANK 对比）
- 端到端演示流程走通并记录
- 前端错误/空/边界状态补齐

**Non-Goals:**
- 不引入新的向量数据库（保持 ChromaDB）
- 不替换现有 LLM/Embedding/Rerank 模型（保持 DashScope）
- 不重构已有的 RAG 流水线架构
- 不做新前端页面开发

## Decisions

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| QA 测试对格式 | JSON / Python / YAML | Python（保持 qa_pairs.py 风格） | 与现有 `src/config/qa_pairs.py` 一致，IDE 可做类型检查 |
| 对比实验脚本 | 独立 CLI / eval_ragas 扩展 | `eval_ragas` 扩展 + 新增 `scripts/compare_chunk.py` | eval_ragas 已承担评估职责，对比逻辑独立成脚本避免耦合 |
| 测试文档 | 茅台年报 + 灿坤年报 | 两者均用 | 覆盖 A 股消费类财报两个不同行业，验证泛化能力 |
| Rerank TOP_K 调优 | 固定 / 配置可调 | 保持 config 配置化 | 现有 `TOP_K_RERANK` 已存在，调参只需改 env |
| 报告格式 | CSV / Markdown | CSV（原有格式）+ Markdown 摘要 | CSV 便于后续分析，Markdown 便于人类阅读 |

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|----------|
| RAGAS 评估调用 Qwen 产生 API 费用 | 控制 QA 对数量（20-25 组），单次评估约 2-4 元 |
| 不同 chunk_size 的结果波动大 | 每个配置跑 3 次取平均，消除随机性 |
| 测试 PDF 文档较大导致分块耗时 | 评估脚本加 tqdm 进度条 + 预估时间输出 |
