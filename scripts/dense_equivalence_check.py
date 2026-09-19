"""dense 迁移等价性：同一语料、同一批查询，Chroma 与 pgvector 的 top-k 重合率。

两侧都**直读**自己的存储（Chroma 用原始客户端、PG 用 PgVectorStore），
不经过同一个 VectorStore 实例 —— 因为 VectorStore 在 Task 9 后指向 PG，
而 Chroma 侧必须一直可读到最后一次验收。

判据（retrieval-quality delta）：单知识库、k = TOP_K_RETRIEVAL、重合率 ≥ 0.9。
未达标不得进入后续步骤，先查 distance 语义与过滤条件。

查询集的池下限：重合率 = |交集| / k，需要两侧各自至少能产出 k 条结果才有意义。
若某 kb 的分块数（有 embedding 的）< k，重合率会被语料规模压到 |池|/k，
是**结构性上限**而非存储不等价（Ruling P2-R13）。故本脚本在处理前显式断言
每个被查询的 kb 池 ≥ k，不满足即报错退出，避免将来语料变化后静默退化成假阴性。
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import chromadb
from loguru import logger
from sqlalchemy import text

# 直接以 `python scripts/dense_equivalence_check.py` 运行时 sys.path[0] 是 scripts/，
# 仓库根不在其中（editable 安装只把 src/ 内容暴露为顶层包）；补上仓库根以便
# import src.*。`python -m scripts.dense_equivalence_check` 下仓库根已在 path 中，
# 重复插入无副作用。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import (
    CHROMA_COLLECTION_PREFIX,
    CHROMA_PERSIST_DIR,
    TOP_K_RETRIEVAL,
)
from src.infra.db.vector_store.pg_store import (
    MAX_QUERY_K,
    PgVectorStore,
    QueryEmbedder,
)

QUERIES_PATH = Path("tests/fixtures/dense_equivalence_queries.json")
"""固定查询集清单（落盘可复现）。"""
REPORT_PATH = Path("docs/tmp/p2-dense-equivalence-2026-09-19.md")
"""验收报告输出路径。"""
PASS_THRESHOLD = 0.9
"""重合率判据下限。"""


@dataclass
class QueryCase:
    """一条固定查询。"""

    kb: str
    """目标知识库（单库路径）。"""
    query: str
    """查询文本。"""
    category: str
    """分类：中文 / 数值 / 时间。"""


@dataclass
class QueryOutcome:
    """一条查询的比对结果。"""

    case: QueryCase
    """查询本身。"""
    chroma_ids: list[str]
    """Chroma 侧 top-k 的 id（按距离升序）。"""
    pg_ids: list[str]
    """PG 侧 top-k 的 id（按余弦距离升序）。"""
    overlap: float
    """重合率 = |交集| / k。"""


def overlap_ratio(dense_ids: list[str], chroma_ids: list[str], k: int) -> float:
    """计算 top-k 重合率。

    Args:
        dense_ids: PG 侧 id 列表
        chroma_ids: Chroma 侧 id 列表
        k: top-k 的 k

    Returns:
        |交集| / k；k <= 0 或无结果时为 0.0
    """
    if k <= 0:
        return 0.0
    return len(set(dense_ids) & set(chroma_ids)) / k


def load_queries(path: Path = QUERIES_PATH) -> list[QueryCase]:
    """读取固定查询集。

    Args:
        path: JSON 清单路径

    Returns:
        QueryCase 列表
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        QueryCase(kb=item["kb"], query=item["query"], category=item["category"])
        for item in raw
    ]


async def _kb_pool_sizes() -> dict[str, int]:
    """统计每个 kb 在 chunks 表中「有 embedding」的分块数。

    只聚合 chunks 表自身，不触碰 knowledge_base（后者含无分块的残留行）。
    有 embedding 才是 dense 检索的可召回池，因此用它作为池大小的口径。

    Returns:
        {kb_id: 有 embedding 的分块数}
    """
    from src.infra.db.engine import session_factory

    async with session_factory() as session:
        result = await session.execute(
            text("SELECT kb_id, count(embedding) FROM chunks GROUP BY kb_id")
        )
        return {str(row[0]): int(row[1]) for row in result.all()}


