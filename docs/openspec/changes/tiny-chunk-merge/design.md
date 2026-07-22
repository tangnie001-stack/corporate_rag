## Context

当前 `_process_document_task` 流程中，`ParentChildChunker` 使用 `RecursiveCharacterTextSplitter(chunk_size=256)` 切分文本段。当一段文本 300 tokens 时，splitter 在 ~256 token 处切分，产生 256 + 44 两个 chunk。44 token 的 tiny chunk 语义不完整、检索价值低，且拖低粒度评分。

现有 validator.py 的 tiny chunk 检测只告警不处理。完整的方案分析见 `docs/superpowers/specs/2026-07-22-tiny-chunk-merge-design.md`。

## Goals / Non-Goals

**Goals:**
- 在 chunking 完成后、入库前，自动合并 tokens < 50 的 tiny chunk 到前一个 chunk
- 仅对 `parent_child` 和 `table_preserving` 策略生效
- 保持 `_enrich_chunk_pages` 的页码映射正确性

**Non-Goals:**
- 不涉及 parser、chunker、validator 等底层模块改动
- 不改 chunk_size / chunk_overlap 等参数
- 不涉及已入库文档的重新处理

## Decisions

| 决策 | 选择 | 理由 |
|------|------|------|
| 阈值 | 50 tokens | 对齐 validator.py 的 tiny 判定标准 |
| 合并方向 | 向后合并到前驱 | tiny chunk 和前驱 chunk 语义连续（同一 text segment 切分产物） |
| QA 策略 | 跳过 | QA pair 是完整语义单元，合并会破坏结构 |
| 执行顺序 | `_enrich_chunk_pages` 先于 merge | 防 `full_text.find()` 找不到合并后内容 |
| 元数据继承 | 自动继承前驱的 page/block_type | 前驱已在 `_enrich_chunk_pages` 中标好页码 |
| warning 日志 | 保留不删 | 安全兜底，防合并逻辑失效时遗漏问题 |

## Risks / Trade-offs

| 风险 | 缓解措施 |
|------|---------|
| 合并后 chunk 从 256 膨胀到 ~305 tokens | 仍在 parent_child PARENT_SIZE(1024) 范围内，检索精度影响可忽略 |
| 合并后 page 信息丢失（tiny 来自不同页） | 前驱 page 已由 `_enrich_chunk_pages` 预先标好 |
| merge 后 validator 的 tiny 告警永不触发 | 保留代码作为安全兜底 |
