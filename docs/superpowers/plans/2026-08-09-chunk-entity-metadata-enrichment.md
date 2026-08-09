# Chunk Entity Metadata Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽取文档级业务实体（company/report_period/sec_code 核心 3 类 + person/currency/report_type 可选）注入 chunk metadata，渲染进生产 prompt 与 RAGAS NLI 上下文，并动态化 clarify 追问、注入当前日期。

**Architecture:** 引入 pymupdf4llm（page_chunks=True）替代手写 fitz 文本提取，产出带标题层级 Markdown + 逐页 chunks 保页码；新增标题段区间定位器反推 chunk 的 heading_path（只写 metadata 不改 content，方案 C）；新增三层实体抽取器（文件名正则 + 标题栈规则层 + LLM 校验兜底三态开关）；RAGContext 增加开放 entities dict 透传并渲染；存量文档清除重建。

**Tech Stack:** Python 3.12 / PyMuPDF 1.28.2 / pymupdf4llm 1.28.2 / ChromaDB / FastAPI / pytest / ruff / pyright

## Global Constraints

- `pymupdf==1.28.2`（升级自 1.27.2.3）、`pymupdf4llm==1.28.2`（新增）
- 实体核心 3 类渲染进 prompt（company/report_period/sec_code）；可选 3 类仅补充字段（person/currency/report_type）
- `ENTITY_LLM_FALLBACK` 三态：`off` / `on` / `auto`（默认 auto）；`ENTITY_TEXT_PREFIX_LEN=600`
- pymupdf4llm 调用参数：`to_markdown(doc, page_chunks=True, show_progress=False, write_images=False)`
- 标题段定位只写 `chunk.metadata["heading_path"]`，**不修改 content**（inject_heading_prefix 传空）
- 标题段反推与页码反推同顺序（chunk 后、`_merge_tiny_chunks` 前），并入 `_enrich_chunk_pages`
- 代码风格：不用三元表达式；类型不确定用显式判断；新常量进 `src/config/`（settings/prompts/const）
- 不用 `getattr(x, "attr", default)` 隐式兜底；文档/注释用中文
- 测试 mock 外部依赖，不发起真实网络调用

---

### Task 1: 依赖升级与 pymupdf4llm 兼容性验证

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/parsers/test_pymupdf_parser.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces: 验证结论——pymupdf4llm 对 3 份样本 PDF 的标题层级/表格格式；表格 Markdown 与 `TablePreservingChunker.TABLE_PATTERN` 的兼容性

- [ ] **Step 1: 更新依赖**

```bash
# 编辑 pyproject.toml: 将 "pymupdf==1.27.2.3" 改为 "pymupdf==1.28.2"，dependencies 末尾新增 "pymupdf4llm==1.28.2"
pip install pymupdf==1.28.2 pymupdf4llm==1.28.2
```

- [ ] **Step 2: 验证 pymupdf4llm 输出**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag && python3 -c "
import fitz, pymupdf4llm
for fname in ['data/test_docs/neusoft_2025_q1.pdf', 'data/test_docs/tencent_2024_annual.pdf', 'data/test_docs/canki_2019_annual.pdf']:
    doc = fitz.open(fname)
    pages = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False, write_images=False)
    print(fname, 'pages=', len(pages))
    # 标题层级检查: 统计 # 前缀
    full = '\n\n'.join(p['text'] for p in pages)
    import re
    heads = [l for l in full.split('\n') if re.match(r'^#{1,4} ', l)]
    print('  headings=', len(heads), 'first:', heads[:3])
    # 表格检查
    print('  has_table=', '|' in full)
    doc.close()
"
```

Expected: 3 份样本均输出多页 dict，包含 `#` 标题行和 `|` 表格行。

- [ ] **Step 3: 验证表格兼容性**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag && python3 -c "
import fitz, pymupdf4llm, re
from src.chunking.strategies.table_preserving import TablePreservingChunker
pat = TablePreservingChunker.TABLE_PATTERN
for fname in ['data/test_docs/neusoft_2025_q1.pdf', 'data/test_docs/tencent_2024_annual.pdf']:
    doc = fitz.open(fname)
    pages = pymupdf4llm.to_markdown(doc, page_chunks=True, show_progress=False, write_images=False)
    full = '\n\n'.join(p['text'] for p in pages)
    matches = pat.findall(full)
    print(fname, 'TABLE_PATTERN matches=', len(matches))
    doc.close()
"
```

Expected: 表格匹配数 > 0（兼容）；若为 0，记录表格格式差异（如表头前有说明文字），作为 Task 2 调整 TABLE_PATTERN 的依据。

- [ ] **Step 4: 跑现有 PDF 测试确认回归面**

Run: `pytest tests/parsers/test_pymupdf_parser.py -v`
Expected: 记录当前通过/失败状态（Task 2 改造后再对齐）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build(deps): upgrade pymupdf to 1.28.2, add pymupdf4llm 1.28.2"
```

---

### Task 2: PDF 解析器改造（pymupdf4llm 替代 + 标题树 + 页码保留）

**Files:**
- Modify: `src/parsers/base.py`（ParseResult 新增 heading_tree 字段）
- Modify: `src/parsers/pymupdf_parser.py`（替换文本/表格提取逻辑）
- Test: `tests/parsers/test_pymupdf_parser.py`

**Interfaces:**
- Consumes: Task 1 的兼容性结论
- Produces: `ParseResult.heading_tree: list[tuple[int, str]]`；`parse_result.chunks` 保留 page 元数据；`parse_result.total_chars`/`is_scanned` 正确

- [ ] **Step 1: ParseResult 新增 heading_tree 字段（先写测试）**

```python
# tests/parsers/test_base.py 追加
def test_parse_result_heading_tree_default():
    from src.parsers.base import ParseResult
    r = ParseResult(chunks=[], total_pages=0, file_type="txt")
    assert r.heading_tree == []
```

```python
# src/parsers/base.py 修改
@dataclass
class ParseResult:
    chunks: list[ChunkData] = field(default_factory=list)
    total_pages: int = 0
    total_chars: int = 0
    is_scanned: bool = False
    encoding: str = "utf-8"
    file_type: str = ""
    heading_tree: list[tuple[int, str]] = field(
        default_factory=list
    )  # (level, heading) 标题层级，PDF/docx 提取，txt 空
```

- [ ] **Step 2: 写标题树提取的失败测试**

```python
# tests/parsers/test_pymupdf_parser.py 追加
class TestHeadingTree:
    def setup_method(self):
        self.parser = PyMuPDFParser()

    def test_pdf_heading_tree_extracted(self):
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        assert isinstance(result.heading_tree, list)
        assert len(result.heading_tree) > 0
        # 至少有一个章节标题（如"主要财务数据"）
        titles = [h for _, h in result.heading_tree]
        assert any("财务" in t for t in titles)

    def test_pdf_page_preserved(self):
        path = "data/test_docs/neusoft_2025_q1.pdf"
        if not os.path.exists(path):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(path)
        pages = {c.metadata.get("page") for c in result.chunks}
        assert pages and all(p >= 1 for p in pages)
```

