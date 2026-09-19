"""PG baseline 迁移的验收测试（需真实 PostgreSQL）。

本测试自建引擎、不依赖 src.infra.db.engine —— 后者的切换在 Task 6，
本任务只验证「迁移把库建对了」。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config.settings import build_postgres_dsn

pytestmark = pytest.mark.asyncio

EXPECTED_TABLES = {
    "users",
    "knowledge_base",
    "document",
    "sessions",
    "conversation_history",
    "eval_report",
    "feedback",
    "chunks",
}


async def _collect(sql: str, **params) -> list:
    """一次性连接执行查询并返回全部行，用完即释放连接。"""
    engine = create_async_engine(build_postgres_dsn(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            return list(result)
    finally:
        await engine.dispose()


async def _execute(sql: str, **params) -> None:
    """一次性连接执行写入并提交。"""
    engine = create_async_engine(build_postgres_dsn(), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
    finally:
        await engine.dispose()


async def _columns(table: str) -> dict[str, str]:
    rows = await _collect(
        "SELECT column_name, is_generated FROM information_schema.columns "
        "WHERE table_name = :t",
        t=table,
    )
    return {row[0]: row[1] for row in rows}


async def test_all_eight_tables_exist():
    rows = await _collect("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    got = {row[0] for row in rows}
    assert EXPECTED_TABLES <= got


async def test_vector_extension_installed():
    rows = await _collect(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    )
    assert rows, "vector 扩展必须已创建"


async def test_critical_columns_present():
    """两个模型的差异列与既有缺陷表的关键列必须都在。"""
    assert "agent" in await _columns("sessions")
    assert "process" in await _columns("conversation_history")
    assert "trace_id" in await _columns("feedback")
    assert "md5" in await _columns("document")


async def test_chunks_tsv_is_generated_column():
    cols = await _columns("chunks")
    assert cols.get("tsv") == "ALWAYS", "tsv 必须是生成列"


async def test_knowledge_base_unique_constraint():
    rows = await _collect(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'knowledge_base'::regclass AND contype = 'u'"
    )
    assert rows, "knowledge_base 必须有 (user_id, name) 唯一约束"


@pytest.mark.parametrize(
    ("index_name", "table_name"),
    [
        ("idx_user_kb", "document"),
        ("idx_session", "conversation_history"),
        ("idx_user", "sessions"),
        ("idx_updated_at", "sessions"),
        ("idx_kb_date", "eval_report"),
        ("ix_chunks_kb_id", "chunks"),
        ("ix_chunks_doc_id", "chunks"),
    ],
)
async def test_indexes_exist_in_database(index_name, table_name):
    """索引必须真的被建出来了 —— ORM 里声明 ≠ 数据库里有。"""
    rows = await _collect(
        "SELECT indexname FROM pg_indexes WHERE tablename = :t AND indexname = :i",
        t=table_name,
        i=index_name,
    )
    assert rows, f"{table_name} 缺少索引 {index_name}"


async def test_chunks_tsv_gin_index_is_gin():
    """tsv 索引必须是 GIN，否则全文检索会退化为顺序扫描。"""
    rows = await _collect(
        "SELECT indexdef FROM pg_indexes "
        "WHERE tablename = 'chunks' AND indexname = 'ix_chunks_tsv'"
    )
    assert rows, "chunks 缺少 ix_chunks_tsv 索引"
    assert "USING gin" in rows[0][0], rows[0][0]


# --- chunks 可用性冒烟（表存在 ≠ 表可用）-----------------------------------
# P1 期间没有任何业务代码读写 chunks，所以只有下面这几条测试会碰它。
# 若生成列表达式写错、或维度写成 1023，结构断言全绿而这里会立刻暴露。

PROBE_KB = "p1probe-kb"
PROBE_DOC = "p1probe-doc"


def _vec(axis: int) -> str:
    """构造一个 1024 维单位向量字面量（axis 位置为 1）。"""
    values = ["0"] * 1024
    values[axis] = "1"
    return "[" + ",".join(values) + "]"


async def _cleanup_probe() -> None:
    await _execute("DELETE FROM chunks WHERE kb_id = :k", k=PROBE_KB)
    await _execute("DELETE FROM knowledge_base WHERE id = :k", k=PROBE_KB)


async def test_chunks_is_writable_and_searchable():
    """插入 → 生成列自动算 → 词法可命中 → 向量可排序。"""
    await _cleanup_probe()
    # knowledge_base 的 description/doc_count/is_deleted 只有 Python 侧默认值，
    # 没有 server_default，因此裸 SQL 插入必须显式给值。
    await _execute(
        "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
        " VALUES (:k, 'p1probe', 'p1probe', '', 0, 0)",
        k=PROBE_KB,
    )
    try:
        await _execute(
            "INSERT INTO chunks (id, kb_id, doc_id, chunk_index, chunk_total,"
            " content, content_seg, embedding, source, page)"
            " VALUES (:i, :k, :d, 0, 2, '营业收入同比增长', '营业 收入 同比 增长',"
            " CAST(:e AS vector), 'probe.pdf', 1)",
            i="p1probe-1",
            k=PROBE_KB,
            d=PROBE_DOC,
            e=_vec(0),
        )
        await _execute(
            "INSERT INTO chunks (id, kb_id, doc_id, chunk_index, chunk_total,"
            " content, content_seg, embedding, source, page)"
            " VALUES (:i, :k, :d, 1, 2, '资产负债率上升', '资产 负债率 上升',"
            " CAST(:e AS vector), 'probe.pdf', 2)",
            i="p1probe-2",
            k=PROBE_KB,
            d=PROBE_DOC,
            e=_vec(1),
        )

        rows = await _collect(
            "SELECT tsv IS NOT NULL AS ok FROM chunks WHERE id = 'p1probe-1'"
        )
        assert rows[0][0] is True, "tsv 生成列必须自动填充（未写入却应有值）"

        rows = await _collect(
            "SELECT id FROM chunks WHERE kb_id = :k"
            " AND tsv @@ plainto_tsquery('simple', '营业')",
            k=PROBE_KB,
        )
        assert [r[0] for r in rows] == ["p1probe-1"], "词法路必须能命中"

        rows = await _collect(
            "SELECT id FROM chunks WHERE kb_id = :k"
            " ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1",
            k=PROBE_KB,
            q=_vec(1),
        )
        assert rows[0][0] == "p1probe-2", "余弦距离排序必须生效"
    finally:
        await _cleanup_probe()


async def test_chunks_kb_id_foreign_key_is_enforced():
    """kb_id 外键必须真的约束住 —— 否则 Parent P2 的"按库归属"是空话。"""
    with pytest.raises(IntegrityError):
        await _execute(
            "INSERT INTO chunks (id, kb_id, doc_id, chunk_index, chunk_total,"
            " content, content_seg)"
            " VALUES ('p1probe-bad', 'p1probe-nonexistent', 'd', 0, 1, 'c', 'c')"
        )
