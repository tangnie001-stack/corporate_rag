"""检索结果类型 — ChromaDB 语义检索和 BM25 词法检索的统一输出类型。"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class ChunkResult:
    """检索结果统一类型。

    替代 similarity_search / BM25 search / RRF fusion / rerank 之间的 list[dict]。
    统一 ChromaDB 语义检索和 BM25 词法检索的输出格式。
    """

    id: str
    """分块 ID，格式为 {doc_id}:{chunk_index}（ChromaDB）或解析器生成（BM25）。"""
    content: str
    """分块的文本内容，由文档解析器生成，可能包含 Markdown 格式。"""
    metadata: dict = field(default_factory=dict)
    """元数据字典，包含 source（文件名）、page（页码）、doc_id（文档ID）等。"""
    distance: Optional[float] = None
    """余弦距离，仅语义检索时有值（越小越相似），BM25 检索和分页查询时为 None。"""
    bm25_score: Optional[float] = None
    """BM25 词法检索分数，仅 BM25 检索时有值，语义检索和分页查询时为 None。"""


@dataclass(slots=True)
class ChunkQueryResult:
    """分块分页查询结果（get_chunks_paginated 的返回类型）。"""

    items: list[ChunkResult]
    """当前页的分块列表。"""
    total: int
    """该文档的总分块数量。"""
    page: int
    """当前页码，从 1 开始。"""
    page_size: int
    """每页条数。"""