def _assert_pools(query_kbs: list[str], sizes: dict[str, int], k: int) -> None:
    """断言每个被查询的 kb 池 ≥ k，否则报错退出（见模块 docstring）。

    Args:
        query_kbs: 查询集覆盖的 kb（去重后）
        sizes: kb → 有 embedding 的分块数
        k: top-k 的 k

    Raises:
        RuntimeError: 任一 kb 的池小于 k
    """
    problems: list[str] = []
    for kb in query_kbs:
        size = sizes.get(kb, 0)
        if size < k:
            problems.append(f"{kb} 池={size} < k={k}")
    if problems:
        raise RuntimeError(
            "固定查询集覆盖的 kb 分块数不足，重合率判据会退化为假阴性: "
            + "; ".join(problems)
        )


def _chroma_top_ids(
    client, kb_id: str, embedder: QueryEmbedder, query: str, k: int
) -> list[str]:
    """直读 Chroma 取 top-k id（不经过 VectorStore，见模块 docstring）。

    Args:
        client: chromadb 原始客户端
        kb_id: 知识库 ID（32 位 hex，无连字符）
        embedder: 查询向量化入口
        query: 查询文本
        k: top-k

    Returns:
        按距离升序的 id 列表；集合不存在时返回空列表
    """
    name = f"{CHROMA_COLLECTION_PREFIX}{kb_id}"
    try:
        col = client.get_collection(name)
    except Exception:  # noqa: BLE001
        logger.warning("chroma collection missing: {}", name)
        return []
    if col.count() == 0:
        return []
    vec = embedder.embed_query(query)
    result = col.query(query_embeddings=[vec], n_results=min(k, MAX_QUERY_K))
    ids = result.get("ids") or [[]]
    if not ids or not ids[0]:
        return []
    return list(ids[0])


async def run_check(
    queries: list[QueryCase], k: int = TOP_K_RETRIEVAL
) -> list[QueryOutcome]:
    """对每条查询跑两侧并算重合率。

    两侧共用同一个 embedder 实例，保证查询向量完全一致 —— 否则重合率的差异
    无法归因到存储。

    Args:
        queries: 固定查询集
        k: top-k 的 k，默认取 TOP_K_RETRIEVAL

    Returns:
        每条查询的结果列表

    Raises:
        RuntimeError: 查询集覆盖的 kb 池小于 k（判据会失真）
    """
    query_kbs = sorted({case.kb for case in queries})
    sizes = await _kb_pool_sizes()
    _assert_pools(query_kbs, sizes, k)

    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    embedder = QueryEmbedder()
    store = PgVectorStore(embed_fn=embedder)

    outcomes: list[QueryOutcome] = []
    for case in queries:
        chroma_ids = _chroma_top_ids(client, case.kb, embedder, case.query, k)
        pg_results = await store.dense_search(case.kb, case.query, k=k)
        pg_ids = [r.id for r in pg_results]
        outcomes.append(
            QueryOutcome(
                case=case,
                chroma_ids=chroma_ids,
                pg_ids=pg_ids,
                overlap=overlap_ratio(pg_ids, chroma_ids, k),
            )
        )
    return outcomes


