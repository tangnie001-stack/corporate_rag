"""解析器基础模块 — 定义数据模型和抽象基类。

本模块定义了文档解析层的三个核心数据结构：
  - ChunkData: 单个分块的内容和元数据
  - ParseResult: 文档解析的完整结果（分块列表 + 统计信息）
  - BaseParser: 所有具体解析器（TXT/DOCX/PDF）必须继承的抽象基类

在 RAG 流水线中的位置：
  用户上传文件 → DocRouter 路由 → 具体 Parser.parse() → ParseResult
  ParseResult.chunks → VectorStore.add_chunks() → 入库
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChunkData:
    """单个文档分块 — 解析器输出的最小语义单元。

    Attributes:
        content: 分块的文本内容
        metadata: 元数据字典，至少包含：
            - source: 原始文件名
            - page: 页码（TXT 为 1，PDF 为实际页码）
        chunk_id: 分块的唯一标识（格式："{source}:{index}" 或 "{source}:p{page}:{index}"）
    """

    content: str
    metadata: dict
    chunk_id: str


@dataclass
class ParseResult:
    """文档解析结果 — 包含分块列表和文档级统计信息。

    Attributes:
        chunks: 分块数据列表
        total_pages: 文档总页数（TXT/DOCX 固定为 1，PDF 为实际页数）
        total_chars: 文档总字符数（如为 0 则自动从 chunks 计算）
        is_scanned: 是否为扫描件（仅 PDF 有意义，扫描件无文字层）
        encoding: 文件编码（仅 TXT 有意义，如 utf-8 / gbk）
        file_type: 文件类型（txt / docx / pdf）
    """

    chunks: list[ChunkData] = field(default_factory=list)
    total_pages: int = 0
    total_chars: int = 0
    is_scanned: bool = False
    encoding: str = "utf-8"
    file_type: str = ""

    def __post_init__(self):
        """自动计算 total_chars（如果构造时未传入）。"""
        if self.total_chars == 0 and self.chunks:
            self.total_chars = sum(len(c.content) for c in self.chunks)


class BaseParser(ABC):
    """文档解析器抽象基类 — 所有具体解析器必须继承此类。

    子类需实现 parse() 方法，将文件解析为 ParseResult。
    目前有三个子类：TxtParser、DocxParser、PyMuPDFParser。
    """

    @abstractmethod
    def parse(self, file_path: str) -> ParseResult:
        """解析文档文件，返回结构化的 ParseResult。

        Args:
            file_path: 文档文件的完整路径

        Returns:
            ParseResult 对象

        Raises:
            FileNotFoundError: 文件不存在
        """
        ...
