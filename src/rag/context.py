"""RAG 上下文数据类 — 单个检索结果块的封装。"""

from dataclasses import dataclass


@dataclass
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。"""

    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0

    def to_citation(self) -> str:
        """格式化为 Markdown 引用块。"""
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"
