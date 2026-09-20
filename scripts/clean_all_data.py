"""全量清理脚本 — 清 PostgreSQL 应用库、清 Redis、删 MinIO 文件。

适用场景：开发/测试环境重置、表结构大改后重建。
"""

import asyncio

from sqlalchemy import text

from src.config import (
    MINIO_ACCESS_KEY,
    MINIO_DOC_BUCKET,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    REDIS_URL,
)
from src.infra.db.engine import engine


async def reset_postgres():
    """删除应用库 public schema 下的全部表，供 alembic 从零重建。

    只删表、不删 schema —— vector 扩展由超级用户在库初始化时创建，
    应用账号无权重建，删掉会连带扩展一起丢，导致后续迁移失败。
    """
    drop_all_tables = text(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
                EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
        """
    )
    async with engine.begin() as conn:
        await conn.execute(drop_all_tables)
    await engine.dispose()
    print("[PostgreSQL] 已删除应用库 public schema 下的全部表")


async def flush_redis():
    import redis.asyncio as redis_async

    client = redis_async.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    print(f"[Redis] 已清空: {REDIS_URL}")


async def clean_minio():
    from minio import Minio

    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    if client.bucket_exists(MINIO_DOC_BUCKET):
        for obj in client.list_objects(MINIO_DOC_BUCKET, recursive=True):
            if obj.object_name is not None:
                client.remove_object(MINIO_DOC_BUCKET, obj.object_name)
        client.remove_bucket(MINIO_DOC_BUCKET)
        print(f"[MinIO] 已删除 bucket: {MINIO_DOC_BUCKET}")
    client.make_bucket(MINIO_DOC_BUCKET)
    print(f"[MinIO] 已重建 bucket: {MINIO_DOC_BUCKET}")


async def main():
    print("即将执行：")
    print("  1. PostgreSQL — 删除应用库全部表（保留 vector 扩展）")
    print("  2. Redis — FLUSHDB")
    print("  3. MinIO — 清空 bucket + 删除后重建")
    confirm = input("输入 YES 确认执行: ")
    if confirm != "YES":
        print("已取消。")
        return
    await reset_postgres()
    await flush_redis()
    await clean_minio()
    print("\n✅ 全部清理完成。")
    print("   下一步：alembic upgrade head 重建 PostgreSQL 表结构")


if __name__ == "__main__":
    asyncio.run(main())
