"""DOCX 文档解析器 — 使用 python-docx 提取正文和表格内容。

处理流程：
  1. 用 python-docx 打开 .docx 文件
  2. 提取所有非空段落文本
  3. 遍历所有表格，将每行转为管道符分隔的文本（保留表格结构）
  4. 将段落文本和表格文本合并
  5. 使用 RecursiveCharacterTextSplitter 分块

表格提取策略：
  金融文档（年报、审计报告）中大量数据以表格形式存在，
  本解析器将表格转为完整的 Markdown 表格（含表头和 |---| 分隔行），
  保留表格的结构化信息，便于 LLM 理解数字数据。

延迟导入说明：
  python-docx 在 parse() 内部才 import，避免模块顶层依赖，
  这样在未安装 python-docx 时其他解析器仍可正常使用。
"""

import os
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP

# 匹配 Markdown 表格（以 | 开头和结尾的行组成的多行表格）
TABLE_PATTERN = re.compile(r"^\|.+\|[\s\S]*?^\|.+\|", re.MULTILINE)


class DocxParser(BaseParser):
    """DOCX 文档解析器 — 提取段落文本 + 表格数据。"""

    def parse(self, file_path: str) -> ParseResult:
        """解析 DOCX 文件并分块。

        Args:
            file_path: DOCX 文件路径

        Returns:
            ParseResult，file_type="docx"，total_pages=1

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # 延迟导入 python-docx（避免模块级依赖）
        from docx import Document

        doc = Document(file_path)

        # ====== 提取段落文本 ======
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n".join(paragraphs)

        # ====== 提取表格数据（Markdown 格式）======
        table_texts = self._extract_tables(doc)

        # 将表格文本追加到正文之后（用双换行分隔）
        if table_texts:
            text += "\n\n" + "\n\n".join(table_texts)

        source = os.path.basename(file_path)
        # 分块：与 TXT 使用相同的中文友好分隔符
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )
        texts = splitter.split_text(text)
        chunks = []
        for i, t in enumerate(texts):
            block_type = "table" if TABLE_PATTERN.search(t) else "text"
            chunks.append(
                ChunkData(
                    content=t,
                    metadata={"source": source, "page": 1, "block_type": block_type},
                    chunk_id=f"{source}:{i}",
                )
            )
        return ParseResult(
            chunks=chunks,
            total_pages=1,
            total_chars=len(text),
            file_type="docx",
        )

    def _extract_tables(self, doc) -> list[str]:
        """提取 DOCX 文档中的所有表格，返回 Markdown 格式字符串列表。

        Args:
            doc: python-docx Document 对象

        Returns:
            Markdown 表格字符串列表（每个元素是一个完整的 Markdown 表格）
        """

        result = []
        for table in doc.tables:
            md = self._docx_table_to_markdown(table)
            if md:
                result.append(md)
        return result

    def _docx_table_to_markdown(self, table) -> str:
        """将 python-docx Table 对象转换为 Markdown 格式字符串。

        Args:
            table: python-docx Table 对象

        Returns:
            Markdown 格式的表格字符串，空表格返回空字符串
        """
        rows = []
        for row in table.rows:
            cells = [self.sanitize_cell(cell.text) for cell in row.cells]
            rows.append(cells)
        if not rows:
            return ""
        lines = []
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)
