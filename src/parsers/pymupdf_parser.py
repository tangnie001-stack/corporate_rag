"""PDF 文档解析器 — 使用 pymupdf4llm 提取文字和表格，支持扫描件检测。

处理流程：
  1. 用 pymupdf 打开 PDF
  2. 用 pymupdf4llm.to_markdown(page_chunks=True) 逐页转 Markdown
     （内部自动识别标题层级 # 前缀、表格转 Markdown）
  3. 检测扫描件（每页可提取文字少于 MIN_TEXT_CHARS 视为扫描页）
  4. 从拼接全文提取标题树 (level, heading)
  5. 按页分块（每页独立分块，保留页码元数据）

扫描件检测逻辑：
  如果所有页面的可提取文字都少于 MIN_TEXT_CHARS（200 字符），
  则标记 is_scanned=True，上层可以提示用户该文档需要 OCR 处理。
  （MVP 阶段不支持 OCR，仅做检测和警告。）

表格提取：
  pymupdf4llm 内置表格识别，自动转为 Markdown 表格，
  分块时通过 TABLE_PATTERN 标记 block_type="table"。
"""

import os
import re
from typing import cast

import pymupdf
import pymupdf4llm
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import CHUNK_OVERLAP, CHUNK_SIZE, MIN_TEXT_CHARS
from src.parsers.base import BaseParser, ChunkData, ParseResult

# 匹配 Markdown 表格（以 | 开头和结尾的行组成的多行表格）
TABLE_PATTERN = re.compile(r"^\|.+\|[\s\S]*?^\|.+\|", re.MULTILINE)

# 匹配 pymupdf4llm 加粗/斜体残留（**_X_**、**X**、_X_）
EMPHASIS_PATTERN = re.compile(r"\*{1,2}_([^*_]+)_\*{1,2}|\*\*([^*]+)\*\*|_([^_]+)_")
# 匹配 HTML 上标标签残留（<sup>X</sup>）
SUP_PATTERN = re.compile(r"<sup>([^<]*)</sup>")


