"""探针的纯函数：词项派生与命中共 2 项。"""

from scripts.lexical_probe import derive_terms, hit_rate


def test_derive_terms_excludes_df_one_and_high_frequency():
    """df 筛选：df=1 无从判断召回、高频词平凡通过，二者都要剔除。"""
    docs = [
        "资产负债率 资产负债率 净利润",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
        "资产负债率 净利润 营业收入",
    ]
    terms = derive_terms(docs, max_terms=50)
    assert "资产负债率" not in terms  # df = 11 > 0.1 * 11 → 高频，剔除
    assert "净利润" not in terms  # df = 11 → 同上，剔除
    assert all(len(t) >= 2 for t in terms)


def test_derive_terms_is_deterministic():
    """同一语料两次派生必须完全一致（报告可复现）。"""
    docs = ["营业收入 同比 增长", "资产负债率 上升", "研发 费用 增加"]
    assert derive_terms(docs, 10) == derive_terms(docs, 10)


def test_hit_rate_uses_raw_containment():
    """命中判据是原始正文的字符串包含，与分词器无关。"""
    assert hit_rate(["公司资产负债率上升", "无关内容"], "资产负债率") is True
    assert hit_rate(["公司资产负债率上升"], "营业收入") is False
    assert hit_rate([], "营业收入") is False
