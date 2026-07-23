## Why

`RecursiveCharacterTextSplitter(chunk_size=256)` 对稍超阈值（如 300 tokens）的文本段切分时，会在尾部产生仅 30-50 tokens 的碎片 chunk。这些 tiny chunk 语义不完整、检索价值低，还会拖低分块质量评分。当前 validator.py 虽然能检测到 tiny chunk，但只告警不处理。

## What Changes

- `src/api/documents.py`: 新增 `_merge_tiny_chunks()` 后处理函数
- 在 `_process_document_task` 中调整执行顺序：`_enrich_chunk_pages` → `_merge_tiny_chunks` → `validate_chunks`
- 仅对 `parent_child` 和 `table_preserving` 策略生效，`qa` 策略跳过
- 阈值 tokens < 50，对齐 validator.py 的 tiny 判定标准

## Capabilities

### New Capabilities

- `tiny-chunk-merge`: 文档分块完成后自动合并小于 50 tokens 的碎片 chunk 到前一个 chunk

### Modified Capabilities

无

## Impact

- `src/api/documents.py`: 新增约 25 行函数 + 1 行 import + 1 行调用
- 不涉及 parser、chunker、validator 改动
- 已入库文档不受影响（仅新上传文档执行合并）
