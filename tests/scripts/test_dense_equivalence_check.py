"""等价性比对的纯函数：top-k 重合率。"""

from scripts.dense_equivalence_check import overlap_ratio


def test_overlap_ratio_identical_is_one():
    assert overlap_ratio(["a", "b", "c"], ["a", "b", "c"], 3) == 1.0


def test_overlap_ratio_uses_intersection_over_k():
    # 交集 {a,b} / k=3
    assert abs(overlap_ratio(["a", "b", "c"], ["a", "b", "z"], 3) - 2 / 3) < 1e-9


def test_overlap_ratio_empty_is_zero():
    assert overlap_ratio([], ["a"], 3) == 0.0
