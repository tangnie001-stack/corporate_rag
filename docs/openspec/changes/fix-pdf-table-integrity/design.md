## Context

当前 parser 是 pymupdf4llm 单通道（`chunk-entity-metadata-enrichment` 引入），Q4 RAGAS 评估回归：faithfulness 0.8889 vs 基线 0.9333。根因链：

pymupdf4llm Layout 引擎把跨行表格单元格拆成独立行（`购建固定资产、无形资产和其他` + `长期资产支付的现金|63,134,713`）→ table_preserving 的 `_split_large_tables`（2000 字符大表拆分）把标签行与数值行分到不同 chunk → 数值 `63,134,713` 丢失 → reranker 对残缺表格低分 → `RERANK_MIN_SCORE=0.3` 剪掉上下文 → Q4 拒答。旧 fitz parser 的 `sanitize_cell` 把单元格内 `\n` 拍平为空格，标签+数值保持同行，Q4 当时为 1.0。

候选方案排查（前序）：
- 方案 A（回退 fitz）：表格恢复，但丢失标题层级 → 实体抽取/heading_path 退化，否决。
- 方案 B（Layout + `table_output="html"`）：表格结构保留，但 HTML 整表单行、chunker 的 `|...|` TABLE_PATTERN 不匹配，表格 4 层保护失效；且无业界先例、性能最差。否决。
- 方案 C（跨行合并启发式）：46 处匹配仅 1 处真跨行（45 处误报），否决。
- **方案 D（双通道，本文档）**：fitz 全文+表格（内容权威源）+ pymupdf4llm 标题树（结构数据源）。

**关键实测（本轮 brainstorming 验证）**：
1. **F5 致命发现**：`import pymupdf4llm`（经 `pymupdf.layout` 的编译扩展 `onnx`/`pymupdf_util`）在 import 时设置全局 mupdf 状态，**永久破坏同进程内 `find_tables().extract()` 单元格顺序**：`63,134,713` → `63 134 713\n, ,`。`unset_quad_corrections(False)`、`extra.set_skip_quad_corrections(False)`、`pymupdf._get_layout=None`、显式 strategy 均无法恢复。→ 双通道不能在单进程内共存，pm 通道必须子进程隔离。
2. **F2 边距冲突**：`HEADER_FOOTER_MARGIN=80` 丢弃 tencent 页首 6 个块（5 个标题 + p4 内容段 `营销服务8业务...`，y≈52-66）。单一常量调小会让页脚回灌（neusoft 页脚 y=768、tencent 页脚 y=779 都要求底部边距 >74）。拆成 `HEADER_MARGIN=45`/`FOOTER_MARGIN=80` 后 6 块全部恢复、页脚仍剔除。
3. **F1 归一化定义错误**：`\s+`→单空格无法处理单空格差异（`收入高质量增长 运营效率持续提升` vs 无空格标题）。必须**去全部空白**（中文无词间空格），命中率 16/26 → 20/26，并顺带收敛 `约 1 , 120 亿港元` vs `约 1,120 亿港元`、`8% ，毛利` vs `8%，毛利`。
4. **残余缺口**：`其他财务资料` 是表格内标题（`|...|` 包裹），精确/归一化匹配均无法命中（1/26），静默跳过，接受。
5. **pm 标题树已清洗**：`_extract_heading_tree` 已对每条标题应用 `_clean_heading`，实测 0 强调残留。

## Goals / Non-Goals

**Goals:**
- 恢复表格完整性：跨行单元格不拆行，数值列随标签同行，Q4 回归基线
- 保留 pymupdf4llm 标题树：实体抽取 + heading_path 不倒退
- 双通道一致性：格式级差异（空白）通过去空白归一化收敛；页首内容块通过边距拆分保留
- 避免 F5 全局污染：pm 通道子进程隔离，主进程 fitz 表格提取保持干净
- `ParseResult` 契约不变，上层（chunker/service/agent）零改动

**Non-Goals:**
- 不重写 chunker（表格 4 层保护保留）
- 不做并行（GIL 实测并行 3.90s > 串行 2.41s，无收益）
- 不改实体抽取规则/LLM 兜底逻辑
- 不引入 MinerU/GPU 解析
- 不兼容迁移存量文档（清除重建）
- 不实现持久化 pm worker（子进程每文档一次的优化留作后续）

## Decisions

### D1: fitz 通道为内容唯一权威源（主进程）
- **选择**：`pymupdf.open()` + 逐页 `find_tables()` + `page.get_text("blocks")`，复用旧 parser 逻辑：
  - 表格按视觉顺序排序，过滤不足 2 行的误检
  - 表格转 Markdown 时单元格过 `sanitize_cell`（`str(value or "").strip().replace("\n", " ")`），跨行单元格拍平成同行
  - 有表格时按 Y 中心位置把"非表格文本块 + 表格 markdown"交错组装；无表格时按块提取
  - 跳过空白块（`not txt.strip()`）
  - `MIN_TEXT_CHARS` 扫描件检测不变
