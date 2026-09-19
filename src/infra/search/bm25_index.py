"""BM25 词法检索引擎 — 基于 BM25Okapi 的稀疏检索+RRF 融合函数。"""

import pickle
from pathlib import Path

from loguru import logger
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

    def rebuild_from_results(self, kb_id: str, results: list) -> None:
        """从分块存储读回的全部分块重建 KB 的 BM25 索引（全量覆盖写）。

        get_all_chunks 返回的是 ChunkResult（id/content/metadata），
        BM25 持久化需要 ChunkData（content/metadata/chunk_id），此处做适配：
        chunk_id 复用分块 id（格式 {doc_id}:{index}），保证 search 侧
        chunk.chunk_id 能还原。空结果视为删除索引（无语料可建）。

        Args:
            kb_id: 知识库 ID
            results: 全量分块（ChunkResult 列表）
        """
        if not results:
            self.delete_index(kb_id)
            return
        chunk_data = [
            ChunkData(content=r.content, metadata=r.metadata, chunk_id=r.id)
            for r in results
        ]
        self.build_index(kb_id, chunk_data)

    def delete_index(self, kb_id: str) -> None:
        """删除知识库的 BM25 索引（删除 KB 时同步清理，防幽灵检索）。

        Args:
            kb_id: 知识库 ID
        """
        import shutil

        kb_dir = self.index_dir / kb_id
        if kb_dir.exists():
            shutil.rmtree(kb_dir)
            logger.info("BM25 index deleted: kb_id={}", kb_id)

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
        for rank, idx in enumerate(ranked):
            chunk = chunks[idx]
            # 兼容旧格式：chunks 可能是 dict（历史 pickle）或 ChunkData（新格式）
            if isinstance(chunk, dict):
                results.append(
                    ChunkResult(
                        id=chunk.get("id", chunk.get("chunk_id", "")),
                        content=chunk.get("content", ""),
                        metadata=chunk.get("metadata", {}),
                        lexical_score=float(scores[idx]),
                        sparse_rank=rank,
                    )
                )
            else:
                results.append(
                    ChunkResult(
                        id=chunk.chunk_id,
                        content=chunk.content,
                        metadata=chunk.metadata,
                        lexical_score=float(scores[idx]),
                        sparse_rank=rank,
                    )
                )
        return results


def _merge_path_ranks(
    existing: ChunkResult | None,
    incoming: ChunkResult,
    *,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """把一路的名次合并进已有结果。

    位置名次兜底（生产者未填时用融合时的位置），生产者已填的值优先；
    同一 id 出现在另一路时，把那一侧的排名携带过来 —— 融合只按 RRF 重排，
    不得抹掉任一路的排名（来源可辨）。
    """
    if existing is None:
        if dense_rank is not None and incoming.dense_rank is None:
            incoming.dense_rank = dense_rank
        if sparse_rank is not None and incoming.sparse_rank is None:
            incoming.sparse_rank = sparse_rank
        return incoming
    if dense_rank is not None and existing.dense_rank is None:
        existing.dense_rank = dense_rank
    if sparse_rank is not None and existing.sparse_rank is None:
        existing.sparse_rank = sparse_rank
    if existing.dense_rank is None:
        existing.dense_rank = incoming.dense_rank
    if existing.sparse_rank is None:
        existing.sparse_rank = incoming.sparse_rank
    return existing


def rrf_fusion(
    dense: list[ChunkResult],
    bm25_res: list[ChunkResult],
    k: int = 60,
    top_n: int = 50,
) -> list[ChunkResult]:
    """RRF 融合 Dense 语义检索和 BM25 词法检索结果。

    融合只按 RRF 得分重排；每个结果的 dense_rank / sparse_rank 按路保留，
    某条结果未出现在某一路时该路排名为 None。

    Args:
        dense: 向量检索（Dense）结果列表
        bm25_res: BM25 词法检索结果列表
        k: RRF 排序常数，控制排名权重衰减速度，默认 60
        top_n: 融合后保留的 top-N 结果数，默认 50

    Returns:
        融合后的结果列表，按 RRF 得分降序排列，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}

    for rank, doc in enumerate(dense):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, dense_rank=rank)

    for rank, doc in enumerate(bm25_res):
        scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
        merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, sparse_rank=rank)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [merged[doc_id] for doc_id, _ in ranked[:top_n]]


def rrf_fusion_multi(
    results_groups: list[list[ChunkResult]],
    k: int = 60,
    top_n: int = 50,
) -> list[ChunkResult]:
    """任意路 RRF 融合多组检索结果。

    每路按排名贡献 1/(k+rank+1)，跨路累加后按得分降序取 top_n。
    此函数不区分 dense / 词法，排名按融合时的位置兜底写入 dense_rank，
    sparse_rank 保持生产者已填的值（缺省为 None）。

    Args:
        results_groups: 多组检索结果（每组一个查询的 dense 或 bm25 结果）
        k: RRF 排序常数（默认 60）
        top_n: 融合后保留的 top-N 结果数

    Returns:
        融合结果列表，按 RRF 得分降序，长度不超过 top_n
    """
    scores: dict[str, float] = {}
    merged: dict[str, ChunkResult] = {}
    for group in results_groups:
        for rank, doc in enumerate(group):
            scores[doc.id] = scores.get(doc.id, 0) + 1.0 / (k + rank + 1)
            merged[doc.id] = _merge_path_ranks(merged.get(doc.id), doc, dense_rank=rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [merged[doc_id] for doc_id, _ in ranked[:top_n]]
