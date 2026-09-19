"""词法查询构造与转义 —— H1/H2/H3 三个静默缺陷的守卫。"""

from dataclasses import FrozenInstanceError

import pytest

from src.infra.db.lexical_query import LexicalQuery, build_lexical_query, escape_like
from src.infra.search.tokenizer import tokenize


def test_prefix_wildcard_per_token():
    """H2：每个词元必须带前缀通配，否则词形不一致时静默 0 命中。"""
    plan = build_lexical_query("营业收入增长")
    assert plan.use_substring is False
    assert plan.tsquery == " & ".join(f"{t}:*" for t in plan.terms)
    assert plan.tsquery.count(":*") == len(plan.terms)


def test_query_terms_equal_tokenize_output():
    """写入侧与查询侧必须同源：查询词元等于 tokenize 的输出（无二次加工）。"""
    query = "腾讯控股2024年全年营收6603亿元"
    plan = build_lexical_query(query)
    assert plan.terms == tuple(tokenize(query))


def test_all_single_char_query_falls_back_to_substring():
    """H1：词元全被滤掉时必须走原文子串，不得提交空条件。"""
    plan = build_lexical_query("涨了吗")
    assert plan.use_substring is True
    assert plan.tsquery == ""
    assert plan.raw == "涨了吗"

    plan = build_lexical_query("5 月")
    assert plan.use_substring is True
    assert plan.raw == "5 月"


@pytest.mark.parametrize(
    "query",
    [
        "研发费用 5 月",
        "C&C",
        "a:",
        "a | b",
        "!重要",
        "(测试)",
        "报告<->期",
        "50%-100%",
        "a_b",
    ],
)
def test_special_chars_never_raise_and_never_leak_operators(query):
    """H3：查询语法字符不得进入 tsquery，也不得抛错。"""
    plan = build_lexical_query(query)
    for op in ("&", "|", "!", "(", ")", "<->", ":"):
        if plan.use_substring:
            assert plan.tsquery == ""
            continue
        # 词元之间才允许出现 ` & `，单看词元部分不得含任何操作符
        for term in plan.terms:
            assert op not in term


def test_sanitize_drops_unsafe_token_but_keeps_chinese():
    """安全化只剔字符，不改变中文词元的可用性。"""
    plan = build_lexical_query("资产负债率")
    assert plan.use_substring is False
    assert plan.terms == ("资产负债率",)


def test_escape_like_neutralizes_wildcards():
    """F6：子串兜底的 LIKE 模式必须转义 % 与 _。"""
    assert escape_like("50%") == "50\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("c\\d") == "c\\\\d"


def test_dataclass_is_frozen():
    """查询条件是不可变值对象，避免被下游改写。"""
    plan = build_lexical_query("资产负债率")
    assert isinstance(plan, LexicalQuery)
    with pytest.raises(FrozenInstanceError):
        plan.tsquery = "x"  # pyright: ignore[reportAttributeAccessIssue]  # 故意写入以验证 frozen 抛错