- **边距过滤拆分**：新增 `HEADER_MARGIN`（顶部，默认 45）与 `FOOTER_MARGIN`（底部，默认 80）两个配置项，**删除** `HEADER_FOOTER_MARGIN`（不留 fallback——本 change 本身 BREAKING，死配置会造成 `.env` 静默失效的困惑）；过滤条件 `y1 < HEADER_MARGIN or y0 > page_height - FOOTER_MARGIN`。实测恢复 tencent 页首 5 个标题 + p4 内容段；页脚仍剔除；neusoft 不受影响（顶部无内容、页脚在底部）
- **理由**：这套逻辑是 Q4 回归前验证过的路径；`sanitize_cell` 是确定性修复；边距拆分解决"页首标题被误当页眉丢弃"的冲突
- **替代方案**：保留单一 `HEADER_FOOTER_MARGIN=80` → 6 个页首块丢失、heading_path 错误归属导致 chunk 内容被拼上错误 `【上一节】` 前缀（比无前缀更糟），否决

### D2: pm 通道子进程隔离（标题树）
- **选择**：新增 `src/parsers/pdf_heading_extractor.py`，作为可 `python -m src.parsers.pdf_heading_extractor <file>` 调用的子进程助手：
  - `__main__` 块内才 `import pymupdf4llm`，**完整复刻现有 pm 管道**：`to_markdown(doc, page_chunks=True, show_progress=False, write_images=False, header=False, footer=False)` → 逐页 `_clean_markdown_noise` → `\n` 拼接 → `_extract_heading_tree` → 输出最终清洗后标题树 JSON `[[level, title], ...]` 到 stdout
  - 主进程 parser 通过 `subprocess.run([sys.executable, "-m", "src.parsers.pdf_heading_extractor", file_path], ...)` 调用，解析 JSON 存入 `ParseResult.heading_tree`
  - **并发限制**：模块级 `threading.Semaphore(2)`（parse 是 to_thread 里的同步调用，跨线程限制需用 threading 而非 asyncio），acquire 在 `subprocess.run` 前、release 在 finally——避免批量入库并发子进程 × ~200MB 内存击穿容器 mem_limit
  - **cwd 解析**：`subprocess.run(..., cwd=Path(__file__).resolve().parents[2])`（src/parsers/ 上溯两级 = 仓库根），不依赖调用方 cwd（容器 WORKDIR=/app，开发在仓库根，显式指定两者皆稳）
  - **失败降级**：子进程非零退出/超时 → `logger.warning`（含 stderr 尾部便于排查）+ 返回空标题树（实体抽取退回文件名+LLM 兜底），不阻塞入库、不重试、不改文档状态
- **理由**：F5 实测 `import pymupdf4llm` 永久破坏同进程 fitz 表格提取且不可逆；子进程隔离是唯一让两通道各按其全局状态运行的方式（已端到端验证：主进程 fitz 干净、子进程 pm 25/18 标题、子进程跑完后主进程仍干净）；复刻现有管道保证标题树与今天逐字节一致（实测 26/26）
- **扫描件短路**：`parse()` 中 fitz 通道计算 `is_scanned` 后，若为扫描件直接跳过 pm 子进程（`heading_tree` 留空）——扫描件无文字层、标题树必为空，省一次子进程 spawn
- **替代方案**：
  - R3 legacy `to_markdown`（`use_layout(False)`）内容通道：表格正确但标题树只剩 1 个（vs 18），实体抽取饿死，否决
  - 主进程 import + 每文档子进程重开 fitz：pm import 的全局污染不可逆，多文档场景第一个文档后 fitz 即被破坏，否决

### D3: 双通道一致性 = 标题清洗 + 去空白归一化
- **标题清洗（子进程内完成）**：子进程**完整复刻现有 pm 管道**——`to_markdown` 后逐页 `_clean_markdown_noise` → `\n` 拼接 → `_extract_heading_tree`，直接输出最终清洗后的标题树 JSON。实测与当前 `parse()` 的树**逐字节一致**（tencent 26/26）。若只输出原始标题或换扫描顺序（逐页扫 vs 拼接扫），树会漂移（25 vs 26，如 `# #` 伪标题），导致实体抽取标题栈规则层看到不同标题集
- **归一化匹配**：`heading_locator._locate_heading_line` 增加退化路径——逐行比较前将标题与行文本**去全部空白**（`re.sub(r"\s+", "", text)`）。中文无词间空格，此规则覆盖：pm 合并空格（`收入高质量增长运营效率持续提升` vs fitz `收入高质量增长 运营效率持续提升`）、强调标点残留（`约 1 , 120 亿港元` vs `约 1,120 亿港元`）、分隔符邻接空格（`8% ，毛利` vs `8%，毛利`）。实测 16/26 → 20/26；配合边距拆分后在服务层 chunk 拼接 full_text 上 25/26（tencent）、14/14（neusoft）
- **残余缺口（接受）**：`其他财务资料` 为表格内标题（fitz 表格 markdown 里是 `|...|` 包裹），精确/归一化匹配均不命中，静默跳过（现有 `build_heading_segments` 行为）。影响：该段 heading_path 缺失/归属上一段（元数据 + chunk 前缀），实体抽取不受影响（直接用标题树）。neusoft 14/14 无此问题
- **不命中兜底**：归一化后仍不匹配 → 静默跳过，不阻断其余标题处理
- **理由**：实测两样本差异均为格式级（空白/标点邻接），内容级一致；去空白是唯一能收敛单空格差异的规则