- [ ] **Step 3: Run 测试确认失败**

Run: `pytest tests/parsers/test_pymupdf_parser.py::TestHeadingTree -v`
Expected: FAIL（ParseResult 无 heading_tree 属性 / parser 未提取）

- [ ] **Step 4: 实现 pymupdf4llm 解析**

```python
# src/parsers/pymupdf_parser.py parse() 中替换核心提取逻辑
import pymupdf4llm

# 在 parse() 内部（fitz 打开 doc 之后）：
pages = pymupdf4llm.to_markdown(
    doc, page_chunks=True, show_progress=False, write_images=False
)
text_by_page = []
for p in pages:
    text = p["text"]
    page_num = p["metadata"]["page_number"]  # 1-based
    char_count = len(text.strip())
    total_chars += char_count
    if char_count < MIN_TEXT_CHARS:
        scanned_pages += 1
    text_by_page.append((text, page_num))

# 标题树提取（在 text_by_page 组装完成后）
heading_tree: list[tuple[int, str]] = []
for line in full_text.split("\n"):
    stripped = line.rstrip()
    if stripped.startswith("#"):
        level = len(stripped) - len(stripped.lstrip("#"))
        heading_tree.append((level, stripped.lstrip("#").strip()))
```

注意：
- 删除原 `find_tables()` / `table_bboxes` / `_extract_tables_from_page` 逻辑（pymupdf4llm 已内置表格转 Markdown）
- `HEADER_FOOTER_MARGIN` 页眉页脚过滤逻辑删除（pymupdf4llm 内部处理）
- 保留 `is_scanned = scanned_pages == total_pages` 判定
- `parse_result.heading_tree = heading_tree`

- [ ] **Step 5: Run 测试确认通过**

Run: `pytest tests/parsers/test_pymupdf_parser.py -v`
Expected: 全部 PASS（含新 TestHeadingTree；原测试中依赖 find_tables 的具体断言按需调整）

- [ ] **Step 6: 若 TABLE_PATTERN 不兼容，调整**

```python
# src/chunking/strategies/table_preserving.py
# 若 Task 1 Step 3 显示 pymupdf4llm 表格匹配数为 0，检查输出格式后调整 TABLE_PATTERN
# 常见差异: pymupdf4llm 表格前可能有 "<!-- Table -->" 注释或说明行
# 调整示例（仅在需要时）:
# TABLE_PATTERN = re.compile(r"(^\|.+\|[\s\S]*?^\|.+\|)", re.MULTILINE)
```

- [ ] **Step 7: Commit**

```bash
git add src/parsers/base.py src/parsers/pymupdf_parser.py tests/parsers/test_base.py tests/parsers/test_pymupdf_parser.py
git commit -m "feat(parser): replace fitz text extraction with pymupdf4llm, add heading_tree"
```

---

### Task 3: DOCX 标题树提取 + 实体抽取文件类型分流

**Files:**
- Modify: `src/parsers/docx_parser.py`
- Test: `tests/parsers/test_docx_parser.py`

**Interfaces:**
- Consumes: `ParseResult.heading_tree` 字段（Task 2）
- Produces: DOCX 的 `heading_tree`；实体抽取分流约定（PDF/docx 完整三层，txt 文件名+LLM）

- [ ] **Step 1: 写 DOCX 标题树提取测试**

```python
# tests/parsers/test_docx_parser.py 追加
def test_docx_heading_tree_extracted(self):
    from src.parsers.base import ParseResult
    result = self.parser.parse(self.sample_docx)  # 复用现有 sample
    assert isinstance(result, ParseResult)
    assert hasattr(result, "heading_tree")
```

- [ ] **Step 2: 实现 DOCX 标题提取**

```python
# src/parsers/docx_parser.py parse() 中，在提取 paragraphs 后：
from docx.document import Document as _Doc
heading_tree: list[tuple[int, str]] = []
for para in doc.paragraphs:
    if not para.text.strip():
        continue
    style_name = (para.style.name if para.style else "") or ""
    # python-docx heading 样式: "Heading 1" 或中文 "标题 1"
    if style_name.lower().startswith("heading"):
        try:
            level = int(style_name.split()[-1])
        except ValueError:
            level = 1
        heading_tree.append((level, para.text.strip()))
    elif "标题" in style_name:
        m = re.search(r"(\d)", style_name)
        level = int(m.group(1)) if m else 1
        heading_tree.append((level, para.text.strip()))

# 写回 parse_result.heading_tree = heading_tree
```

- [ ] **Step 3: Run 测试**

Run: `pytest tests/parsers/test_docx_parser.py -v`
Expected: PASS

- [ ] **Step 4: 确认分流约定（写入代码注释/常量）**

```python
# src/config/const.py（Task 4 会正式添加，此处先约定）
# 实体抽取文件类型分流:
#   PDF / DOCX: 完整三层（文件名正则 + 标题栈规则层 + LLM 兜底）
#   TXT:       文件名正则 + LLM 兜底（无标题栈规则层）
ENTITY_FULL_PIPELINE_TYPES: tuple[str, ...] = ("pdf", "docx")
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/docx_parser.py tests/parsers/test_docx_parser.py
git commit -m "feat(parser): extract heading_tree from docx styles"
```

---

### Task 4: 标题段区间定位器 + 反推合并进 _enrich_chunk_pages

**Files:**
- Create: `src/rag/heading_locator.py`（标题段区间定位器）
- Modify: `src/services/document_service.py`（`_enrich_chunk_pages` 并入 heading_path 反推）
- Test: `tests/rag/test_heading_locator.py`

**Interfaces:**
- Consumes: `ParseResult.heading_tree`（Task 2/3）
- Produces:
  - `build_heading_segments(full_text: str, heading_tree: list[tuple[int, str]]) -> list[dict]`：返回 `[{"path": str, "start": int, "end": int}, ...]`
  - `locate_heading_path(content: str, full_text: str, segments: list[dict]) -> str`：返回空串表示未命中
  - `_enrich_chunk_pages` 改为同时反推 `page` 和 `heading_path`

- [ ] **Step 1: 写标题段定位器失败测试**

```python
# tests/rag/test_heading_locator.py
"""标题段区间定位器测试。"""
from src.rag.heading_locator import build_heading_segments, locate_heading_path


def test_build_heading_segments():
    full_text = "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    assert len(segs) == 3
    # 第一个段从标题行开始，到第二个一级标题前
    assert segs[0]["start"] == 0
    assert segs[1]["path"] == "一、主要财务数据 > （一）会计数据" or segs[1]["path"] == "（一）会计数据"


def test_locate_heading_path_matches():
    full_text = "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    # 内容B 属于 （一）会计数据
    path = locate_heading_path("内容B", full_text, segs)
    assert path != ""


def test_locate_heading_path_no_match():
    segs = []
    assert locate_heading_path("任意", "任意全文", segs) == ""
```

