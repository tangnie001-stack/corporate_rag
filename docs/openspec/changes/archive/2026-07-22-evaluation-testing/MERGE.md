# 评估与测试（合并归档）

归档日期：2026-07-22

## 包含的 Change

1. **add-ragas-testset-generation** — RAGAS 测试集自动生成 CLI（`--generate`模式），替换手工 QA 对
2. **api-test-fix** — API 测试修复：mock 路径更新、新增未覆盖端点的测试、公共 conftest 提取
3. **eval-ragas-refactor** — RAGAS 评估重构：删除 Benchmark 模式、`--kb-name` 改为必填、独立评估 LLM 配置

## 共同主题
测试覆盖和评估流程相关的改进。
