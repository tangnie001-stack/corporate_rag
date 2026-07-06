## Why

原 MVP 规划中的核心技术栈（向量检索、RAG 问答链路）已在 Phase 2 基础设施重构中提前实现。当前项目已具备完整的文档解析→向量入库→语义检索→Rerank 精排→LLM 流式生成→引用溯源的全链路能力。

当前 gap：缺乏系统性的 RAGAS 评估覆盖、多策略对比实验、以及用户端的质量体验打磨。

## What Changes

### 已完成（无需重复实现）

- 向量检索：ChromaDB PersistentClient 封装，支持按知识库/文档增删查、全局搜索
- RAG 链路：检索 → DashScope Rerank 精排 → Prompt 拼装 → Qwen-max 流式生成 → 引用提取
- 对话历史：Redis 优先 + 内存降级、滑动窗口、MySQL 持久化
- 评估脚本：RAGAS 评估（faithfulness/answer_relevancy/context_precision/context_recall），7 组 QA 测试对
- CLI 工具：check_chunks 质量报告、check_retrieval 检索测试

### 本次新增

1. **评估体系完善** — 扩展 QA 测试对覆盖（目标 20+），补充 chunk_size 对比实验
2. **检索质量优化** — 解决边缘案例（短查询、跨文档聚合），提升 rerank 后 N 的调优
3. **演示与验证** — 端到端演示流程、RAGAS 报告存档、质量达标验证

## Capabilities

### New Capabilities

- `evaluation-pipeline`: RAGAS 评估全流程自动化，含 QA 测试集管理、批量评估、报告生成
- `retrieval-quality`: 检索质量优化，含多版本 chunk_size 对比、rerank 调参、边缘案例修复
- `demo-verification`: 端到端演示流程、测试报告存档、质量门禁检查

### Modified Capabilities

- `rag-generation`: 补充流式响应的错误处理和降级策略
- `chat-interface`: 前端错误状态展示优化（检索失败/LLM 超时等用户可感知的边界情况）

## Impact

- `src/eval_ragas.py` — 扩展评估逻辑
- `src/config/qa_pairs.py` — 增加 QA 测试对
- `src/rag_chain.py` — 检索质量参数调优
- `src/api/routes/chat.py` — 错误处理增强
- `nginx/html/` — 前端边界状态展示
- 新增 `reports/` — RAGAS 评估报告存档
