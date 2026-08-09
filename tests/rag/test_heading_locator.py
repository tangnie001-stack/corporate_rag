"""标题段区间定位器测试。"""
from src.rag.heading_locator import build_heading_segments, locate_heading_path


def test_build_heading_segments():
    full_text = "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    assert len(segs) == 3
    # 第一个段从标题行开始，到第二个一级标题前
    assert segs[0]["start"] == 0
    assert segs[1]["path"] == "一、主要财务数据 > （一）会计数据" or segs[1]["path"] == "（一）会计数据"


def test_locate_heading_path_matches():
    full_text = "# 一、主要财务数据\n内容A\n## （一）会计数据\n内容B\n# 二、股东信息\n内容C"
    heading_tree = [(1, "一、主要财务数据"), (2, "（一）会计数据"), (1, "二、股东信息")]
    segs = build_heading_segments(full_text, heading_tree)
    # 内容B 属于 （一）会计数据
    path = locate_heading_path("内容B", full_text, segs)
    assert path != ""


def test_locate_heading_path_no_match():
    segs = []
    assert locate_heading_path("任意", "任意全文", segs) == ""
