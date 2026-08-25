# 分块问题排查记录

本文件记录了在 neusoft_2025_q1.pdf 的表格分块过程中遇到的所有问题及解决方案。

## 分块问题

### 1. 表格被文本层内容污染
**问题**：`page.get_text()` 提取的文本层包含了表格区域内的文字（列名、数值），这些文字没有 `|` 标记，被当成普通文本 chunk，同时 `find_tables()` 又提取了同样的数据作为表格 markdown，造成内容重复。

**解决**：用 `page.get_text("blocks")` 获取每个文本块的坐标，排除与表格 bbox 有重叠（面积占比 > 50%）的文本块，避免文本层和表格 markdown 内容重复。

### 2. 表格识别误检（单行表格）
**问题**：`find_tables()` 在"重要内容提示"段落误检了一个 1 行 2 列的表格 `| 、 | 监事 |`，这个内容在 PDF 中根本不是表格。

**解决**：过滤 `find_tables()` 结果中不足 2 行的表格（`len(t.extract()) >= 2`），单行"表格"在财报 PDF 中基本都是误检。

### 3. 表格内换行符破坏 Markdown 行结构
**问题**：`_table_to_markdown()` 生成的表格行中，单元格内容含有 `\n`（如 `"本报告期比上年同期\n增减变动幅度(%)"`），导致 Markdown 行被拆成两段，每段都不满足 `^\|.*\|$` 模式，`_split_by_table_boundary` 无法识别为表格行，被当成文本处理。

**解决**：在 `sanitize_cell()`（基类 `BaseParser`）中对单元格值做 `str(value or "").strip().replace("\n", " ")`，将换行替换为空格。

### 4. `fitz.Rect.intersect()` 原地修改对象
**问题**：`fitz.Rect.intersect(rect)` 会修改原 Rect 对象并返回结果。在循环中先调用 `bbox.intersect(Table1)` 后，`bbox` 被修改为与 Table1 的交集（无效矩形），后续与 Table2/Table3 的比较全部失效。

**解决**：先用 `bbox.intersects(tr)`（不修改 bbox，返回 bool）判断是否相交，再用 `fitz.Rect(x0,y0,x1,y1).intersect(tr)`（新建 Rect 计算交集面积）。

### 5. 表格过滤区域判断：intersects 太宽、contains 太严
**问题**：用 `bbox.intersects(tb)` 判断文本块是否在表格内时，"重要内容提示"文本块与极小表格（`、｜监事`）的 bbox 有微小重叠被误杀。用 `tb.contains(bbox)` 则太严，部分在表格外的表格内文本块会被漏掉。

**解决**：使用**面积占比法**，计算文本块与表格 bbox 的交集面积占文本块总面积的比例，> 50% 才视为在表格内。

### 6. 文本块与表格 markdown 顺序错乱
**问题**：非表格文本块和表格 markdown 先分两组收集，再先后追加，导致文本块和表格的**视觉阅读顺序**错乱。例如页 2 先收集了"对公司将《...》"注记文本，再追加"外，非金融企业..."表格 markdown，但注记在 PDF 中实际在表格下方。

**解决**：将非表格文本块和表格 markdown 统一收集为 `[(y_center, content, is_table)]` 列表，按 Y 中心坐标排序后交错组装文本。

### 7. 跨页表格合并阈值难调
**问题**：用纯字符数阈值判断是否合并跨页表格时，设 100 太长（跨页表格合并不了），设 250 太短（注记被吞进表格）。反复调参没有结果。

**解决**：改用**列数一致性检测**——比较两个表格段首行的 `|` 个数。列数相同的才合并，列数不同则不合并。配合短文本阈值（< 100 字）防止吞注记。空文本（`\n\n` join 产生的 len=0）也允许合并。

### 8. 链式合并缺失
**问题**：`TABLE → text → TABLE` 的三段合并只做一次，如果中间有两个连续的空文本段（`TABLE → empty → TABLE → empty → TABLE`），第一次合并后循环跳过了第三个 TABLE，无法链式合并。

**解决**：第一次合并后增加 while 循环，继续检查 `merged[-1]` 与下一个 TABLE 段是否满足合并条件，实现链式合并。

