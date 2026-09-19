"""PgVectorStore 读取路径（打真实 PG，embedding 用假的，不发网络）。"""

import uuid

import pytest
from sqlalchemy import text

from src.chunking.validator import ChunkData
from src.infra.db.engine import session_factory

# 共享 fixture（store_and_kb）定义在 p2_fakes，以插件方式加载：
# 直接 import fixture 名会与测试函数的同名参数冲突（ruff F811）。
pytest_plugins = ["tests.infra.db.p2_fakes"]

pytestmark = pytest.mark.asyncio


async def test_dense_search_orders_by_distance_and_ranks(store_and_kb):
    """dense 检索：按余弦距离升序、dense_rank 从 0 连续、limit 生效。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content=f"文本{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(4)
    ]
    # 必须用非零向量：零向量的余弦距离在 pgvector 里是 NaN，会破坏下面的升序断言。
    # FakeEmbedder 把 "查询"（len=2）映射到轴 2，据此构造严格的距离梯度：
    # 第 3 段与查询同向（距离 0），其余与查询的余弦相似度依次降低。
    embeddings = [[0.0] * 1024 for _ in chunks]
    embeddings[2][2] = 1.0
    embeddings[0][2] = embeddings[0][3] = 1.0
    embeddings[1][2] = embeddings[1][3] = embeddings[1][4] = 1.0
    embeddings[3][3] = 1.0
    await store.add_chunks(kb_id, chunks, doc_id, embeddings)

    results = await store.dense_search(kb_id, "查询", k=3)
    assert len(results) == 3
    assert [r.dense_rank for r in results] == [0, 1, 2]
    assert results[0].id == f"{doc_id}:2"
    assert all(r.distance is not None for r in results)
    assert results[0].distance <= results[1].distance <= results[2].distance


async def test_similarity_search_is_the_same_entry(store_and_kb):
    """similarity_search 与 dense_search 是同一实现的两个名字（D8 契约保留）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="唯一一段", metadata={"source": "a.pdf"}, chunk_id="x:0")],
        doc_id,
        [[1.0] + [0.0] * 1023],
    )
    a = await store.similarity_search(kb_id, "查询", k=5)
    b = await store.dense_search(kb_id, "查询", k=5)
    assert [r.id for r in a] == [r.id for r in b]


async def test_k_is_capped_at_100(store_and_kb):
    """上限 100 保留（F6）：请求 500 也只取 100。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(105)
    ]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])
    results = await store.dense_search(kb_id, "查询", k=500)
    assert len(results) == 100


async def test_metadata_contract_is_backfilled_on_every_result(store_and_kb):
    """D4：每条结果的 metadata 都含 5 个契约键 + 自定义键。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [
            ChunkData(
                content="正文",
                metadata={"source": "茅台.pdf", "page": 5, "parent_content": "母公司"},
                chunk_id="x:0",
            )
        ],
        doc_id,
        [[1.0] + [0.0] * 1023],
    )
    for result in await store.dense_search(kb_id, "查询", k=5):
        assert result.metadata["doc_id"] == doc_id
        assert result.metadata["chunk_index"] == 0
        assert result.metadata["chunk_total"] == 1
        assert result.metadata["source"] == "茅台.pdf"
        assert result.metadata["page"] == 5
        assert result.metadata["parent_content"] == "母公司"


