"""实跑验证：父块级去重后，单文档库的检索条数不再被文档数压顶。

用法：POSTGRES_HOST=localhost .venv/bin/python scripts/verify_retrieval_dedup.py
"""

import asyncio
import sys
from pathlib import Path

# 直接以 `python scripts/verify_retrieval_dedup.py` 运行时，`scripts/` 会被加入
# sys.path 而非仓库根目录，导致 `import src` 失败；此处显式把仓库根插到最前。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import TOP_K_RERANK
from src.infra.db.engine import run_and_dispose
from src.infra.db.vector_store import VectorStore
from src.models import get_rerank
from src.rag import retrieval

CASES = [
    ("4a1dcb8bb340473c8149ead5b7f75873", "腾讯 2024年第四季度 业绩 营收 净利润", 1),
    ("b9e74e820e0a4bad8472304446e54f5c", "东软集团 年报 营业收入 净利润", 2),
    ("ea84fb7235a941f9b64bcf4f5fa4b7f2", "东软集团 2024年年度报告 营业收入", 3),
]


async def main() -> None:
    # 独立脚本不会走 app 的 setup_logging，loguru 默认 sink 会把 pg_store 的
    # DEBUG（含完整分块正文）全部打到 stderr；这里收敛到 INFO，保留
    # rerank done / dedup done 事件行作为证据，去掉正文噪声。
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    store = VectorStore()
    reranker = get_rerank()
    for kb_id, query, expect_floor in CASES:
        raw = await retrieval.search(query, kb_id, store)
        contexts = retrieval.rerank_results(query, raw, reranker)
        print(
            f"kb={kb_id[:8]} result_count={len(contexts)} "
            f"(期望 ≤ {TOP_K_RERANK}，且 > {expect_floor} 表示天花板已解除)"
        )
        if len(contexts) > TOP_K_RERANK:
            raise SystemExit("FAIL: 超过 TOP_K_RERANK")
        if len(contexts) <= expect_floor:
            raise SystemExit("FAIL: 仍未突破原天花板")


if __name__ == "__main__":
    asyncio.run(run_and_dispose(main()))
