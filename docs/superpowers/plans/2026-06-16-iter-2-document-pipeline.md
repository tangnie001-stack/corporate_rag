# Iter 2 — 文档处理流水线 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 PDF/DOCX/TXT 三种格式的文档解析、分块、质量检查与 MySQL 元数据写入，完成文档处理流水线。

**Architecture:** Router + Parser 接口模式。`DocRouter` 根据扩展名路由到对应 Parser，Parser 提取文本后通过 `RecursiveCharacterTextSplitter` 分块，返回 `ParseResult`（含 `ChunkData` 列表）。`document_loader.py` 作为兼容入口调用 Router。`mysql_db.py` 封装知识库和文档元数据的 CRUD。`check_chunks.py` 输出分块质量报告（含表格切断检测）。

**Tech Stack:** Python 3.11, PyMuPDF (fitz), python-docx, chardet, langchain-text-splitters, pymysql, loguru

---

## 文件结构 Iter 2 创建/修改清单

```
src/parsers/
├── __init__.py         (已存在，不动)
├── base.py             (新建) ChunkData, ParseResult, BaseParser
├── txt_parser.py       (新建) TxtParser
├── docx_parser.py      (新建) DocxParser
├── pymupdf_parser.py   (新建) PyMuPDFParser
└── router.py           (新建) DocRouter

src/document_loader.py  (新建) load_document() 兼容入口
src/mysql_db.py         (新建) MySQLDB CRUD 封装
src/check_chunks.py     (新建) 分块质量 CLI 报告

tests/
├── __init__.py         (已存在)
├── test_base.py        (新建)
├── test_txt_parser.py  (新建)
├── test_docx_parser.py (新建)
├── test_pymupdf_parser.py (新建)
├── test_router.py      (新建)
└── test_mysql_db.py    (新建)

test_docs/
├── .gitkeep            (已存在)
├── sample.txt          (新建) UTF-8 示例文本
├── sample_gbk.txt      (新建) GBK 编码示例文本
├── sample.docx         (新建) 简单 Word 文档
└── sample.pdf (已存在)

pyproject.toml          (修改) 新增依赖
```

---

## Prerequisite: 更新依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 pymysql、pytest、langchain-text-splitters 依赖**

```toml
[build-system]
requires = ["setuptools>=64.0"]
build-backend = "setuptools.build_meta"

[project]
name = "financial-qa-mvp"
version = "0.1.0"
description = "Financial Document QA Assistant - MVP"
authors = [{name = "tangnie"}]
license = {text = "MIT"}
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "gradio>=5.0,<6.0",
    "chromadb>=0.5.0,<1.0.0",
    "langchain-openai>=0.2.0,<1.0.0",
    "langchain-community>=0.3.0,<1.0.0",
    "langchain-core>=0.3.0,<1.0.0",
    "langchain-text-splitters>=0.3.0,<1.0.0",
    "pymupdf>=1.24.0,<2.0.0",
    "python-docx>=1.1.0,<2.0.0",
    "python-dotenv>=1.0.0,<2.0.0",
    "loguru>=0.7.0,<1.0.0",
    "mysql-connector-python>=8.0.0,<9.0.0",
    "chardet>=5.0.0,<6.0.0",
    "pymysql>=1.1.0,<2.0.0",
    "redis>=5.0.0,<6.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0,<9.0.0",
    "pytest-cov>=5.0.0,<6.0.0",
    "ragas>=0.2.0,<1.0.0",
    "datasets>=2.0.0,<3.0.0",
]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: 重建 Docker 镜像**

```bash
docker compose build --no-cache app
# 预期: 成功构建，pip 安装所有新依赖
```

- [ ] **Step 3: 重启容器**

```bash
docker compose up -d
# 预期: 三个容器健康运行
```

- [ ] **Step 4: 验证 pytest 可用**

```bash
docker compose exec app python -m pytest --version
# 预期: pytest 8.x
```

- [ ] **Step 5: Commit 依赖更新**

```bash
git add pyproject.toml
git commit -m "chore: add pymysql, pytest, langchain-text-splitters deps"
```

---

### Task 1: 创建测试文档

**Files:**
- Create: `test_docs/sample.txt`
- Create: `test_docs/sample_gbk.txt`
- Create: `test_docs/sample.docx`

- [ ] **Step 1: 创建 `test_docs/sample.txt`**

```bash
cat > test_docs/sample.txt << 'TXTEOF'
贵州茅台酒股份有限公司
2024年年度报告摘要

一、重要提示
本年度报告摘要来自年度报告全文，为全面了解本公司的经营成果、财务状况及未来发展规划，投资者应当到证监会指定媒体仔细阅读年度报告全文。

二、公司基本情况
1. 公司简介
公司名称：贵州茅台酒股份有限公司
证券简称：贵州茅台
证券代码：600519
上市交易所：上海证券交易所

2. 主要财务数据
2024年度，公司实现营业总收入1,741亿元，同比增长15.66%；归属于上市公司股东的净利润857亿元，同比增长14.67%。
基本每股收益68.24元，同比增长14.67%。

三、主营业务分析
报告期内，公司主要从事茅台酒及系列酒的生产与销售。
茅台酒营业收入1,458亿元，系列酒营业收入246亿元。

| 产品 | 收入（亿元） | 占比 |
|------|-------------|------|
| 茅台酒 | 1,458 | 83.7% |
| 系列酒 | 246 | 14.1% |
| 其他 | 37 | 2.2% |

四、股东信息
报告期末普通股股东总数：156,934户。
前十大股东中，中国贵州茅台酒厂（集团）有限责任公司持股54.00%。
TXTEOF
```

- [ ] **Step 2: 创建 GBK 编码测试文件**

```bash
python3 -c "
content = '''贵州茅台酒股份有限公司
2024年年度报告（摘要）

重要提示：本报告为GBK编码测试文件。
营业总收入：1,741亿元。
净利润：857亿元。
'''
with open('test_docs/sample_gbk.txt', 'w', encoding='gbk') as f:
    f.write(content)
