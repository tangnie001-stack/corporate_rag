"""P2 PG 向量存储测试的共享辅助：假embedder 与真实 KB fixture。

Task 5（写入路径）与 Task 6（读取路径）共用本模块，避免在两个测试文件里
各自重复定义同一份 fixture 与假实现。
"""

import uuid

import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.vector_store.pg_store import PgVectorStore, QueryEmbedder


class FakeEmbedder(QueryEmbedder):
    """确定性假向量：按文本长度区分方向，便于断言排序。

    继承 QueryEmbedder 只为满足 PgVectorStore(embed_fn=...) 的标称类型约束；
    两个方法都被覆盖，不会触达真实的 DashScope。
    """

    def embed_query(self, text: str) -> list[float]:
        """把查询文本映射为一个 1024 维 one-hot 向量。

        Args:
            text: 查询文本

        Returns:
            第 len(text) % 1024 维为 1、其余为 0 的向量
        """
        vec = [0.0] * 1024
        vec[len(text) % 1024] = 1.0
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，逐条复用 embed_query。

        Args:
            texts: 文本列表

        Returns:
            与输入一一对应的向量列表
        """
        return [self.embed_query(t) for t in texts]


@pytest_asyncio.fixture
async def store_and_kb():
    """建真实 KB 行（chunks 有外键），返回 (PgVectorStore, kb_id)，测后清理。

    Yields:
        (PgVectorStore, kb_id)：接假 embedder 的存储实例与所属知识库 ID
    """
    kb_id = f"p2write-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p2test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p2-{kb_id[-6:]}"},
        )
        await s.commit()
    store = PgVectorStore(embed_fn=FakeEmbedder())
    yield store, kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()
