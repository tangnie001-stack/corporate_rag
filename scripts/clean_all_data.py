"""全量清理脚本 — 删库、清 Redis、删 MinIO 文件、清 ChromaDB。

适用场景：开发/测试环境重置、表结构大改后重建。
"""

import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    REDIS_URL, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MINIO_DOC_BUCKET, CHROMA_COLLECTION_PREFIX, CHROMA_PERSIST_DIR,
)


async def drop_mysql():
    dsn = f"mysql+aiomysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}"
    engine = create_async_engine(dsn)
    async with engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS `{MYSQL_DATABASE}`"))
        await conn.execute(
            text(f"CREATE DATABASE `{MYSQL_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
        await conn.commit()
        print(f"[MySQL] 已删除并重建 database: {MYSQL_DATABASE}")
    await engine.dispose()


async def flush_redis():
    import redis.asyncio as redis_async
    client = redis_async.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    print(f"[Redis] 已清空: {REDIS_URL}")


async def clean_minio():
    from minio import Minio
    client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                   secret_key=MINIO_SECRET_KEY, secure=False)
    if client.bucket_exists(MINIO_DOC_BUCKET):
        for obj in client.list_objects(MINIO_DOC_BUCKET, recursive=True):
            client.remove_object(MINIO_DOC_BUCKET, obj.object_name)
        client.remove_bucket(MINIO_DOC_BUCKET)
        print(f"[MinIO] 已删除 bucket: {MINIO_DOC_BUCKET}")
    client.make_bucket(MINIO_DOC_BUCKET)
    print(f"[MinIO] 已重建 bucket: {MINIO_DOC_BUCKET}")


async def reset_chromadb():
    import chromadb
    from chromadb.config import Settings
    from pathlib import Path
    persist_path = Path(CHROMA_PERSIST_DIR)
    if not persist_path.exists():
        print(f"[ChromaDB] 持久化目录不存在，跳过: {CHROMA_PERSIST_DIR}")
        return
    client = chromadb.PersistentClient(
        path=str(persist_path),
        settings=Settings(anonymized_telemetry=False),
    )
    names = [c.name for c in client.list_collections()
             if c.name.startswith(CHROMA_COLLECTION_PREFIX)]
    for name in names:
        client.delete_collection(name)
    print(f"[ChromaDB] 已删除 {len(names)} 个 collection")


async def main():
    print("即将执行：")
    print("  1. MySQL — DROP DATABASE + 重建")
    print("  2. Redis — FLUSHDB")
    print("  3. MinIO — 清空 bucket + 删除后重建")
    print("  4. ChromaDB — 删除所有 collection")
    confirm = input("输入 YES 确认执行: ")
    if confirm != "YES":
        print("已取消。")
        return
    await drop_mysql()
    await flush_redis()
    await clean_minio()
    await reset_chromadb()
    print("\n✅ 全部清理完成。")
    print("   下一步：alembic upgrade head 重建 MySQL 表结构")


if __name__ == "__main__":
    asyncio.run(main())
