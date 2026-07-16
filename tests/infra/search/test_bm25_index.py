"""BM25 检索引擎的单元测试。

测试范围：
  - BM25Index：索引构建、检索与空知识库处理
  - rrf_fusion：RRF 融合算法的正确性
"""

import tempfile


from src.infra.search.bm25_index import BM25Index, rrf_fusion


class TestBM25Index:
    """BM25Index 构建与检索功能的测试。"""

    def test_build_and_search(self):
        """构建索引后应能检索到相关内容。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            chunks = [
                {"id": "1", "content": "2024年营业收入3943亿元"},
                {"id": "2", "content": "净利润862亿元"},
            ]
            index.build_index("test_kb", chunks)
            results = index.search("test_kb", "营业收入", k=2)
            assert len(results) >= 1
            # 结果应包含 bm25_score 字段
            assert "bm25_score" in results[0]

    def test_search_unknown_kb(self):
        """搜索不存在的知识库应返回空列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = BM25Index(index_dir=tmpdir)
            assert index.search("nonexistent", "test", k=10) == []

    def test_default_index_dir(self):
        """默认 index_dir 应为 'data/bm25_index'。"""
        index = BM25Index()
        assert str(index.index_dir) == "data/bm25_index"


class TestRRFFusion:
    """RRF 融合函数的测试。"""

    def test_fusion_both_empty(self):
        """两个空列表融合应返回空列表。"""
        assert rrf_fusion([], []) == []

    def test_fusion_only_dense(self):
        """只有 dense 结果时，应全部返回。"""
        dense = [{"id": "1", "content": "doc1"}, {"id": "2", "content": "doc2"}]
        result = rrf_fusion(dense, [], top_n=10)
        assert len(result) == 2
        assert result[0]["id"] == "1"

    def test_fusion_only_bm25(self):
        """只有 BM25 结果时，应全部返回。"""
        bm25 = [{"id": "a", "content": "doc_a"}, {"id": "b", "content": "doc_b"}]
        result = rrf_fusion([], bm25, top_n=10)
        assert len(result) == 2
        assert result[0]["id"] == "a"

    def test_fusion_interleaving(self):
        """两个列表有重叠 ID 时，融合后不应出现重复。"""
        dense = [{"id": "1"}, {"id": "2"}]
        bm25 = [{"id": "2"}, {"id": "3"}]
        result = rrf_fusion(dense, bm25, top_n=10)
        # 不重复且包含三个不同文档
        ids = [r["id"] for r in result]
        assert len(ids) == len(set(ids)) == 3

    def test_fusion_top_n_limit(self):
        """top_n 参数应限制返回结果数量。"""
        dense = [{"id": str(i)} for i in range(10)]
        bm25 = [{"id": str(i + 10)} for i in range(10)]
        result = rrf_fusion(dense, bm25, top_n=5)
        assert len(result) == 5

    def test_fusion_ranking_order(self):
        """同时出现在两个列表头部的文档应排在前面。"""
        dense = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        bm25 = [{"id": "1"}, {"id": "3"}, {"id": "4"}]
        result = rrf_fusion(dense, bm25, k=60, top_n=10)
        # "1" 出现在两个列表的第 1 位，应排最前
        assert result[0]["id"] == "1"
        # "3" 在两个列表的第 3 位，"2" 在 dense 第 2 位但不在 bm25 中
        # 取决于 RRF 分数，但确保 1 排在第一位
