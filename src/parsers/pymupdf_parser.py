"""PDF 文档解析器 — 双通道：fitz 全文+表格（主进程）+ pymupdf4llm 标题树（子进程）。

处理流程：
  1. fitz 通道（主进程）：逐页 find_tables() + blocks 文本提取，sanitize_cell 拍平跨行单元格
  2. 检测扫描件（每页可提取文字少于 MIN_TEXT_CHARS 视为扫描页）
  3. pm 通道（子进程）：pdf_heading_extractor 完整复刻 pymupdf4llm 标题树管道，输出清洗后标题树
  4. 按页分块（每页独立分块，保留页码元数据）

为什么 pm 走子进程：
  import pymupdf4llm 会设置全局 mupdf 状态，永久破坏同进程内
  find_tables().extract() 的单元格顺序（63,134,713 → 63 134 713\n, ,）。
  因此主进程不 import pymupdf4llm，标题树提取放子进程隔离全局污染。
"""

import os
import re

import pymupdf
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    FOOTER_MARGIN,
    HEADER_MARGIN,
    MIN_TEXT_CHARS,
)
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.parsers.pdf_heading_extractor import extract_heading_tree

# 匹配 Markdown 表格（以 | 开头和结尾的行组成的多行表格）
TABLE_PATTERN = re.compile(r"^\|.+\|[\s\S]*?^\|.+\|", re.MULTILINE)

# 匹配 pymupdf4llm 强调残留（**_X_** 加粗+斜体、**X** 加粗），保留文本内容。
# 注意：不含裸 _X_ 斜体规则——年报表格里邮箱等含下划线的真实内容会被误伤。
EMPHASIS_PATTERN = re.compile(r"\*{1,2}_([^*_]+)_\*{1,2}|\*\*([^*]+)\*\*")
# 匹配 <br> 换行残留（表格行内的换行会破坏 | 结构，统一替换为空格）
BR_PATTERN = re.compile(r"<br\s*/?>")
# 匹配其余内联 HTML 标签残留（<sup>、<mark>、<u> 等，保留标签内文本）
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
# 匹配页脚页码行（如 "6 / 12"），子进程清洗标题时兜底剔除
FOOTER_PAGE_NUM_PATTERN = re.compile(r"^\d+\s*/\s*\d+\s*$", re.MULTILINE)
# 伪标题过滤：复选框行（√/□ 适用/不适用 等）、编制单位标注行
PSEUDO_HEADING_PATTERN = re.compile(r"[√□]|^编制单位")


