"""Langfuse trace 保留期清理 —— 删掉早于保留期的 trace。

**删除后端是 SQL（直连 Langfuse PG）**：langfuse v2.95.11 没有公开删除 API
（`DELETE /api/public/traces[/{id}]` 一律 405，v3 才有），自托管 Data Retention
又属企业版功能。选型、删除面与顺序见 ADR-0013；SQL 语句集中在
`src/infra/llm/langfuse_purge.py`。

**为什么带这么多护栏**：这是对运行中观测库的**不可逆删除**。一条误配命令就能
删光整库，因此下界、确认、上限、审计、环境约束缺一不可。

用法：
    python -m src.cli.purge_langfuse_traces --dry-run
    LANGFUSE_PURGE_ALLOW=1 python -m src.cli.purge_langfuse_traces --yes

退出码：0 = 正常（含 dry-run 与空结果）；2 = 被护栏拒绝；1 = 执行失败（DB 不可达等）。
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError

from src.config import (
    LANGFUSE_PG_DATABASE,
    LANGFUSE_PG_USER,
    POSTGRES_HOST,
    POSTGRES_PORT,
)
from src.config.const import PURGE_ALLOW_ENV_VAR as ALLOW_ENV_VAR
from src.config.const import PURGE_DEFAULT_RETENTION_DAYS as DEFAULT_RETENTION_DAYS
from src.config.const import PURGE_MAX_DELETE_PER_RUN as MAX_DELETE_PER_RUN
from src.config.const import PURGE_MIN_RETENTION_DAYS as MIN_RETENTION_DAYS
from src.infra.llm.langfuse_purge import (
    LangfuseProjectScopeError,
    LangfuseSqlPurgeBackend,
    PurgeBackend,
)


def _target_label() -> str:
    """返回可安全打印的目标库标识。

    Returns:
        `host:port/db (user=...)`；不含密码，也不含完整 DSN。
    """
    return (
        f"{POSTGRES_HOST}:{POSTGRES_PORT}/{LANGFUSE_PG_DATABASE}"
        f" (user={LANGFUSE_PG_USER})"
    )


async def run(
    *,
    backend: PurgeBackend,
    retention_days: int,
    dry_run: bool,
    confirmed: bool,
    allowed: bool,
) -> int:
    """执行一次清理，返回退出码。

    Args:
        backend: 清理后端（SQL 直连实现，或测试替身）
        retention_days: 保留期天数
        dry_run: True 时只列出不删除
        confirmed: 是否已显式确认（`--yes`）
        allowed: 环境是否已武装（`LANGFUSE_PURGE_ALLOW=1`）

    Returns:
        0 = 正常结束；2 = 被护栏拒绝；1 = 执行失败（DB 不可达 / SQL 报错）
    """
    if retention_days < MIN_RETENTION_DAYS:
        print(
            f"[error] 保留期 {retention_days} 天低于下界 {MIN_RETENTION_DAYS} 天，拒绝执行",
            file=sys.stderr,
        )
        return 2

    if not dry_run and not confirmed:
        print("[error] 非 dry-run 必须显式传 --yes", file=sys.stderr)
        return 2

    if not dry_run and not allowed:
        print(
            f"[error] 未武装环境（{ALLOW_ENV_VAR}=1），拒绝真删。"
            f"当前目标库：{_target_label()}",
            file=sys.stderr,
        )
        return 2

    # cutoff 必须是 naive UTC：traces.timestamp 是 timestamp WITHOUT time zone，
    # 传 tz-aware 值会因比较语义不一致而删错范围。
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
    try:
        traces = await backend.list_expired(cutoff, MAX_DELETE_PER_RUN + 1)
    except LangfuseProjectScopeError as exc:
        print(f"[error] {exc}，拒绝执行", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError) as exc:
        # DB 侧失败统一收敛为一行 [error]，不向上裸抛 traceback
        print(f"[error] 查询超期 trace 失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"[info] 目标库={_target_label()} 保留期={retention_days}天 命中={len(traces)}条"
    )

    if not traces:
        print("[info] 无超期 trace，退出")
        return 0

    if len(traces) > MAX_DELETE_PER_RUN:
        print(
            f"[error] 命中 {len(traces)} 条超过单次上限 {MAX_DELETE_PER_RUN}，"
            f"未删除任何数据；请分批执行",
            file=sys.stderr,
        )
        return 2

    if dry_run:
        print("[dry-run] 以下 trace 将被删除：")
        for trace in traces:
            print(f"  - {trace.id}")
        return 0

    try:
        counts = await backend.delete(traces)
    except (SQLAlchemyError, OSError) as exc:
        # DB 侧失败统一收敛为一行 [error]，不向上裸抛 traceback
        print(f"[error] 删除失败：{exc}", file=sys.stderr)
        return 1

    print(
        f"[info] 级联删除 observations={counts.observations} scores={counts.scores} "
        f"trace_media={counts.trace_media} "
        f"observation_media={counts.observation_media} "
        f"traces={counts.traces} sessions={counts.sessions}"
    )
    print(f"[info] 已删除 {len(traces)} 条超期 trace：")
    for trace in traces:
        print(f"  - {trace.id}")
    return 0


async def _run_cli(args: argparse.Namespace) -> int:
    """构造 SQL 后端、驱动 run()，并在结束时释放引擎。

    Args:
        args: 已解析的命令行参数

    Returns:
        run() 的退出码；后端构造失败时为 1
    """
    try:
        backend = LangfuseSqlPurgeBackend()
    except RuntimeError as exc:
        print(f"[error] 无法构造 Langfuse 删除后端：{exc}", file=sys.stderr)
        return 1
    try:
        return await run(
            backend=backend,
            retention_days=args.retention_days,
            dry_run=args.dry_run,
            confirmed=args.yes,
            allowed=os.getenv(ALLOW_ENV_VAR) == "1",
        )
    finally:
        await backend.aclose()


def main() -> None:
    """CLI 入口：解析参数、驱动异步流程、以退出码结束进程。"""
    parser = argparse.ArgumentParser(description="Purge expired Langfuse traces")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    code = asyncio.run(_run_cli(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
