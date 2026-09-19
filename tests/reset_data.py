"""数据重置工具 — 一键清除 PostgreSQL、BM25 索引、Redis 的全部数据。

用法（独立运行）：
    source .venv/bin/activate
    python tests/reset_data.py

用法（在测试中导入）：
    from tests.reset_data import reset_all
    reset_all()  # 在 setUp 或 fixture 中调用
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import text

from src.config import BM25_INDEX_DIR, REDIS_URL
from src.infra.db.engine import engine
from src.infra.db.models import *
from src.services.app_service import AppService

# reset 的范围与改造前一致：不含 users / sessions / eval_report / feedback
_RESET_TABLES = "conversation_history, document, knowledge_base, chunks"


async def _reset_pg_async() -> None:
    """异步清空 PostgreSQL 的业务数据（见 reset_pg 的范围说明）。"""
    async with engine.begin() as conn:
        # CASCADE：chunks.kb_id 外键指向 knowledge_base
        await conn.execute(text(f"TRUNCATE {_RESET_TABLES} CASCADE"))
    logger.info("PostgreSQL: 已清空业务表 {}", _RESET_TABLES)


def reset_pg() -> None:
    """清空 PostgreSQL 中本项目的业务数据。

    与改造前的 MySQL 版保持同一范围：只清会话历史、文档、知识库，
    以及随它们派生的分块表。

    ⚠ 刻意不清 users —— 清了就登不进 dev 界面（凭据在 .env 的 TEST_ACCOUNT，
    供 cookbook.md 的手工 API 调试使用；全仓 .py 无引用）。
    现状亦不清 sessions / eval_report / feedback，此处保持现状，不在本变更扩大范围。
    本函数只连应用库，不会误删同实例上的 Langfuse 库。
    """
    asyncio.run(_reset_pg_async())


def reset_bm25_index() -> None:
    """清空 BM25 索引目录（词法索引仍是磁盘文件，P3 由 PG 全文检索取代）。

    注意：**不再删除 Chroma 的 persist 目录** —— 自 P2 起 dense 数据由
    chunks 表承载（reset_pg 已清），而 data/chroma_persist 是 dense 等价性
    验收与回滚的依据，删除它会使两者同时失效。
    """
    path = Path(BM25_INDEX_DIR)
    if path.exists():
        shutil.rmtree(path)
        logger.info("BM25: 已删除索引目录 '{}'", BM25_INDEX_DIR)
    path.mkdir(parents=True, exist_ok=True)
    logger.info("BM25: 已重建空目录")


def reset_redis() -> None:
    """清空 Redis 全部数据。

    直接新建 Redis 连接后 FLUSHALL，不绕 AppService / ChatManager。
    避免引入 VectorStore / RAGChain 等重量级依赖的初始化。
    """
    try:
        import redis

        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        r.flushall()
        logger.info("Redis: 已 FLUSHALL")
        r.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis: 连接失败或 FLUSHALL 异常: {}，跳过", e)


def reset_all(
    service: AppService | None = None,
) -> None:
    """一键重置全部数据存储（PostgreSQL + BM25 索引 + Redis）。

    Args:
        service: 已有的 AppService 实例（可选，用于通过其 chat_manager 清 Redis）

    异常：PostgreSQL 连接失败时，由底层驱动（SQLAlchemy/asyncpg）抛出的异常
    会原样向上传播，本函数不做捕获或类型转换。Redis 连接失败仅记 warning
    后跳过，不会向上抛。
    """
    logger.info("========== 开始重置所有数据 ==========")

    # PostgreSQL
    reset_pg()

    # BM25 索引（直接删目录，不和客户端交互）
    reset_bm25_index()

    # Redis
    if service is not None:
        cm = service.chat_manager
        if cm._in_memory:
            cm._memory_store.clear()
            logger.warning("Redis: 内存降级模式，已清空内存存储")
        else:
            try:
                if cm._redis is None:
                    logger.warning("Redis: 连接未初始化，跳过 FLUSHALL")
                else:
                    cm._redis.flushall()
                    logger.info("Redis: 已 FLUSHALL")
            except Exception as e:  # noqa: BLE001
                logger.error("Redis: FLUSHALL 失败: {}", e)
    else:
        reset_redis()

    logger.info("========== 数据重置完成 ==========")


if __name__ == "__main__":
    # 独立运行时直接通过 docker compose exec 操作（避开 Python 客户端锁竞争）
    import subprocess
    import sys

    logger.info("========== 开始重置所有数据 (docker compose exec 模式) ==========")

    # PostgreSQL
    sql = f"TRUNCATE {_RESET_TABLES} CASCADE;"
    r = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "corporate_rag",
            "-d",
            "corporate_rag",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,  # returncode 在下方手动检查
    )
    if r.returncode == 0:
        logger.info("PostgreSQL: 已清空业务表 {}", _RESET_TABLES)
    else:
        logger.error("PostgreSQL: 清空失败: {}", r.stderr)
        sys.exit(1)

    # BM25 索引
    import shutil as _su
    from pathlib import Path as _P

    p = _P(BM25_INDEX_DIR)
    if p.exists():
        _su.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    logger.info("BM25: 已重置")

    # Redis
    r2 = subprocess.run(
        [
            "docker",
            "exec",
            "financial-qa-redis",
            "redis-cli",
            "-a",
            "financial_qa_pass",
            "FLUSHALL",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,  # returncode 在下方手动检查
    )
    if r2.returncode == 0:
        logger.info("Redis: 已 FLUSHALL")
    else:
        logger.error("Redis: FLUSHALL 失败: {}", r2.stderr)

    logger.info("========== 数据重置完成 ==========")
