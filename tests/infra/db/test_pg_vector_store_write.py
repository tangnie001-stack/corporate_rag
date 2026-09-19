"""PgVectorStore 写入路径（打真实 PG，embedding 用假的，不发网络）。"""

import uuid

import pytest
from sqlalchemy import text

from src.chunking.validator import ChunkData
from src.infra.db.engine import session_factory

# 共享 fixture（store_and_kb）定义在 p2_fakes，以插件方式加载：
# 直接 import fixture 名会与测试函数的同名参数冲突（ruff F811）。
pytest_plugins = ["tests.infra.db.p2_fakes"]

pytestmark = pytest.mark.asyncio


async def _count(kb_id: str) -> int:
    """统计某知识库当前的分块行数。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM chunks WHERE kb_id = :k"), {"k": kb_id}
            )
        )


async def test_add_chunks_writes_rows_with_columns_and_jsonb(store_and_kb):
    """入库：行落库、契约键升列、自定义键进 jsonb。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    chunks = [
        ChunkData(
            content="第一段",
            metadata={"source": "a.pdf", "page": 1, "parent_content": "P"},
            chunk_id="x:0",
        ),
        ChunkData(
            content="第二段", metadata={"source": "a.pdf", "page": 2}, chunk_id="x:1"
        ),
    ]
    embeddings = store._embed_fn.embed_documents([c.content for c in chunks])
    assert await store.add_chunks(kb_id, chunks, doc_id, embeddings) == 2
    assert await _count(kb_id) == 2
    async with session_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT source, page, metadata, chunk_index, chunk_total FROM chunks"
                    " WHERE kb_id = :k AND chunk_index = 0"
                ),
                {"k": kb_id},
            )
        ).one()
    assert row[0] == "a.pdf"
    assert row[1] == 1
    assert row[2] == {"parent_content": "P"}  # 契约键不得混进 jsonb
    assert row[3] == 0
    assert row[4] == 2


async def test_add_chunks_is_idempotent_and_replaces_content(store_and_kb):
    """同 doc 重传（Ruling 2）：覆盖内容，不产生重复行。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    first = [ChunkData(content="旧内容", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(
        kb_id, first, doc_id, store._embed_fn.embed_documents(["旧内容"])
    )
    second = [ChunkData(content="新内容", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(
        kb_id, second, doc_id, store._embed_fn.embed_documents(["新内容"])
    )
    assert await _count(kb_id) == 1
    async with session_factory() as s:
        content = await s.scalar(
            text("SELECT content FROM chunks WHERE kb_id = :k AND chunk_index = 0"),
            {"k": kb_id},
        )
    assert content == "新内容"


async def test_add_chunks_removes_tail_when_chunk_count_shrinks(store_and_kb):
    """重传后分块数变少：尾部残留必须删掉（Ruling 2 的已知残留）。"""
    store, kb_id = store_and_kb
    doc_id = uuid.uuid4().hex
    three = [
        ChunkData(content=f"段{i}", metadata={"source": "a.pdf"}, chunk_id=f"x:{i}")
        for i in range(3)
    ]
    await store.add_chunks(
        kb_id,
        three,
        doc_id,
        store._embed_fn.embed_documents([c.content for c in three]),
    )
    assert await _count(kb_id) == 3
    one = [ChunkData(content="只剩一段", metadata={"source": "a.pdf"}, chunk_id="x:0")]
    await store.add_chunks(
        kb_id, one, doc_id, store._embed_fn.embed_documents(["只剩一段"])
    )
    assert await _count(kb_id) == 1


async def test_add_chunks_empty_returns_zero(store_and_kb):
    """空输入直接返回 0，不触碰数据库。"""
    store, kb_id = store_and_kb
    assert await store.add_chunks(kb_id, [], uuid.uuid4().hex, []) == 0
