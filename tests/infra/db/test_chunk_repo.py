"""ChunkRepo 与 chunks 表的一致性测试（打真实 PG）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.mapping import ChunkRow

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def kb_row():
    """建一个真实 knowledge_base 行（chunks.kb_id 有外键），测后清理。"""
    kb_id = f"p2test-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p2test-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def test_chunks_embedding_dimension_is_1024():
    """维度一致性守卫的对照对象：chunks.embedding 列的维度（F8）。"""
    async with session_factory() as s:
        result = await s.execute(
            text(
                "SELECT atttypmod FROM pg_attribute"
                " WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
            )
        )
        assert result.scalar_one() == 1024


async def test_upsert_then_read_back(kb_row):
    """写入后能按 doc 读回，且 content_seg 已落库（tsv 由生成列算）。"""
    repo = ChunkRepo(session_factory)
    doc = uuid.uuid4().hex
    rows = [
        ChunkRow(
            id=f"{doc}:0",
            kb_id=kb_row,
            doc_id=doc,
            chunk_index=0,
            chunk_total=1,
            content="贵州茅台2024年营业收入1741亿元",
            content_seg="贵州茅台2024年营业收入1741亿元",
            embedding=[0.5] * 1024,
            source="a.pdf",
            page=3,
            extra={"parent_content": "母公司"},
        ),
    ]
    assert await repo.upsert_chunks(rows) == 1
    got = await repo.get_by_doc(doc, kb_row)
    assert len(got) == 1
    assert got[0].extra["parent_content"] == "母公司"
    assert got[0].source == "a.pdf"
    assert got[0].page == 3
    # content_seg 必须真的落了库（P2 是正文原值占位；P3 换 jieba 输出）
    assert got[0].content_seg == "贵州茅台2024年营业收入1741亿元"


async def test_chunks_server_defaults_match_baseline():
    """D1 对 server_default 是盲的（Alembic 默认 compare_server_default=False），
    故单独断言这三列的默认值与 baseline 的 sa.text(...) 逐字一致。"""
    async with session_factory() as s:
        result = await s.execute(
            text(
                "SELECT column_name, column_default FROM information_schema.columns"
                " WHERE table_name = 'chunks' AND column_name IN ('source', 'page', 'metadata')"
            )
        )
        defaults = {name: value for name, value in result.all()}
    assert defaults["source"] == "''::text"
    assert defaults["page"] == "0"
    assert defaults["metadata"] == "'{}'::jsonb"


async def test_tsv_is_generated_from_content_seg(kb_row):
    """tsv 是生成列：不写入也应自动有值（且不为 NULL）。"""
    repo = ChunkRepo(session_factory)
    doc = uuid.uuid4().hex
    rows = [
        ChunkRow(
            id=f"{doc}:0",
            kb_id=kb_row,
            doc_id=doc,
            chunk_index=0,
            chunk_total=1,
            content="营业收入同比增长",
            content_seg="营业 收入 同比 增长",
            embedding=[0.5] * 1024,
            source="a.pdf",
            page=1,
            extra={},
        ),
    ]
    assert await repo.upsert_chunks(rows) == 1
    async with session_factory() as s:
        value = await s.scalar(
            text("SELECT tsv IS NOT NULL FROM chunks WHERE id = :i"), {"i": f"{doc}:0"}
        )
    assert value is True
