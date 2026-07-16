#!/usr/bin/env python3
"""检索质量检查 CLI — 对已有知识库进行语义检索并展示结果。

用法：
    python -m src.cli.check_retrieval --kb <kb_name> --query "<你的问题>"
    python -m src.cli.check_retrieval --kb <kb_name> --query "<问题>" --top-k 10

前提条件：
  - 知识库已创建且文档已入库（通过 Iter 2 文档处理流水线）
  - .env 中配置了有效的 DASHSCOPE_API_KEY（用于 Embedding 计算）

使用场景：
  - 验证文档入库后检索是否正常
  - 调整 TOP_K_RETRIEVAL 参数，观察检索结果质量
  - 调试 RAG 流水线中"检索"环节的效果
"""

import argparse
import sys

from loguru import logger

from src.core.logging import setup_logging
from src.infra.db.mysql_db import MySQLDB
from src.infra.db.vector_store import VectorStore
from src.config import TOP_K_RERANK

setup_logging()


def main() -> None:
    """CLI 入口 — 解析参数、执行检索、打印结果。"""
    parser = argparse.ArgumentParser(description="Retrieval quality checker")
    parser.add_argument("--kb", required=True, help="Knowledge base name")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument(
        "--top-k",
        type=int,
        default=TOP_K_RERANK,
        help="Number of results (default: %(default)s)",
    )
    args = parser.parse_args()

    # ====== Step 1: 通过知识库名称查找 kb_id ======
    logger.info("Looking up knowledge base: {}", args.kb)
    with MySQLDB() as db:
        kb_id = db.get_kb_by_name(args.kb)
        if not kb_id:
            logger.error("Error: Knowledge base '{}' not found.", args.kb)
            print("Available KBs:")
            for kid, name in db.get_all_kb():
                print(f"  - {name} ({kid})")
            sys.exit(1)

    # ====== Step 2: 在 ChromaDB 中执行语义检索 ======
    logger.info("Searching for: '{}' (top-k={})", args.query, args.top_k)
    store = VectorStore()
    try:
        results = store.similarity_search(kb_id, args.query, k=args.top_k)
    except Exception as e:
        logger.exception("Search failed: {}", e)
        print("Hint: Ensure DASHSCOPE_API_KEY is set and documents have been added.")
        sys.exit(1)

    # ====== Step 3: 格式化打印检索结果 ======
    print("=" * 60)
    print("  Retrieval Results")
    print(f"  Knowledge Base:  {args.kb}")
    print(f"  Query:            {args.query}")
    print(f"  Results:         {len(results)}")
    print("=" * 60)

    if not results:
        print("  (no results)")
        print("=" * 60)
        return

    # 逐条打印每个检索结果：距离分数 + 来源 + 内容摘要
    for i, r in enumerate(results):
        dist_str = (
            f"  [{i + 1}] Distance: {r.get('distance', 'N/A'):.4f}"
            if r.get("distance")
            else f"  [{i + 1}]"
        )
        print(f"\n{dist_str}")
        print(
            f"      Source: {r['metadata'].get('source', 'unknown')} "
            f"(page {r['metadata'].get('page', '?')})"
        )
        # 只显示前 200 字符的摘要，避免刷屏
        print(f"      Content: {r['content'][:200]}...")
        print("-" * 60)


if __name__ == "__main__":
    main()