async def test_get_chunks_by_doc_id_and_paginated(store_and_kb):
    """按 doc 取全量 / 分页；分路字段为 None。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(5)
    ]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])

    all_chunks = await store.get_chunks_by_doc_id(doc_id, kb_id)
    assert [c.id for c in all_chunks] == [f"{doc_id}:{i}" for i in range(5)]
    assert all(c.distance is None and c.dense_rank is None for c in all_chunks)

    page = await store.get_chunks_paginated(doc_id, kb_id, page=2, page_size=2)
    assert page.total == 5
    assert page.page == 2
    assert page.page_size == 2
    assert [c.id for c in page.items] == [f"{doc_id}:2", f"{doc_id}:3"]


async def test_get_all_chunks_returns_sorted_rows(store_and_kb):
    """get_all_chunks 覆盖 ChunkRepo.get_by_kb：全量性 + 确定性顺序 + 分路字段缺席。

    该方法的调用方是评测脚本的空库检查，顺序不确定会让结果不可复现。
    """
    store, kb_id = store_and_kb
    doc_a = uuid.uuid4().hex
    doc_b = uuid.uuid4().hex
    # 交错写入：先 b 的两段、再 a 的一段，确保返回顺序不是「插入顺序碰巧正确」
    await store.add_chunks(
        kb_id,
        [
            ChunkData(content="b0", metadata={"source": "b.pdf"}, chunk_id="x:0"),
            ChunkData(
                content="b1",
                metadata={"source": "b.pdf", "parent_content": "P"},
                chunk_id="x:1",
            ),
        ],
        doc_b,
        store._embed_fn.embed_documents(["b0", "b1"]),
    )
    await store.add_chunks(
        kb_id,
        [ChunkData(content="a0", metadata={"source": "a.pdf"}, chunk_id="y:0")],
        doc_a,
        store._embed_fn.embed_documents(["a0"]),
    )

    rows = await store.get_all_chunks(kb_id)
    assert len(rows) == 3
    # 全量且不混入别的 kb
    assert {r.metadata["doc_id"] for r in rows} == {doc_a, doc_b}
    # 确定性顺序：按 (doc_id, chunk_index)
    order = [(r.metadata["doc_id"], r.metadata["chunk_index"]) for r in rows]
    assert order == sorted(order)
    # 不是检索 → 分路字段与得分全为 None
    assert all(r.distance is None and r.lexical_score is None for r in rows)
    assert all(r.dense_rank is None and r.sparse_rank is None for r in rows)
    # metadata 回填契约键必须在（去重/引用/实体透传依赖它们）
    for r in rows:
        for key in ("doc_id", "chunk_index", "chunk_total", "source", "page"):
            assert key in r.metadata
    assert any(r.metadata.get("parent_content") == "P" for r in rows)


async def test_list_collections_returns_kbs_with_chunks_without_side_effects(
    store_and_kb,
):
    """枚举不产生副作用（hybrid-retrieval 的「遍历不产生副作用」）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    await store.add_chunks(
        kb_id,
        [ChunkData(content="段0", metadata={"source": "a.pdf"}, chunk_id="x:0")],
        doc_id,
        [[0.0] * 1024],
    )
    names = await store.list_collections()
    assert kb_id in names
    # 再枚举一次，结果不变（没有「读时创建」）
    assert await store.list_collections() == names


async def test_delete_document_and_collection(store_and_kb):
    """删除路径：按 doc 删除返回行数；按 kb 删除返回布尔。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(3)
    ]
    await store.add_chunks(kb_id, chunks, doc_id, [[0.0] * 1024 for _ in chunks])

    assert await store.delete_document(kb_id, doc_id) == 3
    assert await store.get_chunks_by_doc_id(doc_id, kb_id) == []
    assert await store.delete_collection(kb_id) is False  # 已无行


async def test_delete_collection_true_when_rows_exist(store_and_kb):
    """delete_collection 的布尔语义 = 是否删掉了行（正向分支）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [ChunkData(content="段0", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(
        kb_id, chunks, doc_id, store._embed_fn.embed_documents(["段0"])
    )
    assert await store.delete_collection(kb_id) is True
    assert await store.delete_collection(kb_id) is False  # 再删已无行


async def test_get_or_create_collection_is_side_effect_free(store_and_kb):
    """PG 没有 collection：该方法退化为返回 kb_id，且不写任何数据。"""
    store, kb_id = store_and_kb
    assert await store.get_or_create_collection(kb_id) == kb_id
    async with session_factory() as s:
        n = await s.scalar(
            text("SELECT count(*) FROM chunks WHERE kb_id = :k"), {"k": kb_id}
        )
    assert n == 0
