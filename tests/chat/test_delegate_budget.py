"""会话级委派预算：进程内计数、TTL 惰性清理、显式复位。"""

from src.chat.delegate_budget import SessionDelegateBudget


def test_under_limit_allows_and_counts():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 2) is True
    assert budget.check_and_incr("s1", 2) is True
    assert budget.used("s1") == 2


def test_over_limit_denies_without_increment():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 1) is True
    assert budget.check_and_incr("s1", 1) is False
    assert budget.used("s1") == 1


def test_sessions_are_isolated():
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 1) is True
    assert budget.check_and_incr("s2", 1) is True


def test_reset_clears_session():
    budget = SessionDelegateBudget()
    budget.check_and_incr("s1", 1)
    budget.reset("s1")
    assert budget.used("s1") == 0
    assert budget.check_and_incr("s1", 1) is True


def test_sweep_expired_drops_stale_sessions(monkeypatch):
    """条目超 TTL 未计数 → 惰性清理整条删除。

    用可控时钟（先固定、再前进超过 TTL）验证过期语义：单点绝对时间戳
    会随真实当前时间漂移，无法稳定触发清理。
    """
    budget = SessionDelegateBudget(ttl_seconds=10)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr("src.chat.delegate_budget.time.time", lambda: clock["now"])
    budget.check_and_incr("s1", 5)
    clock["now"] += 11  # 前进超过 TTL
    budget.sweep_expired()
    assert budget.used("s1") == 0


def test_check_does_not_rollback_on_failure():
    """已发起即计数：没有回滚接口（design D10，取消/异常不回滚）。"""
    budget = SessionDelegateBudget()
    assert not hasattr(budget, "decrement")


def test_zero_limit_denies_immediately():
    """limit=0 → 一次都不放行（边界：0 额度是合法配置，非"无上限"）。"""
    budget = SessionDelegateBudget()
    assert budget.check_and_incr("s1", 0) is False
    assert budget.used("s1") == 0
