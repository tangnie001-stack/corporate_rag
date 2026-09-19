"""存量重写脚本的纯逻辑（不连库）。"""

from scripts.rewrite_content_seg import is_stale


def test_stale_when_raw_text():
    """P2 的正文占位必须被判定为过期。"""
    assert (
        is_stale(
            "公司资产负债率上升，研发费用增加。", "公司资产负债率上升，研发费用增加。"
        )
        is True
    )


def test_fresh_when_tokenized():
    """分词输出必须被判定为最新。"""
    content = "公司资产负债率上升，研发费用增加。"
    seg = "公司 资产负债率 上升 研发 费用 增加"
    assert is_stale(content, seg) is False


def test_stale_when_tokenizer_changed():
    """分词口径变更（词典升级）后存量必须被判过期。"""
    assert (
        is_stale("营业收入同比增长率保持稳定", "营业收入 同比 增长 率 保持 稳定")
        is True
    )
