"""TXT 文本解析器 — 支持自动编码检测（UTF-8 / GBK / GB2312）。

处理流程：
  1. 读取二进制文件内容
  2. 使用 chardet 检测编码（特别处理中文 GBK/GB2312/GB18030）
  3. 解码为文本（失败时降级为 UTF-8 + 替换字符）
  4. 使用 LangChain 的 RecursiveCharacterTextSplitter 分块
  5. 包装为 ChunkData 列表并返回 ParseResult

分块策略：
  - 分隔符优先级：双换行 > 单换行 > 句号 > 分号 > 空格 > 空字符串
  - 中文金融文档中句号"。"是重要分隔点，保证句子不被从中间切断
"""

import os
import chardet
from loguru import logger
from langchain_text_splitters import RecursiveCharacterTextSplitter
from src.parsers.base import BaseParser, ChunkData, ParseResult
from src.config import CHUNK_SIZE, CHUNK_OVERLAP


class TxtParser(BaseParser):
    """纯文本文件解析器 — 自动编码检测 + 中文友好分块。"""

    def parse(self, file_path: str) -> ParseResult:
        """解析 TXT 文件并分块。

        Args:
            file_path: TXT 文件路径

        Returns:
            ParseResult，file_type="txt"，total_pages=1

        Raises:
            FileNotFoundError: 文件不存在
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # 读取文件内容并检测编码
        raw, encoding = self._read_file(file_path)
        # 空文件直接返回空结果
        if not raw.strip():
            return ParseResult(
                chunks=[],
                total_pages=1,
                total_chars=0,
                file_type="txt",
                encoding=encoding,
            )

        source = os.path.basename(file_path)
        # 分块策略：优先按段落/句子切分，保证中文句子完整性
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", " ", ""],
        )
        texts = splitter.split_text(raw)
        chunks = [
            ChunkData(
                content=t,
                metadata={"source": source, "page": 1},  # TXT 无页码概念，固定为 1
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
        """读取文件并检测编码，GBK 系列统一归为 gbk。

        编码检测策略：
          1. chardet 检测最可能的编码
          2. GB2312/GBK/GB18030/HZ 统一映射为 gbk（GBK 是 GB2312 的超集）
          3. 解码失败时降级为 UTF-8 + errors="replace"（不中断流程）

        Args:
            file_path: 文件路径

        Returns:
            (文本内容, 检测到的编码) 元组
        """
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        # chardet 编码检测
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        # 中文编码统一映射为 gbk（覆盖 GB2312/GB18030 等变体）
        if encoding.lower() in ("gb2312", "gbk", "gb18030", "hz"):
            encoding = "gbk"

        try:
            text = raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            # 编码检测不准时降级：用 UTF-8 解码，无法识别的字符替换为
            logger.warning(
                "Encoding {} failed for {}, falling back to UTF-8 with replacement",
                encoding,
                file_path,
            )
            encoding = "utf-8"
            text = raw_bytes.decode("utf-8", errors="replace")

        return text, encoding