- [ ] **Step 2: Run 测试确认失败**

Run: `pytest tests/rag/test_heading_locator.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现标题段定位器**

```python
# src/rag/heading_locator.py
"""标题段区间定位器 — 由标题树 + 全文建立标题段区间，反推 chunk 归属。"""

import re


def build_heading_segments(
    full_text: str, heading_tree: list[tuple[int, str]]
) -> list[dict]:
    """由标题树和全文建立标题段区间表。

    Args:
        full_text: 文档全文（Markdown）
        heading_tree: (level, heading) 列表，来自 ParseResult.heading_tree

    Returns:
        标题段区间列表，每项 {"path", "start", "end"}；
        path 为父级拼接的标题路径，start/end 为全文中的字符偏移。
        无标题树时返回空列表。
    """
    if not heading_tree:
        return []

    # 在 full_text 中定位每个标题行的偏移
    offsets = []
    lines = full_text.split("\n")
    pos = 0
    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            offsets.append((pos, level, title))
        pos += len(line) + 1  # +1 换行符

    segments = []
    stack: list[tuple[int, str]] = []  # (level, title)
    for i, (off, level, title) in enumerate(offsets):
        # 父级继承：pop 掉 level >= 当前 level 的帧
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " > ".join(t for _, t in stack)
        end = offsets[i + 1][0] if i + 1 < len(offsets) else len(full_text)
        segments.append({"path": path, "start": off, "end": end})

    return segments


def locate_heading_path(
    content: str, full_text: str, segments: list[dict]
) -> str:
    """反推 chunk 内容所属的标题段路径。

    Args:
        content: chunk 内容
        full_text: 文档全文
        segments: build_heading_segments 的返回值

    Returns:
        标题段路径字符串，未命中返回空串。
    """
    if not segments:
        return ""
    pos = full_text.find(content)
    if pos < 0:
        return ""
    end = pos + len(content)
    for seg in segments:
        if seg["start"] <= pos and end <= seg["end"]:
            return seg["path"]
    # 兜底：取包含 pos 的最近段
    for seg in segments:
        if seg["start"] <= pos < seg["end"]:
            return seg["path"]
    return ""
```

- [ ] **Step 4: Run 测试确认通过**

Run: `pytest tests/rag/test_heading_locator.py -v`
Expected: PASS

- [ ] **Step 5: 更新 _enrich_chunk_pages 并入 heading_path 反推**

```python
# src/services/document_service.py
# 将 _enrich_chunk_pages 改造为 _enrich_chunk_metadata（同时反推 page + heading_path）
def _enrich_chunk_metadata(
    self,
    chunks: list[ChunkData],
    parse_chunks: list,
    full_text: str,
    heading_segments: list[dict],
) -> None:
    """从解析器分块反推 chunk 页码和标题段路径。

    在 _merge_tiny_chunks 之前调用（content 未被合并改写时 find 有效）。
    """
    offset = 0
    page_map = []
    for c in parse_chunks:
        page = c.metadata.get("page", 1)
        page_map.append((offset, offset + len(c.content), page))
        offset += len(c.content) + 2
    for chunk in chunks:
        text = chunk.content
        pos = full_text.find(text)
        if pos < 0:
            continue
        end = pos + len(text)
        pages = {p for s, e, p in page_map if s < end and e > pos}
        chunk.metadata["page"] = min(pages)
        # 标题段反推
        if heading_segments:
            chunk.metadata["heading_path"] = locate_heading_path(
                text, full_text, heading_segments
            )
```

```python
# process_document 中调用处修改（原 _enrich_chunk_pages 调用点，约 L378）
from src.rag.heading_locator import build_heading_segments
heading_segments = build_heading_segments(full_text, parse_result.heading_tree)
self._enrich_chunk_metadata(chunks, parse_result.chunks, full_text, heading_segments)
```

- [ ] **Step 6: 确认 inject_heading_prefix 不修改 content**

```python
# 确认 chunker 调用时 heading_path 传空（现已是 metadata.get("heading_path", "")，默认空）
# src/chunking/strategies/base.py inject_heading_prefix 保持不动：
#   if not heading_path: return content  ← 已保证不修改 content
```

- [ ] **Step 7: Run 相关测试**

Run: `pytest tests/rag/test_heading_locator.py tests/services/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/rag/heading_locator.py src/services/document_service.py tests/rag/test_heading_locator.py
git commit -m "feat(chunking): heading segment locator + reverse-lookup heading_path in enrich"
```

---

### Task 5: 实体抽取配置与 prompt

**Files:**
- Modify: `src/config/const.py`（ENTITY_TYPES / ENTITY_RENDER_ORDER）
- Modify: `src/config/settings.py`（ENTITY_LLM_FALLBACK / ENTITY_TEXT_PREFIX_LEN）
- Modify: `src/config/prompts.py`（ENTITY_EXTRACTION_SYSTEM_PROMPT / USER_TEMPLATE）
- Test: `tests/config/test_settings.py`

**Interfaces:**
- Consumes: 无（配置先行）
- Produces:
  - `ENTITY_TYPES: tuple[str, ...]` = ("company", "report_period", "sec_code")
  - `ENTITY_RENDER_ORDER: tuple[str, ...]` = ("company", "report_period", "sec_code")
  - `ENTITY_LLM_FALLBACK: str`（"off"/"on"/"auto"，默认 "auto"）
  - `ENTITY_TEXT_PREFIX_LEN: int` = 600
  - `ENTITY_EXTRACTION_SYSTEM_PROMPT` / `ENTITY_EXTRACTION_USER_TEMPLATE`

- [ ] **Step 1: 写 settings 测试**

```python
# tests/config/test_settings.py 追加
def test_entity_llm_fallback_default_auto():
    from src.config.settings import ENTITY_LLM_FALLBACK
    assert ENTITY_LLM_FALLBACK in ("off", "on", "auto")
    assert ENTITY_LLM_FALLBACK == "auto"


def test_entity_text_prefix_len_default():
    from src.config.settings import ENTITY_TEXT_PREFIX_LEN
    assert ENTITY_TEXT_PREFIX_LEN == 600
