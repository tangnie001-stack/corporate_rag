"""三条跨表路径的原子性验收（故障注入，真实 PG）。

判据不是"成功路径能跑通"，而是"中途注入异常后两边都不落库"。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.chunking.validator import ChunkData
from src.infra.db.engine import session_factory
from src.infra.db.repos import DocumentRepo
from src.infra.db.vector_store.pg_store import PgVectorStore
from src.services.document_service import DocumentService

pytestmark = pytest.mark.asyncio


class _FakeEmbedder:
    """本文件的用例都显式传 embeddings，故不需要真实向量化；仅为构造 PgVectorStore。"""

    def embed_query(self, text: str) -> list[float]:
        """返回全零 1024 维向量（本文件不会走到这里）。"""
        return [0.0] * 1024

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """逐条返回全零向量（本文件不会走到这里）。"""
        return [[0.0] * 1024 for _ in texts]


@pytest_asyncio.fixture
async def atomic_kb():
    """建真实 KB，返回 (kb_id, doc_repo)，测后清理该 KB 的一切痕迹。"""
    kb_id = f"p4atom-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p4test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p4-{kb_id[-6:]}"},
        )
        await s.commit()
    yield kb_id, DocumentRepo(session_factory)
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM document WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def _insert_doc(
    doc_id: str, kb_id: str, *, status: str = "processing", user_id: str = ""
) -> None:
    """插一行最小 document。

    Args:
        doc_id: 文档 ID
        kb_id: 所属知识库
        status: 文档状态（删文档的用例需要 ready）
        user_id: 属主（删文档的用例需要与调用者一致）
    """
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO document (id, kb_id, filename, file_type, file_size,"
                " status, is_deleted, user_id, processing_state, chunk_count,"
                " processing_progress)"
                " VALUES (:i, :k, 'a.txt', 'txt', 10, :st, 0, :u, 'running', 0, 0)"
            ),
            {"i": doc_id, "k": kb_id, "st": status, "u": user_id},
        )
        await s.commit()


async def _status(doc_id: str) -> str:
    """读文档状态。"""
    async with session_factory() as s:
        return str(
            await s.scalar(
                text("SELECT status FROM document WHERE id = :i"), {"i": doc_id}
            )
        )


async def _is_deleted(doc_id: str) -> int:
    """读文档软删标记。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT is_deleted FROM document WHERE id = :i"), {"i": doc_id}
            )
        )


async def _chunk_count(kb_id: str, doc_id: str) -> int:
    """读某文档的分块数。"""
    async with session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT count(*) FROM chunks WHERE kb_id = :k AND doc_id = :i"),
                {"k": kb_id, "i": doc_id},
            )
        )


