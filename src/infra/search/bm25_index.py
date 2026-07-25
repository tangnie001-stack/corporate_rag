"""BM25 词法检索引擎 — 基于 BM25Okapi 的稀疏检索+RRF 融合函数。"""

import pickle
from pathlib import Path
from rank_bm25 import BM25Okapi
from src.infra.db.entities import ChunkResult
from src.parsers.base import ChunkData


class BM25Index:
    """基于 BM25Okapi 的词法检索引擎。"""

    def __init__(self, index_dir: str = "data/bm25_index"):
        self.index_dir = Path(index_dir)

    def build_index(self, kb_id: str, chunks: list[ChunkData]) -> None:
        kb_dir = self.index_dir / kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        corpus = [list(chunk.content) for chunk in chunks]
        bm25 = BM25Okapi(corpus)
        with open(kb_dir / "bm25.pkl", "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)

    def search(self, kb_id: str, query: str, k: int = 150) -> list[ChunkResult]:
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
                results.append(ChunkResult(
                    id=chunk.get("id", chunk.get("chunk_id", "")),
                    content=chunk.get("content", ""),
                    metadata=chunk.get("metadata", {}),
                    bm25_score=float(scores[idx]),
                ))
            else:
                results.append(ChunkResult(
                    id=chunk.chunk_id,
                    content=chunk.content,
                    metadata=chunk.metadata,
                    bm25_score=float(scores[idx]),
                ))
        return results


def rrf_fusion(
    dense: list[ChunkResult], bm25_res: list[ChunkResult],
    k: int = 60, top_n: int = 50,
) -> list[ChunkResult]:
    """RRF 融合 Dense 语义检索和 BM25 词法检索结果。"""
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
