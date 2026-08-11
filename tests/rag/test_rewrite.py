import json
from unittest.mock import MagicMock

from src.infra.search.query_router import _llm_rewrite


def _resp(content, pt=10, ct=5):
    r = MagicMock()
    r.content = content
    r.response_metadata = {
        "token_usage": {"prompt_tokens": pt, "completion_tokens": ct}
    }
    return r


def test_llm_rewrite_medium_success():
    llm = MagicMock()
    llm.invoke.return_value = _resp(
        json.dumps({"standalone_query": "腾讯2024年毛利率是多少"})
    )
    result, pt, _ = _llm_rewrite("毛利率呢", [], "medium", llm)
    assert result == ["腾讯2024年毛利率是多少"]
    assert pt == 10


def test_llm_rewrite_complex_sub_queries():
    llm = MagicMock()
    llm.invoke.return_value = _resp(
        json.dumps({"sub_queries": ["腾讯利润", "东软利润"]})
    )
    result, _, _ = _llm_rewrite("对比腾讯东软利润", [], "complex", llm)
    assert result == ["腾讯利润", "东软利润"]


def test_llm_rewrite_fallback_on_bad_json():
    llm = MagicMock()
    llm.invoke.return_value = _resp("{bad json")
    result, _, _ = _llm_rewrite("对比腾讯东软利润", [], "complex", llm)
    assert isinstance(result, list) and result  # 回退到规则 decompose
