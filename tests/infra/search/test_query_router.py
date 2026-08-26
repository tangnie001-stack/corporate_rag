"""QueryRouter — 三层意图路由模块的单元测试。"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.agents.graph.state import LangGraphNode
from src.infra.search.query_router import (
    KbEntityAggregate,
    QueryRouter,
    aggregate_kb_entities,
)


def test_l0_greeting_returns_simple() -> None:
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()
    result = router.route("你好", history=[])
    assert result[LangGraphNode.Classify.INTENT]["route"] == "simple"
    router._llm_classify.assert_not_called()


def test_l0_short_query_returns_simple() -> None:
    """≤2 字符的业务查询（如"营收"）走 simple 路由，但不跳过检索。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()
    result = router.route("营收", history=[])
    assert result[LangGraphNode.Classify.INTENT]["route"] == "simple"
    assert result[LangGraphNode.Classify.SKIP_RETRIEVAL] is False
    router._llm_classify.assert_not_called()


def test_entity_extraction_included() -> None:
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={"route": "medium", "missing_entities": [], "confidence": 0.9}
    )
    result = router.route("2024年营收多少", history=[])
    assert len(result[LangGraphNode.Classify.EXTRACTED_ENTITIES]) > 0


def test_missing_entity_triggers() -> None:
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={
            "route": "medium",
            "missing_entities": [{"type": "year", "question": "哪一年？"}],
            "confidence": 0.85,
        }
    )
    result = router.route("营收多少", history=[])
    assert len(result[LangGraphNode.Classify.MISSING_ENTITIES]) > 0


def test_no_year_triggers_missing() -> None:
    """不含年份的查询应触发 missing_entities。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={
            "route": "medium",
            "missing_entities": [
                {"type": "year", "question": "请问您想查询哪一年的数据？"}
            ],
            "confidence": 0.85,
        }
    )
    result = router.route("营收多少", history=[])
    assert len(result[LangGraphNode.Classify.MISSING_ENTITIES]) == 1
    assert result[LangGraphNode.Classify.MISSING_ENTITIES][0]["type"] == "year"


def test_history_resolves_entity() -> None:
    """历史对话中的已提及实体不应再出现在 missing_entities。"""
    from src.infra.llm.chat_message import ChatMessage

    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={
            "route": "medium",
            "missing_entities": [],
            "confidence": 0.92,
        }
    )
    history = [
        ChatMessage(role="user", content="2024年营收多少"),
        ChatMessage(role="assistant", content="2024年营收为100亿"),
    ]
    result = router.route("利润率呢", history=history)
    assert len(result[LangGraphNode.Classify.MISSING_ENTITIES]) == 0


def test_cache_hits() -> None:
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={"route": "medium", "missing_entities": [], "confidence": 0.9}
    )
    router.route("2024年营收", [])
    router.route("2024年营收", [])
    assert router._llm_classify.call_count == 1


def test_greeting_sets_skip_retrieval() -> None:
    """问候查询应设置 skip_retrieval=True。"""
    from unittest.mock import Mock

    from src.infra.search.query_router import QueryRouter

    router = QueryRouter(llm=Mock())
    result = router.route("你好", [])
    assert result[LangGraphNode.Classify.SKIP_RETRIEVAL] is True


def test_normal_query_not_skip_retrieval() -> None:
    """普通查询应设置 skip_retrieval=False。"""
    from src.infra.search.query_router import QueryRouter

    router = QueryRouter(llm=None)
    result = router.route("2024年营收多少", [])
    assert result[LangGraphNode.Classify.SKIP_RETRIEVAL] is False


def test_route_passes_kb_entities_to_llm_classify() -> None:
    """route 应将 kb_entities 参数透传给 _llm_classify（末位位置参数）。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock(
        return_value={"route": "medium", "missing_entities": [], "confidence": 0.9}
    )
    router.route("营收多少", history=[], kb_entities="公司: 腾讯、阿里巴巴")
    assert router._llm_classify.call_args.args[-1] == "公司: 腾讯、阿里巴巴"


def test_kb_entity_aggregate_to_suggestions() -> None:
    """to_suggestions 应为有候选的类型生成建议映射并追加"其他"。"""
    agg = KbEntityAggregate(text="公司: 腾讯", companies=["腾讯"], periods=[], codes=[])
    assert agg.to_suggestions() == {"company": ["腾讯", "其他"]}


