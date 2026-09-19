"""分词入口契约：单字过滤与写入/查询同源。"""

import inspect

from src.config.const import LEXICAL_MIN_TOKEN_LEN
from src.infra.search import tokenizer


def test_filters_single_char_tokens():
    """长度小于阈值的词项必须被剔除（含空格与中文标点）。"""
    tokens = tokenizer.tokenize("公司资产负债率上升，研发费用 5 月增加")
    assert "，" not in tokens
    assert " " not in tokens
    assert "5" not in tokens
    assert "月" not in tokens
    assert "公司" in tokens
    assert "资产负债率" in tokens


def test_all_single_char_query_yields_no_token():
    """全单字查询必须返回空列表 —— 这是子串兜底的触发条件。"""
    assert tokenizer.tokenize("涨了吗") == []
    assert tokenizer.tokenize("5 月") == []


def test_lexical_text_is_space_joined_tokens():
    """写入检索文本 = 词项以空格连接，且不含被过滤项。"""
    text = tokenizer.to_lexical_text("腾讯控股2024年全年营收6603亿元")
    assert text == " ".join(tokenizer.tokenize("腾讯控股2024年全年营收6603亿元"))
    assert "  " not in text
    assert not text.startswith(" ")
    assert not text.endswith(" ")


def test_threshold_comes_from_config():
    """阈值必须取自 config（不得在 tokenizer 内联字面量）。"""
    source = inspect.getsource(tokenizer)
    assert "LEXICAL_MIN_TOKEN_LEN" in source
    assert LEXICAL_MIN_TOKEN_LEN == 2