async def test_ingest_is_atomic_when_status_update_fails(atomic_kb, monkeypatch):
    """分块写入与文档状态更新同一事务：状态更新失败时**分块也不得落库**。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id)

    svc = DocumentService(
        doc_repo=doc_repo,
        # 假 embedder 仅占位，用例显式传 embeddings
        vector_store=PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder()),  # type: ignore[reportArgumentType]
        router=None,  # type: ignore[reportArgumentType]  # 本用例不走解析路径
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: status update failed")

    monkeypatch.setattr(doc_repo, "update_document_status", _boom)

    with pytest.raises(RuntimeError, match="inject: status update failed"):
        await svc._write_chunks_and_mark_ready(
            kb_id=kb_id,
            doc_id=doc_id,
            chunks=[ChunkData(content="资产负债率上升", metadata={})],
            embeddings=[[0.1] * 1024],
        )

    assert await _chunk_count(kb_id, doc_id) == 0
    assert await _status(doc_id) == "processing"


async def test_delete_document_is_atomic_when_chunk_delete_fails(
    atomic_kb, monkeypatch
):
    """删分块失败时文档**不得**被软删（否则产生永久孤儿分块）。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())  # type: ignore[reportArgumentType]
    svc = DocumentService(
        doc_repo=doc_repo,
        vector_store=store,
        router=None,  # type: ignore[reportArgumentType]  # 本用例不走解析路径
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: chunk delete failed")

    monkeypatch.setattr(store, "delete_document", _boom)
    with pytest.raises(RuntimeError):
        await svc.delete_document(kb_id, doc_id, user_id="p4test")

    assert await _is_deleted(doc_id) == 0


async def test_delete_document_happy_path_removes_both(atomic_kb):
    """两步都成功时：分块归零 + 文档软删。"""
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())  # type: ignore[reportArgumentType]
    svc = DocumentService(
        doc_repo=doc_repo,
        vector_store=store,
        router=None,  # type: ignore[reportArgumentType]  # 本用例不走解析路径
    )

    await svc._write_chunks_and_mark_ready(
        kb_id=kb_id,
        doc_id=doc_id,
        chunks=[ChunkData(content="资产负债率上升", metadata={})],
        embeddings=[[0.1] * 1024],
    )
    assert await _chunk_count(kb_id, doc_id) == 1

    result = await svc.delete_document(kb_id, doc_id, user_id="p4test")

    assert result["status"] == "deleted"
    assert await _chunk_count(kb_id, doc_id) == 0
    assert await _is_deleted(doc_id) == 1


async def test_delete_document_rolls_back_chunks_when_soft_delete_fails(
    atomic_kb, monkeypatch
):
    """软删文档失败时**分块必须回滚**（不得留下有分块、文档未删的半删状态）。

    注入点在事务第二步：第一步的删分块已执行、尚未提交；异常由事务边界回滚，
    已执行的删分块随之撤销 —— 断言分块数即断言边界的回滚。
    """
    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())  # type: ignore[reportArgumentType]
    svc = DocumentService(
        doc_repo=doc_repo,
        vector_store=store,
        router=None,  # type: ignore[reportArgumentType]  # 本用例不走解析路径
    )

    await svc._write_chunks_and_mark_ready(
        kb_id=kb_id,
        doc_id=doc_id,
        chunks=[ChunkData(content="资产负债率上升", metadata={})],
        embeddings=[[0.1] * 1024],
    )
    assert await _chunk_count(kb_id, doc_id) == 1

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: soft delete failed")

    monkeypatch.setattr(doc_repo, "soft_delete_document", _boom)
    with pytest.raises(RuntimeError):
        await svc.delete_document(kb_id, doc_id, user_id="p4test")

    assert await _chunk_count(kb_id, doc_id) == 1
    assert await _is_deleted(doc_id) == 0


async def test_delete_knowledge_base_is_atomic_when_chunk_delete_fails(
    atomic_kb, monkeypatch
):
    """删知识库时删分块失败 → 文档与知识库都**不得**被软删（且异常向上抛）。"""
    from src.infra.db.repos import KbRepo
    from src.services.app_service import AppService

    kb_id, doc_repo = atomic_kb
    doc_id = str(uuid.uuid4())
    await _insert_doc(doc_id, kb_id, status="ready", user_id="p4test")

    store = PgVectorStore(chunk_repo=None, embed_fn=_FakeEmbedder())  # type: ignore[reportArgumentType]
    svc = AppService.__new__(AppService)  # 绕开构造器：本用例只测 delete 编排
    svc._doc_repo = doc_repo
    svc._kb_repo = KbRepo(session_factory)
    svc.vector_store = store

    async def _boom(*args, **kwargs):
        raise RuntimeError("inject: chunk delete failed")

    monkeypatch.setattr(store, "delete_collection", _boom)
    with pytest.raises(RuntimeError):
        await svc.delete_knowledge_base(kb_id)

    assert await _is_deleted(doc_id) == 0
    async with session_factory() as s:
        kb_deleted = await s.scalar(
            text("SELECT is_deleted FROM knowledge_base WHERE id = :k"), {"k": kb_id}
        )
    assert kb_deleted == 0
