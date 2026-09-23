"""清理 CLI 的护栏契约（D7）。全部用替身 backend，不发网络 / DB 连接。"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from src.cli import purge_langfuse_traces as purge
from src.infra.llm import langfuse_purge
from src.infra.llm.langfuse_purge import (
    CascadeCounts,
    ExpiredTrace,
    LangfuseProjectScopeError,
)


class _FakeBackend:
    """实现 PurgeBackend 窄接口的替身；记录调用、可按需注入异常。"""

    def __init__(
        self,
        ids: list[str],
        *,
        session_id: str | None = "s1",
        project_error: bool = False,
        list_error: Exception | None = None,
        delete_error: Exception | None = None,
        counts: CascadeCounts | None = None,
    ) -> None:
        self._traces = [ExpiredTrace(id=i, session_id=session_id) for i in ids]
        self._project_error = project_error
        self._list_error = list_error
        self._delete_error = delete_error
        self._counts = counts
        self.list_calls: list[tuple[datetime, int]] = []
        self.deleted: list[list[ExpiredTrace]] = []

    async def list_expired(self, cutoff: datetime, limit: int) -> list[ExpiredTrace]:
        self.list_calls.append((cutoff, limit))
        if self._project_error:
            raise LangfuseProjectScopeError(2)
        if self._list_error is not None:
            raise self._list_error
        return list(self._traces[:limit])

    async def delete(self, traces: list[ExpiredTrace]) -> CascadeCounts:
        self.deleted.append(list(traces))
        if self._delete_error is not None:
            raise self._delete_error
        if self._counts is not None:
            return self._counts
        return CascadeCounts(
            observations=len(traces),
            scores=0,
            trace_media=0,
            observation_media=0,
            traces=len(traces),
            sessions=1,
        )


def test_retention_below_lower_bound_is_rejected():
    """保留期低于下界必须拒绝，且不查不删。"""
    fake = _FakeBackend(["t1"])
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=0, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code != 0
    assert fake.deleted == []
    assert fake.list_calls == []


def test_unconfirmed_deletion_is_rejected():
    """非 dry-run 且未确认 → 拒绝执行，且不查不删。"""
    fake = _FakeBackend(["t1"])
    code = asyncio.run(
        purge.run(
            backend=fake,
            retention_days=30,
            dry_run=False,
            confirmed=False,
            allowed=True,
        )
    )
    assert code != 0
    assert fake.deleted == []
    assert fake.list_calls == []


def test_environment_guard_blocks_real_deletion():
    """未武装环境变量 → 拒绝真删（dry-run 不受限），且不查不删。"""
    fake = _FakeBackend(["t1"])
    code = asyncio.run(
        purge.run(
            backend=fake,
            retention_days=30,
            dry_run=False,
            confirmed=True,
            allowed=False,
        )
    )
    assert code != 0
    assert fake.deleted == []
    assert fake.list_calls == []


def test_dry_run_lists_but_does_not_delete(capsys):
    """dry-run 输出待删标识且不删。"""
    fake = _FakeBackend(["t1", "t2"])
    code = asyncio.run(
        purge.run(
            backend=fake,
            retention_days=30,
            dry_run=True,
            confirmed=False,
            allowed=False,
        )
    )
    assert code == 0
    assert fake.deleted == []
    out = capsys.readouterr().out
    assert "t1" in out and "t2" in out


def test_over_limit_aborts_without_deleting():
    """超过单次上限 → 中止且不删任何数据。"""
    fake = _FakeBackend([f"t{i}" for i in range(purge.MAX_DELETE_PER_RUN + 1)])
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code != 0
    assert fake.deleted == []


def test_real_deletion_passes_cutoff_and_audits(capsys):
    """真删：cutoff 为 now-retention，删除被调用，且审计输出含数量。"""
    fake = _FakeBackend(["t1", "t2"])
    before = datetime.now(UTC) - timedelta(days=30)
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 0
    assert len(fake.deleted) == 1
    assert [t.id for t in fake.deleted[0]] == ["t1", "t2"]
    cutoff = fake.list_calls[0][0]
    assert abs((cutoff - before.replace(tzinfo=None)).total_seconds()) < 120
    out = capsys.readouterr().out
    assert "命中=2" in out and "t1" in out


def test_empty_result_is_safe():
    """无超期数据 → 正常结束、不报错、不调用删除。"""
    fake = _FakeBackend([])
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 0
    assert fake.deleted == []


def test_cascade_delete_order_contract():
    """表序契约：子表先于主表，顺序与 ADR-0013 决策 3 一致（防后人加表/改序）。"""
    assert langfuse_purge.CASCADE_DELETE_ORDER == (
        "observations",
        "scores",
        "trace_media",
        "observation_media",
        "traces",
    )


def test_cutoff_is_naive_utc():
    """传给后端的 cutoff 必须是 naive UTC，且贴合 now(UTC)-retention。"""
    fake = _FakeBackend([])
    before = datetime.now(UTC) - timedelta(days=30)
    code = asyncio.run(
        purge.run(
            backend=fake,
            retention_days=30,
            dry_run=True,
            confirmed=False,
            allowed=False,
        )
    )
    assert code == 0
    cutoff, limit = fake.list_calls[0]
    assert cutoff.tzinfo is None
    assert abs((cutoff - before.replace(tzinfo=None)).total_seconds()) < 120
    assert limit == purge.MAX_DELETE_PER_RUN + 1


def test_audit_reports_cascade_counts(capsys):
    """真删后审计输出含逐表级联计数与空 session 清理数。"""
    fake = _FakeBackend(
        ["t1", "t2"],
        counts=CascadeCounts(
            observations=3,
            scores=0,
            trace_media=0,
            observation_media=0,
            traces=2,
            sessions=1,
        ),
    )
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "observations=3" in out
    assert "traces=2" in out
    assert "sessions=1" in out


def test_project_count_not_one_is_rejected(capsys):
    """库内 project 数 ≠ 1 → 拒绝执行、exit 2，且不删。"""
    fake = _FakeBackend(["t1"], project_error=True)
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 2
    assert fake.deleted == []
    err = capsys.readouterr().err
    assert "[error]" in err


def test_db_error_exits_one_with_single_error_line(capsys):
    """DB 不可达 / SQL 报错 → 退出码 1，stderr 恰好一行 [error]，不裸抛 traceback。"""
    fake = _FakeBackend(["t1"], list_error=ConnectionRefusedError("db unreachable"))
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 1
    assert fake.deleted == []
    err = capsys.readouterr().err
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("[error]")


def test_delete_failure_exits_one_with_single_error_line(capsys):
    """删除路径 DB 报错 → 退出码 1，stderr 恰好一行 [error]，不裸抛 traceback。"""
    fake = _FakeBackend(["t1"], delete_error=SQLAlchemyError("delete boom"))
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 1
    assert len(fake.deleted) == 1
    err = capsys.readouterr().err
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("[error]")


def test_delete_path_project_scope_refusal_exits_two(capsys):
    """delete 路径的项目作用域拒绝与 list 路径一致：exit 2、恰好一行 [error]、不裸抛。"""
    fake = _FakeBackend(["t1"], delete_error=LangfuseProjectScopeError(2))
    code = asyncio.run(
        purge.run(
            backend=fake, retention_days=30, dry_run=False, confirmed=True, allowed=True
        )
    )
    assert code == 2
    assert len(fake.deleted) == 1
    err = capsys.readouterr().err
    lines = err.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("[error]")


class _FakeResult:
    """`execute()` 的最小结果替身：同时提供 `rowcount` 与 `scalar_one`。"""

    def __init__(self, *, rowcount: int = 1, scalar: object = 1) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one(self) -> object:
        """返回预设的标量（模拟 `SELECT count(*)` / `SELECT id`）。"""
        return self._scalar


class _RecordingConnection:
    """记录 `execute()` 的 SQL 与绑定参数，并按语句返回替身结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, params: dict[str, object] | None = None
    ) -> _FakeResult:
        """记录一次执行并返回结果；project 守卫语句返回单 project。"""
        sql = str(statement)
        recorded: dict[str, object] = {}
        if params is not None:
            recorded = params
        self.calls.append((sql, recorded))
        if "count(*)" in sql:
            return _FakeResult(scalar=1)
        if "SELECT id FROM projects" in sql:
            return _FakeResult(scalar="proj-1")
        return _FakeResult(rowcount=1)

    def sessions_params(self) -> list[dict[str, object]]:
        """返回所有绑定 `:sessions` 的调用参数。"""
        return [params for sql, params in self.calls if ":sessions" in sql]


class _ConnectionContext:
    """`async with engine.begin()` 的异步上下文替身。"""

    def __init__(self, conn: _RecordingConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _RecordingConnection:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _RecordingEngine:
    """`engine.begin()` 的替身：始终交出同一个记录型连接。"""

    def __init__(self, conn: _RecordingConnection) -> None:
        self._conn = conn

    def begin(self) -> _ConnectionContext:
        """返回异步上下文替身。"""
        return _ConnectionContext(self._conn)


def test_delete_drops_none_session_before_binding():
    """`session_id=None` 的 trace 不得进入 `:sessions` 绑定参数。"""
    conn = _RecordingConnection()
    engine = _RecordingEngine(conn)
    with patch.object(langfuse_purge, "create_async_engine", return_value=engine):
        backend = langfuse_purge.LangfuseSqlPurgeBackend(
            dsn="postgresql+asyncpg://u:p@localhost:5432/langfuse"
        )
    asyncio.run(
        backend.delete(
            [
                ExpiredTrace(id="t1", session_id="s1"),
                ExpiredTrace(id="t2", session_id=None),
            ]
        )
    )
    sessions_params = conn.sessions_params()
    assert len(sessions_params) == 1
    assert sessions_params[0]["sessions"] == ["s1"]