```

- [ ] **Step 2: Run 测试确认失败**

Run: `pytest tests/config/test_settings.py -v`
Expected: FAIL（属性不存在）

- [ ] **Step 3: 添加配置**

```python
# src/config/settings.py 追加（文档处理分区内）
# 实体抽取 LLM 兜底开关（三态）: off=纯规则 / on=每文档无条件走 LLM / auto=规则空或缺关键类型才走
ENTITY_LLM_FALLBACK: str = os.getenv("ENTITY_LLM_FALLBACK", "auto")
# 实体抽取 LLM 兜底输入的正文前缀长度（字符数）
ENTITY_TEXT_PREFIX_LEN: int = int(os.getenv("ENTITY_TEXT_PREFIX_LEN", "600"))
```

```python
# src/config/const.py 追加
"""实体抽取常量。"""

# 核心实体类型：文档级属性，渲染进 prompt 支撑 faithfulness 锚点
ENTITY_TYPES: tuple[str, ...] = ("company", "report_period", "sec_code")
# 核心实体渲染顺序（to_prompt_text 按此顺序渲染存在的实体）
ENTITY_RENDER_ORDER: tuple[str, ...] = ("company", "report_period", "sec_code")
# 可选实体：LLM 兜底顺带返回，仅补充字段不渲染
ENTITY_OPTIONAL_TYPES: tuple[str, ...] = ("person", "currency", "report_type")
# 实体抽取完整三层流水线的文件类型（其余如 txt 走文件名+LLM）
ENTITY_FULL_PIPELINE_TYPES: tuple[str, ...] = ("pdf", "docx")
```

- [ ] **Step 4: Run 测试确认通过**

Run: `pytest tests/config/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: 添加抽取 prompt**

```python
# src/config/prompts.py 追加

# 实体抽取系统提示词 — 引导 LLM 校验规则候选并补全盲区。
# 输入含文件名、标题树、正文前缀、规则候选；输出 JSON 三字段。
# 关键约束：规则结果与原文一致时保留，仅当原文无依据或明显不是实体时才纠正。
ENTITY_EXTRACTION_SYSTEM_PROMPT: str = """你是一个金融文档实体抽取专家。基于文件名、文档标题结构和正文片段，提取文档级实体。

核心实体（必须尽力提取）：company（公司名）、report_period（报告期，如"2025年第一季度"）、sec_code（证券代码）。
可选实体（顺带返回，不勉强）：person（人名）、currency（币种）、report_type（报表类型）。

规则层已给出候选结果，可能正确也可能错误。你的任务：
1. 校验规则候选：与原文一致则保留；原文无依据或明显不是实体（如描述性短语）则纠正
2. 补全规则漏掉的核心实体
3. 只返回 JSON，不要其他内容。"""

# 实体抽取用户消息模板。
# 占位符: {filename} 文件名; {heading_tree} 标题树; {text_prefix} 正文前缀; {rule_candidates} 规则候选
ENTITY_EXTRACTION_USER_TEMPLATE: str = """文件名：{filename}

文档标题结构：
{heading_tree}

正文开头：
{text_prefix}

规则层候选（可能正确也可能错误）：
{rule_candidates}

输出 JSON（严格按此格式）：
{{
  "rule_correct": true,
  "reason": "简要说明校验依据",
  "entities": {{
    "company": "公司名",
    "report_period": "报告期",
    "sec_code": "证券代码"
  }}
}}
"""
```

- [ ] **Step 6: Commit**

```bash
git add src/config/settings.py src/config/const.py src/config/prompts.py tests/config/test_settings.py
git commit -m "feat(config): entity extraction config + prompt templates"
```

---

### Task 6: 实体抽取器（规则层 + LLM 兜底层）

**Files:**
- Create: `src/infra/search/document_entity_extractor.py`
- Test: `tests/infra/test_document_entity_extractor.py`

**Interfaces:**
- Consumes: `ENTITY_TYPES` / `ENTITY_OPTIONAL_TYPES` / `ENTITY_LLM_FALLBACK` / `ENTITY_TEXT_PREFIX_LEN`（Task 5）；`ENTITY_EXTRACTION_*` prompts（Task 5）
- Produces:
  - `DocumentEntityExtractor.extract(filename: str, heading_tree: list[tuple[int, str]], text: str, llm=None) -> dict`：返回 entities dict（含 core + 可选）
  - `extract_from_filename(filename: str) -> dict`：文件名正则
  - `extract_from_headings(heading_tree: list[tuple[int, str]]) -> dict`：标题栈规则层

- [ ] **Step 1: 写规则层失败测试**

```python
# tests/infra/test_document_entity_extractor.py
"""文档级实体抽取器测试。"""
from src.infra.search.document_entity_extractor import (
    DocumentEntityExtractor,
    extract_from_filename,
    extract_from_headings,
)


class TestExtractFromFilename:
    def test_neusoft_filename(self):
        entities = extract_from_filename("neusoft_2025_q1.pdf")
        assert entities.get("company") == "neusoft"
        assert entities.get("year") == "2025"
        assert entities.get("quarter") == "q1"

    def test_tencent_filename(self):
        entities = extract_from_filename("tencent_2024_annual.pdf")
        assert entities.get("company") == "tencent"
        assert entities.get("year") == "2024"

    def test_report_name(self):
        entities = extract_from_filename("report.pdf")
        assert entities == {}


class TestExtractFromHeadings:
    def test_neusoft_headings(self):
        heading_tree = [(1, "2025 年第一季度报告"), (1, "一、主要财务数据")]
        entities = extract_from_headings(heading_tree)
        assert entities.get("year") == "2025"
        assert entities.get("quarter") in ("一", "Q1")

    def test_report_type_extracted(self):
        heading_tree = [(2, "（一）资产负债表")]
        entities = extract_from_headings(heading_tree)
        assert entities.get("report_type") == "资产负债表"
```

- [ ] **Step 2: Run 测试确认失败**

Run: `pytest tests/infra/test_document_entity_extractor.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现规则层**

```python
# src/infra/search/document_entity_extractor.py
"""文档级实体抽取器 — 规则层（文件名 + 标题栈）+ LLM 校验兜底。

规则层零成本：文件名正则提取 company/year/quarter；
标题栈规则层复用 financial_rag ContextStack 思路提取 year/quarter/report_type/company/currency。
LLM 兜底层由三态开关 ENTITY_LLM_FALLBACK 控制。
"""

import json
import re
from typing import Any

from loguru import logger

from src.config import ENTITY_LLM_FALLBACK, ENTITY_TEXT_PREFIX_LEN
from src.config.const import ENTITY_FULL_PIPELINE_TYPES, ENTITY_OPTIONAL_TYPES, ENTITY_TYPES
from src.config.prompts import (
    ENTITY_EXTRACTION_SYSTEM_PROMPT,
    ENTITY_EXTRACTION_USER_TEMPLATE,
)

# 文件名模式: {company}_{year}_{quarter|annual}.pdf
_FILENAME_PATTERN = re.compile(
    r"^(?P<company>[a-zA-Z0-9_]+)_(?P<year>20\d{2})_(?P<period>q[1-4]|annual|yearly)\.\w+$"
)

