"""BM25 检索引擎的单元测试。

测试范围：
  - BM25Index：索引构建、检索与空知识库处理
"""

import tempfile

from src.infra.db.vector_store.types import ChunkResult
from src.infra.search.bm25_index import BM25Index
from src.parsers.base import ChunkData


class TestBM25Index:
    """BM25Index 构建与检索功能的测试。"""

    def test_build_and_search(self):
        """构建索引后应能检索到相关内容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            chunks = [
                ChunkData(content="2024年营业收入3943亿元", metadata={}, chunk_id="1"),
                ChunkData(content="净利润862亿元", metadata={}, chunk_id="2"),
            ]
            index.build_index("test_kb", chunks)
            results = index.search("test_kb", "营业收入", k=2)
            assert len(results) >= 1
            # 结果应包含 lexical_score 字段
            assert results[0].lexical_score is not None

    def test_search_unknown_kb(self):
        """搜索不存在的知识库应返回空列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            assert index.search("nonexistent", "test", k=10) == []

    def test_default_index_dir(self):
        """默认 index_dir 应为 'data/bm25_index'。"""
        index = BM25Index()
        assert str(index.index_dir) == "data/bm25_index"

    def test_rebuild_from_results(self):
        """从 Chroma 全量 ChunkResult 重建后应能检索（chunk_id 复用 Chroma id）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            results = [
                ChunkResult(content="2024年营业收入3943亿元", id="doc1:0"),
                ChunkResult(content="净利润862亿元", id="doc1:1"),
            ]
            index.rebuild_from_results("test_kb", results)
            hits = index.search("test_kb", "营业收入", k=5)
            assert len(hits) >= 1
            assert hits[0].id == "doc1:0"
            assert hits[0].lexical_score is not None

    def test_rebuild_empty_deletes_index(self):
        """空结果重建应删除已有索引（Chroma 空库场景），再检索返回空。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            results = [ChunkResult(content="内容", id="d:0")]
            index.rebuild_from_results("test_kb", results)
            assert index.search("test_kb", "内容", k=5)
            index.rebuild_from_results("test_kb", [])
            assert index.search("test_kb", "内容", k=5) == []

    def test_delete_index(self):
        """删除 KB 索引后检索返回空，且不影响其他 KB。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            index.build_index(
                "kb_a", [ChunkData(content="A内容", metadata={}, chunk_id="a0")]
            )
            index.build_index(
                "kb_b", [ChunkData(content="B内容", metadata={}, chunk_id="b0")]
            )
            index.delete_index("kb_a")
            assert index.search("kb_a", "A内容", k=5) == []
            assert index.search("kb_b", "B内容", k=5)
