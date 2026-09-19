import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from src.infra.db.base import Base
from src.infra.db.models import *

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线生成 SQL 脚本（不连库）。

    也需要 DSN：改用与应用同一处拼装，避免与 alembic.ini 漂移。
    """
    from src.config.settings import build_postgres_dsn

    context.configure(
        url=build_postgres_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """用应用自己的 DSN 跑迁移，避免 alembic.ini 与引擎配置漂移。"""
    from src.config.settings import build_postgres_dsn

    connectable = create_async_engine(build_postgres_dsn(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