# 标题栈提取规则（对齐 financial_rag HEADING_EXTRACTORS + sec_code）
_HEADING_EXTRACTORS: list[tuple[str, str, Any]] = [
    (r"(\d{4})\s*年", "year", None),
    (r"第([一二三四])季度", "quarter", None),
    (r"Q([1-4])", "quarter", None),
    (r"(利润表|资产负债表|现金流量表|所有者权益变动表)", "report_type", None),
    (r"([\u4e00-\u9fa5]{2,10}(?:公司|集团|有限))", "company", None),
    (r"(人民币|USD|CNY|美元|欧元|港币)", "currency", None),
]

# 证券代码正则（正文/开头）：证券代码[:：]?\s*(\d{6})
_SEC_CODE_PATTERN = re.compile(r"证券代码[:：]?\s*(\d{6})")

# 报告期正则（正文）：20XX年第X季度 / 20XX年
_REPORT_PERIOD_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*年\s*第(?P<quarter>[1-4一二三四])季度"
)


def extract_from_filename(filename: str) -> dict:
    """从文件名提取实体（company/year/quarter）。"""
    m = _FILENAME_PATTERN.match(filename or "")
    if not m:
        return {}
    entities = {
        "company": m.group("company"),
        "year": m.group("year"),
    }
    period = m.group("period")
    if period.startswith("q"):
        entities["quarter"] = period.upper()
    return entities


def extract_from_headings(heading_tree: list[tuple[int, str]]) -> dict:
    """标题栈规则层：遍历标题，父级继承、兄弟零泄漏。"""
    result: dict = {}
    # 简化的上下文栈：只保留最后一级提取的键值，子级继承父级
    for level, title in heading_tree or []:
        for pattern, key, transform in _HEADING_EXTRACTORS:
            m = re.search(pattern, title)
            if m:
                value = transform(m) if transform else m.group(1)
                result[key] = value
    return result


def _extract_from_text(text: str) -> dict:
    """从正文前缀提取 sec_code / report_period。"""
    entities: dict = {}
    m = _SEC_CODE_PATTERN.search(text or "")
    if m:
        entities["sec_code"] = m.group(1)
    m = _REPORT_PERIOD_PATTERN.search(text or "")
    if m:
        year = m.group("year")
        q = m.group("quarter")
        entities["report_period"] = f"{year}年第一季度" if q == "一" else f"{year}年Q{q}"
    return entities


class DocumentEntityExtractor:
    """文档级实体抽取器（三层链路）。"""

    def __init__(self, llm=None):
        self._llm = llm  # CLASSIFY_MODEL 实例，可为空（off 模式不调用）

    def extract(
        self,
        filename: str,
        heading_tree: list[tuple[int, str]],
        text: str,
        file_type: str,
    ) -> dict:
        """执行三层实体抽取，返回合并后的 entities dict。

        Args:
            filename: 文档文件名
            heading_tree: ParseResult.heading_tree
            text: 文档全文（用于 LLM 兜底输入取前缀）
            file_type: "pdf"/"docx"/"txt"

        Returns:
            entities dict，含 core 实体（company/report_period/sec_code）与可选实体
        """
        # ① 文件名正则
        rule_entities: dict = extract_from_filename(filename)

        # ② 标题栈规则层（仅 PDF/docx）
        if file_type in ENTITY_FULL_PIPELINE_TYPES:
            heading_entities = extract_from_headings(heading_tree)
            for k, v in heading_entities.items():
                rule_entities.setdefault(k, v)

        # 正文正则（sec_code / report_period）
        text_entities = _extract_from_text(text[:ENTITY_TEXT_PREFIX_LEN])
        for k, v in text_entities.items():
            rule_entities.setdefault(k, v)

        # ③ LLM 校验兜底（三态开关）
        llm_mode = ENTITY_LLM_FALLBACK
        should_llm = False
        if llm_mode == "on":
            should_llm = True
        elif llm_mode == "auto":
            core_missing = [t for t in ENTITY_TYPES if not rule_entities.get(t)]
            should_llm = (not rule_entities) or bool(core_missing)
        # off 或其他情况不调 LLM

        if should_llm and self._llm is not None:
            try:
                llm_entities = self._llm_fallback(
                    filename, heading_tree, text, rule_entities
                )
                if llm_entities:
                    # LLM 结果覆盖规则层同名键；只补不删
                    rule_entities.update(llm_entities)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Entity LLM fallback failed for '{}': {} (using rule result)",
                    filename,
                    e,
                )
        return rule_entities

    def _llm_fallback(
        self,
        filename: str,
        heading_tree: list[tuple[int, str]],
        text: str,
        rule_candidates: dict,
    ) -> dict:
        """调用 CLASSIFY_MODEL 校验规则候选并补全。"""
        heading_text = "\n".join(f"{'#' * lvl} {title}" for lvl, title in heading_tree)
        prefix = text[:ENTITY_TEXT_PREFIX_LEN]
        candidates_text = json.dumps(rule_candidates, ensure_ascii=False)
        prompt = ENTITY_EXTRACTION_USER_TEMPLATE.format(
            filename=filename,
            heading_tree=heading_text or "（无标题结构）",
            text_prefix=prefix,
            rule_candidates=candidates_text,
        )
        from langchain_core.messages import HumanMessage

        assert self._llm is not None
        resp = self._llm.invoke([HumanMessage(content=prompt)])
        raw = resp.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        data = json.loads(raw)
        entities = data.get("entities", {}) or {}
        # 过滤：只保留核心 + 可选类型内的键
        allowed = set(ENTITY_TYPES) | set(ENTITY_OPTIONAL_TYPES)
        return {k: v for k, v in entities.items() if k in allowed}
```

- [ ] **Step 4: Run 测试确认通过**

Run: `pytest tests/infra/test_document_entity_extractor.py -v`
Expected: PASS

- [ ] **Step 5: 写 LLM 兜底 + 三态开关测试（mock）**

```python
# tests/infra/test_document_entity_extractor.py 追加
class TestLLMFallback:
    class _FakeLLM:
        """mock LLM：返回固定 JSON 内容。"""
        def __init__(self, content: str):
            self._content = content

        def invoke(self, messages, **kwargs):
            class _Resp:
                content = None
            r = _Resp()
            r.content = self._content
            return r

    def test_auto_mode_skips_when_rules_complete(self, monkeypatch):
        # 规则层已抽齐核心实体（company/year 文件名即给 company）
        from src.config import settings
        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        extractor = DocumentEntityExtractor(llm=self._FakeLLM('{"entities": {}}'))
        # neusoft 文件名给出 company/year，正文给出 sec_code/report_period
        text = "证券代码：600718\n2025 年第一季度"
        heading_tree = [(1, "2025 年第一季度报告")]
        entities = extractor.extract("neusoft_2025_q1.pdf", heading_tree, text, "pdf")
        # 规则已齐，auto 模式不调 LLM → 结果就是规则结果
        assert entities.get("company") == "neusoft"

    def test_auto_mode_triggers_when_missing_core(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "auto")
        # LLM 补 report_period
        llm = self._FakeLLM(
            '{"entities": {"report_period": "2025年第一季度"}}'
        )
        extractor = DocumentEntityExtractor(llm=llm)
        entities = extractor.extract(
            "report.pdf", [], "无证券代码 无年份", "pdf"
        )
        assert entities.get("report_period") == "2025年第一季度"

    def test_off_mode_never_calls_llm(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "ENTITY_LLM_FALLBACK", "off")
        extractor = DocumentEntityExtractor(llm=self._FakeLLM('{"entities": {"company": "X"}}'))
        entities = extractor.extract("report.pdf", [], "无内容", "pdf")
        assert "company" not in entities
