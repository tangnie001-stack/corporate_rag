"""有界 TTL 映射：TTL 淘汰、容量淘汰、去重语义（design D10/D14）。"""

from src.channels.wecom.bounded_map import BoundedTtlMap


def _clock():
    """可控时钟：返回 (取当前值, 前进) 两个可调用对象。"""
    now = {"t": 1000.0}

    def read() -> float:
        return now["t"]

    def advance(seconds: float) -> None:
        now["t"] += seconds

    return read, advance


def test_mark_if_new_reports_first_sight_only():
    read, _advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    assert table.mark_if_new("M1", "") is True
    assert table.mark_if_new("M1", "") is False
    assert table.mark_if_new("M2", "") is True


def test_mark_if_new_hit_does_not_renew_ttl():
    """命中不续期：窗口自首次写入起算，重推不得延长它（D10 的有界性来源）。"""
    read, advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    assert table.mark_if_new("M1", "") is True
    # 窗口内反复重推：每次都应是"已见过"，且累计时间越过 ttl 后必须重新视为新消息
    advance(60.0)
    assert table.mark_if_new("M1", "") is False
    advance(39.0)
    assert (
        table.mark_if_new("M1", "") is False
    )  # 累计 99s，若命中续期则此处仍会 False、但下面会红
    advance(2.0)
    assert table.mark_if_new("M1", "") is True  # 累计 101s > ttl → 已过期


def test_ttl_eviction_allows_reprocessing_after_window():
    read, advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    assert table.mark_if_new("M1", "") is True
    advance(101.0)
    assert table.mark_if_new("M1", "") is True  # 过期后视为新消息


def test_capacity_eviction_drops_oldest():
    table = BoundedTtlMap(capacity=2, ttl_seconds=1000.0, monotonic=lambda: 1000.0)

    table.mark_if_new("M1", "")
    table.mark_if_new("M2", "")
    table.mark_if_new("M3", "")

    assert len(table) == 2
    assert table.mark_if_new("M1", "") is True  # 最旧已被淘汰


def test_put_get_roundtrip_and_expiry():
    read, advance = _clock()
    table = BoundedTtlMap(capacity=10, ttl_seconds=100.0, monotonic=read)

    table.put("S1", "U1")
    assert table.get("S1") == "U1"
    advance(101.0)
    assert table.get("S1") is None


def test_stays_bounded_under_many_writes():
    table = BoundedTtlMap(capacity=5, ttl_seconds=10_000.0, monotonic=lambda: 0.0)

    for index in range(50):
        table.put(f"K{index}", "V")

    assert len(table) == 5