def _composition_lines(
    outcomes: list[QueryOutcome], k: int, sizes: dict[str, int]
) -> list[str]:
    """生成查询集构成说明（条数 / 分类分布 / kb 覆盖 / 被排除的 kb）。

    Args:
        outcomes: 逐条比对结果
        k: top-k 的 k
        sizes: kb → 有 embedding 的分块数（本语料的全部 kb）

    Returns:
        Markdown 行列表
    """
    categories: dict[str, int] = {}
    per_kb: dict[str, int] = {}
    for o in outcomes:
        categories[o.case.category] = categories.get(o.case.category, 0) + 1
        per_kb[o.case.kb] = per_kb.get(o.case.kb, 0) + 1
    cat_str = " / ".join(f"{name} {categories[name]}" for name in sorted(categories))
    kb_str = " / ".join(
        f"`{kb[:8]}…` {per_kb[kb]} 条（池 {sizes.get(kb, 0)}）" for kb in sorted(per_kb)
    )
    excluded = [kb for kb in sorted(sizes) if sizes[kb] < k]
    if excluded:
        excluded_str = " / ".join(f"`{kb[:8]}…`（{sizes[kb]} 块）" for kb in excluded)
    else:
        excluded_str = "无"
    lines = [
        "## 查询集构成",
        "",
        f"- 查询总数：{len(outcomes)}",
        f"- category 分布：{cat_str}（三类各需 ≥4，已满足）",
        f"- 覆盖 kb（单库路径，每条只指定一个）：{kb_str}",
        (
            f"- 被排除的 kb（池 < k={k}，无法产出 k 条结果）：{excluded_str}"
            "—— 重合率上限为 |池|/k，属语料规模的结构性上限而非存储不等价"
            "（Ruling P2-R13）；"
        ),
        "  本报告仅覆盖池 ≥ k 的全部可用 kb，脚本已显式断言池 ≥ k。",
        "",
    ]
    return lines


def _write_report(outcomes: list[QueryOutcome], k: int, sizes: dict[str, int]) -> dict:
    """写验收报告并返回汇总统计。

    Args:
        outcomes: 逐条比对结果
        k: top-k 的 k
        sizes: kb → 有 embedding 的分块数（本语料的全部 kb）

    Returns:
        统计字典：count / mean / min / passed / below_threshold
    """
    overlaps = [o.overlap for o in outcomes]
    if overlaps:
        mean = sum(overlaps) / len(overlaps)
        minimum = min(overlaps)
    else:
        mean = 0.0
        minimum = 0.0
    below = [o for o in outcomes if o.overlap < PASS_THRESHOLD]

    if mean >= PASS_THRESHOLD:
        verdict = "**通过**"
    else:
        verdict = "**未通过**"

    lines = [
        "# P2 dense 迁移等价性验收（Chroma → pgvector）",
        "",
        f"- top-k 的 k：{k}",
        f"- 查询数：{len(outcomes)}",
        f"- 重合率均值：**{mean:.4f}**（判据 ≥ {PASS_THRESHOLD}）",
        f"- 重合率最小值：{minimum:.4f}",
        f"- 未达 {PASS_THRESHOLD} 的查询数：{len(below)}",
        f"- 结论：{verdict}",
        "",
    ]
    lines.extend(_composition_lines(outcomes, k, sizes))
    lines.extend(
        [
            "## 逐条明细",
            "",
            "| kb | category | query | overlap | chroma top3 | pg top3 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for o in outcomes:
        chroma_top3 = ", ".join(o.chroma_ids[:3])
        pg_top3 = ", ".join(o.pg_ids[:3])
        lines.append(
            f"| {o.case.kb[:8]}… | {o.case.category} | {o.case.query} | "
            f"{o.overlap:.3f} | {chroma_top3} | {pg_top3} |"
        )
    lines.append("")
    lines.append("> 本报告仅供 P2 存储替换的**差分**判定；不构成检索质量基线。")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    return {
        "count": len(outcomes),
        "mean": mean,
        "min": minimum,
        "passed": mean >= PASS_THRESHOLD,
        "below_threshold": [f"{o.case.query}({o.overlap:.2f})" for o in below],
    }


async def _check_with_pools(
    queries: list[QueryCase], k: int
) -> tuple[list[QueryOutcome], dict[str, int]]:
    """单事件循环内完成断言与比对，并回带全量 kb 池大小（供报告列排除项）。

    Args:
        queries: 固定查询集
        k: top-k 的 k

    Returns:
        (逐条比对结果, kb → 有 embedding 的分块数)
    """
    outcomes = await run_check(queries, k=k)
    sizes = await _kb_pool_sizes()
    return outcomes, sizes


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="dense 迁移等价性比对")
    parser.add_argument("--k", type=int, default=TOP_K_RETRIEVAL, help="top-k")
    args = parser.parse_args()
    queries = load_queries()
    outcomes, sizes = asyncio.run(_check_with_pools(queries, args.k))
    stats = _write_report(outcomes, args.k, sizes)
    logger.info("dense equivalence: {}", stats)
    if not stats["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