```

- [ ] **Step 6: Run 测试确认通过**

Run: `pytest tests/infra/test_document_entity_extractor.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/infra/search/document_entity_extractor.py tests/infra/test_document_entity_extractor.py
git commit -m "feat(entities): document entity extractor with rule + LLM fallback (3-state)"
```

---

### Task 7: 接入 process_document（实体注入 chunk + meta_info 聚合）

**Files:**
- Modify: `src/services/document_service.py`
- Test: `tests/services/test_document_service.py`

**Interfaces:**
- Consumes: `DocumentEntityExtractor.extract`（Task 6）；`build_heading_segments`（Task 4）
- Produces: chunk.metadata 含实体字段（company/report_period/sec_code 等）+ heading_path；`document.meta_info["entities"]` 聚合

- [ ] **Step 1: 写接入测试（mock extractor）**

```python
# tests/services/test_document_service.py 追加
"""实体抽取接入 document_service 的测试。"""
from unittest.mock import patch


class TestEntityInjection:
    def test_entities_injected_into_chunks(self):
        """实体注入每个 chunk.metadata + meta_info 聚合。"""
        with patch(
            "src.infra.search.document_entity_extractor.DocumentEntityExtractor.extract",
            return_value={
                "company": "东软集团",
                "report_period": "2025年第一季度",
                "sec_code": "600718",
            },
        ):
            # 构造一个最小 document_service（或复用现有 fixture）
            # 断言: 所有 chunk.metadata 含 company/sec_code；meta_info 含 entities
            pass  # 具体断言依赖现有测试 fixture，实施时填充
```

注意：此测试依赖现有 `test_document_service.py` 的 fixture 结构，实施时先读该文件再补全断言。若现有测试无合适 fixture，可改用对 `process_document` 核心片段（实体注入部分）的纯函数级测试。

- [ ] **Step 2: 实现接入**

```python
# src/services/document_service.py process_document 中，在分块与 _enrich 之后、add_chunks 之前：

# 实体抽取（文档级一次）
from src.infra.search.document_entity_extractor import DocumentEntityExtractor

entity_extractor = DocumentEntityExtractor(llm=get_classify_llm())
doc_entities = await asyncio.to_thread(
    entity_extractor.extract,
    filename,
    parse_result.heading_tree,
    full_text,
    parse_result.file_type,
)
if doc_entities:
    # 注入每个 chunk.metadata（扁平键，ChromaDB 兼容）
    for c in chunks:
        for k, v in doc_entities.items():
            c.metadata[k] = v
    # meta_info 聚合（update_document_meta_info 是合并，不覆盖 eval）
    await self._doc_repo.update_document_meta_info(doc_id, {"entities": doc_entities})
    logger.info(
        "Entity extraction for '{}': {}",
        filename,
        list(doc_entities.keys()),
    )
```

注意：
- `get_classify_llm()` 需从 `src.models` 导入（已在 models.py）
- 调用位置在 `_enrich_chunk_metadata` 之后、`_merge_tiny_chunks` 与 `add_chunks` 之前均可（metadata 注入不依赖 content）
- 若 `get_classify_llm()` 创建成本高，可在模块级或文档级复用实例

- [ ] **Step 3: Run 测试**

Run: `pytest tests/services/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/services/document_service.py tests/services/test_document_service.py
git commit -m "feat(ingest): inject document entities into chunk metadata + meta_info"
```

---

### Task 8: RAGContext.entities 透传 + to_prompt_text 渲染

**Files:**
- Modify: `src/rag/context.py`（RAGContext + to_prompt_text）
- Modify: `src/rag/retrieval.py`（rerank_results 透传）
- Modify: `tests/agents/graph/test_graph.py`（RAGContext 构造，如需要）
- Test: `tests/rag/test_retrieval.py`、`tests/agents/graph/test_state.py`

**Interfaces:**
- Consumes: `ENTITY_RENDER_ORDER`（Task 5）
- Produces:
  - `RAGContext.entities: dict = field(default_factory=dict)`
  - `rerank_results` 返回的 RAGContext 带 entities（从 chunk.metadata 读取核心实体键）
  - `to_prompt_text()` 渲染存在的核心实体

- [ ] **Step 1: 写 to_prompt_text 渲染测试**

```python
# tests/rag/test_retrieval.py 追加
def test_to_prompt_text_with_entities():
    from src.rag.context import RAGContext
    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
        entities={"company": "东软集团", "report_period": "2025年第一季度"},
    )
    text = ctx.to_prompt_text()
    assert "东软集团" in text
    assert "2025年第一季度" in text


def test_to_prompt_text_without_entities():
    from src.rag.context import RAGContext
    ctx = RAGContext(
        content="营收增长",
        source="neusoft_2025_q1.pdf",
        page=3,
        doc_id="d",
        chunk_id="c",
    )
    text = ctx.to_prompt_text()
    # 无实体时保持原格式（来源/页码/内容）
    assert text == "来源: neusoft_2025_q1.pdf (第3页)\n内容: 营收增长"
```

- [ ] **Step 2: Run 测试确认失败**

Run: `pytest tests/rag/test_retrieval.py -v`
Expected: FAIL（RAGContext 无 entities 字段）

- [ ] **Step 3: 实现 RAGContext**

```python
# src/rag/context.py
from dataclasses import dataclass, field
from src.config.const import ENTITY_RENDER_ORDER

@dataclass(slots=True)
class RAGContext:
    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0
    entities: dict = field(default_factory=dict)  # 业务实体，来自 chunk.metadata

    def to_prompt_text(self) -> str:
        """渲染为喂给生成模型的单个上下文文本。

        生产 prompt（prompt.format_context）与 RAGAS 评估的 NLI 上下文
        共用此格式。实体按 ENTITY_RENDER_ORDER 渲染存在的核心实体。
        """
        parts = [f"来源: {self.source} (第{self.page}页)"]
        entity_parts = []
        for key in ENTITY_RENDER_ORDER:
            value = self.entities.get(key)
            if value:
                label = {"company": "公司", "report_period": "期间", "sec_code": "代码"}.get(
                    key, key
                )
                entity_parts.append(f"{label}: {value}")
        if entity_parts:
            parts.append(" ".join(entity_parts))
        parts.append(f"内容: {self.content}")
        return "\n".join(parts)