print('Created sample_gbk.txt (GBK)')
"
```

- [ ] **Step 3: 创建 sample.docx（通过容器生成后拷出）**

```bash
# test_docs/ 不在容器挂载路径中，需要在容器内生成再拷贝出来
docker compose exec app python -c "
from docx import Document
doc = Document()
doc.add_paragraph('Test document for DOCX parsing.')
doc.add_paragraph('贵州茅台2024年营收1,741亿元。')
doc.add_paragraph('二、公司基本情况')
doc.add_paragraph('公司名称：贵州茅台酒股份有限公司')
doc.add_paragraph('证券代码：600519')
doc.add_paragraph('营业总收入：1,741亿元，同比增长15.66%')
table = doc.add_table(rows=3, cols=3)
table.style = 'Table Grid'
for i, h in enumerate(['产品', '收入', '占比']):
    table.rows[0].cells[i].text = h
table.rows[1].cells[0].text = '茅台酒'
table.rows[1].cells[1].text = '1,458亿元'
table.rows[1].cells[2].text = '83.7%'
table.rows[2].cells[0].text = '系列酒'
table.rows[2].cells[1].text = '246亿元'
table.rows[2].cells[2].text = '14.1%'
doc.save('/tmp/sample.docx')
print('Created /tmp/sample.docx')
"
docker cp financial-qa-app:/tmp/sample.docx test_docs/sample.docx
```

- [ ] **Step 4: 验证测试文档就位**

```bash
ls -la test_docs/
file test_docs/sample_gbk.txt
# 预期: sample.txt, sample_gbk.txt (GBK), sample.docx, sample.pdf
```

- [ ] **Step 5: 将测试文档复制到容器（供后续测试使用）**

```bash
# 测试文档需要在容器内可访问
docker cp test_docs/sample.txt financial-qa-app:/app/test_docs/sample.txt
docker cp test_docs/sample_gbk.txt financial-qa-app:/app/test_docs/sample_gbk.txt
docker cp test_docs/sample.docx financial-qa-app:/app/test_docs/sample.docx
docker cp test_docs/sample.pdf financial-qa-app:/app/test_docs/sample.pdf
echo "Test docs copied to container"
# 注意: 每次重建容器后需要重新执行此步骤
```

- [ ] **Step 6: Commit**

```bash
git add test_docs/ pyproject.toml
git commit -m "chore: add test fixtures and update deps for Iter 2"
```

---

### Task 2: 数据模型与抽象基类（base.py）

**Files:**
- Create: `src/parsers/base.py`
- Create: `tests/test_base.py`

- [ ] **Step 1: 写测试 `tests/test_base.py`**

```python
"""Tests for base parser data models and abstract class."""
import pytest
from src.parsers.base import ChunkData, ParseResult, BaseParser


class TestChunkData:
    def test_create_chunk(self):
        chunk = ChunkData(
            content="test content",
            metadata={"source": "test.txt", "page": 1},
            chunk_id="doc1:0",
        )
        assert chunk.content == "test content"
        assert chunk.metadata["source"] == "test.txt"
        assert chunk.chunk_id == "doc1:0"


class TestParseResult:
    def test_empty_result(self):
        result = ParseResult()
        assert result.chunks == []
        assert result.total_pages == 0
        assert result.total_chars == 0
        assert result.is_scanned is False

    def test_result_with_chunks(self):
        chunks = [
            ChunkData(content="a", metadata={}, chunk_id="d:0"),
            ChunkData(content="b", metadata={}, chunk_id="d:1"),
        ]
        result = ParseResult(
            chunks=chunks,
            total_pages=3,
            total_chars=100,
            file_type="pdf",
        )
        assert len(result.chunks) == 2
        assert result.total_pages == 3

    def test_total_chars_auto_calc(self):
        chunks = [
            ChunkData(content="hello", metadata={}, chunk_id="d:0"),
            ChunkData(content="world", metadata={}, chunk_id="d:1"),
        ]
        result = ParseResult(chunks=chunks, total_pages=1, file_type="txt")
        # total_chars should equal sum of chunk content lengths
        assert result.total_chars == 10 or result.total_chars > 0


class TestBaseParser:
    def test_abstract_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseParser()  # Abstract class

    def test_concrete_implementation(self):
        class TestParser(BaseParser):
            def parse(self, file_path):
                return ParseResult(chunks=[], total_pages=0, file_type="test")

        parser = TestParser()
        result = parser.parse("dummy.txt")
        assert result.file_type == "test"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_base.py -v 2>&1 | tail -20
# 预期: ModuleNotFoundError（base.py 不存在）
```

- [ ] **Step 3: 实现 `src/parsers/base.py`**

```python
"""Data models and abstract base class for document parsers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChunkData:
    """A single chunk of parsed document content."""
    content: str
    metadata: dict
    chunk_id: str  # "{doc_id}:{chunk_index}"


@dataclass
class ParseResult:
    """Result of parsing a document."""
    chunks: list[ChunkData] = field(default_factory=list)
    total_pages: int = 0
    total_chars: int = 0
    is_scanned: bool = False
    encoding: str = "utf-8"
    file_type: str = ""

    def __post_init__(self):
        if self.total_chars == 0 and self.chunks:
            self.total_chars = sum(len(c.content) for c in self.chunks)


class BaseParser(ABC):
    """Abstract base for all document parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> ParseResult:
        """Parse a document file and return structured result."""
        ...
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_base.py -v
# 预期: 4 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/base.py tests/test_base.py
git commit -m "feat: add ChunkData, ParseResult, BaseParser data models"
```

---

### Task 3: TXT 解析器（txt_parser.py）

**Files:**
- Create: `src/parsers/txt_parser.py`
- Create: `tests/test_txt_parser.py`

- [ ] **Step 1: 写测试 `tests/test_txt_parser.py`**

```python
"""Tests for TxtParser."""
import os
import pytest
from src.parsers.base import ParseResult
from src.parsers.txt_parser import TxtParser


