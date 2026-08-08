"""QueryRouter — 三层意图路由模块的单元测试。"""

from unittest.mock import Mock

from src.agents.graph.state import LangGraphNode
from src.infra.search.query_router import QueryRouter


def test_l0_greeting_returns_simple() -> None:
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()
    result = router.route("你好", history=[])
    assert result[LangGraphNode.Classify.INTENT].route == "simple"
    router._llm_classify.assert_not_called()


def test_l0_short_query_returns_simple() -> None:
    """≤2 字符的业务查询（如"营收"）走 simple 路由，但不跳过检索。"""
    router = QueryRouter(llm=Mock())
    router._llm_classify = Mock()
    result = router.route("营收", history=[])
    assert result[LangGraphNode.Classify.INTENT].route == "simple"
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
