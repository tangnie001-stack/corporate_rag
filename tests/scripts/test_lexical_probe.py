"""探针的纯函数：词项派生与命中（不连库）。

fixture 规模 N=40 ⇒ 高频上界 `upper = int(0.1 × 40) = 4`，保留带为 `2 <= df <= 4`。
每条 doc 由**空格分隔的 2 字 CJK 词项**组成：CJK 连续段恰好 2 字，n-gram 只产出
该词项本身（3/4 元无对应滑动窗口），因此候选词项集合是显式可控的，三类词项
（df=1 / 2<=df<=upper / df>upper）互不混淆。
"""

from scripts.lexical_probe import derive_terms, hit_rate

# doc0..doc4 逐级退场造出三档 df；doc5..doc39 只含「资产」，用于把「资产」顶到
# df=N 的平凡通过档，同时把 N 撑到 40。
_HEAD = [
    "孤本 净利 营收 投资 资产 负债",  # df: 孤本=1 净利=3 营收=2 投资=4 资产=40 负债=5
    "净利 营收 投资 资产 负债",
    "净利 投资 资产 负债",
    "投资 资产 负债",
    "资产 负债",
]


def _corpus() -> list[str]:
    """构造 N=40 的确定性语料。"""
    docs = list(_HEAD)
    while len(docs) < 40:
        docs.append("资产")
    return docs


def test_derive_terms_keeps_exactly_the_mid_frequency_terms():
    """保留集合恰等于预期：df=1 与 df>upper 都被剔除，中间档保留。"""
    docs = _corpus()
    assert len(docs) == 40
    terms = derive_terms(docs, max_terms=50)
    # upper = int(0.1 × 40) = 4：
    #   孤本 df=1  < DF_MIN=2      → 剔除（无从判断召回）
    #   营收 df=2  = DF_MIN        → 保留
    #   净利 df=3                  → 保留
    #   投资 df=4  = upper         → 保留
    #   负债 df=5  > upper         → 剔除（平凡通过）
    #   资产 df=40 > upper         → 剔除（平凡通过）
    assert terms == ["营收", "净利", "投资"]


def test_derive_terms_separates_the_three_df_classes():
    """三类词项逐一断言，避免「某词项不在」在空集上真空成立。"""
    terms = set(derive_terms(_corpus(), max_terms=50))
    assert len(terms) > 0
    assert "孤本" not in terms  # df=1 < DF_MIN
    assert "营收" in terms  # df=2 = DF_MIN（下边界保留）
    assert "投资" in terms  # df=4 = upper（上边界保留）
    assert "负债" not in terms  # df=5 = upper + 1
    assert "资产" not in terms  # df=40 >> upper
    assert all(len(term) >= 2 for term in terms)


def test_derive_terms_is_deterministic_on_non_empty_result():
    """同一语料两次派生必须完全一致（报告可复现），且结果非空。"""
    docs = _corpus()
    first = derive_terms(docs, 10)
    second = derive_terms(docs, 10)
    assert first == ["营收", "净利", "投资"]
    assert first == second


def test_hit_rate_uses_raw_containment():
    """命中判据是原始正文的字符串包含，与分词器无关。"""
    assert hit_rate(["公司资产负债率上升", "无关内容"], "资产负债率") is True
    assert hit_rate(["公司资产负债率上升"], "营业收入") is False
    assert hit_rate([], "营业收入") is False
