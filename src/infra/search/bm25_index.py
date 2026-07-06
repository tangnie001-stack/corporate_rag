"""BM25 词法检索引擎 — 基于 BM25Okapi 的稀疏检索+RRF 融合函数。

本模块提供两大核心功能：
  1. BM25Index：对中文分块文本构建 BM25 索引（按知识库隔离），支持持久化
  2. rrf_fusion：将 Dense 语义检索与 BM25 词法检索结果通过 RRF 算法融合

在 RAG 流水线中的位置（Dense + BM25 并行 → RRF 融合 → Reranker）：
  用户提问 → asyncio.gather(Dense 检索, BM25 检索) → rrf_fusion → Reranker → LLM
"""

import pickle
from pathlib import Path

from rank_bm25 import BM25Okapi


class BM25Index:
    """基于 BM25Okapi 的词法检索引擎。

    每个知识库（kb_id）在 index_dir 下拥有独立的子目录，
    通过 pickle 序列化/反序列化持久化 BM25 模型和分块元数据。

    使用方式：
        index = BM25Index()
        index.build_index("kb_uuid", chunks)
        results = index.search("kb_uuid", "营业收入", k=150)
    """

    def __init__(self, index_dir: str = "data/bm25_index"):
        """初始化 BM25Index。

        Args:
            index_dir: BM25 索引的持久化根目录（每个 kb 独立子目录）
        """
        self.index_dir = Path(index_dir)

    def build_index(self, kb_id: str, chunks: list[dict]) -> None:
        """为指定知识库构建 BM25 索引并持久化。

        Args:
            kb_id: 知识库 UUID
            chunks: 分块列表，每个元素必须包含 "id" 和 "content" 字段
        """
        kb_dir = self.index_dir / kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        # 中文按字符切分作为 token
        corpus = [list(chunk["content"]) for chunk in chunks]
        bm25 = BM25Okapi(corpus)
        with open(kb_dir / "bm25.pkl", "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)

    def search(self, kb_id: str, query: str, k: int = 150) -> list[dict]:
        """在指定知识库中进行 BM25 词法检索。

        Args:
            kb_id: 知识库 UUID
            query: 用户查询文本
            k: 返回结果数量上限

        Returns:
            检索结果列表，每个元素包含原始 chunk 字段外加 bm25_score。
            如果知识库索引不存在返回空列表。
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
        return [{**chunks[idx], "bm25_score": float(scores[idx])} for idx in ranked]


def rrf_fusion(
    dense: list[dict], bm25_res: list[dict], k: int = 60, top_n: int = 50
) -> list[dict]:
    """将 Dense 语义检索和 BM25 词法检索的结果通过 RRF 算法融合。

    Reciprocal Rank Fusion (RRF) 将两个排序列表的排名倒数相加，
    k 参数控制排名衰减速度（k 越大，排名靠后的文档也能获得一定分数）。

    Args:
        dense: Dense 语义检索结果列表（需包含 "id" 字段）
        bm25_res: BM25 词法检索结果列表（需包含 "id" 字段）
        k: RRF 排名常数（默认 60）
        top_n: 融合后返回的结果数量上限

    Returns:
        RRF 融合后的结果列表（按融合分数降序排列）
    """
    scores: dict[str, float] = {}
    data: dict[str, dict] = {}

    for rank, doc in enumerate(dense):
        doc_id = doc.get("id", "")
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        data[doc_id] = doc

    for rank, doc in enumerate(bm25_res):
        doc_id = doc.get("id", "")
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        if doc_id not in data:
            data[doc_id] = doc

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [data[doc_id] for doc_id, _ in ranked[:top_n]]