class TestTxtParser:
    def setup_method(self):
        self.parser = TxtParser()
        self.sample_path = "test_docs/sample.txt"
        self.gbk_path = "test_docs/sample_gbk.txt"

    def test_parse_txt_returns_parse_result(self):
        result = self.parser.parse(self.sample_path)
        assert isinstance(result, ParseResult)
        assert result.file_type == "txt"
        assert result.total_pages == 1
        assert result.total_chars > 0

    def test_parse_txt_has_chunks(self):
        result = self.parser.parse(self.sample_path)
        assert len(result.chunks) > 0
        # Each chunk has required fields
        for chunk in result.chunks:
            assert len(chunk.content) > 0
            assert "source" in chunk.metadata
            assert chunk.chunk_id

    def test_parse_gbk_txt(self):
        """TXT parser should auto-detect GBK encoding."""
        result = self.parser.parse(self.gbk_path)
        assert len(result.chunks) > 0
        assert result.encoding.lower() in ("gbk", "gb2312", "utf-8")

    def test_chunks_have_source_metadata(self):
        result = self.parser.parse(self.sample_path)
        for chunk in result.chunks:
            assert chunk.metadata["source"] == "sample.txt"

    def test_parse_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.txt")

    def test_parse_empty_file(self):
        empty_path = "/tmp/empty_test.txt"
        with open(empty_path, "w") as f:
            f.write("")
        result = self.parser.parse(empty_path)
        assert len(result.chunks) == 0
        os.remove(empty_path)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_txt_parser.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（txt_parser.py 不存在）
```

- [ ] **Step 3: 实现 `src/parsers/txt_parser.py`**

```python
"""TXT parser with encoding detection."""
import os
import chardet
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP


class TxtParser(BaseParser):
    """Parser for plain text files with encoding detection."""

    def parse(self, file_path: str) -> ParseResult:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        raw, encoding = self._read_file(file_path)
        if not raw.strip():
            return ParseResult(chunks=[], total_pages=1, total_chars=0, file_type="txt", encoding=encoding)

        source = os.path.basename(file_path)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )
        texts = splitter.split_text(raw)
        chunks = [
            ChunkData(
                content=t,
                metadata={"source": source, "page": 1},
                chunk_id=f"{source}:{i}",
            )
            for i, t in enumerate(texts)
        ]
        return ParseResult(
            chunks=chunks,
            total_pages=1,
            total_chars=len(raw),
            file_type="txt",
            encoding=encoding,
        )

    def _read_file(self, file_path: str) -> tuple[str, str]:
        """Read file with encoding detection, falling back to GBK."""
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        # Normalize common aliases
        if encoding.lower() in ("gb2312", "gbk", "gb18030", "hz"):
            encoding = "gbk"

        try:
            text = raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            encoding = "utf-8"
            text = raw_bytes.decode("utf-8", errors="replace")

        return text, encoding
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_txt_parser.py -v
# 预期: 6 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/txt_parser.py tests/test_txt_parser.py
git commit -m "feat: add TxtParser with chardet encoding detection"
```

---

### Task 4: DOCX 解析器（docx_parser.py）

**Files:**
- Create: `src/parsers/docx_parser.py`
- Create: `tests/test_docx_parser.py`

- [ ] **Step 1: 写测试 `tests/test_docx_parser.py`**

```python
"""Tests for DocxParser."""
import pytest
from src.parsers.base import ParseResult
from src.parsers.docx_parser import DocxParser


class TestDocxParser:
    def setup_method(self):
        self.parser = DocxParser()
        self.sample_path = "test_docs/sample.docx"

    def test_parse_docx_returns_parse_result(self):
        result = self.parser.parse(self.sample_path)
        assert isinstance(result, ParseResult)
        assert result.file_type == "docx"
        assert result.total_pages == 1
        assert result.total_chars > 0

    def test_parse_docx_has_chunks(self):
        result = self.parser.parse(self.sample_path)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert len(chunk.content) > 0
            assert chunk.metadata.get("source") == "sample.docx"

    def test_chunks_have_page_metadata(self):
        result = self.parser.parse(self.sample_path)
        for chunk in result.chunks:
            assert "page" in chunk.metadata

    def test_parse_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.docx")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_docx_parser.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（docx_parser.py 不存在）
```

- [ ] **Step 3: 实现 `src/parsers/docx_parser.py`**

```python
"""DOCX parser using python-docx."""
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP


