"""公开入口 VectorStore（PG 后端）的冒烟测试。

覆盖两件事：
- 公开入口确实指向 `pg_store.PgVectorStore`，且 Chroma 的四个实现模块已不存在；
- 装配后的增删查基本行为（打真实 PG，embedding 用假实现，不发网络）。

写库用例必须先建 `knowledge_base` 行（`chunks.kb_id` 有外键），
统一复用 p2_fakes 的 `store_and_kb`（含建库与测后清理）。
"""

import asyncio
import uuid

import pytest

from src.chunking.validator import ChunkData
from src.infra.db.vector_store import VectorStore

# 共享 fixture（store_and_kb）定义在 p2_fakes，以插件方式加载：
# 直接 import fixture 名会与测试函数的同名参数冲突（ruff F811）。
pytest_plugins = ["tests.infra.db.p2_fakes"]


def test_public_vector_store_is_pg_backed():
    """公开入口 VectorStore 背后必须是 PG 实现，且不再有 Chroma 的模块。"""
    import importlib

    module = importlib.import_module("src.infra.db.vector_store")
    assert module.VectorStore.__module__ == "src.infra.db.vector_store.pg_store"
    for gone in ("client", "embedding", "store", "search"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"src.infra.db.vector_store.{gone}")


def test_global_retrieval_entries_are_gone():
    """全局检索路径已移除：不指定知识库的检索在生产链路上不可达，只被测试养着。"""
    vs = VectorStore.__dict__
    assert "similarity_search_all" not in vs
    assert "similarity_search_multi" not in vs


def test_search_source_has_no_global_branch():
    """retrieval.search 源码里不得再出现 not kb_id 的全局分支。"""
    import inspect

    from src.rag import retrieval

    src = inspect.getsource(retrieval.search)
    assert "similarity_search_all" not in src


@pytest.mark.asyncio
async def test_get_or_create_collection_is_side_effect_free(store_and_kb):
    """get_or_create_collection 是兼容方法：返回 kb_id 且不写库。"""
    store, kb_id = store_and_kb
    assert await store.get_or_create_collection(kb_id) == kb_id
    assert await store.get_all_chunks(kb_id) == []


@pytest.mark.asyncio
async def test_add_chunks_and_dense_search(store_and_kb):
    """写入分块后可被 dense 检索取回，结果带距离与 0 起连续排名。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
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
    count = await store.add_chunks(kb_id, chunks, doc_id)
    assert count == 2
    results = await store.similarity_search(kb_id, "营业收入", k=5)
    assert len(results) == 2
    assert all(r.distance is not None for r in results)
    assert [r.dense_rank for r in results] == [0, 1]


@pytest.mark.asyncio
async def test_delete_collection(store_and_kb):
    """删除知识库全部分块：删后无分块，再次删除返回 False。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="内容", metadata={"source": "t.txt"}, chunk_id="t:0")],
        doc_id,
    )
    assert await store.delete_collection(kb_id) is True
    assert await store.get_all_chunks(kb_id) == []
    assert await store.delete_collection(kb_id) is False


@pytest.mark.asyncio
async def test_delete_nonexistent_collection(store_and_kb):
    """删除无分块的知识库返回 False。"""
    store, _kb_id = store_and_kb
    assert await store.delete_collection("nonexistent_kb_id") is False


@pytest.mark.asyncio
async def test_list_collections_reflects_kbs_with_chunks(store_and_kb):
    """list_collections 语义是「含分块的知识库 ID」：无分块不出现、有分块出现。"""
    store, kb_id = store_and_kb
    assert kb_id not in await store.list_collections()
    await store.add_chunks(
        kb_id,
        [ChunkData(content="内容", metadata={"source": "t.txt"}, chunk_id="t:0")],
        uuid.uuid4().hex,
    )
    assert kb_id in await store.list_collections()


@pytest.mark.asyncio
async def test_concurrent_dense_search_safe(store_and_kb):
    """并发 dense 检索不抛异常：PG 连接池可并发，无 Chroma 的线程锁约束。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content=f"文本{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(4)
    ]
    # 非零 one-hot 向量：pgvector 对零向量的余弦距离是 NaN，会破坏使用性判断
    embeddings = [[0.0] * 1024 for _ in chunks]
    for i, emb in enumerate(embeddings):
        emb[i] = 1.0
    await store.add_chunks(kb_id, chunks, doc_id, embeddings)

    results = await asyncio.gather(
        *[store.dense_search(kb_id, "查询", k=3) for _ in range(8)]
    )
    assert all(len(r) == 3 for r in results)