```

注意：`to_citation` 方法保持不动（引用展示格式不变）。

- [ ] **Step 4: 实现 rerank 透传**

```python
# src/rag/retrieval.py rerank_results 的 RAGContext 构造处：
from src.config.const import ENTITY_TYPES, ENTITY_OPTIONAL_TYPES

_ALL_ENTITY_KEYS = tuple(ENTITY_TYPES) + tuple(ENTITY_OPTIONAL_TYPES)

# 构造 RAGContext 时：
contexts.append(
    RAGContext(
        content=content,
        source=r.metadata.get("source", ""),
        page=r.metadata.get("page", 0),
        doc_id=r.metadata.get("doc_id", ""),
        chunk_id=r.id,
        parent_content=pc,
        score=score,
        entities={k: r.metadata.get(k) for k in _ALL_ENTITY_KEYS if r.metadata.get(k)},
    )
)
```

- [ ] **Step 5: Run 测试**

Run: `pytest tests/rag/ tests/agents/graph/ -v`
Expected: PASS（若现有 RAGContext 构造测试报缺参，检查 entities 默认值是否生效）

- [ ] **Step 6: Commit**

```bash
git add src/rag/context.py src/rag/retrieval.py tests/rag/test_retrieval.py
git commit -m "feat(rag): RAGContext.entities passthrough + entity rendering in to_prompt_text"
```

---

### Task 9: 当前日期注入 prompt

**Files:**
- Modify: `src/infra/llm/prompt_manager.py`
- Test: `tests/infra/test_prompt_manager.py`（如存在，否则新建）

**Interfaces:**
- Consumes: 无
- Produces: `get_system_prompt()` 返回含今日日期的 prompt（"今天是 YYYY年M月D日"）

- [ ] **Step 1: 写测试**

```python
# tests/infra/test_prompt_manager.py
"""PromptManager 当前日期注入测试。"""
from datetime import date
from src.infra.llm.prompt_manager import PromptManager


def test_system_prompt_contains_today_date():
    pm = PromptManager(cache_ttl=0)  # 关缓存避免跨天干扰
    prompt = pm.get_system_prompt()
    today = date.today()
    expected = f"{today.year}年{today.month}月{today.day}日"
    assert expected in prompt
```

- [ ] **Step 2: Run 测试确认失败**

Run: `pytest tests/infra/test_prompt_manager.py -v`
Expected: FAIL（无日期）

- [ ] **Step 3: 实现日期注入**

```python
# src/infra/llm/prompt_manager.py
from datetime import date

def _with_current_date(prompt: str) -> str:
    """在系统提示词中追加今日日期，锚定相对时间表达（本报告期/今年）。"""
    today = date.today()
    date_line = f"\n今天是 {today.year}年{today.month}月{today.day}日。\n"
    if date_line.strip() in prompt:
        return prompt
    return prompt + date_line

# get_system_prompt() 中，在返回前追加：
prompt = self._get(self.PROMPT_NAMES["system"], _FALLBACK_SYSTEM_PROMPT)
if _INLINE_CITATION_INSTRUCTION not in prompt:
    prompt += _INLINE_CITATION_INSTRUCTION
return _with_current_date(prompt)
```

- [ ] **Step 4: Run 测试确认通过**

Run: `pytest tests/infra/test_prompt_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/prompt_manager.py tests/infra/test_prompt_manager.py
git commit -m "feat(prompt): inject current date into system prompt"
```

---

### Task 10: clarify 追问动态化

**Files:**
- Modify: `src/config/prompts.py`（CLASSIFIER_USER_TEMPLATE 加 {kb_entities}）
- Modify: `src/infra/search/query_router.py`（聚合 KB 候选注入 prompt）
- Modify: `src/services/agent_service.py`（SUGGESTIONS_MAP → 动态候选）
- Test: `tests/infra/test_query_router.py`、`tests/services/test_agent_service.py`（如存在）

**Interfaces:**
- Consumes: document.meta_info（MySQL，实体聚合源）
- Produces: classifier prompt 含 `{kb_entities}`；clarify suggestions 来自 KB 候选

- [ ] **Step 1: 更新 CLASSIFIER_USER_TEMPLATE**

```python
# src/config/prompts.py
CLASSIFIER_USER_TEMPLATE: str = """用户问题：{query}

已提取实体（正则）：
{entities}

知识库候选实体（供追问参考）：
{kb_entities}

复杂度评分（规则预判）：{complexity_score}

对话历史（最近2轮）：
{history}

输出 JSON（严格按此格式）：
{{
  "route": "simple|medium|complex",
  "missing_entities": [
    {{"type": "year", "question": "请问您想查询哪一年的数据？"}}
  ],
  "confidence": 0.0
}}
"""
```

- [ ] **Step 2: 实现 KB 候选聚合 + 注入**

```python
# src/infra/search/query_router.py
async def _aggregate_kb_entities(self, kb_ids: list[str] | None) -> str:
    """从 KB 文档的 meta_info 聚合候选实体（公司/报告期/代码），返回格式化字符串。"""
    if not kb_ids:
        return "无"
    from src.infra.db.engine import session_factory
    from src.infra.db.mysql_db import DocumentRepo

    repo = DocumentRepo(session_factory)
    companies: set[str] = set()
    periods: set[str] = set()
    codes: set[str] = set()
    for kb_id in kb_ids:
        docs = await repo.get_documents(kb_id)
        for d in docs:
            meta = json.loads(d.meta_info or "{}")
            entities = meta.get("entities", {}) or {}
            if entities.get("company"):
                companies.add(str(entities["company"]))
            if entities.get("report_period"):
                periods.add(str(entities["report_period"]))
            if entities.get("sec_code"):
                codes.add(str(entities["sec_code"]))
    parts = []
    if companies:
        parts.append("公司: " + "、".join(sorted(companies)))
    if periods:
        parts.append("报告期: " + "、".join(sorted(periods)))
    if codes:
        parts.append("代码: " + "、".join(sorted(codes)))
    return "; ".join(parts) if parts else "无"