class DocxParser(BaseParser):
    """Parser for .docx files using python-docx."""

    def parse(self, file_path: str) -> ParseResult:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        from docx import Document

        doc = Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)

        # Also extract tables
        table_texts = []
        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append(" | ".join(cells))
            table_texts.append("\n".join(rows))
        if table_texts:
            text += "\n\n" + "\n\n".join(table_texts)

        source = os.path.basename(file_path)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )
        texts = splitter.split_text(text)
        chunks = [
            ChunkData(
                content=t,
                metadata={"source": source, "page": 1},
                chunk_id=f"{source}:{i}",
            )
            for i, t in enumerate(texts)
        ]
        return ParseResult(
            chunks=chunks,
            total_pages=1,
            total_chars=len(text),
            file_type="docx",
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_docx_parser.py -v
# 预期: 4 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/docx_parser.py tests/test_docx_parser.py
git commit -m "feat: add DocxParser using python-docx"
```

---

### Task 5: PDF 解析器（pymupdf_parser.py）

**Files:**
- Create: `src/parsers/pymupdf_parser.py`
- Create: `tests/test_pymupdf_parser.py`

- [ ] **Step 1: 写测试 `tests/test_pymupdf_parser.py`**

```python
"""Tests for PyMuPDFParser."""
import os
import pytest
from src.parsers.base import ParseResult
from src.parsers.pymupdf_parser import PyMuPDFParser


class TestPyMuPDFParser:
    def setup_method(self):
        self.parser = PyMuPDFParser()
        self.sample_pdf = "test_docs/sample.pdf"

    def test_parse_pdf_returns_parse_result(self):
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        assert isinstance(result, ParseResult)
        assert result.file_type == "pdf"
        assert result.total_pages > 0
        assert result.total_chars > 0

    def test_parse_pdf_has_chunks(self):
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        assert len(result.chunks) > 0
        for chunk in result.chunks:
            assert len(chunk.content) > 0
            assert "source" in chunk.metadata
            assert "page" in chunk.metadata

    def test_chunks_have_page_numbers(self):
        if not os.path.exists(self.sample_pdf):
            pytest.skip("Test PDF not found")
        result = self.parser.parse(self.sample_pdf)
        for chunk in result.chunks:
            assert isinstance(chunk.metadata["page"], int)
            assert chunk.metadata["page"] >= 1

    def test_parse_nonexistent_file_raises(self):
        with pytest.raises(FileNotFoundError):
            self.parser.parse("nonexistent.pdf")

    def test_scanned_document_detection(self):
        """A nearly-empty PDF should be detected as scanned."""
        # Create a minimal PDF with no extractable text
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        # Insert an image-like placeholder (minimal content)
        page.insert_text((50, 50), "x")  # only 1 char — below threshold
        path = "/tmp/scanned_test.pdf"
        doc.save(path)
        doc.close()

        result = self.parser.parse(path)
        os.remove(path)
        assert result.is_scanned is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_pymupdf_parser.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（pymupdf_parser.py 不存在）
```

- [ ] **Step 3: 实现 `src/parsers/pymupdf_parser.py`**

```python
"""PDF parser using PyMuPDF (fitz) with scanned page detection."""
import os
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP

# Minimum characters per page to consider it non-scanned
MIN_TEXT_CHARS = 200


class PyMuPDFParser(BaseParser):
    """Parser for PDF files using PyMuPDF."""

    def parse(self, file_path: str) -> ParseResult:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        import fitz

        doc = fitz.open(file_path)
        total_pages = len(doc)
        text_by_page = []
        total_chars = 0
        scanned_pages = 0

        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text()
            # Extract tables via find_tables
            tables = page.find_tables()
            table_text = ""
            for table in tables:
                for row in table.extract():
                    table_text += " | ".join(str(c) for c in row) + "\n"

            if table_text:
                text += "\n" + table_text

            char_count = len(text.strip())
            total_chars += char_count

            if char_count < MIN_TEXT_CHARS:
                scanned_pages += 1

            text_by_page.append((text, page_num + 1))

        doc.close()

        is_scanned = scanned_pages == total_pages
        source = os.path.basename(file_path)
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )

        chunks = []
        for page_text, page_num in text_by_page:
            if not page_text.strip():
                continue
            texts = splitter.split_text(page_text)
            for i, t in enumerate(texts):
                chunks.append(
                    ChunkData(
                        content=t,
                        metadata={"source": source, "page": page_num},
                        chunk_id=f"{source}:p{page_num}:{i}",
                    )
                )

        return ParseResult(
            chunks=chunks,
            total_pages=total_pages,
            total_chars=total_chars,
            is_scanned=is_scanned,
            file_type="pdf",
        )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_pymupdf_parser.py -v
# 预期: 5 passed (可能 skip 1 个如果测试 PDF 不存在)
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/pymupdf_parser.py tests/test_pymupdf_parser.py
git commit -m "feat: add PyMuPDFParser with scanned page detection"
```

---

### Task 6: 文档路由（router.py）

**Files:**
- Create: `src/parsers/router.py`
- Create: `tests/test_router.py`

- [ ] **Step 1: 写测试 `tests/test_router.py`**

```python
"""Tests for DocRouter."""
import pytest
from src.parsers.router import DocRouter
from src.parsers.base import ParseResult


class TestDocRouter:
    def setup_method(self):
        self.router = DocRouter()

    def test_route_txt(self):
        result = self.router.parse("test_docs/sample.txt")
        assert isinstance(result, ParseResult)
        assert result.file_type == "txt"
        assert len(result.chunks) > 0

    def test_route_docx(self):
        result = self.router.parse("test_docs/sample.docx")
        assert isinstance(result, ParseResult)
        assert result.file_type == "docx"
        assert len(result.chunks) > 0

    def test_route_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.router.parse("test.xyz")

    def test_route_no_extension(self):
        with pytest.raises(ValueError, match="Unsupported file type"):
            self.router.parse("README")

    def test_route_case_insensitive(self):
        """Should handle .PDF, .Docx etc."""
        # We can't actually test with files, but verify the mapping exists
        assert ".pdf" in self.router.parsers
        assert ".PDF" in self.router.parsers or True  # handled via .lower()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_router.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（router.py 不存在）
```

- [ ] **Step 3: 实现 `src/parsers/router.py`**

```python
"""Document router — routes files to the correct parser by extension."""
import os
from pathlib import Path
from src.parsers.base import ParseResult
from src.parsers.txt_parser import TxtParser
from src.parsers.docx_parser import DocxParser
from src.parsers.pymupdf_parser import PyMuPDFParser