### D4: 串行执行（不做并行）
- **选择**：`parse()` 顺序跑主进程 fitz 通道 → 子进程 pm 通道
- **理由**：fitz 通道 CPU-bound，GIL 下 `asyncio.to_thread` 并行实测 3.90s > 串行 2.41s；pm 已子进程化，天然与主进程隔离，无并行收益
- **替代方案**：`ProcessPoolExecutor` 真并行 → 多进程/序列化开销，收益不匹配成本（否决）

### D5: 保留解析契约，上层零改动
- `ParseResult`（chunks/total_pages/total_chars/is_scanned/heading_tree）、`ChunkData`（content/metadata/chunk_id）结构不变
- `chunk_id=f"{source}:p{page}:{i}"`、`block_type`（`TABLE_PATTERN` 判定 table/text）、页码 1 起不变
- 常量归属：`HEADER_MARGIN`/`FOOTER_MARGIN` 进 `src/config/settings.py`（`HEADER_FOOTER_MARGIN` 删除）；子进程超时 `PDF_HEADING_SUBPROCESS_TIMEOUT`（默认 180s）也进 settings；`_normalize_ws` 放 `heading_locator.py`

## Risks / Trade-offs

- [F5 全局污染（pymupdf4llm import 破坏 fitz 表格）] → 子进程隔离已端到端验证；主进程 parser 移除模块级 `import pymupdf4llm`，仅子进程 `__main__` 内 import
- [子进程每文档开销（~0.5s + to_markdown 耗时）] → 批量入库累计成本；持久化 pm worker 列为后续优化；单文档解析可接受
- [子进程失败（超时/异常）] → 降级返回空标题树 + `logger.warning`，实体抽取退回文件名+LLM 兜底，不阻塞入库
- [顶部边距降到 45 的未来文档页眉回灌风险] → 当前两样本顶部 45-80 区间仅真实内容无重复页眉；财报页眉通常在 y<45；pm `header=False/footer=False` 仍是内容侧兜底
- [表格内标题（`其他财务资料`）heading_path 缺口] → 1/26 标题静默跳过，元数据级影响，接受
- [归一化放宽导致误定位] → 归一化仅用于退化路径的整行相等比较（不做子串包含），不改变精确 find 优先逻辑，误定位风险与现状持平
- [Q4 重测仍不达基线] → 需验证 reranker 对修复后完整表格的打分；若不达标再查 rerank 阈值链路（ledger 中的待办），超出本 change 范围

## Migration Plan

1. 常量拆分：settings.py 增 `HEADER_MARGIN=45`/`FOOTER_MARGIN=80`，删 `HEADER_FOOTER_MARGIN`
2. 新增 `src/parsers/pdf_heading_extractor.py`（子进程助手）
3. 重写 `src/parsers/pymupdf_parser.py` 为双通道（fitz 主进程 + pm 子进程），移除模块级 pymupdf4llm import
4. `src/rag/heading_locator.py` 的 `_locate_heading_line` 增加去空白归一化匹配 + `_normalize_ws` 辅助
5. 单测：跨行单元格不拆行、边距拆分（页首保留/页脚剔除）、标题清洗残留、归一化匹配命中/不命中、子进程调用 mock、失败降级
6. 存量重建：复用 `scripts/rebuild_kb_data.py`（保留 doc 记录、清 ChromaDB chunks、重置状态、重新 process_document）
7. Q4 RAGAS 回归评估，目标 faithfulness ≥ 基线 0.9333；全量评估回归无倒退

**Rollback**：代码 `git revert` 即可回到 pm 单通道；存量数据已重建，回滚后需再次重建（数据可从 MinIO 原始文件恢复）。

## Open Questions

- Q4 修复后 reranker 对完整表格的得分是否恢复（若未恢复需跟进 rerank 阈值链路，是否纳入本 change 视重测结果定）
- 子进程每文档开销是否值得引入持久化 pm worker（性能优化，本期不做）
