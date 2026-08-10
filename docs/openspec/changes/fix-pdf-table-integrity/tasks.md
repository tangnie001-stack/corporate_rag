## 1. 常量与子进程助手

- [x] 1.1 settings.py：`HEADER_FOOTER_MARGIN` 删除，新增 `HEADER_MARGIN=45`（顶部）与 `FOOTER_MARGIN=80`（底部）；新增子进程超时常量 `PDF_HEADING_SUBPROCESS_TIMEOUT`（默认 180s）
- [x] 1.2 新增 `src/parsers/pdf_heading_extractor.py`：`python -m src.parsers.pdf_heading_extractor <file>` 调用，模块顶层不 import pymupdf4llm；`__main__` 内才 import，**完整复刻现有 pm 管道**：`to_markdown(page_chunks=True, show_progress=False, write_images=False, header=False, footer=False)` → 逐页 `_clean_markdown_noise` → `\n` 拼接 → `_extract_heading_tree` → 输出最终清洗后标题树 JSON `[[level, title], ...]` 到 stdout
- [x] 1.3 `pdf_heading_extractor.py` 模块级 `threading.Semaphore(2)` 限制并发子进程（parse 在 to_thread 同步调用，用 threading 而非 asyncio），acquire 在 `subprocess.run` 前、finally release

## 2. Parser 双通道重写

- [x] 2.1 重写 `src/parsers/pymupdf_parser.py`：**移除模块级 `import pymupdf4llm`**；`parse()` 顺序执行 fitz 通道（主进程）→ pm 通道（子进程）
- [x] 2.2 fitz 通道 `_extract_text_by_page_fitz`：逐页 `find_tables()` + `sanitize_cell` 拍平跨行单元格 + blocks 表格区域排除（交集 >50%）+ 跳过空白块 + `HEADER_MARGIN`/`FOOTER_MARGIN` 边距过滤 + `MIN_TEXT_CHARS` 扫描件检测
- [x] 2.3 pm 通道：`subprocess.run([sys.executable, "-m", "src.parsers.pdf_heading_extractor", file_path], cwd=Path(__file__).resolve().parents[2], timeout=PDF_HEADING_SUBPROCESS_TIMEOUT, capture_output=True, text=True)` 解析 JSON 存入 heading_tree；**is_scanned 时跳过 pm 子进程**；失败/超时 → `logger.warning`（含 stderr 尾部）+ 空标题树降级，不阻塞、不重试
- [x] 2.4 自测（临时脚本验证后删除）：neusoft 跨行单元格 `购建固定资产...|63,134,713` 同行、页首块（top45）保留/页脚剔除、pm 子进程标题树与主进程 fitz 无互相污染

## 3. 标题定位归一化匹配

- [x] 3.1 `src/rag/heading_locator.py` 新增 `_normalize_ws` 辅助：`re.sub(r"\s+", "", text)` 去全部空白
- [x] 3.2 `_locate_heading_line` 增加归一化退化路径：精确 find + 行校验不命中后，逐行 `_normalize_ws` 整行相等比较；不命中返回 -1（保持现有静默跳过语义）
- [x] 3.3 确认 `build_heading_segments`/`locate_heading_path` 调用方无需改动，归一化只在比较层生效

## 4. 单元测试

- [x] 4.1 `tests/parsers/test_pymupdf_parser.py` 更新：跨行单元格不拆行（模拟 `extract()` 含 `\n` 的单元格 → 单行 Markdown）、边距拆分（页首标题保留、页脚剔除）、标题树清洗（`**_AI_**` 残留 → `AI`、伪标题过滤）、block_type 判定、扫描件检测
- [x] 4.2 pm 子进程 mock：patch `subprocess.run` 返回清洗后标题树 JSON，验证 heading_tree 组装；验证失败降级（非零退出/异常 → 空树 + 不抛异常）；**测试进程内不 import pymupdf4llm**（会污染共享 pytest 进程的 fitz 表格提取）
- [x] 4.3 **一个端到端集成测试**（真子进程）：`parse()` 真跑 neusoft → 断言 heading_tree 含"财务" + 表格 chunk 含 `63,134,713` 数值同行，验证子进程链路与无污染
- [x] 4.4 `tests/rag/test_heading_locator.py` 更新：`_normalize_ws` 单测（单空格/标点邻接空格/多空白）、`_locate_heading_line` 命中（单空格差异、`约 1 , 120 亿港元`）与不命中（表格内 `|...|` 包裹标题、截断标题）场景
- [x] 4.5 全量 `pytest tests/ -v` 通过，确认无既有断言因解析换源失联

## 5. 存量重建与回归评估

- [x] 5.1 复用 `scripts/rebuild_kb_data.py` 重建存量文档（保留 doc 记录、清 ChromaDB chunks、重置状态、重新 process_document）
- [x] 5.2 Q4 单问回归：确认表格数值列随标签同行、不再拒答
- [x] 5.3 Q4 RAGAS 评估 faithfulness ≥ 基线 0.9333；全量测试集评估无倒退（与基线对比表）

## 6. 质量门禁与契约同步

- [x] 6.1 `ruff check .` 无错误、`pyright src/` 新增/修改代码不引入新 error、无遗留 `print()`/TODO/调试代码
- [x] 6.2 修改公共方法签名或响应结构时同步 `docs/api_contract.md` 与受影响测试断言（本 change 契约不变，若 2.4 自测发现结构变化需补记）
- [x] 6.3 确认 `docs/agents/chunking-issues.md` 或评估 README 补记 Q4 根因、F5 全局污染与双通道子进程方案（如已有相关记录则更新）
