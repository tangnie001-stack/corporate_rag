"""文档加载入口模块 — 对外提供统一的文档解析接口。

本模块是 document_loader 的对外门面（Facade），
上层调用者（如 app.py）通过 load_document() 函数解析文档，
内部委托给 DocRouter 根据文件扩展名路由到对应的 Parser。

调用链：
  load_document(file_path)
    → DocRouter.parse(file_path)
      → TxtParser / DocxParser / PyMuPDFParser（按扩展名选择）
        → 返回 ParseResult（chunks + 元数据）
"""

import os
from loguru import logger
from src.parsers.router import DocRouter
from src.parsers.base import ParseResult

# 全局单例路由器实例（内部已注册 TXT/DOCX/PDF 解析器）
_router = DocRouter()


def load_document(file_path: str) -> ParseResult:
    """加载并解析文档文件 — 对外统一入口。

    支持的文件类型：.txt、.docx、.pdf
    自动检测编码（TXT）、提取表格（DOCX/PDF）、检测扫描件（PDF）。

    Args:
        file_path: 文档文件的完整路径

    Returns:
        ParseResult 对象，包含：
        - chunks: 分块数据列表（每个 chunk 含内容和 metadata）
        - total_pages: 总页数（TXT 固定为 1）
        - total_chars: 总字符数
        - is_scanned: 是否为扫描件（仅 PDF）
        - file_type: 文件类型

    Raises:
        FileNotFoundError: 文件不存在时抛出
        ValueError: 文件类型不受支持时抛出（由 DocRouter 内部触发）
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

    # 扫描件检测：如果 PDF 没有可提取的文字，提示用户（MVP 不支持 OCR）
    if result.is_scanned:
        logger.warning(
            "Document '{}' appears to be scanned (no extractable text). "
            "OCR is not supported in MVP.",
            os.path.basename(file_path),
        )

    return result
