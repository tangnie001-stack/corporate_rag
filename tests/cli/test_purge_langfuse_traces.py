"""清理 CLI 的护栏契约（D7）。全部用替身 client，不发网络。"""

from datetime import UTC, datetime, timedelta

from src.cli import purge_langfuse_traces as purge


class _FakeTrace:
    def __init__(self, trace_id: str) -> None:
        self.id = trace_id


class _FakeTraces:
    def __init__(self, ids: list[str]) -> None:
        self.data = [_FakeTrace(i) for i in ids]
        self.meta = type("M", (), {"page": 1, "total_pages": 1})()


class _FakeTraceApi:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.deleted: list[list[str]] = []
        self.list_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        # 分页语义（R2）：仅第 1 页返回数据，第 2 页起返回空页，
        # 让 _fetch_expired 能在拿到空页时正常 break，而不是反复累加同一批 id
        page = kwargs.get("page", 1)
        if page > 1:
            return _FakeTraces([])
        return _FakeTraces(self._ids)

    def delete_multiple(self, *, trace_ids):
        self.deleted.append(list(trace_ids))
        return type("R", (), {"status": "ok"})()


class _FakeApi:
    def __init__(self, trace: _FakeTraceApi) -> None:
        self.trace = trace


class _FakeClient:
    def __init__(self, ids: list[str]) -> None:
        self.api = _FakeApi(_FakeTraceApi(ids))


def test_retention_below_lower_bound_is_rejected():
    """保留期低于下界必须拒绝，且不查不删。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=0, dry_run=False, confirmed=True, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []
    assert fake.api.trace.list_calls == []


def test_unconfirmed_deletion_is_rejected():
    """非 dry-run 且未确认 → 拒绝执行。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=False, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_environment_guard_blocks_real_deletion():
    """未武装环境变量 → 拒绝真删（dry-run 不受限）。"""
    fake = _FakeClient(["t1"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=False
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_dry_run_lists_but_does_not_delete():
    """dry-run 输出待删标识且不删。"""
    fake = _FakeClient(["t1", "t2"])
    code = purge.run(
        client=fake, retention_days=30, dry_run=True, confirmed=False, allowed=False
    )
    assert code == 0
    assert fake.api.trace.deleted == []


def test_over_limit_aborts_without_deleting():
    """超过单次上限 → 中止且不删任何数据。"""
    fake = _FakeClient([f"t{i}" for i in range(purge.MAX_DELETE_PER_RUN + 1)])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code != 0
    assert fake.api.trace.deleted == []


def test_real_deletion_passes_cutoff_and_audits(capsys):
    """真删：cutoff 为 now-retention，删除被调用，且审计输出含数量。"""
    fake = _FakeClient(["t1", "t2"])
    before = datetime.now(UTC) - timedelta(days=30)
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code == 0
    assert fake.api.trace.deleted == [["t1", "t2"]]
    cutoff = fake.api.trace.list_calls[0]["to_timestamp"]
    assert abs((cutoff - before).total_seconds()) < 120
    out = capsys.readouterr().out
    assert "2" in out and "t1" in out


def test_empty_result_is_safe():
    """无超期数据 → 正常结束、不报错、不调用删除。"""
    fake = _FakeClient([])
    code = purge.run(
        client=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
    )
    assert code == 0
    assert fake.api.trace.deleted == []