class PyMuPDFParser(BaseParser):
    """PDF 文档解析器 — pymupdf4llm 驱动，支持表格转 Markdown 和扫描件检测。"""

    def parse(self, file_path: str) -> ParseResult:
        """解析 PDF 文件，按页提取文字并分块，同时提取标题树。

        Args:
            file_path: PDF 文件路径

        Returns:
            ParseResult，file_type="pdf"，total_pages=实际页数，
            heading_tree=标题层级列表

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        doc = pymupdf.open(file_path)
        # 用 try-finally 保证异常时也关闭文件句柄，防止资源泄漏
        try:
            text_by_page, total_chars, scanned_pages = self._extract_text_by_page(doc)
        finally:
            doc.close()

        # 所有页都是扫描页 → 标记为扫描件
        total_pages = len(text_by_page)
        is_scanned = scanned_pages == total_pages
        source = os.path.basename(file_path)

        # 标题树提取（从拼接全文，按 # 前缀数判断层级）
        full_text = "\n".join(t for t, _ in text_by_page)
        heading_tree = self._extract_heading_tree(full_text)

        chunks = self._split_chunks(text_by_page, source)

        return ParseResult(
            chunks=chunks,
            total_pages=total_pages,
            total_chars=total_chars,
            is_scanned=is_scanned,
            file_type="pdf",
            heading_tree=heading_tree,
        )

    def _extract_text_by_page(self, doc) -> tuple[list[tuple[str, int]], int, int]:
        """用 pymupdf4llm 将 PDF 逐页转 Markdown，统计字符数与扫描页数。

        Args:
            doc: pymupdf 打开的文档对象

        Returns:
            (text_by_page, total_chars, scanned_pages)：
              - text_by_page: [(页面文字, 页码), ...]，页码从 1 开始
              - total_chars: 全文总字符数
              - scanned_pages: 文字量低于 MIN_TEXT_CHARS 的页数
        """
        md_pages = pymupdf4llm.to_markdown(
            doc, page_chunks=True, show_progress=False, write_images=False
        )
        # page_chunks=True 时始终返回 list[dict]，显式声明类型便于静态检查
        pages = cast(list[dict], md_pages)
        text_by_page = []  # [(页面文字, 页码), ...]
        total_chars = 0
        scanned_pages = 0  # 扫描页计数器

        # ====== 逐页提取 Markdown 文字 ======
        for p in pages:
            text = p["text"]
            page_num = p["metadata"]["page_number"]  # 1-based
            char_count = len(text.strip())
            total_chars += char_count
            # 扫描页检测：文字极少说明该页可能是图片扫描件
            if char_count < MIN_TEXT_CHARS:
                scanned_pages += 1
            text_by_page.append((text, page_num))
        return text_by_page, total_chars, scanned_pages

    def _split_chunks(
        self, text_by_page: list[tuple[str, int]], source: str
    ) -> list[ChunkData]:
        """按页分块，每页独立分块并保留页码元数据。

        Args:
            text_by_page: [(页面文字, 页码), ...]
            source: 原始文件名

        Returns:
            分块列表，block_type 按 TABLE_PATTERN 标记为 table/text
        """
        # 分块器（中文友好分隔符）
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )

        chunks = []
        for page_text, page_num in text_by_page:
            if not page_text.strip():
                continue  # 跳过空白页
            texts = splitter.split_text(page_text)
            for i, t in enumerate(texts):
                block_type = "table" if TABLE_PATTERN.search(t) else "text"
                chunks.append(
                    ChunkData(
                        content=t,
                        metadata={
                            "source": source,
                            "page": page_num,
                            "block_type": block_type,
                        },
                        # chunk_id 包含页码，便于定位和去重
                        chunk_id=f"{source}:p{page_num}:{i}",
                    )
                )
        return chunks

    def _extract_heading_tree(self, full_text: str) -> list[tuple[int, str]]:
        """从拼接后的全文提取标题树。

        pymupdf4llm 输出的 Markdown 标题以 # 前缀标记（# 数量表示层级），
        逐行扫描提取 (level, heading) 元组，并清洗 Markdown 强调残留。

        Args:
            full_text: 拼接后的全文 Markdown 文本

        Returns:
            标题层级列表 [(level, heading), ...]
        """
        heading_tree: list[tuple[int, str]] = []
        for line in full_text.split("\n"):
            stripped = line.rstrip()
            if not stripped.startswith("#"):
                continue
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = self._clean_heading(stripped.lstrip("#").strip())
            if heading:
                heading_tree.append((level, heading))
        return heading_tree

    @staticmethod
    def _clean_heading(text: str) -> str:
        """清洗标题中的 pymupdf4llm Markdown 标记残留。

        pymupdf4llm 会把强调文本渲染为 **_X_** / **X** / _X_，
        上标渲染为 <sup>X</sup>，这些标记残留在标题树中，统一去除。

        Args:
            text: 含 Markdown 标记的原始标题文本

        Returns:
            去除强调/上标标记后的标题文本
        """
        # 依次去掉 **_X_**（加粗+斜体混合）、**X**（加粗）、_X_（斜体）
        cleaned = EMPHASIS_PATTERN.sub(r"\1\2\3", text)
        # 去掉 <sup>X</sup> 上标标签
        cleaned = SUP_PATTERN.sub(r"\1", cleaned)
        return cleaned.strip()

    def _table_to_markdown(self, table) -> str:
        """将 PyMuPDF 表格对象转换为 Markdown 格式字符串。

        Args:
            table: PyMuPDF 表格对象（find_tables() 返回的条目）

        Returns:
            Markdown 格式的表格字符串，空表格返回空字符串

        Note:
            extract() 在畸形表格结构下可能返回 None，
            空单元格为 None，用 str(c or "") 保证输出空字符串。
            单元格内换行符替换为空格，避免破坏 Markdown 表格行结构。
        """
        rows = table.extract()
        if not rows or len(rows) < 1:
            return ""
        lines = []
        header = "| " + " | ".join(self.sanitize_cell(c) for c in rows[0]) + " |"
        lines.append(header)
        lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(self.sanitize_cell(c) for c in row) + " |")
        return "\n".join(lines)
