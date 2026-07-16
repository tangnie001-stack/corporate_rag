"""PDF 文档解析器 — 使用 PyMuPDF（fitz）提取文字和表格，支持扫描件检测。

处理流程：
  1. 用 PyMuPDF 打开 PDF，逐页提取文字
  2. 对每页同时提取表格数据（find_tables）
  3. 检测扫描件（每页可提取文字少于 200 字符视为扫描页）
  4. 按页分块（每页独立分块，保留页码元数据）

扫描件检测逻辑：
  如果所有页面的可提取文字都少于 MIN_TEXT_CHARS（200 字符），
  则标记 is_scanned=True，上层可以提示用户该文档需要 OCR 处理。
  （MVP 阶段不支持 OCR，仅做检测和警告。）

表格提取：
  PyMuPDF 的 find_tables() 能识别 PDF 中的表格结构，
  转为完整的 Markdown 表格（含表头和 |---| 分隔行）追加到页面文字后。
"""

import os
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP, MIN_TEXT_CHARS, HEADER_FOOTER_MARGIN

# 匹配 Markdown 表格（以 | 开头和结尾的行组成的多行表格）
TABLE_PATTERN = re.compile(r"^\|.+\|[\s\S]*?^\|.+\|", re.MULTILINE)


class PyMuPDFParser(BaseParser):
    """PDF 文档解析器 — PyMuPDF 驱动，支持表格提取和扫描件检测。"""

    def parse(self, file_path: str) -> ParseResult:
        """解析 PDF 文件，按页提取文字和表格并分块。

        Args:
            file_path: PDF 文件路径

        Returns:
            ParseResult，file_type="pdf"，total_pages=实际页数

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # 延迟导入 PyMuPDF（避免模块级依赖）
        import fitz

        doc = fitz.open(file_path)
        # 用 try-finally 保证异常时也关闭文件句柄，防止资源泄漏
        try:
            total_pages = len(doc)
            text_by_page = []  # [(页面文字, 页码), ...]
            total_chars = 0
            scanned_pages = 0  # 扫描页计数器

            # ====== 逐页提取文字 + 表格 ======
            for page_num in range(total_pages):
                page = doc[page_num]
                page_height = page.rect.height

                # 先提取表格，按视觉顺序排序，过滤误检（不足 2 行的视为误检）
                tables = sorted(page.find_tables(), key=lambda t: (t.bbox[1], t.bbox[0]))
                tables = [t for t in tables if len(t.extract()) >= 2]
                table_mds = self._extract_tables_from_page(page, tables)
                table_bboxes = [t.bbox for t in tables]

                if table_bboxes:
                    # 有表格时：只提取表格区域外的文字，避免文本层与表格内容重复
                    blocks = page.get_text("blocks")
                    items = []  # [(y_center, content, is_table), ...]
                    # 收集非表格文本块
                    for b in blocks:
                        x0, y0, x1, y1, *_ = b
                        if y1 < HEADER_FOOTER_MARGIN or y0 > page_height - HEADER_FOOTER_MARGIN:
                            continue
                        bbox = fitz.Rect(x0, y0, x1, y1)
                        block_area = (x1 - x0) * (y1 - y0)
                        in_table = False
                        for tb in table_bboxes:
                            tr = fitz.Rect(tb)
                            if bbox.intersects(tr):
                                inter = fitz.Rect(x0, y0, x1, y1).intersect(tr)
                                inter_area = (inter.x1 - inter.x0) * (inter.y1 - inter.y0)
                                if inter_area / block_area > 0.5:
                                    in_table = True
                                    break
                        if not in_table:
                            items.append(((y0 + y1) / 2, b[4] if len(b) > 4 else "", False))
                    # 收集表格 markdown（取表格的 Y 中心位置）
                    for table, md in zip(tables, table_mds):
                        tb = table.bbox
                        items.append(((tb[1] + tb[3]) / 2, md, True))
                    # 按 Y 位置排序后组装文本
                    items.sort(key=lambda x: x[0])
                    text_parts = []
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
                        y0 = b[1]
                        y1 = b[3]
                        if y1 < HEADER_FOOTER_MARGIN or y0 > page_height - HEADER_FOOTER_MARGIN:
                            continue
                        content_blocks.append(b[4] if len(b) > 4 else "")
                    text = "\n".join(content_blocks)

                if table_mds and not table_bboxes:
                    text += "\n\n" + "\n\n".join(table_mds)

                char_count = len(text.strip())
                total_chars += char_count

                # 扫描页检测：文字极少说明该页可能是图片扫描件
                if char_count < MIN_TEXT_CHARS:
                    scanned_pages += 1

                # 记录页码（从 1 开始，方便用户理解）
                text_by_page.append((text, page_num + 1))
        finally:
            doc.close()

        # 所有页都是扫描页 → 标记为扫描件
        is_scanned = scanned_pages == total_pages
        source = os.path.basename(file_path)

        # 分块器（中文友好分隔符）
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )

        # ====== 按页分块（每页独立分块，保留页码）======
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

        return ParseResult(
            chunks=chunks,
            total_pages=total_pages,
            total_chars=total_chars,
            is_scanned=is_scanned,
            file_type="pdf",
        )

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