### 9. 合并后表格过大超过 embedding 限制
**问题**：链式合并把所有列数相同的表格段都合在一起，某些大表（如资产负债表、利润表跨 3 页）合并后超过 4000 字符（约 2000 token），超出 `text-embedding-v1` 的 2048 token 截断限制，尾部数据检索不到。

**解决**：增加 `MAX_TABLE_TOKENS=2048` 配置项，合并前检查总长度，超过限制则不合并。

### 10. chunks API 截断导致排查困难
**问题**：`chunks` 接口返回的 `content` 截断到 500 字符，导致 table chunk 后半部分数据显示不出来，排查时以为数据丢失。

**解决**：截断长度从 500 改为 2000，覆盖全部表块内容。

## 代码组织

### 1. 配置值散落在代码中
**问题**：`MIN_TEXT_CHARS`、`HEADER_FOOTER_MARGIN`、`CROSS_PAGE_TABLE_MERGE_THRESHOLD`、`MAX_TABLE_TOKENS` 等控制参数直接硬编码在 parser/chunker 中，无法通过环境变量调整，也不知道参数含义。

**解决**：统一迁移到 `src/config/settings.py`，支持 `os.getenv` 覆盖，加注释说明用途。

### 2. 表格单元格清洗代码重复
**问题**：`pymupdf_parser.py` 和 `docx_parser.py` 各自内联了相同的 `str(c or "").replace("\n", " ")` 处理逻辑，新增 parser 容易遗漏。

**解决**：在 `BaseParser` 基类中新增 `sanitize_cell()` 静态方法，所有 parser 统一调用。

## 双通道解析重构（Q4 数值列回归与 F5 全局污染）

### 1. Q4 回归根因：pymupdf4llm Layout 引擎拆跨行单元格
**问题**：pymupdf4llm Layout 引擎会把跨行表格单元格拆成独立行，导致 `table_preserving` 的 `_split_large_tables` 把标签行与数值行切到不同 chunk，数值列（如 `63,134,713`）丢失，reranker 低分，最终拒答。

**解决**：改为双通道解析，fitz 主进程用 `find_tables().extract()` + `sanitize_cell` 拍平跨行单元格，保证标签与数值同行输出 Markdown 表格。

### 2. F5 全局污染：import pymupdf4llm 不可逆破坏 fitz 表格提取
**问题**：`import pymupdf4llm` 会设置全局 mupdf 状态（quad corrections / layout 分析器），永久破坏同进程内 `find_tables().extract()` 的单元格顺序（`63,134,713` → `63 134 713\n, ,`），且 `unset_quad_corrections(False)`、`extra.set_skip_quad_corrections(False)`、`pymupdf._get_layout=None` 均无法恢复。

**解决**：主进程不 import pymupdf4llm，标题树提取放子进程 `src/parsers/pdf_heading_extractor.py`（`python -m src.parsers.pdf_heading_extractor <file>` 调用，完整复刻 pm 管道，仅在 `__main__` 内才 import pymupdf4llm）。

### 3. 双通道方案与标题定位归一化
**问题**：同一进程内无法同时安全使用 fitz 表格提取和 pymupdf4llm 标题树；且标题定位对空白差异敏感，边距过滤参数过粗。

**解决**：
- fitz 主进程产全文+表格（`sanitize_cell` 拍平跨行单元格），pm 子进程产标题树；
- 标题定位 `_locate_heading_line` 增加去全部空白归一化匹配（`_normalize_ws`，`re.sub(r"\s+", "", text)`），不命中返回 -1 保持静默跳过语义；
- 边距过滤拆为 `HEADER_MARGIN=45`（顶部）/`FOOTER_MARGIN=80`（底部）。

### 4. 效果：Q4 数值列回归修复
**问题**：重构前 Q4 faithfulness 仅 0.8889，其中 Q4 题 faithfulness 0.3333，数值列丢失导致 reranker 低分拒答。

**解决**：双通道重构 + 归一化匹配后，Q4 faithfulness 0.8889 → 0.9833（超基线 0.9333），Q4 题 faithfulness 0.3333 → 1.0。

## 如何更新

遇到分块问题（解析、切分、表格、embedding 限制等）并修复后，按"问题 → 解决"格式追加到对应分区。
判断标准：该问题可复发吗？会 → 记；一次性笔误 → 不记。