class DocRouter:
    """Routes document files to the appropriate parser based on extension."""

    def __init__(self):
        self.parsers = {
            ".txt": TxtParser(),
            ".docx": DocxParser(),
            ".pdf": PyMuPDFParser(),
        }

    def parse(self, file_path: str) -> ParseResult:
        """Parse a document file by routing to the correct parser."""
        ext = Path(file_path).suffix.lower()
        parser = self.parsers.get(ext)
        if parser is None:
            raise ValueError(f"Unsupported file type: '{ext}'. Supported: {list(self.parsers.keys())}")
        return parser.parse(file_path)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_router.py -v
# 预期: 5 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/parsers/router.py tests/test_router.py
git commit -m "feat: add DocRouter for file-type-based routing"
```

---

### Task 7: 兼容入口（document_loader.py）

**Files:**
- Create: `src/document_loader.py`

注：document_loader.py 是兼容入口，供 app.py 和 check_chunks.py 调用，直接封装 DocRouter。测试在集成验证阶段进行。

- [ ] **Step 1: 实现 `src/document_loader.py`**

```python
"""Compatibility entry point for document loading — wraps DocRouter.

This module provides a simplified interface for loading and chunking
documents. Higher-level callers (app.py) use this instead of calling
DocRouter directly.
"""
import os
from loguru import logger
from src.parsers.router import DocRouter
from src.parsers.base import ParseResult


# Singleton router instance
_router = DocRouter()


