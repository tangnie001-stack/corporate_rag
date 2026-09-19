"""词法取数：tsquery 命中、ts_rank 排序、子串兜底（真实 PG）。"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.infra.db.engine import session_factory
from src.infra.db.lexical_query import build_lexical_query
from src.infra.db.mysql_db.chunk_repo import ChunkRepo
from src.infra.db.vector_store.pg_store import PgVectorStore
from src.infra.search.tokenizer import to_lexical_text

pytestmark = pytest.mark.asyncio

_DOCS = [
    # 取自 design.md D4 的实测 jieba 输出：content_seg 为「营业 收入 同比 增长率 保持稳定」，
    # 因此查询「营业收入」被切成「营业」+「收入」后仍能靠前缀通配召回（H2）
    ("d1", "营业收入同比增长率保持稳定"),
    # 实测输出含单字被滤掉的「5」与「月」，是 H1 的子串兜底用例
    ("d2", "公司资产负债率上升，研发费用 5 月增加"),
    ("d3", "前五名客户合计销售金额 36.18%，占年度销售总额"),
    # 与 d2 共享 资产负债率，用于验证排序与 sparse_rank 序列
    ("d4", "公司资产负债率保持稳定，研发费用增加"),
]


@pytest_asyncio.fixture
async def lexical_kb():
    """建真实 KB 与 4 条已分词的分块，返回 (repo, store, kb_id)。"""
    kb_id = f"p3lex-{uuid.uuid4().hex[:12]}"
    async with session_factory() as s:
        await s.execute(
            text(
                "INSERT INTO knowledge_base (id, user_id, name, description, doc_count, is_deleted)"
                " VALUES (:k, 'p3test', :n, '', 0, 0)"
            ),
            {"k": kb_id, "n": f"p3-{kb_id[-6:]}"},
        )
        for doc_id, content in _DOCS:
            await s.execute(
                text(
                    "INSERT INTO chunks (id, kb_id, doc_id, chunk_index, chunk_total,"
                    " content, content_seg, source, page, metadata)"
                    " VALUES (:i, :k, :d, 0, 1, :c, :seg, 'r.pdf', 1, '{}'::jsonb)"
                ),
                {
                    "i": f"{doc_id}:0",
                    "k": kb_id,
                    "d": doc_id,
                    "c": content,
                    "seg": to_lexical_text(content),
                },
            )
        await s.commit()
    repo = ChunkRepo(session_factory)
    yield repo, PgVectorStore(chunk_repo=repo), kb_id
    async with session_factory() as s:
        await s.execute(text("DELETE FROM chunks WHERE kb_id = :k"), {"k": kb_id})
        await s.execute(text("DELETE FROM knowledge_base WHERE id = :k"), {"k": kb_id})
        await s.commit()


async def test_prefix_wildcard_recalls_different_word_form(lexical_kb):
    """H2：查询「营业收入」而文档词元是「营业」+「收入」，必须仍能召回 d1。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("营业收入"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d1"]


async def test_prefix_wildcard_recalls_longer_document_token(lexical_kb):
    """H2：查询「增长」而文档词元是「增长率」，必须靠前缀通配召回 d1。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("增长"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d1"]


async def test_rank_is_descending_and_reproducible(lexical_kb):
    """ts_rank 排序：得分单调不增，且同一查询两次结果一致。"""
    repo, _store, kb_id = lexical_kb
    plan = build_lexical_query("公司")
    first = await repo.search_lexical(kb_id, plan, 10)
    second = await repo.search_lexical(kb_id, plan, 10)
    assert {row.doc_id for row, _ in first} == {"d2", "d4"}
    scores = [score for _, score in first]
    assert scores == sorted(scores, reverse=True)
    assert [row.id for row, _ in first] == [row.id for row, _ in second]


async def test_substring_fallback_hits_raw_content(lexical_kb):
    """H1：全单字查询走正文子串兜底，且能命中。"""
    repo, _store, kb_id = lexical_kb
    plan = build_lexical_query("5 月")
    assert plan.use_substring is True
    pairs = await repo.search_lexical(kb_id, plan, 10)
    assert [row.doc_id for row, _ in pairs] == ["d2"]
    assert [score for _, score in pairs] == [0.0]


async def test_like_wildcard_is_escaped(lexical_kb):
    """F6：子串兜底的 % 必须被转义 —— 只命中正文里真的有 % 的那一条，而非全库。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("%"), 10)
    assert [row.doc_id for row, _ in pairs] == ["d3"]


async def test_other_kb_is_not_visible(lexical_kb):
    """词法取数必须限定在单个知识库内。"""
    repo, _store, kb_id = lexical_kb
    pairs = await repo.search_lexical(kb_id, build_lexical_query("资产负债率"), 10)
    assert {row.kb_id for row, _ in pairs} == {kb_id}


async def test_store_lexical_search_fills_rank_and_score(lexical_kb):
    """VectorStore 层：sparse_rank 为 0 起名次，lexical_score 有值，distance 为 None。"""
    _repo, store, kb_id = lexical_kb
    results = await store.lexical_search(kb_id, "资产负债率", 10)
    assert [r.sparse_rank for r in results] == list(range(len(results)))
    assert all(r.lexical_score is not None for r in results)
    assert all(r.distance is None for r in results)
    assert {r.metadata["doc_id"] for r in results} == {"d2", "d4"}
    assert all(r.metadata["source"] == "r.pdf" for r in results)
    assert all(r.metadata["page"] == 1 for r in results)


async def test_store_lexical_search_empty_for_unknown_kb(lexical_kb):
    """无分块的知识库返回空列表而不是抛错。"""
    _repo, store, _kb_id = lexical_kb
    assert await store.lexical_search("no-such-kb", "资产负债率", 10) == []


async def test_blank_query_returns_empty_without_hitting_db(lexical_kb):
    """is_blank 短路：空/纯空白查询直接返回 []，不得提交 tsquery、也不得退化为 LIKE '%%'。"""
    repo, _store, kb_id = lexical_kb
    for blank in ("", "   ", "\t\n"):
        plan = build_lexical_query(blank)
        assert plan.is_blank is True
        assert await repo.search_lexical(kb_id, plan, 10) == []


async def test_store_lexical_search_respects_max_query_k(lexical_kb):
    """k 上限仍是 100（MAX_QUERY_K），与 dense_search 一致。"""
    _repo, store, kb_id = lexical_kb
    results = await store.lexical_search(kb_id, "公司", 500)
    assert len(results) <= 100


async def test_store_lexical_search_handles_tsquery_syntax_chars(lexical_kb):
    """H3：含查询语法字符的输入不得抛错（并发取数路径上抛错影响面更大）。"""
    _repo, store, kb_id = lexical_kb
    for query in ("研发费用 5 月", "C&C", "a:", "a | b", "!重要", "(测试)"):
        results = await store.lexical_search(kb_id, query, 10)
        assert isinstance(results, list)
