"""向量存储的公开入口。

后端是 PostgreSQL + pgvector（单表 chunks + kb_id 列，无 collection 概念）。
Chroma 的依赖与数据目录均已随 P4 退役，回滚到 Chroma 不再可能。
"""

from src.infra.db.vector_store.pg_store import PgVectorStore as VectorStore
from src.infra.db.vector_store.types import ChunkQueryResult, ChunkResult

__all__ = ["ChunkQueryResult", "ChunkResult", "VectorStore"]
