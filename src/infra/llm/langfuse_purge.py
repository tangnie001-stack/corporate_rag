"""Langfuse trace 保留期清理的 SQL 后端 —— 直连 `langfuse` 库按序级联删除。

**为什么直连 PG**：Langfuse v2（本项目 pin 2.95.11）没有公开删除 API
（`DELETE /api/public/traces[/{id}]` 一律 405，v3 才有），自托管 Data Retention
又是企业版功能。官方社区对 v2 self-hosted 保留期给出的建议就是清 PostgreSQL。
决策与删除面见 ADR-0013。

**级联必须自己保证**：`traces` 没有任何外键指向它，删主表不会带走子表行。
故在**同一事务**内按 `CASCADE_DELETE_ORDER` 先删子表、再删主表，并连带清理
本次触及且已无任何 trace 的 `trace_sessions` 行（避免 UI 留下空会话）。

**不处理**（ADR-0013「不解决的问题」）：对象存储里的 media blob、`dataset_run_items`、
`comments`。

本模块只做「列超期 / 删超期」，不含任何护栏 —— dry-run、保留期下界、单次上限、
双重确认与审计输出都留在 CLI。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from src.config import build_langfuse_postgres_dsn

#: 删除顺序契约（子表先于主表；ADR-0013 决策 3）。改动即改变删除面，须同步 ADR。
CASCADE_DELETE_ORDER: tuple[str, ...] = (
    "observations",
    "scores",
    "trace_media",
    "observation_media",
    "traces",
)

#: 各表圈定本次 trace 的列名：子表用 trace_id，主表用主键 id。
_TRACE_ID_COLUMN: dict[str, str] = {
    "observations": "trace_id",
    "scores": "trace_id",
    "trace_media": "trace_id",
    "observation_media": "trace_id",
    "traces": "id",
}

#: 列超期 trace：按 timestamp 升序（与官方 Data Retention 对 Traces 的判据一致）。
_LIST_EXPIRED_SQL = text(
    "SELECT id, session_id FROM traces"
    " WHERE project_id = :pid AND timestamp < :cutoff"
    " ORDER BY timestamp ASC"
    " LIMIT :limit"
)
#: project 作用域守卫：库内 project 数 ≠ 1 即拒绝（ADR-0013 决策 5）。
_COUNT_PROJECTS_SQL = text("SELECT count(*) FROM projects")
_SELECT_PROJECT_ID_SQL = text("SELECT id FROM projects")
#: 空 session 清理：仅限本次触及的 session，且删完后已无任何 trace 才删。
_DELETE_EMPTY_SESSIONS_SQL = text(
    "DELETE FROM trace_sessions"
    " WHERE project_id = :pid AND id = ANY(:sessions)"
    "   AND NOT EXISTS (SELECT 1 FROM traces t"
    "                    WHERE t.project_id = :pid AND t.session_id = trace_sessions.id)"
)


@dataclass
class ExpiredTrace:
    """一条超期 trace 的最小标识。"""

    id: str  # traces.id（Langfuse trace 主键）
    session_id: str | None  # traces.session_id；可空，无 session 的 trace 为 None


@dataclass
class CascadeCounts:
    """一次级联删除的逐表行数与空 session 清理数（审计口径）。"""

    observations: int  # observations 表删除行数
    scores: int  # scores 表删除行数
    trace_media: int  # trace_media 表删除行数
    observation_media: int  # observation_media 表删除行数
    traces: int  # traces 主表删除行数
    sessions: int  # 连带清理的空 trace_sessions 行数


class LangfuseProjectScopeError(RuntimeError):
    """目标库 project 数 ≠ 1 —— 无法安全确定删除作用域（ADR-0013 决策 5）。"""

    def __init__(self, project_count: int) -> None:
        """记录实测到的 project 数，供 CLI 生成可读的拒绝信息。

        Args:
            project_count: `SELECT count(*) FROM projects` 的结果
        """
        self.project_count = project_count
        super().__init__(f"langfuse 库 project 数={project_count}，非单 project 部署")


class PurgeBackend(Protocol):
    """清理后端的最窄接口：列超期 + 删超期（供 CLI 依赖与测试替身）。"""

    async def list_expired(self, cutoff: datetime, limit: int) -> list[ExpiredTrace]:
        """列出早于 cutoff 的超期 trace，最多 limit 条。

        Args:
            cutoff: 超期界限（naive UTC，对齐 traces.timestamp 的无时区列）
            limit: 最多返回条数；CLI 传单次上限 + 1 用于判超限

        Returns:
            按 timestamp 升序的 ExpiredTrace 列表（可能为 limit 条）
        """
        ...

    async def delete(self, traces: list[ExpiredTrace]) -> CascadeCounts:
        """在单个事务内按序级联删除给定 trace，返回逐表行数。

        Args:
            traces: 待删 trace 列表

        Returns:
            逐表删除行数与清理掉的空 session 数
        """
        ...


class LangfuseSqlPurgeBackend:
    """直连 Langfuse PostgreSQL 的清理后端实现。"""

    def __init__(self, dsn: str | None = None) -> None:
        """建立异步引擎（不在此处建立连接）。

        Args:
            dsn: 覆盖用 DSN；None 时取 build_langfuse_postgres_dsn()。
                显式传参便于测试注入，且引擎不在导入期创建。
        """
        resolved = dsn
        if resolved is None:
            resolved = build_langfuse_postgres_dsn()
        self._engine: AsyncEngine = create_async_engine(
            resolved, pool_pre_ping=True, echo=False
        )

    async def _resolve_project_id(self, conn: AsyncConnection) -> str:
        """取库内唯一的 project id；project 数 ≠ 1 时拒绝执行。

        Args:
            conn: 已打开的异步连接（沿用调用方的事务 / 连接上下文）

        Returns:
            库内唯一 project 的 id

        Raises:
            LangfuseProjectScopeError: project 数 ≠ 1 时抛出
        """
        count = (await conn.execute(_COUNT_PROJECTS_SQL)).scalar_one()
        if count != 1:
            raise LangfuseProjectScopeError(count)
        project_id = (await conn.execute(_SELECT_PROJECT_ID_SQL)).scalar_one()
        return str(project_id)

    async def list_expired(self, cutoff: datetime, limit: int) -> list[ExpiredTrace]:
        """列出早于 cutoff 的超期 trace。

        Args:
            cutoff: 超期界限（naive UTC；traces.timestamp 无时区）
            limit: 最多返回条数；CLI 传单次上限 + 1 用于判超限

        Returns:
            按 timestamp 升序的 ExpiredTrace 列表
        """
        async with self._engine.connect() as conn:
            pid = await self._resolve_project_id(conn)
            rows = (
                await conn.execute(
                    _LIST_EXPIRED_SQL,
                    {"pid": pid, "cutoff": cutoff, "limit": limit},
                )
            ).all()
        return [ExpiredTrace(id=row.id, session_id=row.session_id) for row in rows]

    async def delete(self, traces: list[ExpiredTrace]) -> CascadeCounts:
        """在单个事务内按 CASCADE_DELETE_ORDER 级联删除并清理空 session。

        Args:
            traces: 待删 trace 列表；空列表直接返回零计数，不开启事务

        Returns:
            逐表删除行数与清理掉的空 session 数
        """
        if not traces:
            return CascadeCounts(
                observations=0,
                scores=0,
                trace_media=0,
                observation_media=0,
                traces=0,
                sessions=0,
            )
        ids = [trace.id for trace in traces]
        # 删除前收集本次触及的 session（会话清理以此为准，不扫全表）
        sessions = [
            trace.session_id for trace in traces if trace.session_id is not None
        ]
        counts: dict[str, int] = {}
        async with self._engine.begin() as conn:
            pid = await self._resolve_project_id(conn)
            for table in CASCADE_DELETE_ORDER:
                column = _TRACE_ID_COLUMN[table]
                # 表名/列名取自模块内冻结常量，非外部输入；id 一律走绑定参数
                statement = text(
                    f"DELETE FROM {table}"
                    f" WHERE project_id = :pid AND {column} = ANY(:ids)"
                )
                result = await conn.execute(statement, {"pid": pid, "ids": ids})
                counts[table] = result.rowcount
            sessions_deleted = 0
            if sessions:
                result = await conn.execute(
                    _DELETE_EMPTY_SESSIONS_SQL, {"pid": pid, "sessions": sessions}
                )
                sessions_deleted = result.rowcount
        return CascadeCounts(
            observations=counts["observations"],
            scores=counts["scores"],
            trace_media=counts["trace_media"],
            observation_media=counts["observation_media"],
            traces=counts["traces"],
            sessions=sessions_deleted,
        )

    async def aclose(self) -> None:
        """释放连接池（进程退出前调用）。"""
        await self._engine.dispose()
