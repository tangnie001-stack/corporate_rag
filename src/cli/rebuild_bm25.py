#!/usr/bin/env python3
"""存量知识库 BM25 索引回填 CLI。

背景：BM25 索引原本只在文档入库/删除时全量重建，历史已入库的知识库
没有索引（`data/bm25_index/{kb_id}/bm25.pkl` 缺失），混合检索的 BM25 路
空跑、退化为纯 dense。本命令扫描所有未删除知识库，对仍有分块的 KB
补建索引。

用法：
    python -m src.cli.rebuild_bm25                  # 全部知识库
    python -m src.cli.rebuild_bm25 --kb <kb_id>     # 指定知识库

前提条件：
  - 知识库已创建且文档已入库（Chroma 中已有分块）
  - .env 中配置 DASHSCOPE_API_KEY（VectorStore 初始化用）
"""

import argparse
import asyncio

from loguru import logger

from src.config import BM25_INDEX_DIR, HYBRID_SEARCH_ENABLED
from src.core.logging import setup_logging
from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import KbRepo
from src.infra.db.vector_store import VectorStore
from src.infra.search.bm25_index import BM25Index

setup_logging(configure_trace_id=True)


async def main() -> None:
    """CLI 入口 — 解析参数、重建指定/全部 KB 的 BM25 索引。"""
    parser = argparse.ArgumentParser(description="Rebuild BM25 indexes for KBs")
    parser.add_argument("--kb", default="", help="Knowledge base id (default: all)")
    args = parser.parse_args()

    if not HYBRID_SEARCH_ENABLED:
        logger.info("HYBRID_SEARCH_ENABLED=false, skip BM25 rebuild")
        return

    vector_store = VectorStore()
    bm25 = BM25Index(index_dir=BM25_INDEX_DIR)
    kb_repo = KbRepo(session_factory)

    if args.kb:
        kb_ids = [args.kb]
    else:
        kbs = await kb_repo.get_all_kb()
        kb_ids = [kb.id for kb in kbs]
        logger.info("Found {} KBs", len(kb_ids))

    rebuilt = skipped = failed = 0
    for kb_id in kb_ids:
        try:
            chunks = await asyncio.to_thread(vector_store.get_all_chunks, kb_id)
            if not chunks:
                logger.warning("kb={} has no chunks, skip", kb_id)
                skipped += 1
                continue
            await asyncio.to_thread(bm25.rebuild_from_results, kb_id, chunks)
            logger.info("rebuilt kb={} chunks={}", kb_id, len(chunks))
            rebuilt += 1
        except Exception as e:  # noqa: BLE001
            logger.exception("rebuild failed kb={}: {}", kb_id, e)
            failed += 1

    logger.info(
        "BM25 rebuild done: rebuilt={} skipped={} failed={}", rebuilt, skipped, failed
    )


if __name__ == "__main__":
    asyncio.run(main())