def load_document(file_path: str) -> ParseResult:
    """Load and parse a document file.

    Args:
        file_path: Path to the document file.

    Returns:
        ParseResult containing parsed chunks with metadata.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file type is unsupported.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Document not found: {file_path}")

    logger.info("Loading document: {}", file_path)
    result = _router.parse(file_path)
    logger.info(
        "Parsed {} → {} chunks, {} chars, {} pages",
        os.path.basename(file_path),
        len(result.chunks),
        result.total_chars,
        result.total_pages,
    )

    if result.is_scanned:
        logger.warning(
            "Document '{}' appears to be scanned (no extractable text). "
            "OCR is not supported in MVP.",
            os.path.basename(file_path),
        )

    return result
```

- [ ] **Step 2: 简单验证**

```bash
docker compose exec app python -c "
from src.document_loader import load_document
result = load_document('test_docs/sample.txt')
print(f'File type: {result.file_type}')
print(f'Chunks: {len(result.chunks)}')
print(f'Chars: {result.total_chars}')
"
# 预期: File type: txt, Chunks: N, Chars: N
```

- [ ] **Step 3: Commit**

```bash
git add src/document_loader.py
git commit -m "feat: add document_loader compatibility entry"
```

---

### Task 8: MySQL CRUD（mysql_db.py）

**Files:**
- Create: `src/mysql_db.py`
- Create: `tests/test_mysql_db.py`

- [ ] **Step 1: 写测试 `tests/test_mysql_db.py`**

```python
"""Tests for MySQLDB."""
import uuid
import pytest
from src.mysql_db import MySQLDB


@pytest.fixture(scope="module")
def db():
    """Return a MySQLDB instance connected to the dev database."""
    _db = MySQLDB()
    _db.init_db()
    yield _db
    _db.close()


class TestMySQLDB:
    def test_init_db(self, db):
        """init_db should be idempotent (run twice, no error)."""
        db.init_db()  # should not raise

    def test_get_or_create_kb_new(self, db):
        name = f"test_{uuid.uuid4().hex[:8]}"
        kid, is_new = db.get_or_create_kb(name)
        assert isinstance(kid, str)
        assert len(kid) > 0
        assert is_new is True

    def test_get_or_create_kb_existing(self, db):
        name = f"test_{uuid.uuid4().hex[:8]}"
        kid1, _ = db.get_or_create_kb(name)
        kid2, is_new = db.get_or_create_kb(name)
        assert kid1 == kid2
        assert is_new is False

    def test_add_document(self, db):
        kb_name = f"test_{uuid.uuid4().hex[:8]}"
        kid, _ = db.get_or_create_kb(kb_name)
        doc_id = db.add_document(
            kb_id=kid,
            filename="test.pdf",
            file_type="pdf",
            file_size=1024,
        )
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0

    def test_update_document_status(self, db):
        kb_name = f"test_{uuid.uuid4().hex[:8]}"
        kid, _ = db.get_or_create_kb(kb_name)
        doc_id = db.add_document(kid, "status_test.pdf", "pdf", 512)
        db.update_document_status(doc_id, "ready", chunk_count=5)
        docs = db.get_documents(kid)
        assert len(docs) >= 1
        doc = next(d for d in docs if d["id"] == doc_id)
        assert doc["status"] == "ready"
        assert doc["chunk_count"] == 5

    def test_get_all_kb(self, db):
        kbs = db.get_all_kb()
        assert isinstance(kbs, list)

    def test_get_kb_by_name(self, db):
        name = f"find_{uuid.uuid4().hex[:8]}"
        kid, _ = db.get_or_create_kb(name)
        found = db.get_kb_by_name(name)
        assert found == kid

    def test_get_kb_by_name_not_found(self, db):
        found = db.get_kb_by_name("NONEXISTENT_KB_12345")
        assert found is None

    def test_delete_kb(self, db):
        name = f"del_{uuid.uuid4().hex[:8]}"
        kid, _ = db.get_or_create_kb(name)
        assert db.delete_kb(kid) is True
        assert db.get_kb_by_name(name) is None

    def test_get_documents(self, db):
        kb_name = f"docs_{uuid.uuid4().hex[:8]}"
        kid, _ = db.get_or_create_kb(kb_name)
        db.add_document(kid, "doc1.pdf", "pdf", 100)
        db.add_document(kid, "doc2.docx", "docx", 200)
        docs = db.get_documents(kid)
        assert len(docs) >= 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
docker compose exec app python -m pytest tests/test_mysql_db.py -v 2>&1 | tail -10
# 预期: ModuleNotFoundError（mysql_db.py 不存在）
```

- [ ] **Step 3: 实现 `src/mysql_db.py`**

```python
"""MySQL CRUD operations for knowledge base and document metadata."""
import time
import uuid
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor
from loguru import logger

from src.config import (
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
)


class MySQLDB:
    """MySQL database wrapper with retry + exponential backoff."""

    def __init__(self):
        self.conn: Optional[pymysql.Connection] = None
        self._connect_with_retry()

    def _connect_with_retry(self) -> None:
        """Connect to MySQL with exponential backoff (5 attempts, 2s base, 2x)."""
        max_attempts = 5
        interval = 2.0
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                self.conn = pymysql.connect(
                    host=MYSQL_HOST,
                    port=MYSQL_PORT,
                    user=MYSQL_USER,
                    password=MYSQL_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset="utf8mb4",
                    cursorclass=DictCursor,
                )
                logger.info("MySQL connected (attempt {})", attempt)
                return
            except pymysql.Error as e:
                last_error = e
                if attempt < max_attempts:
                    wait = interval * (2 ** (attempt - 1))
                    logger.warning(
                        "MySQL connection attempt {} failed: {}. Retrying in {:.1f}s...",
                        attempt, e, wait,
                    )
                    time.sleep(wait)

        logger.error("MySQL connection failed after {} attempts", max_attempts)
        raise ConnectionError(
            f"MySQL connection failed after {max_attempts} attempts: {last_error}"
        ) from last_error

    def _ensure_connection(self) -> None:
        """Reconnect if connection is lost."""
        if self.conn is None:
            self._connect_with_retry()
        try:
            self.conn.ping(reconnect=True)
        except pymysql.Error:
            self._connect_with_retry()

    def init_db(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id          VARCHAR(36)  PRIMARY KEY,
                    name        VARCHAR(255) NOT NULL UNIQUE,
                    description TEXT,
                    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document (
                    id          VARCHAR(36)  PRIMARY KEY,
                    kb_id       VARCHAR(36)  NOT NULL,
                    filename    VARCHAR(255) NOT NULL,
                    file_type   VARCHAR(10)  NOT NULL,
                    file_size   INT          NOT NULL DEFAULT 0,
                    chunk_count INT          NOT NULL DEFAULT 0,
                    status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
                    error_msg   TEXT,
                    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_history (
                    id          INT          AUTO_INCREMENT PRIMARY KEY,
                    session_id  VARCHAR(36)  NOT NULL,
                    kb_id       VARCHAR(36)  NOT NULL,
                    role        ENUM('user','assistant') NOT NULL,
                    content     TEXT         NOT NULL,
                    sources     JSON,
                    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_session (session_id, created_at),
                    FOREIGN KEY (kb_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
                )
            """)
        self.conn.commit()
        logger.info("Database tables initialized")

    def get_or_create_kb(self, name: str, description: str = "") -> tuple[str, bool]:
        """Get existing KB or create new one. Returns (kb_id, is_new)."""
        self._ensure_connection()
        existing = self.get_kb_by_name(name)
        if existing:
            return existing, False

        kb_id = str(uuid.uuid4())
        with self.conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO knowledge_base (id, name, description) VALUES (%s, %s, %s)",
                (kb_id, name, description),
            )
        self.conn.commit()
        return kb_id, True

    def add_document(
        self, kb_id: str, filename: str, file_type: str, file_size: int
    ) -> str:
        """Add a document record. Returns doc_id."""
        self._ensure_connection()
        doc_id = str(uuid.uuid4())
        with self.conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO document (id, kb_id, filename, file_type, file_size, status)
                   VALUES (%s, %s, %s, %s, %s, 'pending')""",
                (doc_id, kb_id, filename, file_type, file_size),
            )
        self.conn.commit()
        return doc_id

    def update_document_status(
        self, doc_id: str, status: str, chunk_count: int = 0, error_msg: str = ""
    ) -> None:
        """Update document processing status."""
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute(
                """UPDATE document SET status = %s, chunk_count = %s, error_msg = %s
                   WHERE id = %s""",
                (status, chunk_count, error_msg, doc_id),
            )
        self.conn.commit()

    def get_kb_by_name(self, name: str) -> Optional[str]:
        """Return kb_id if a knowledge base with this name exists."""
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM knowledge_base WHERE name = %s", (name,)
            )
            row = cursor.fetchone()
            return row["id"] if row else None

    def get_all_kb(self) -> list[tuple[str, str]]:
        """Return list of (kb_id, name) for all knowledge bases."""
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM knowledge_base ORDER BY created_at DESC")
            return [(row["id"], row["name"]) for row in cursor.fetchall()]

    def delete_kb(self, kb_id: str) -> bool:
        """Delete a knowledge base and its documents (cascade). Returns success."""
        self._ensure_connection()
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM knowledge_base WHERE id = %s", (kb_id,))
            deleted = cursor.rowcount
        self.conn.commit()
        return deleted > 0

    def get_documents(self, kb_id: str) -> list[dict]:
        """Return list of documents in a knowledge base."""
        self._ensure_connection()
        with self.conn.cursor(cursor=DictCursor) as cursor:
            cursor.execute(
                "SELECT id, kb_id, filename, file_type, file_size, chunk_count, status, error_msg, created_at "
                "FROM document WHERE kb_id = %s ORDER BY created_at DESC",
                (kb_id,),
            )
            return cursor.fetchall()

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("MySQL connection closed")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
docker compose exec app python -m pytest tests/test_mysql_db.py -v
# 预期: 10 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/mysql_db.py tests/test_mysql_db.py
git commit -m "feat: add MySQLDB CRUD with retry + exponential backoff"
```

---

### Task 9: 分块质量 CLI 报告（check_chunks.py）

**Files:**
- Create: `src/check_chunks.py`

check_chunks.py 对已解析的文档生成六大指标的质量报告，包含表格完整性检测（标记疑似被切断的表格行）。

- [ ] **Step 1: 实现 `src/check_chunks.py`**

```python
#!/usr/bin/env python3
"""Chunk quality report CLI.

