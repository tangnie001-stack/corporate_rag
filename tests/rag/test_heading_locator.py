"""标题段区间定位器测试。"""

from src.rag.heading_locator import (
    _locate_heading_line,
    _normalize_ws,
    build_heading_segments,
    locate_heading_path,
)


def test_build_heading_segments():
    """PDF 形态：标题行带 # 前缀，精确断言父级拼接路径与 start/end 偏移。"""
    full_text = (
        "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    )
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    assert len(segs) == 3
    # 第一个段从标题行开始，到第二个一级标题前
    assert segs[0]["start"] == 0
    assert segs[0]["path"] == "一、主要财务数据"
    assert segs[1]["path"] == "一、主要财务数据 > （一）会计数据"
    assert segs[2]["path"] == "二、股东信息"
    # 相邻段首尾相接，最后一个段延伸到全文末尾
    assert segs[0]["end"] == segs[1]["start"]
    assert segs[1]["end"] == segs[2]["start"]
    assert segs[2]["end"] == len(full_text)


def test_build_heading_segments_docx_plain_text():
    """docx 形态：标题为无 # 前缀的纯文本段落，可正常定位。"""
    full_text = "第一章 简介\n内容A\n\n第二章 详情\n内容B"
    heading_tree = [(1, "第一章 简介"), (1, "第二章 详情")]
    segs = build_heading_segments(full_text, heading_tree)
    assert len(segs) == 2
    assert segs[0]["path"] == "第一章 简介"
    assert segs[0]["start"] == 0
    assert segs[0]["end"] == segs[1]["start"]
    assert segs[1]["path"] == "第二章 详情"
    assert segs[1]["end"] == len(full_text)


def test_build_heading_segments_sibling_unwind():
    """同层退栈：[1,2,2,1] 序列下同级二级标题互不继承。"""
    full_text = (
        "# 一、总体\n内容A\n"
        "## （一）细项甲\n内容B\n"
        "## （二）细项乙\n内容C\n"
        "# 二、总体二\n内容D"
    )
    heading_tree = [
        (1, "一、总体"),
        (2, "（一）细项甲"),
        (2, "（二）细项乙"),
        (1, "二、总体二"),
    ]
    segs = build_heading_segments(full_text, heading_tree)
    assert len(segs) == 4
    assert segs[0]["path"] == "一、总体"
    assert segs[1]["path"] == "一、总体 > （一）细项甲"
    assert segs[2]["path"] == "一、总体 > （二）细项乙"
    assert segs[3]["path"] == "二、总体二"
    assert segs[2]["end"] == segs[3]["start"]


def test_build_heading_segments_empty_tree():
    """空标题树返回空列表。"""
    assert build_heading_segments("任意全文", []) == []


def test_locate_heading_path_matches():
    """正文内容命中其所属标题段。"""
    full_text = (
        "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    )
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    # 内容B 属于 （一）会计数据
    path = locate_heading_path("内容B", full_text, segs)
    assert path == "一、主要财务数据 > （一）会计数据"


def test_locate_heading_path_cross_segment():
    """content 跨两个标题段时，取起始段路径。"""
    full_text = "第一章 简介\n内容A\n\n第二章 详情\n内容B"
    heading_tree = [(1, "第一章 简介"), (1, "第二章 详情")]
    segs = build_heading_segments(full_text, heading_tree)
    path = locate_heading_path("内容A\n\n第二章 详情", full_text, segs)
    assert path == "第一章 简介"


def test_locate_heading_path_no_match():
    """空段表或内容不在全文中时返回空串。"""
    segs = []
    assert locate_heading_path("任意", "任意全文", segs) == ""


def test_normalize_ws_removes_all_whitespace():
    """_normalize_ws 去掉全部空白（含单空格与标点邻接空格）。"""
    assert (
        _normalize_ws("收入高质量增长 运营效率持续提升")
        == "收入高质量增长运营效率持续提升"
    )
    assert _normalize_ws("约 1 , 120 亿港元") == "约1,120亿港元"
    assert _normalize_ws("同比 8% ，毛利") == "同比8%，毛利"


def test_locate_heading_line_whitespace_diff():
    """单空格差异：pm 标题无空格，fitz 正文有空格，仍能定位。"""
    full_text = "收入高质量增长 运营效率持续提升\n内容A"
    assert _locate_heading_line(full_text, "收入高质量增长运营效率持续提升") == 0


def test_locate_heading_line_emphasis_punct_diff():
    """强调标点邻接空格：'约 1 , 120 亿港元' vs '约 1,120 亿港元' 仍能定位。"""
    full_text = "回购增长逾倍至约 1,120 亿港元\n内容A"
    assert _locate_heading_line(full_text, "回购增长逾倍至约 1 , 120 亿港元") == 0


def test_locate_heading_line_table_row_not_matched():
    """表格内 |...| 包裹的标题不作为独立标题行定位（接受缺口）。"""
    full_text = "| 其他财务资料 | 100 | 200 |\n内容A"
    assert _locate_heading_line(full_text, "其他财务资料") == -1


def test_locate_heading_line_truncated_not_matched():
    """截断标题（标题是正文子串但整行不相等）不匹配。"""
    full_text = "总收入：同比增长 8% 至 5,835 亿港元\n内容A"
    assert _locate_heading_line(full_text, "总收入：同比增长 8%") == -1
