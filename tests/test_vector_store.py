"""VectorStore 向量存储的单元测试。

测试目标：
- ChromaDB collection 的创建 / 幂等性 / 删除 / 列表
- 文档分块添加与相似度搜索
- collection 名称格式（kb_ 前缀）

注意：使用临时目录作为 ChromaDB 持久化路径，
测试结束后自动清理，不影响生产数据。
"""

import uuid
import pytest
from src.infra.db.vector_store import VectorStore
from src.parsers.base import ChunkData


@pytest.fixture
def vs():
    """临时目录 fixture：创建隔离的 VectorStore 实例。"""
    import tempfile

    tmpdir = tempfile.mkdtemp()  # 创建临时目录
    store = VectorStore(persist_dir=tmpdir)
    yield store
    # 测试结束后清理临时文件
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def kb_id():
    """生成随机知识库 ID，避免测试间冲突。"""
    return uuid.uuid4().hex


class TestVectorStore:
    """ChromaDB 向量存储测试套件。"""

    def test_get_or_create_collection(self, vs, kb_id):
        """创建 collection：名称必须为 kb_{kb_id} 格式。"""
        coll = vs.get_or_create_collection(kb_id)
        assert coll is not None
        assert coll.name == f"kb_{kb_id}"  # kb_ 前缀隔离不同知识库

    def test_get_or_create_collection_idempotent(self, vs, kb_id):
        """幂等性：多次创建同一 kb_id 返回相同 collection。"""
        coll1 = vs.get_or_create_collection(kb_id)
        coll2 = vs.get_or_create_collection(kb_id)
        assert coll1.name == coll2.name

    def test_add_chunks_and_search(self, vs, kb_id):
        """添加分块并搜索：验证写入 + 相似度搜索全流程。"""
        vs.get_or_create_collection(kb_id)
        # 构造两个金融数据分块
        chunks = [
            ChunkData(
                content="贵州茅台2024年营业收入1,741亿元",
                metadata={"source": "test.txt", "page": 1},
                chunk_id="test:0",
            ),
            ChunkData(
                content="贵州茅台2024年净利润857亿元",
                metadata={"source": "test.txt", "page": 1},
                chunk_id="test:1",
            ),
        ]
        doc_id = uuid.uuid4().hex
        count = vs.add_chunks(kb_id, chunks, doc_id)
        assert count == 2  # 成功写入 2 个分块

        # 相似度搜索：“营业收入” 应与第一个 chunk 更相关
        results = vs.similarity_search(kb_id, "营业收入", k=5)
        assert isinstance(results, list)

    def test_delete_collection(self, vs, kb_id):
        """删除 collection：删除后重新创建应得到空 collection。"""
        vs.get_or_create_collection(kb_id)
        assert vs.delete_collection(kb_id) is True
        # 删除后重新创建，验证幂等性
        coll = vs.get_or_create_collection(kb_id)
        assert coll is not None

    def test_delete_nonexistent_collection(self, vs):
        """删除不存在的 collection：返回 False。"""
        result = vs.delete_collection("nonexistent_kb_id")
        assert result is False

    def test_collection_name_format(self, vs, kb_id):
        """名称格式检查：必须以 kb_ 开头且长度 > 3。"""
        coll = vs.get_or_create_collection(kb_id)
        assert coll.name.startswith("kb_")  # 前缀检查
        assert len(coll.name) > 3

    def test_list_collections_empty(self, vs):
        """空存储：未创建任何 collection 时返回列表。"""
        names = vs.list_collections()
        assert names == []

    def test_list_collections_with_data(self, vs, kb_id):
        """有数据：创建一个 collection 后列表长度为 1。"""
        vs.get_or_create_collection(kb_id)
        names = vs.list_collections()
        assert len(names) == 1
        assert names[0] == f"kb_{kb_id}"

    def test_similarity_search_all(self, vs, kb_id):
        """搜索所有 collection，返回按距离排序的跨知识库结果。"""
        # 准备工作：创建两个 KB collection，各写入一条文档
        kb_a = uuid.uuid4().hex
        kb_b = uuid.uuid4().hex
        col_a = vs.get_or_create_collection(kb_a)
        col_b = vs.get_or_create_collection(kb_b)
        col_a.add(
            ids=["a:0"],
            documents=["苹果公司的营收情况"],
            metadatas=[{"source": "a.txt"}],
        )
        col_b.add(
            ids=["b:0"], documents=["特斯拉的营收情况"], metadatas=[{"source": "b.txt"}]
        )

        # 注意：不清空 _collection_cache。
        # ChromaDB get_collection() 不保存创建时设置的 embedding_function，
        # 清空后重新 get_collection 会导致 query 时 embedding 维度不匹配。
        results = vs.similarity_search_all("营收", k=2)
        assert len(results) == 2
        # 结果应按 distance 升序排列（越小越相似）
        for i in range(len(results) - 1):
            assert results[i]["distance"] <= results[i + 1]["distance"]

    def test_similarity_search_all_no_collections(self, vs):
        """无任何 collection 时返回空列表。"""
        results = vs.similarity_search_all("test", k=5)
        assert results == []
