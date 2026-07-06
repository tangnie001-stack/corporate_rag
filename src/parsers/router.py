"""文档路由器 — 根据文件扩展名将文件分发到对应的解析器。

设计模式：策略模式（Strategy Pattern）
  DocRouter 维护一个 {扩展名: Parser实例} 的映射表，
  调用 parse() 时根据文件后缀自动选择正确的解析器。

为什么用策略模式：
  金融文档类型多样（PDF年报、Word合同、TXT公告等），每种格式的解析逻辑差异很大，
  用策略模式可以将各解析器解耦——新增格式只需写一个新 Parser 类并注册，
  不需要修改路由器和已有解析器的代码，符合开闭原则。

支持的文件类型：
  - .txt  → TxtParser（自动编码检测 + 中文分块）
  - .docx → DocxParser（python-docx + 表格提取）
  - .pdf  → PyMuPDFParser（PyMuPDF + 扫描件检测 + 表格提取）

扩展新类型：
  只需创建新的 Parser 类继承 BaseParser，然后在 self.parsers 中注册即可。
"""

from pathlib import Path

# 各格式解析器导入 —— 按需引入，如果后续某格式依赖较重可改为延迟加载
from src.parsers.base import ParseResult
from src.parsers.txt_parser import TxtParser
from src.parsers.docx_parser import DocxParser
from src.parsers.pymupdf_parser import PyMuPDFParser


class DocRouter:
    """文档路由器 — 将文件按扩展名分发到对应的解析器。"""

    def __init__(self):
        """初始化解析器注册表（扩展名 → 解析器实例）。

        在构造时一次性实例化所有解析器，而非每次 parse() 时临时创建，
        避免重复初始化的开销（如 PyMuPDF 解析器内部可能有较重的资源准备）。
        """
        self.parsers = {
            ".txt": TxtParser(),
            ".docx": DocxParser(),
            ".pdf": PyMuPDFParser(),
        }

    def parse(self, file_path: str) -> ParseResult:
        """解析文档 — 自动路由到正确的解析器。

        Args:
            file_path: 文档文件路径

        Returns:
            ParseResult 对象（包含分块和统计信息）

        Raises:
            ValueError: 文件扩展名不在注册表中
        """
        # 统一转小写，避免 .PDF / .Pdf 等大小写差异导致路由失败
        ext = Path(file_path).suffix.lower()
        parser = self.parsers.get(ext)
        if parser is None:
            # 报错时列出所有已注册的扩展名，方便调用方排查
            raise ValueError(
                f"Unsupported file type: '{ext}'. Supported: {list(self.parsers.keys())}"
            )
        return parser.parse(file_path)