```

```python
# route() 中传入 kb_entities：
# 由于 route() 当前是同步方法，且 kb_id 在 state 中，
# 将聚合逻辑做成同步简化版（从缓存读取）或调整调用点。
# 实施时：若 QueryRouter.route 需保持同步，则 kb_entities 由调用方（classify_node）传入参数。
```

- [ ] **Step 3: 更新 classify_node 调用**

```python
# src/agents/graph/nodes.py make_classify_node 中：
result = router.route(state.query, state._history, kb_entities=kb_entities_str)
```

- [ ] **Step 4: 更新 agent_service SUGGESTIONS_MAP**

```python
# src/services/agent_service.py 澄清分支（约 L181）：
# 将 SUGGESTIONS_MAP 静态映射替换为从已聚合的 KB 候选取 suggestions。
# 保留 default 兜底：SUGGESTIONS_MAP["default"]
suggestions = kb_suggestions.get(entity_type, SUGGESTIONS_MAP["default"])
```

- [ ] **Step 5: 单测**

```python
# tests/services/test_agent_service.py（如存在）或新建
# 断言: clarify 分支产出的 suggestions 来自 KB 候选而非硬编码"腾讯/阿里巴巴"
```

- [ ] **Step 6: Run 测试 + Commit**

Run: `pytest tests/infra/test_query_router.py tests/services/ -v`
Expected: PASS

```bash
git add src/config/prompts.py src/infra/search/query_router.py src/services/agent_service.py
git commit -m "feat(clarify): dynamic KB entity candidates for clarify questions"
```

---

### Task 11: 存量清除重建

**Files:**
- Create: `scripts/rebuild_kb_data.py`（清除 + 重入库辅助脚本）
- Test: 手动验证（数据操作，无单测）

**Interfaces:**
- Consumes: 所有前置任务
- Produces: 评估 KB 重建为带实体 metadata 的 chunks

- [ ] **Step 1: 写清除重建脚本**

```python
# scripts/rebuild_kb_data.py
"""存量文档清除重建脚本 — 删除指定 KB 的 ChromaDB collection 与 document 记录。

用法:
  python -m scripts.rebuild_kb_data --kb-id <kb_id> [--all]
"""
import argparse
import asyncio

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import DocumentRepo, KbRepo
from src.infra.db.vector_store import VectorStore


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-id", required=True)
    args = parser.parse_args()

    vector_store = VectorStore()
    repo = DocumentRepo(session_factory)

    # 1. 清 ChromaDB collection（或按 kb_id 清 doc）
    collection = vector_store.get_or_create_collection(args.kb_id)
    ids = collection.get(include=[])["ids"]
    if ids:
        collection.delete(ids=ids)
        print(f"ChromaDB: deleted {len(ids)} chunks in kb_{args.kb_id}")

    # 2. 软删 document 记录
    await repo.soft_delete_documents_by_kb(args.kb_id)
    print(f"MySQL: soft-deleted documents for kb {args.kb_id}")


if __name__ == "__main__":
    asyncio.run(main())
```

注意：脚本实现时先读 `src/infra/db/vector_store/__init__.py` 的现有 API（get_or_create_collection 是否暴露），按实际 API 调整。

- [ ] **Step 2: 重建评估 KB**

```bash
# 删除评估 KB 的存量 chunk
python -m scripts.rebuild_kb_data --kb-id b9e74e820e0a4bad8472304446e54f5c

# 重新上传 2 份文档触发重新入库（通过 API 或直接调 process_document）
# 验证: 新 chunk metadata 含 company/report_period/sec_code + heading_path
```

- [ ] **Step 3: 验证实体落库**

```bash
cd /mnt/d/code/demo/AIAgent/corporate_rag && python3 -c "
import asyncio, sys
sys.path.insert(0, '.')
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.search import get_chunks_by_doc_id
from src.infra.db.mysql_db import DocumentRepo
from src.infra.db.engine import session_factory

async def main():
    kb_id = 'b9e74e820e0a4bad8472304446e54f5c'
    repo = DocumentRepo(session_factory)
    docs = await repo.get_documents(kb_id)
    for d in docs:
        print(d.filename, 'meta_info entities:', (d.meta_info or '{}')[:200])
    vs = VectorStore()
    for d in docs:
        chunks = get_chunks_by_doc_id(vs.get_or_create_collection(kb_id), d.id)
        if chunks:
            print(d.filename, 'first chunk metadata keys:', list(chunks[0].metadata.keys()))
            print('  entities:', {k: chunks[0].metadata[k] for k in ('company','report_period','sec_code') if k in chunks[0].metadata})
asyncio.run(main())
"
```

Expected: 每个文档的 chunk metadata 含 company/report_period/sec_code，meta_info 含 entities

- [ ] **Step 4: Commit**

```bash
git add scripts/rebuild_kb_data.py
git commit -m "feat(scripts): rebuild KB data for entity-enriched ingestion"
```

---

### Task 12: 回归验证

**Files:**
- Modify: `docs/api_contract.md`、`src/cli/README.md`（如响应结构变化）

**Interfaces:**
- Consumes: 所有前置任务
- Produces: 全量测试通过 + RAGAS 评估对比基线

- [ ] **Step 1: 质量门禁**

Run: `pytest tests/ -v`
Expected: 全部 PASS

Run: `ruff check . && ruff format --check .`
Expected: 无错误

Run: `pyright src/`
Expected: 不新增 error（存量第三方库误报除外）

- [ ] **Step 2: 跑 RAGAS 评估对比基线**

```bash
python -m src.cli.eval_ragas --kb-id b9e74e820e0a4bad8472304446e54f5c
# 对比基线: faithfulness=0.9333, answer_relevancy=0.7859,
#          context_precision=1.0000, context_recall=0.9167
# 期望: faithfulness 不降（实体锚点生效），context 指标稳定
```

- [ ] **Step 3: 更新文档**

```bash
# 若 RAGAS 评估上下文格式变化（to_prompt_text 加实体）：
# 更新 docs/api_contract.md 相关说明
# 更新 src/cli/README.md 评估工作流说明（如上下文含实体锚点）
```

- [ ] **Step 4: 最终 Commit**

```bash
git add docs/api_contract.md src/cli/README.md
git commit -m "docs: update contract and eval README for entity enrichment"
```

---

## Self-Review 记录

**Spec 覆盖检查**：
- Document-level entity extraction → Task 5/6（配置+pipeline）
- Entity injection into chunk metadata → Task 7
- Heading path binding → Task 4
- Entity rendering into prompt context → Task 8
- Parser upgrade to pymupdf4llm → Task 1/2/3
- NLI context shared rendering → Task 8（to_prompt_text 共用）
- Current date → Task 9
- clarify 动态化 → Task 10
- 存量重建 → Task 11
- 回归 → Task 12

**类型一致性**：
- `heading_tree: list[tuple[int, str]]` 在 Task 2（base.py）、Task 3（docx）、Task 4（heading_locator）、Task 6（extractor）一致
- `entities: dict` 在 Task 6（产出）、Task 7（注入）、Task 8（消费）一致
- `ENTITY_LLM_FALLBACK` 三态字符串在 Task 5（配置）、Task 6（消费）一致
- `_enrich_chunk_pages` → `_enrich_chunk_metadata` 重命名在 Task 4 一致
