"""时间解析模块（temporal）单元测试。

外部依赖（DocumentRepo.get_documents）通过 monkeypatch mock，不发真实 DB 查询。
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.infra.db.mysql_db.document_repo import DocumentRepo
from src.rag.temporal import (
    compute_missing,
    derive_candidate_years,
    has_temporal_words,
    parse_temporal,
)


def test_has_temporal_words_hit():
    """含相对时间词的查询应命中粗筛。"""
    assert has_temporal_words("腾讯这几年业绩怎么样") is True
    assert has_temporal_words("近三年营收") is True


def test_has_temporal_words_miss():
    """绝对年份或无时间词的查询不应命中粗筛。"""
    assert has_temporal_words("腾讯 2024 年营收多少") is False
    assert has_temporal_words("腾讯营收构成") is False


def test_compute_missing():
    """要求年份中未被知识库覆盖的年份应被列出，已覆盖的剔除。"""
    assert compute_missing([2023, 2024, 2025], [2024]) == [2023, 2025]
    assert compute_missing([2024], [2024]) == []


def _doc(meta_info: str):
    """构造 meta_info 为给定 JSON 字符串的 mock DocModel（仅用 kb_id/filename/meta_info）。"""
    from src.infra.db.models.document import DocModel

    doc = DocModel(kb_id="kb1", filename="a.pdf")
    doc.meta_info = meta_info
    return doc


def _recent_years(n: int = 3) -> list[int]:
    """最近 n 个完整年度（排除进行中的当年），与实现同口径。"""
    this_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    return [this_year - i for i in range(1, n + 1)]


@pytest.mark.asyncio
async def test_derive_candidates_include_recent_years(monkeypatch):
    """KB 只覆盖 2024 → 候选 = {2024} ∪ 最近 3 个完整年度（排除进行中的当年）。"""

    async def fake_get_documents(self, kb_id):
        """mock DocumentRepo.get_documents（self 为 repo 实例）：返回 meta_info 含 year=2024 的文档。"""
        return [_doc(json.dumps({"entities": {"year": "2024"}}))]

    monkeypatch.setattr(DocumentRepo, "get_documents", fake_get_documents)

    candidates = await derive_candidate_years(["kb1"])

    assert 2024 in candidates
    for y in _recent_years():
        assert y in candidates
    this_year = datetime.now(ZoneInfo("Asia/Shanghai")).year
    assert this_year not in candidates
    assert candidates == sorted({2024, *_recent_years()})


@pytest.mark.asyncio
async def test_derive_candidates_from_report_period(monkeypatch):
    """report_period 实体里的年份应被提取进候选。"""

    async def fake_get_documents(self, kb_id):
        """mock DocumentRepo.get_documents（self 为 repo 实例）：返回 meta_info 含 report_period 的文档。"""
        return [_doc(json.dumps({"entities": {"report_period": "2025年第一季度"}}))]

    monkeypatch.setattr(DocumentRepo, "get_documents", fake_get_documents)

    candidates = await derive_candidate_years(["kb1"])
    assert 2025 in candidates


@pytest.mark.asyncio
async def test_derive_candidates_empty_kb_returns_recent_years(monkeypatch):
    """KB 为空时仍返回最近 3 个完整年度（供"这几年"触发联网询问）。"""

    async def fake_get_documents(self, kb_id):
        """mock DocumentRepo.get_documents（self 为 repo 实例）：返回空列表。"""
        return []

    monkeypatch.setattr(DocumentRepo, "get_documents", fake_get_documents)

    candidates = await derive_candidate_years(["kb1"])
    assert candidates == sorted(_recent_years())


@pytest.mark.asyncio
async def test_parse_temporal_candidates_only(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            return type("R", (), {"content": '{"years": [2023, 2024, 2025]}'})()

    result = await parse_temporal("这几年", [2022, 2023, 2024, 2025], FakeLLM())
    assert result["years"] == [2023, 2024, 2025]
    assert result["has_temporal"] is True


@pytest.mark.asyncio
async def test_parse_temporal_out_of_candidate_rejected(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            return type("R", (), {"content": '{"years": [2019, 2024]}'})()

    result = await parse_temporal("这几年", [2022, 2023, 2024, 2025], FakeLLM())
    assert 2019 not in result["years"]


@pytest.mark.asyncio
async def test_parse_temporal_fallback(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("llm down")

    candidates = [2022, 2023, 2024, 2025]
    result = await parse_temporal("这几年", candidates, FakeLLM())
    assert result["years"] == sorted(y for y in _recent_years() if y in candidates)
    assert result["has_temporal"] is True


@pytest.mark.asyncio
async def test_parse_temporal_empty_output_no_constraint(monkeypatch):
    """LLM 正常返回空 years（判定无时间约束）→ has_temporal=False，不强加最近 N 年。"""

    class FakeLLM:
        async def ainvoke(self, messages, **kwargs):
            return type("R", (), {"content": '{"years": []}'})()

    result = await parse_temporal("这几年", [2022, 2023, 2024, 2025], FakeLLM())
    assert result["years"] == []
    assert result["has_temporal"] is False
