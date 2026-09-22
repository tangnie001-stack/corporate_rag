"""Langfuse trace 保留期清理 —— 删掉早于保留期的 trace。

**为什么必须自建**：Langfuse 的 Data Retention 在自托管下属企业版功能，OSS v2
没有。接线后 trace 会无界增长（ADR-0011 复查条件③的硬要求）。

**为什么带这么多护栏**：这是对运行中观测库的**不可逆删除**。一条误配命令就能
删光整库，因此下界、确认、上限、审计、环境约束缺一不可。

用法：
    python -m src.cli.purge_langfuse_traces --dry-run
    LANGFUSE_PURGE_ALLOW=1 python -m src.cli.purge_langfuse_traces --yes

退出码：0 = 正常（含 dry-run 与空结果）；非 0 = 被护栏拒绝或执行失败。
"""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from langfuse import Langfuse

from src.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
from src.config.const import PURGE_ALLOW_ENV_VAR as ALLOW_ENV_VAR
from src.config.const import PURGE_DEFAULT_RETENTION_DAYS as DEFAULT_RETENTION_DAYS
from src.config.const import PURGE_MAX_DELETE_PER_RUN as MAX_DELETE_PER_RUN
from src.config.const import PURGE_MIN_RETENTION_DAYS as MIN_RETENTION_DAYS


def _fetch_expired(client: Any, cutoff: datetime, limit: int) -> list[str]:
    """取出早于 cutoff 的 trace id，最多取 limit+1 条（多取一条用于判超限）。

    Args:
        client: Langfuse 客户端（测试传替身）
        cutoff: 时间界限，早于此值的 trace 视为超期
        limit: 单次上限

    Returns:
        trace id 列表（可能比 limit 多 1）
    """
    ids: list[str] = []
    page = 1
    while len(ids) <= limit:
        batch = client.api.trace.list(
            to_timestamp=cutoff, page=page, limit=100, order_by="timestamp.asc"
        )
        if not batch.data:
            break
        ids.extend(trace.id for trace in batch.data)
        page += 1
    return ids[: limit + 1]


def run(
    *,
    client: Any,
    retention_days: int,
    dry_run: bool,
    confirmed: bool,
    allowed: bool,
) -> int:
    """执行一次清理，返回退出码。

    Args:
        client: Langfuse 客户端（测试传替身）
        retention_days: 保留期天数
        dry_run: True 时只列出不删除
        confirmed: 是否已显式确认（`--yes`）
        allowed: 环境是否已武装（`LANGFUSE_PURGE_ALLOW=1`）

    Returns:
        0 表示正常结束；非 0 表示被护栏拒绝或执行失败
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
            f"[error] 未武装环境（{ALLOW_ENV_VAR}=1），拒绝真删。当前目标后端：{LANGFUSE_HOST}",
            file=sys.stderr,
        )
        return 2

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    ids = _fetch_expired(client, cutoff, MAX_DELETE_PER_RUN)
    print(
        f"[info] 目标后端={LANGFUSE_HOST} 保留期={retention_days}天 命中={len(ids)}条"
    )

    if not ids:
        print("[info] 无超期 trace，退出")
        return 0

    if len(ids) > MAX_DELETE_PER_RUN:
        print(
            f"[error] 命中 {len(ids)} 条超过单次上限 {MAX_DELETE_PER_RUN}，"
            f"未删除任何数据；请分批执行",
            file=sys.stderr,
        )
        return 2

    if dry_run:
        print("[dry-run] 以下 trace 将被删除：")
        for trace_id in ids:
            print(f"  - {trace_id}")
        return 0

    client.api.trace.delete_multiple(trace_ids=ids)
    print(f"[info] 已删除 {len(ids)} 条超期 trace：")
    for trace_id in ids:
        print(f"  - {trace_id}")
    return 0


def main() -> None:
    """CLI 入口：解析参数、构造客户端、交 run() 执行。"""
    parser = argparse.ArgumentParser(description="Purge expired Langfuse traces")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    client = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        host=LANGFUSE_HOST.rstrip("/"),
    )
    code = run(
        client=client,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
        confirmed=args.yes,
        allowed=os.getenv(ALLOW_ENV_VAR) == "1",
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
