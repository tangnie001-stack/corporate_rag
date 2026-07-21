## 1. 评估管线完善

- [x] 1.1 扩展 QA 测试对：将 `src/config/qa_pairs.py` 从 7 组扩展到 20+ 组，覆盖茅台年报和灿坤年报两份文档的财务指标
- [x] 1.2 新增 `scripts/compare_chunk.py`：自动化对比 chunk_size 512/768/1024 的 RAGAS 指标，输出 Markdown 报告到 `data/reports/chunk_comparison.md`
- [x] 1.3 评估报告存档：`eval_ragas.py` 输出 CSV 时同步生成 Markdown 摘要（日期、配置、逐题得分、聚合均值）到 `data/reports/`
- [x] 1.4 添加 `--check` 参数：校验 QA 对数量是否 >= 20，不足时告警
- [x] 1.5 处理空 KB 场景：评估时知识库无文档则打印错误并 exit(1)

## 2. 检索质量优化

- [x] 2.1 确认 `TOP_K_RETRIEVAL` / `TOP_K_RERANK` 已从环境变量读取，默认值 10/5
- [x] 2.2 短查询防护：在 `rag_chain.py` 的入口处检查 query 长度 < 5 个中文字符时返回友好提示
- [x] 2.3 跨文档聚合验证：确认 `similarity_search_all` 和 `_rerank_results` 能正确处理来自多个文档的混合结果
- [x] 2.4 新增 `scripts/compare_retrieval.py`：对比不同 TOP_K_RETRIEVAL（5/10/15）× TOP_K_RERANK（3/5/8）组合的检索质量

## 3. 前端边界状态处理

- [x] 3.1 `chat.js` 增强：处理检索无结果、LLM 超时、KB 不存在的场景，展示中文友好提示
- [x] 3.2 `chat.js` 增强：网络错误或服务端 5xx 时展示 "服务异常，请稍后重试"
- [x] 3.3 样式补充：`style.css` 新增错误/空状态提示样式（黄色警告条、灰色空状态）

## 4. 演示与验证

- [x] 4.1 编写 `docs/demo-script.md`：端到端演示步骤、预期结果、截图指引
- [x] 4.2 实现 `--gate` 参数：质量门禁检查（faithfulness >= 0.85, context_precision >= 0.80, context_recall >= 0.70, answer_relevancy >= 0.85）
- [x] 4.3 运行完整 RAGAS 评估，生成 `data/reports/mvp-signoff-ragas-report.md`
- [x] 4.4 全流程验证：上传测试文档 → 检索验证 → SSE 流式问答 → 引用溯源 → 会话历史
- [x] 4.5 Ruff 格式检查和测试通过
