# Tiny Chunk 合并方案设计

## 问题

分块完成后，`ParentChildChunker` 的 `RecursiveCharacterTextSplitter(chunk_size=256)`
对稍超阈值（如 300 tokens）的文本段进行切分时，会产生仅 44 tokens 的尾部碎片
chunk。这些 tiny chunk 语义不完整、检索价值低，还会拖低分块质量评分。

## 方案：后处理合并

在 `_process_document_task` 中，chunking 完成后、入库前，加一道后处理扫描：
将所有 tokens < 50 的 chunk 合并到前一个 chunk 末尾。

### 数据流

```
PDF -> Parser -> ParseResult.chunks[]
  -> join full_text
  -> ChunkRouter.detect_strategy + chunker.chunk()
  -> chunks[]                         -- 此时可能含 tiny chunk
  -> _enrich_chunk_pages(...)         -- 先标页码（full_text 内容命中）
  -> _merge_tiny_chunks(chunks, strategy)  -- 后合并（仅 parent_child/table_preserving）
  -> validate_chunks(...)
  -> VectorStore.add_chunks(...)
```

> 注意执行顺序：`_enrich_chunk_pages` 必须在 `_merge_tiny_chunks` **之前**运行。
> 因为 `full_text` 中 parser chunks 用 `\n\n` 连接，合并后的 content 用 `\n` 连接，
> 如果先合并再标页码，`full_text.find()` 找不到合并后的内容。
> 合并时自动继承前驱 chunk 的 page 字段，无需额外处理。

### 合并规则

```
输入: chunks: list[dict], min_tokens=50
输出: merged: list[dict]

遍历 chunks:
  当前 chunk tokens < min_tokens 且 merged 不为空:
    -> 内容追加到 merged[-1]["content"]
    -> merged[-1]["metadata"]["tokens"] 重新计算
  否则:
    -> 直接追加到 merged

返回 merged
```

边界处理：
- **第一个 chunk 就是 tiny**：等下一个非 tiny 到达后合并到它前面
- **连续多个 tiny**：全部累积到同一个前驱 chunk 上
- **table chunk 也是 tiny**：同样合并（极少出现，但逻辑一致）
- **所有 chunk 都是 tiny**：不合并（此时全文档 < 50 tokens）

### 代码位置

```python
# src/api/documents.py, _process_document_task 内
# 在 _enrich_chunk_pages() 之后、validate_chunks() 之前插入

def _merge_tiny_chunks(
    chunks: list[dict],
    strategy: str,
    min_tokens: int = 50,
) -> list[dict]:
    """将 tokens < min_tokens 的 tiny chunk 合并到前一个 chunk。

    仅对 parent_child 和 table_preserving 策略生效。
    qa 策略的 chunk 是完整问答对，合并会破坏语义结构，跳过。

    Args:
        chunks: chunker.chunk() 输出的 chunk 列表
        strategy: 当前文档的分块策略
        min_tokens: tiny chunk 判定阈值

    Returns:
        合并后的 chunk 列表
    """
    if strategy == "qa":
        return chunks

    merged: list[dict] = []
    for c in chunks:
        tokens = c["metadata"].get("tokens", 0) or BaseChunker.count_tokens(
            c["content"]
        )
        if tokens < min_tokens and merged:
            merged[-1]["content"] += "\n" + c["content"]
            merged[-1]["metadata"]["tokens"] = BaseChunker.count_tokens(
                merged[-1]["content"]
            )
        else:
            merged.append(c)
    return merged
```

注意：`_enrich_chunk_pages` 已在合并前为所有 chunk 标好 page，
合并时不用显式复制 page，前驱 chunk 的 page 自然被保留。

### 涉及改动

| 文件 | 改动 |
|------|------|
| `src/api/documents.py` | 新增 `_merge_tiny_chunks` 函数 + 调整 `_process_document_task` 中调用位置 |
| 无需改动的文件 | parser、chunker、validator、models——均为纯数据后处理 |

### 测试

| 测试 | 描述 |
|------|------|
| tiny chunk 合并到前一个 | 正常 text chunk -> tiny -> 合并成功，page 继承前驱 |
| 首个 chunk 就是 tiny | 不合并，等下一个到达 |
| 连续两个 tiny | 全部合并到同一个前驱 |
| 所有 chunk 都正常 | 无 tiny 时不做任何操作 |
| 空列表 | 正常返回空列表 |
| QA 策略跳过合并 | QA pair < 50 tokens 时不合并，原样保留 |

### 其他说明

- **warning 日志保留**：`validate_chunks` 的 `tiny_chunks` 告警日志保留不删，
  作为安全兜底——万一合并逻辑有改动或失效，仍能捕获 tiny chunk 问题。

### 不涉及内容

- 不改 parser（不碰 PyMuPDF 逻辑）
- 不改 chunker（不碰 ParentChildChunker / TablePreservingChunker）
- 不改 chunk_size / chunk_overlap 等参数
