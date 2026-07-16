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
