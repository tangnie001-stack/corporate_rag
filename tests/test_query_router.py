"""QueryRouter 意图路由模块的单元测试。

测试范围:
  - simple 规则匹配（财务数据查询）
  - vague 规则匹配（模糊/短查询）
  - medium 规则匹配（分析/对比类查询）
  - LLM 兜底分类的 fallback 行为
"""

from src.infra.search.query_router import QueryRouter


def test_simple_rule_hit() -> None:
    router = QueryRouter()
    assert router.route("2024年营收多少") == "simple"


def test_vague_identified() -> None:
    router = QueryRouter()
    assert router.route("净利润") == "vague"


def test_medium_rule_hit() -> None:
    router = QueryRouter()
    assert router.route("分析营收增长原因") == "medium"


def test_llm_classify_fallback() -> None:
    router = QueryRouter()
    result = router.route("这个公司业绩如何")
    assert result in ("simple", "vague", "medium", "complex")