def test_kb_entity_aggregate_to_suggestions_empty() -> None:
    """无候选时 to_suggestions 应返回空映射。"""
    agg = KbEntityAggregate(text="无", companies=[], periods=[], codes=[])
    assert agg.to_suggestions() == {}


@pytest.mark.asyncio
async def test_aggregate_kb_entities_empty_returns_no() -> None:
    """kb_ids 为空/None 时聚合应返回 text="无" 且不查库。"""
    agg = await aggregate_kb_entities(None)
    assert agg.text == "无"
    assert agg.companies == []
    assert agg.periods == []
    assert agg.codes == []


@pytest.mark.asyncio
async def test_aggregate_kb_entities_collects_from_meta_info() -> None:
    """aggregate_kb_entities 应从多文档 meta_info 去重聚合候选实体并格式化。"""
    doc1 = MagicMock()
    doc1.meta_info = json.dumps(
        {
            "entities": {
                "company": "腾讯",
                "report_period": "2025年第一季度",
                "sec_code": "00700",
            }
        }
    )
    doc2 = MagicMock()
    doc2.meta_info = json.dumps(
        {"entities": {"company": "阿里巴巴", "report_period": "2024年第四季度"}}
    )
    doc3 = MagicMock()
    doc3.meta_info = None  # 无 meta_info 文档应被跳过

    repo = AsyncMock()
    repo.get_documents.return_value = [doc1, doc2, doc3]

    with (
        patch("src.infra.db.engine.session_factory", MagicMock()),
        patch("src.infra.db.mysql_db.DocumentRepo", return_value=repo),
    ):
        agg = await aggregate_kb_entities(["kb-1", "kb-2"])

    assert agg.companies == ["腾讯", "阿里巴巴"]
    assert agg.periods == ["2024年第四季度", "2025年第一季度"]
    assert agg.codes == ["00700"]
    assert "公司: 腾讯、阿里巴巴" in agg.text
    assert "报告期: 2024年第四季度、2025年第一季度" in agg.text
    assert "代码: 00700" in agg.text
    assert repo.get_documents.call_count == 2


def test_needs_kb_entities_short_circuits() -> None:
    """needs_kb_entities 应与 route 的短路条件一致（空/问候/≤2 字符不需要候选）。"""
    from src.infra.search.query_router import needs_kb_entities

    assert needs_kb_entities("") is False
    assert needs_kb_entities("你好") is False
    assert needs_kb_entities("hi") is False
    assert needs_kb_entities("营收") is False  # ≤2 字符
    assert needs_kb_entities("2024年营收多少") is True


@pytest.mark.asyncio
async def test_aggregate_kb_entities_degrades_on_db_error() -> None:
    """DB 查询失败时聚合应降级为空聚合，不向上抛异常。"""
    repo = AsyncMock()
    repo.get_documents.side_effect = RuntimeError("db down")

    with (
        patch("src.infra.db.engine.session_factory", MagicMock()),
        patch("src.infra.db.mysql_db.DocumentRepo", return_value=repo),
    ):
        agg = await aggregate_kb_entities(["kb-1"])

    assert agg.text == "无"
    assert agg.companies == []
    assert agg.periods == []
    assert agg.codes == []
    assert agg.to_suggestions() == {}


@pytest.mark.asyncio
async def test_aggregate_kb_entities_skips_bad_meta_info() -> None:
    """meta_info 损坏/entities 非 dict 的文档应被跳过，其余文档照常聚合。"""
    bad_doc = MagicMock()
    bad_doc.meta_info = "{invalid json"
    non_dict_doc = MagicMock()
    non_dict_doc.meta_info = json.dumps({"entities": ["x"]})
    list_doc = MagicMock()
    list_doc.meta_info = json.dumps({"entities": {"company": ["腾讯", "腾讯音乐"]}})
    good_doc = MagicMock()
    good_doc.meta_info = json.dumps({"entities": {"company": "阿里巴巴"}})

    repo = AsyncMock()
    repo.get_documents.return_value = [bad_doc, non_dict_doc, list_doc, good_doc]

    with (
        patch("src.infra.db.engine.session_factory", MagicMock()),
        patch("src.infra.db.mysql_db.DocumentRepo", return_value=repo),
    ):
        agg = await aggregate_kb_entities(["kb-1"])

    assert agg.companies == ["腾讯、腾讯音乐", "阿里巴巴"]
    assert "公司: 腾讯、腾讯音乐、阿里巴巴" in agg.text
    assert repo.get_documents.call_count == 1
