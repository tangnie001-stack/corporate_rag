## Why

pymupdf4llm Layout 引擎会把跨行单元格拆成两行（如 `购建固定资产、无形资产和其他` + `长期资产支付的现金|63,134,713` 被拆成独立行），导致 table_preserving 分块把"标签行"和"数值行"切开，数值列（如 `63,134,713`）丢失。reranker 对残缺表格低分 → `RERANK_MIN_SCORE=0.3` 剪掉上下文 → Q4 拒答，faithfulness 从基线 0.9333 掉到 0.8889。需要恢复 fitz 的表格完整性（sanitize_cell 拍平跨行单元格），同时保留 pymupdf4llm 的标题树（实体抽取 + heading_path 的数据源）。

## What Changes

- **双通道解析（方案 D 修订版）**：
  - **fitz 通道（主进程，内容唯一权威源）**：`find_tables()` + `sanitize_cell`（单元格内 `\n` → 空格，跨行单元格保持同行）+ blocks 表格区域排除，恢复 Q4 表格完整性。边距过滤拆分：**`HEADER_MARGIN=45`（顶部）/ `FOOTER_MARGIN=80`（底部）**——实测 tencent 页首 5 个标题 + 1 个内容段（y≈52-66）从丢弃中恢复，页脚（`第 N/11页`）仍被剔除。
  - **pm 通道（子进程，仅标题树）**：`import pymupdf4llm` 会设置全局 mupdf 状态，**永久破坏同进程内 `find_tables().extract()` 单元格顺序**（`63,134,713` → `63 134 713\n, ,`，实测不可逆）。因此标题树提取放**子进程**（新增 `src/parsers/pdf_heading_extractor.py`，`python -m` 调用，完整复刻现有 pm 管道输出清洗后标题树 JSON，实测与当前树逐字节一致），主进程不 import pymupdf4llm。并发受 `threading.Semaphore(2)` 限制，扫描件跳过子进程。
- **双通道一致性处理**：
  - pm 标题树（子进程 JSON）回主进程后过 `_clean_heading`（现有 `_extract_heading_tree` 已应用，实测 0 残留）。
  - `_locate_heading_line` 增加**去全部空白**归一化退化匹配（中文无词间空格）：实测命中率 16/26 → 20/26（格式级差异全收敛）。残余缺口 1 个表格内标题（`其他财务资料`，`|...|` 包裹无法精确匹配），静默跳过，接受。
- **串行执行**：主进程 fitz → 子进程 pm（GIL 并行无收益，实测 2.41s 串行最优）。
- **保留既有行为**：表格 4 层保护（chunker 侧不变）、`TABLE_PATTERN` 表格块判定、扫描件检测、`ParseResult` 契约（chunks/total_pages/total_chars/is_scanned/heading_tree）不变。
- **存量重建**：**BREAKING** — 不做兼容迁移，清除重建（复用 `scripts/rebuild_kb_data.py`）。

## Capabilities

### New Capabilities

- `pdf-parsing`: PDF 双通道解析 —— fitz 主进程产出全文+表格（跨行单元格保持同行不拆列，页首内容块保留、页脚剔除），pm 子进程独立产出标题树（不污染主进程表格提取），标题定位支持去空白归一化匹配。

### Modified Capabilities

（无。`chunk-entity-enrichment`/`retrieval-quality` 的既有需求不因实现换源而改变：heading_tree 仍产出、chunk 仍注入实体、prompt 渲染格式不变。）

## Impact

- **修改文件**：
  - `src/parsers/pymupdf_parser.py`（重写为双通道：fitz 全文+表格主进程 + pm 标题树子进程；**移除模块级 `import pymupdf4llm`**）
  - `src/config/settings.py`（`HEADER_FOOTER_MARGIN` 删除，拆为 `HEADER_MARGIN=45` / `FOOTER_MARGIN=80`；子进程超时常量）
  - `src/rag/heading_locator.py`（`_locate_heading_line` 去全部空白归一化匹配）
- **新增文件**：`src/parsers/pdf_heading_extractor.py`（子进程助手：`python -m` 调用，`__main__` 内 import pymupdf4llm 并输出标题树 JSON）
- **依赖**：不变（`pymupdf==1.28.2` + `pymupdf4llm==1.28.2` 都保留）
- **数据**：存量文档清除重建（ChromaDB collection + MySQL document 表）
- **测试**：更新 parser 单测（跨行单元格不拆行、边距拆分、标题清洗、归一化匹配、子进程调用 mock）；更新 `_locate_heading_line` 相关单测
- **评估**：Q4 RAGAS 回归，目标 faithfulness ≥ 基线 0.9333