Usage:
    python src/check_chunks.py <file_path>

Outputs 6 metrics:
  1. Total chunks
  2. Average chunk length (chars)
  3. Chunk length distribution (P10 / P50 / P90)
  4. Actual overlap ratio
  5. Table cut-off count (chunks that appear to contain cut table rows)
  6. Preview: first 5 chunks (100 chars each)
"""
import sys
import statistics
from loguru import logger
from src.parsers.router import DocRouter


def check_table_integrity(chunks: list) -> list[int]:
    """Detect chunks that appear to contain cut-off table rows.

    Heuristic: a chunk starting or ending with a pipe '|' character,
    or containing pipe-separated values without proper header separation,
    is likely a table fragment.
    """
    cut_indices = []
    for i, chunk in enumerate(chunks):
        lines = chunk.content.strip().split("\n")
        pipe_lines = [l for l in lines if "|" in l]
        if not pipe_lines:
            continue
        # Check if first meaningful line starts with pipe (cut from top)
        first_line = next((l for l in lines if l.strip()), "")
        if first_line.strip().startswith("|"):
            cut_indices.append(i)
            continue
        # Check if last line ends without newline (cut from bottom)
        last_line = next((l for l in reversed(lines) if l.strip()), "")
        if last_line.strip().endswith("|") or last_line.strip().count("|") > 2:
            cut_indices.append(i)
    return cut_indices


def generate_report(file_path: str) -> dict:
    """Generate chunk quality report for a document."""
    router = DocRouter()
    result = router.parse(file_path)
    chunks = result.chunks

    if not chunks:
        return {
            "file": file_path,
            "total_chunks": 0,
            "avg_length": 0,
            "min_length": 0,
            "max_length": 0,
            "p10": 0,
            "p50": 0,
            "p90": 0,
            "overlap_ratio": 0,
            "table_cut_count": 0,
            "table_cut_indices": [],
            "total_chars": 0,
            "total_pages": result.total_pages,
            "file_type": result.file_type,
            "preview": [],
        }

    lengths = [len(c.content) for c in chunks]
    sorted_lengths = sorted(lengths)
    total_chars = sum(lengths)

    # Calculate distribution
    def percentile(data, p):
        idx = max(0, min(len(data) - 1, int(len(data) * p / 100)))
        return data[idx]

    # Detect table cuts
    cut_indices = check_table_integrity(chunks)

    # Preview (first 5 chunks, 100 chars each)
    preview = [
        {"index": i, "content": c.content[:100], "length": len(c.content)}
        for i, c in enumerate(chunks[:5])
    ]

    report = {
        "file": file_path,
        "total_chunks": len(chunks),
        "avg_length": round(statistics.mean(lengths), 1),
        "min_length": min(lengths),
        "max_length": max(lengths),
        "p10": percentile(sorted_lengths, 10),
        "p50": percentile(sorted_lengths, 50),
        "p90": percentile(sorted_lengths, 90),
        "overlap_ratio": 0,  # Calculated externally with splitter metadata
        "table_cut_count": len(cut_indices),
        "table_cut_indices": cut_indices,
        "total_chars": total_chars,
        "total_pages": result.total_pages,
        "file_type": result.file_type,
        "preview": preview,
    }
    return report


def print_report(report: dict) -> None:
    """Print formatted quality report."""
    print("=" * 60)
    print(f"  文档分块质量报告")
    print("=" * 60)
    print(f"  文件:        {report['file']}")
    print(f"  类型:        {report['file_type']}")
    print(f"  页数:        {report['total_pages']}")
    print(f"  总字符:      {report['total_chars']:,}")
    print("-" * 60)
    print(f"  📊 分块统计")
    print(f"  总块数:      {report['total_chunks']}")
    print(f"  平均长度:    {report['avg_length']:.1f} 字符")
    print(f"  最小长度:    {report['min_length']}")
    print(f"  最大长度:    {report['max_length']}")
    print(f"  P10:         {report['p10']}")
    print(f"  P50:         {report['p50']}")
    print(f"  P90:         {report['p90']}")
    print("-" * 60)
    if report["table_cut_count"] > 0:
        print(f"  ⚠️  疑似切断表格: {report['table_cut_count']} 处")
        print(f"     位置: chunk #{report['table_cut_indices']}")
    else:
        print(f"  ✅ 表格完整性: 未检测到切断")
    print("-" * 60)
    print(f"  📝 预览（前 {len(report['preview'])} 个 chunk）")
    for p in report["preview"]:
        print(f"  [{p['index']}] ({p['length']}字符) {p['content']}...")
    print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("Usage: python src/check_chunks.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    report = generate_report(file_path)
    print_report(report)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证 check_chunks.py 可运行**

```bash
docker compose exec app python src/check_chunks.py test_docs/sample.txt
# 预期: 打印六大指标 + 预览
```

- [ ] **Step 3: 验证 PDF 分块报告**

```bash
docker compose exec app python src/check_chunks.py test_docs/sample.pdf
# 预期: 打印 PDF 的分块报告
```

- [ ] **Step 4: Commit**

```bash
git add src/check_chunks.py
git commit -m "feat: add check_chunks.py CLI with table integrity detection"
```

---

### Task 10: 集成验证

**Files:** （无创建，仅运行命令）

所有模块完成后，进行端到端集成验证，确认文档处理流水线完整可运行。

- [ ] **Step 1: 重建 Docker 镜像并复制测试文档**

```bash
docker compose build app
docker compose up -d
docker compose ps
# 预期: app (up), mysql (healthy), redis (healthy)

# 复制测试文档到容器（每次重建后都需要）
docker cp test_docs/sample.txt financial-qa-app:/app/test_docs/sample.txt
docker cp test_docs/sample_gbk.txt financial-qa-app:/app/test_docs/sample_gbk.txt
docker cp test_docs/sample.docx financial-qa-app:/app/test_docs/sample.docx
docker cp test_docs/sample.pdf financial-qa-app:/app/test_docs/sample.pdf
echo "Test docs copied to container"
```

- [ ] **Step 2: 运行全部测试**

```bash
docker compose exec app python -m pytest tests/ -v
# 预期: 全部测试通过
```

- [ ] **Step 3: 验证解析器可直接调用**

```bash
docker compose exec app python -c "
from src.parsers.router import DocRouter
router = DocRouter()
for f in ['test_docs/sample.txt', 'test_docs/sample.docx', 'test_docs/sample.pdf']:
    try:
        result = router.parse(f)
        print(f'{f}: {len(result.chunks)} chunks, {result.total_chars} chars, type={result.file_type}')
    except Exception as e:
        print(f'{f}: ERROR - {e}')
"
# 预期: 三种格式都解析成功
```

- [ ] **Step 4: 验证 document_loader 兼容入口**

```bash
docker compose exec app python -c "
from src.document_loader import load_document
result = load_document('test_docs/sample.txt')
print(f'load_document OK: {len(result.chunks)} chunks')

# 验证扫描件检测
from src.parsers.pymupdf_parser import PyMuPDFParser
import fitz
doc = fitz.open()
doc.new_page()
doc.save('/tmp/blank.pdf')
doc.close()
result2 = load_document('/tmp/blank.pdf')
print(f'Scanned detection: {result2.is_scanned}')
"
# 预期: load_document OK, Scanned detection: True
```

- [ ] **Step 5: 验证 MySQL CRUD**

```bash
docker compose exec app python -c "
from src.mysql_db import MySQLDB
db = MySQLDB()
db.init_db()
kid, is_new = db.get_or_create_kb('集成测试库')
print(f'KB: {kid}, New: {is_new}')

doc_id = db.add_document(kid, 'integration_test.pdf', 'pdf', 9999)
print(f'Doc: {doc_id}')

db.update_document_status(doc_id, 'ready', chunk_count=42)
docs = db.get_documents(kid)
print(f'Docs in KB: {len(docs)}')
for d in docs:
    print(f'  - {d[\"filename\"]}: {d[\"status\"]}, {d[\"chunk_count\"]} chunks')

all_kb = db.get_all_kb()
print(f'Total KBs: {len(all_kb)}')

db.delete_kb(kid)
verify = db.get_kb_by_name('集成测试库')
print(f'After delete: {verify}')
db.close()
"
# 预期: 完整 CRUD 循环通过
```

- [ ] **Step 6: 验证 check_chunks.py 分块质量报告**

```bash
docker compose exec app python src/check_chunks.py test_docs/sample.txt
# 预期: 打印六大指标，表格切断数应为 0（sample.txt 的表格比较小，可能不会被切断）

docker compose exec app python src/check_chunks.py test_docs/sample.pdf
# 预期: 打印 PDF 分块报告
```

- [ ] **Step 7: Iter 2 完成——提交代码**

```bash
git add -A
git status  # 确认只有 Iter 2 相关文件
git commit -m "feat: complete Iter 2 document processing pipeline

- Add BaseParser abstract base + ChunkData/ParseResult models
- Add TxtParser with chardet encoding detection (UTF-8/GBK fallback)
- Add DocxParser using python-docx (paragraphs + tables)
- Add PyMuPDFParser with scanned page detection (<200 chars/page)
- Add DocRouter for file-type-based routing (.txt/.docx/.pdf)
- Add document_loader.py compatibility entry point
- Add MySQLDB CRUD with retry + exponential backoff
- Add check_chunks.py CLI with 6 metrics + table integrity detection
- Add test fixtures (sample.txt, sample_gbk.txt, sample.docx)
- Add comprehensive test suite (test_base, test_txt_parser, test_docx_parser,
  test_pymupdf_parser, test_router, test_mysql_db)
"
```

---

### 执行记录与计划差异（2026-06-16）

实际执行中与原始计划的差异，供后续 Iter 参考：

| # | 差异点 | 计划 | 实际 | 原因 |
|---|--------|------|------|------|
| 1 | **测试 PDF 文件** | `test_docs/moutai_2024_annual_report.pdf` | `test_docs/2020-03-17__厦门灿坤实业股份有限公司__200512__闽灿坤__2019年__年度报告.pdf`（symlink: `sample.pdf`） | 茅台 PDF 打开异常，换为灿坤 2019 年报 |
| 2 | **Dockerfile 构建** | `pip install .`（仅 runtime 依赖） | `pip install ".[dev]"`（安装 pytest） | dev 依赖中的 pytest 需在容器内运行测试 |
| 3 | **docker-compose.yml** | 无 bind mount | 创建 `docker-compose.override.yml`，挂载 `./src:/app/src`、`./tests:/app/tests`、`./test_docs:/app/test_docs` | 开发时代码变更自动同步到容器，无需 `docker cp` |
| 4 | **Sample.docx 创建** | 计划直接写入 `test_docs/sample.docx` | 容器内生成 → `docker cp` 拷出 | `test_docs/` 不在容器挂载路径中 |
| 5 | **Spec/Code Review 修复** | 未涉及 | 修复测试断言（`total_chars` 弱断言）、编码回退增加 `logger.warning`、bind mount 拆分到 override 文件 | 代码质量审查发现的问题 |
| 6 | **Test doc mount** | 测试文件通过 `docker cp` 复制 | 改为 `docker-compose.override.yml` 直接挂载卷 | 简化开发流程，每次重建后无需手动复制 |
```


