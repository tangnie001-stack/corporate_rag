"""数据重置工具 — 一键清除 MySQL、ChromaDB、Redis 的全部数据。

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
from typing import Optional

from loguru import logger
from sqlalchemy import text

from src.services.app_service import AppService
from src.config import CHROMA_PERSIST_DIR, REDIS_URL
from src.infra.db.engine import engine
from src.infra.db.models import *  # noqa: F401, F403 — 注册所有模型到 Base.metadata


async def _reset_mysql_async() -> None:
    """异步清空 MySQL 所有业务表。

    使用 TRUNCATE + 临时关闭外键检查，避免 CASCADE 约束报错。
    """
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        await conn.execute(text("TRUNCATE TABLE conversation_history"))
        await conn.execute(text("TRUNCATE TABLE document"))
        await conn.execute(text("TRUNCATE TABLE knowledge_base"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    logger.info("MySQL: 已清空所有业务表")


def reset_mysql() -> None:
    """清空 MySQL 所有业务表。

    使用 asyncio.run 包装异步实现。
    """
    asyncio.run(_reset_mysql_async())


def reset_vector_store() -> None:
    """清空 ChromaDB 持久目录（删除整个 persist 目录后重建空目录）。

    不初始化 VectorStore 客户端，直接操作文件系统，速度快且无外部依赖。
    """
    path = Path(CHROMA_PERSIST_DIR)
    if path.exists():
        shutil.rmtree(path)
        logger.info("ChromaDB: 已删除 persist 目录 '{}'", CHROMA_PERSIST_DIR)
    path.mkdir(parents=True, exist_ok=True)
    logger.info("ChromaDB: 已重建空目录")


def reset_redis() -> None:
    """清空 Redis 全部数据。

    直接新建 Redis 连接后 FLUSHALL，不绕 AppService / ChatManager。
    避免引入 VectorStore / RAGChain 等重量级依赖的初始化。
    """
    try:
        import redis  # noqa: PLC0415

        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        r.flushall()
        logger.info("Redis: 已 FLUSHALL")
        r.close()
    except Exception as e:
        logger.warning("Redis: 连接失败或 FLUSHALL 异常: {}，跳过", e)


def reset_all(
    service: Optional[AppService] = None,
) -> None:
    """一键重置全部数据存储（MySQL + ChromaDB + Redis）。

    Args:
        service: 已有的 AppService 实例（可选，用于通过其 chat_manager 清 Redis）
    Raises:
        RuntimeError: MySQL 连接失败
    """
    logger.info("========== 开始重置所有数据 ==========")

    # MySQL
    reset_mysql()

    # ChromaDB（直接删目录，不和客户端交互）
    reset_vector_store()

    # Redis
    if service is not None:
        cm = service.chat_manager
        if cm._in_memory:  # noqa: SLF001
            cm._memory_store.clear()  # noqa: SLF001
            logger.warning("Redis: 内存降级模式，已清空内存存储")
        else:
            try:
                cm._redis.flushall()  # noqa: SLF001
                logger.info("Redis: 已 FLUSHALL")
            except Exception as e:
                logger.error("Redis: FLUSHALL 失败: {}", e)
    else:
        reset_redis()

    logger.info("========== 数据重置完成 ==========")


if __name__ == "__main__":
    # 独立运行时直接通过 docker exec 操作（避开 Python 客户端锁竞争）
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    logger.info("========== 开始重置所有数据 (docker exec 模式) ==========")

    # MySQL
    sql = "SET FOREIGN_KEY_CHECKS = 0; TRUNCATE TABLE conversation_history; TRUNCATE TABLE document; TRUNCATE TABLE knowledge_base; SET FOREIGN_KEY_CHECKS = 1;"
    r = subprocess.run(
        [
            "docker",
            "exec",
            "financial-qa-mysql",
            "mysql",
            "-uroot",
            "-pfinancial_qa_pass",
            "financial_qa",
            "-e",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode == 0:
        logger.info("MySQL: 已清空所有业务表")
    else:
        logger.error("MySQL: 清空失败: {}", r.stderr)
        sys.exit(1)

    # ChromaDB
    import shutil as _su
    from pathlib import Path as _P

    p = _P(CHROMA_PERSIST_DIR)
    if p.exists():
        _su.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)
    logger.info("ChromaDB: 已重置")

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
    )
    if r2.returncode == 0:
        logger.info("Redis: 已 FLUSHALL")
    else:
        logger.error("Redis: FLUSHALL 失败: {}", r2.stderr)

    logger.info("========== 数据重置完成 ==========")
