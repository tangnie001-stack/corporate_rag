"""BM25 词法检索引擎 — 基于 BM25Okapi 的稀疏检索+RRF 融合函数。"""

import pickle
from pathlib import Path
from rank_bm25 import BM25Okapi
from src.infra.db.vector_store.types import ChunkResult
from src.parsers.base import ChunkData


class BM25Index:
    """基于 BM25Okapi 的词法检索引擎。"""

    def __init__(self, index_dir: str = "data/bm25_index"):
        """初始化 BM25 索引管理器。

        Args:
            index_dir: 索引文件存储目录，默认为 "data/bm25_index"
        """
        self.index_dir = Path(index_dir)

    def build_index(self, kb_id: str, chunks: list[ChunkData]) -> None:
        """构建并持久化知识库的 BM25 索引。

        Args:
            kb_id: 知识库 ID
            chunks: 文档分块列表

        Raises:
            pickle.PickleError: 索引序列化失败时抛出
        """
        kb_dir = self.index_dir / kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        corpus = [list(chunk.content) for chunk in chunks]
        bm25 = BM25Okapi(corpus)
        with open(kb_dir / "bm25.pkl", "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)

    def search(self, kb_id: str, query: str, k: int = 150) -> list[ChunkResult]:
        """执行 BM25 词法检索。

        Args:
            kb_id: 知识库 ID
            query: 用户查询（按字符级分词）
            k: 返回 top-K 结果数，默认为 150

        Returns:
            BM25 检索结果列表，按相关性降序排列；索引不存在时返回空列表
        """
        kb_dir = self.index_dir / kb_id
        if not (kb_dir / "bm25.pkl").exists():
            return []
        with open(kb_dir / "bm25.pkl", "rb") as f:
            data = pickle.load(f)
        bm25, chunks = data["bm25"], data["chunks"]
        tokenized = list(query)
        scores = bm25.get_scores(tokenized)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        results = []
        for idx in ranked:
            chunk = chunks[idx]
            # chunks 可能是 dict（旧格式）或 ChunkData（新格式）
            if isinstance(chunk, dict):
                results.append(
                    ChunkResult(
                        id=chunk.get("id", chunk.get("chunk_id", "")),
                        content=chunk.get("content", ""),
                        metadata=chunk.get("metadata", {}),
                        bm25_score=float(scores[idx]),
                    )
                )
            else:
                results.append(
                    ChunkResult(
                        id=chunk.chunk_id,
                        content=chunk.content,
                        metadata=chunk.metadata,
                        bm25_score=float(scores[idx]),
                    )
                )
        return results


def rrf_fusion(
    dense: list[ChunkResult],
    bm25_res: list[ChunkResult],
    k: int = 60,
    top_n: int = 50,
) -> list[ChunkResult]:
    """RRF 融合 Dense 语义检索和 BM25 词法检索结果。

    Args:
        dense: 向量检索（Dense）结果列表
        bm25_res: BM25 词法检索结果列表
        k: RRF 排序常数，控制排名权重衰减速度，默认 60
        top_n: 融合后保留的 top-N 结果数，默认 50

    Returns:
        融合后的结果列表，按 RRF 得分降序排列，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    data: dict[str, ChunkResult] = {}

    for rank, doc in enumerate(dense):
        doc_id = doc.id
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        data[doc_id] = doc

    for rank, doc in enumerate(bm25_res):
        doc_id = doc.id
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        if doc_id not in data:
            data[doc_id] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [data[doc_id] for doc_id, _ in ranked[:top_n]]
