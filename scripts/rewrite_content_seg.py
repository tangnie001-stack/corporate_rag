"""存量 `content_seg` 全量重写 —— 分词器变更后的唯一迁移步骤。

`content_seg` 是 `chunks.tsv` 生成列的输入，一旦落库即**固化**：jieba 版本或
词典变更后，存量检索文本与新的查询侧口径不一致，**不会报错、只会静默降召回**。
本脚本就是那条不变量的可执行检查（`--check` 非零退出即表示存量已过期）。

幂等：`--apply` 只改写与 `to_lexical_text(content)` 不一致的行，重复执行改写 0 行。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --check
    POSTGRES_HOST=localhost .venv/bin/python scripts/rewrite_content_seg.py --apply
"""

import argparse
import asyncio
import os
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any

from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# 直接以 `python scripts/rewrite_content_seg.py` 运行时 sys.path[0] 是 scripts/，
# 仓库根不在其中（editable 安装只把 src/ 内容暴露为顶层包）；补上仓库根以便
# import src.*。`python -m scripts.rewrite_content_seg` 下仓库根已在 path 中，
# 重复插入无副作用。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.infra.db.engine import run_and_dispose, session_factory
from src.infra.db.models.chunk import ChunkModel
from src.infra.search.tokenizer import to_lexical_text

# 逐批处理：避免一次把全部分块正文读进内存（量产规模约 2.5–4 万分块）
BATCH_SIZE = 500


def is_stale(content: str, content_seg: str) -> bool:
    """判断一行的检索文本是否与当前分词口径不一致。

    Args:
        content: 分块正文原文
        content_seg: 库中现存的检索文本

    Returns:
        True = 需要重写
    """
    return content_seg != to_lexical_text(content)


async def _iter_batches(
    session: AsyncSession,
) -> AsyncIterator[Sequence[Row[Any]]]:
    """按 id 顺序逐批产出待比对的 (id, content, content_seg) 行。

    Args:
        session: 数据库会话

    Yields:
        每批至多 BATCH_SIZE 行的行集合，读完为止
    """
    offset = 0
    while True:
        result = await session.execute(
            select(ChunkModel.id, ChunkModel.content, ChunkModel.content_seg)
            .order_by(ChunkModel.id)
            .offset(offset)
            .limit(BATCH_SIZE)
        )
        rows = result.all()
        if not rows:
            break
        yield rows
        offset += len(rows)


async def check(session: AsyncSession) -> tuple[int, int]:
    """只读比对，返回 (总行数, 过期行数)。"""
    total = 0
    stale = 0
    async for rows in _iter_batches(session):
        total += len(rows)
        for _id, content, content_seg in rows:
            if is_stale(content, content_seg):
                stale += 1
    return total, stale


async def apply(session: AsyncSession) -> int:
    """逐批重写过期行的检索文本，返回改写行数。"""
    rewritten = 0
    async for rows in _iter_batches(session):
        for _id, content, content_seg in rows:
            # 每行只分词一次：既用于判定过期，也用于写入值
            content_seg_new = to_lexical_text(content)
            if content_seg == content_seg_new:
                continue
            await session.execute(
                update(ChunkModel)
                .where(ChunkModel.id == _id)
                .values(content_seg=content_seg_new)
            )
            rewritten += 1
        await session.commit()
    return rewritten


async def _main(mode: str) -> int:
    async with session_factory() as session:
        if mode == "check":
            total, stale = await check(session)
            print(f"total={total} stale={stale}")
            if stale:
                print(
                    "存量检索文本已过期：跑 --apply 重写（分词器变更必须触发全量重写）"
                )
                return 1
            return 0
        rewritten = await apply(session)
        print(f"rewritten={rewritten}")
        return 0


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="存量 content_seg 全量重写")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只读比对，过期则退出码 1")
    group.add_argument("--apply", action="store_true", help="逐批重写过期行")
    args = parser.parse_args()
    if args.check:
        mode = "check"
    else:
        mode = "apply"
    code = asyncio.run(run_and_dispose(_main(mode)))
    sys.exit(code)


if __name__ == "__main__":
    main()