class PyMuPDFParser(BaseParser):
    """PDF 文档解析器 — fitz 全文+表格（主进程）+ pymupdf4llm 标题树（子进程）。"""

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
            text_by_page, total_chars, scanned_pages = self._extract_text_by_page_fitz(
                doc
            )
        finally:
            doc.close()

        # 所有页都是扫描页 → 标记为扫描件
        total_pages = len(text_by_page)
        is_scanned = scanned_pages == total_pages
        source = os.path.basename(file_path)

        # pm 标题树（子进程）；扫描件无文字层，跳过子进程
        heading_tree: list[tuple[int, str]] = []
        if not is_scanned:
            heading_tree = extract_heading_tree(file_path)

        chunks = self._split_chunks(text_by_page, source)

        return ParseResult(
            chunks=chunks,
            total_pages=total_pages,
            total_chars=total_chars,
            is_scanned=is_scanned,
            file_type="pdf",
            heading_tree=heading_tree,
        )

    def _extract_text_by_page_fitz(self, doc) -> tuple[list[tuple[str, int]], int, int]:
        """fitz 通道：逐页提取文本块与表格，组装页面文字，统计字符数与扫描页数。

        表格用 find_tables().extract() + sanitize_cell 拍平跨行单元格
        （单元格内 \n → 空格），标签与数值保持同行，避免表格分块丢列。

        Args:
            doc: pymupdf 打开的文档对象

        Returns:
            (text_by_page, total_chars, scanned_pages)：
              - text_by_page: [(页面文字, 页码), ...]，页码从 1 开始
              - total_chars: 全文总字符数
              - scanned_pages: 文字量低于 MIN_TEXT_CHARS 的页数
        """
        total_pages = len(doc)
        text_by_page: list[tuple[str, int]] = []
        total_chars = 0
        scanned_pages = 0

        for page_num in range(total_pages):
            page = doc[page_num]
            page_height = page.rect.height

            # 先提取表格，按视觉顺序排序，过滤误检（不足 2 行的视为误检）
            table_finder = page.find_tables()
            if table_finder is None:
                tables = []
            else:
                tables = sorted(table_finder, key=lambda t: (t.bbox[1], t.bbox[0]))
            tables = [t for t in tables if len(t.extract()) >= 2]
            table_mds = self._extract_tables_from_page(page, tables)
            table_bboxes = [t.bbox for t in tables]

            if table_bboxes:
                # 有表格时：只提取表格区域外的文字，避免文本层与表格内容重复
                blocks = page.get_text("blocks")
                items: list[tuple[float, str, bool]] = []
                for b in blocks:
                    x0, y0, x1, y1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                    if y1 < HEADER_MARGIN or y0 > page_height - FOOTER_MARGIN:
                        continue
                    if not b[4].strip():
                        continue
                    bbox = pymupdf.Rect(x0, y0, x1, y1)
                    block_area = (x1 - x0) * (y1 - y0)
                    in_table = False
                    for tb in table_bboxes:
                        tr = pymupdf.Rect(tb)
                        if bbox.intersects(tr):
                            inter = bbox.intersect(tr)
                            inter_area = (inter.x1 - inter.x0) * (inter.y1 - inter.y0)
                            if inter_area / block_area > 0.5:
                                in_table = True
                                break
                    if not in_table:
                        items.append(((y0 + y1) / 2, b[4], False))
                # 收集表格 markdown（取表格的 Y 中心位置）
                for table, md in zip(tables, table_mds):
                    tb = table.bbox
                    items.append(((tb[1] + tb[3]) / 2, md, True))
                # 按 Y 位置排序后组装文本
                items.sort(key=lambda x: x[0])
                text_parts: list[str] = []
                for _, content, is_table in items:
                    if text_parts and is_table:
                        text_parts.append("\n\n" + content)
                    elif text_parts:
                        text_parts.append("\n" + content)
                    else:
                        text_parts.append(content)
                text = "".join(text_parts)
            else:
                # 无表格时：按块提取并排除页眉页脚
                blocks = page.get_text("blocks")
                content_blocks = []
                for b in blocks:
                    y0 = float(b[1])
                    y1 = float(b[3])
                    if y1 < HEADER_MARGIN or y0 > page_height - FOOTER_MARGIN:
                        continue
                    if not b[4].strip():
                        continue
                    content_blocks.append(b[4])
                text = "\n".join(content_blocks)

            char_count = len(text.strip())
            total_chars += char_count
            # 扫描页检测：文字极少说明该页可能是图片扫描件
            if char_count < MIN_TEXT_CHARS:
                scanned_pages += 1
            text_by_page.append((text, page_num + 1))
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

    def _extract_tables_from_page(self, page, tables=None) -> list[str]:
        """从单页 PDF 提取所有表格，返回 Markdown 格式的表格字符串列表。

        Args:
            page: PyMuPDF 页面对象
            tables: 预排序的表格列表（为 None 时自动获取并按视觉顺序排序）

        Returns:
            Markdown 表格字符串列表（每个元素是一个完整的 Markdown 表格）
        """
        if tables is None:
            tables = list(page.find_tables())
            # 按视觉顺序（Y 从上到下，X 从左到右）排序
            tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
        result = []
        for table in tables:
            md = self._table_to_markdown(table)
            if md:
                result.append(md)
        return result

    def _table_to_markdown(self, table) -> str:
        """将 PyMuPDF 表格对象转换为 Markdown 格式字符串。

        Args:
            table: PyMuPDF 表格对象（find_tables() 返回的条目）

        Returns:
            Markdown 格式的表格字符串，空表格返回空字符串

        Note:
            extract() 在畸形表格结构下可能返回 None，
            空单元格为 None，用 sanitize_cell 保证输出空字符串。
        """
        rows = table.extract()
        if not rows or len(rows) < 1:
            return ""
        lines = ["| " + " | ".join(self.sanitize_cell(c) for c in rows[0]) + " |"]
        lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(self.sanitize_cell(c) for c in row) + " |")
        return "\n".join(lines)

    def _extract_heading_tree(self, full_text: str) -> list[tuple[int, str]]:
        """从拼接后的 pm Markdown 全文提取标题树。

        供子进程 pdf_heading_extractor 使用（主进程 parse 不再直接调用）。
        逐行扫描 # 前缀，清洗标记残留、过滤伪标题。

        Args:
            full_text: 拼接后的 pm Markdown 文本

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
        """清洗标题文本并过滤伪标题。

        在 _clean_markdown_noise 基础上，过滤复选框行（√/□ 适用/不适用）、
        编制单位标注行等非真实标题，返回空字符串表示应丢弃。

        Args:
            text: 含 Markdown 标记的原始标题文本

        Returns:
            清洗后的标题文本；伪标题返回空字符串
        """
        cleaned = PyMuPDFParser._clean_markdown_noise(text)
        if PSEUDO_HEADING_PATTERN.search(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _clean_markdown_noise(text: str) -> str:
        """清洗 pymupdf4llm 输出的 Markdown/HTML 标记残留（标题与正文通用）。

        只做轻量清洗，保留文本内容与表格结构：
          - 去掉强调标记 **_X_** / **X**，保留文本内容
          - <br> 替换为空格（表格行内的换行会破坏 | 结构）
          - 去掉其余内联 HTML 标签（<sup>、<mark>、<u> 等），保留标签内文本
          - 去掉页脚页码行（如 "6 / 12"，footer=False 偶有漏网的兜底）

        Args:
            text: 含 Markdown/HTML 标记的原始文本

        Returns:
            清洗后的文本
        """
        # 先替换 <br>（避免表格行内换行破坏 | 结构）
        cleaned = BR_PATTERN.sub(" ", text)
        # 去强调标记
        cleaned = EMPHASIS_PATTERN.sub(r"\1\2", cleaned)
        # 去其余内联 HTML 标签，保留标签内文本
        cleaned = HTML_TAG_PATTERN.sub("", cleaned)
        # 去页脚页码行
        cleaned = FOOTER_PAGE_NUM_PATTERN.sub("", cleaned)
        return cleaned.strip()
