"""词项命中探针 —— 词法路的分词配置、查询构造、打分算法的横向比较。

**判据口径**（`design.md` D6 固化）：
- 探针词项从**原始正文**抽取（CJK 滑窗 n-gram），`SHALL NOT` 用分词后的
  `content_seg` 或 tsquery 自判 —— 否则同一分词器既造索引又造标签，构成自我循环；
- 词项按文档频率筛选 `2 <= df <= DF_RATIO * N`（剔除 df=1 的无从判断项与
  高频的平凡通过项），长度 >= 2；
- 命中以**原始正文的字符串包含**判定，与分词器无关；
- 分组 A 分两档：**A1** 只有 `char-bm25` vs `jieba-bm25` 是同口径对比
  （同 BM25Okapi、同每库全池、同 k），其差值才可归因于分词方式；**A2** 的
  `jieba-tsrank` / `pg-trgm` 相对 A1 同时改了候选生成或打分，**不可归因于
  分词质量**，仅供参考；
- 报告只报命中数与词项总数及完整词项清单，**仅供相对比较**。渲染由
  `scripts/lexical_probe_report.py` 承担（本模块只测量并交出计数器）。

**小语料限制（必须写进报告）**：本探针词项按 `(df 升序, 词项)` 取**最稀有**项，
且每个词项被钉在单个库上，故各臂命中率是**偏保守（偏病态）的下界**；
「平凡通过」只在词项 df 较大、或池规模接近 k 时才成立。探针的用途是
**在同一语料上横向比配置**，`SHALL NOT` 作为质量基线或发布判据。

用法：
    POSTGRES_HOST=localhost .venv/bin/python scripts/lexical_probe.py \
        --out docs/tmp/p3-lexical-probe-2026-09-19.md
"""

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

from rank_bm25 import BM25Okapi
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 直接以 `python scripts/lexical_probe.py` 运行时 sys.path[0] 是 scripts/，
# 仓库根不在其中；补上仓库根以便 import src.*。`python -m scripts.lexical_probe`
# 下仓库根已在 path 中，重复插入无副作用。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.lexical_probe_report import ExplicitCaseResult, render_report
from src.config.const import MAX_QUERY_K
from src.config.settings import TOP_K_RETRIEVAL
from src.infra.db.engine import run_and_dispose, session_factory
from src.infra.db.lexical_query import build_lexical_query, escape_like
from src.infra.search.tokenizer import tokenize

# 探针参数（本脚本专用的分析参数，不是业务阈值）
NGRAM_MIN = 2
NGRAM_MAX = 4
DF_MIN = 2
DF_RATIO = 0.1
MAX_TERMS = 30
CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")

# 显式覆盖的失效类（探针自带的 df 筛选与"原文包含"判据测不到它们）
EXPLICIT_CASES = ("涨了吗", "5 月", "增长", "营业收入", "资产负债率", "净利润")


def derive_terms(docs: list[str], max_terms: int) -> list[str]:
    """从原始正文派生探针词项。

    候选 = CJK 连续段的 2..4 元 n-gram（机械抽取，不经任何分词器），
    再按文档频率筛选，最后按 (df 升序, 词项) 取前 max_terms 个。

    Args:
        docs: 原始正文列表（每条一个分块）
        max_terms: 返回词项数上限

    Returns:
        词项列表（确定性顺序）
    """
    total = len(docs)
    if total == 0:
        return []
    upper = int(DF_RATIO * total)
    df: dict[str, int] = {}
    for doc in docs:
        candidates: set[str] = set()
        for run in CJK_RUN.findall(doc):
            for size in range(NGRAM_MIN, NGRAM_MAX + 1):
                for start in range(len(run) - size + 1):
                    candidates.add(run[start : start + size])
        for term in candidates:
            df[term] = df.get(term, 0) + 1
    kept = [term for term, count in df.items() if DF_MIN <= count <= upper]
    kept.sort(key=lambda t: (df[t], t))
    return kept[:max_terms]


def hit_rate(ranked_contents: list[str], term: str) -> bool:
    """判定某词项是否被召回（原始正文的字符串包含）。

    Args:
        ranked_contents: 检索返回结果的原始正文（按得分降序）
        term: 探针词项

    Returns:
        True = 至少一条含该词项的正文进了返回集
    """
    return any(term in content for content in ranked_contents)


async def _bm25_top_contents(
    docs: list[str], term: str, k: int, tokenizer_fn
) -> list[str]:
    """用 BM25Okapi 在给定 tokenization 上取 top-k 的原始正文。

    Args:
        docs: 语料（原始正文）
        term: 查询词项
        k: 返回条数
        tokenizer_fn: 分词函数（字符级用 list，词项级用 tokenize）

    Returns:
        命中文档的原始正文，按得分降序
    """
    corpus = [tokenizer_fn(doc) for doc in docs]
    query_tokens = tokenizer_fn(term)
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [docs[i] for i in ranked]


async def _sql_top_contents(
    session: AsyncSession, sql: str, params: dict, k: int
) -> list[str]:
    """执行一条候选 SQL 并返回命中文档的原始正文（kb_id 已在 params 里绑定）。"""
    result = await session.execute(text(sql), {**params, "k": k})
    return [row[0] for row in result.all()]


async def _term_kb(session: AsyncSession, term: str) -> str | None:
    """取包含该词项的、按 id 排序的第一个知识库（保持单库路径）。"""
    result = await session.execute(
        text(
            "SELECT kb_id FROM chunks WHERE content LIKE :pat ESCAPE '\\'"
            " ORDER BY kb_id LIMIT 1"
        ),
        {"pat": f"%{escape_like(term)}%"},
    )
    row = result.first()
    if row is None:
        return None
    return row[0]


async def _probe_group_tokenizer(session, corpus, k) -> dict[str, int]:
    """分组 A：分词配置（返回各臂命中计数；比率与总数由渲染侧合成）。

    只有 `char-bm25` 与 `jieba-bm25` 是**唯一同口径对比**（同 BM25Okapi、同在
    每库全池上排序、同 k）。`jieba-tsrank` 还改了候选生成与打分，`pg-trgm` 还改了
    打分函数与切分单位 —— 二者渲染时单列为 A2「混合变量行」，**不可归因于
    分词质量**。
    """
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    hits: dict[str, int] = {
        "char-bm25": 0,
        "jieba-bm25": 0,
        "jieba-tsrank": 0,
        "pg-trgm": 0,
    }
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        docs = corpus["by_kb"][kb_id]
        if hit_rate(await _bm25_top_contents(docs, term, k, list), term):
            hits["char-bm25"] += 1
        if hit_rate(await _bm25_top_contents(docs, term, k, tokenize), term):
            hits["jieba-bm25"] += 1
        plan = build_lexical_query(term)
        rows = await _sql_top_contents(
            session,
            "SELECT content FROM chunks WHERE kb_id = :kb_id"
            " AND tsv @@ to_tsquery('simple', :tsq)"
            " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
            {"kb_id": kb_id, "tsq": plan.tsquery},
            k,
        )
        if hit_rate(rows, term):
            hits["jieba-tsrank"] += 1
        rows = await _sql_top_contents(
            session,
            "SELECT content FROM chunks WHERE kb_id = :kb_id"
            " ORDER BY similarity(content, :term) DESC LIMIT :k",
            {"kb_id": kb_id, "term": term},
            k,
        )
        if hit_rate(rows, term):
            hits["pg-trgm"] += 1
    return hits


async def _probe_group_construction(session, corpus, k) -> dict[str, int]:
    """分组 B：查询构造（固定分词 = jieba，固定打分 = ts_rank）。

    `substring` 是**定义性**臂（词项由 `_term_kb` 保证在库内存在且 df < k，
    命中是同义反复），且相对 `prefix-AND` 还改了排序（`ORDER BY doc_id,
    chunk_index` vs `ts_rank`）—— 渲染时须显式标注，不得读作可选策略。
    """
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    variants = ("prefix-AND", "prefix-OR", "plainto-AND", "substring")
    hits: dict[str, int] = {name: 0 for name in variants}
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        plan = build_lexical_query(term)
        and_tsq = plan.tsquery
        or_tsq = " | ".join(plan.tsquery.split(" & "))
        table = {
            "prefix-AND": (
                (
                    "SELECT content FROM chunks WHERE kb_id = :kb_id"
                    " AND tsv @@ to_tsquery('simple', :tsq)"
                    " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k"
                ),
                {"kb_id": kb_id, "tsq": and_tsq},
            ),
            "prefix-OR": (
                (
                    "SELECT content FROM chunks WHERE kb_id = :kb_id"
                    " AND tsv @@ to_tsquery('simple', :tsq)"
                    " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k"
                ),
                {"kb_id": kb_id, "tsq": or_tsq},
            ),
            "plainto-AND": (
                (
                    "SELECT content FROM chunks WHERE kb_id = :kb_id"
                    " AND tsv @@ plainto_tsquery('simple', :seg)"
                    " ORDER BY ts_rank(tsv, plainto_tsquery('simple', :seg)) DESC, id LIMIT :k"
                ),
                {"kb_id": kb_id, "seg": " ".join(plan.terms)},
            ),
            "substring": (
                (
                    "SELECT content FROM chunks WHERE kb_id = :kb_id"
                    " AND content LIKE :pat ESCAPE '\\'"
                    " ORDER BY doc_id, chunk_index LIMIT :k"
                ),
                {"kb_id": kb_id, "pat": f"%{escape_like(term)}%"},
            ),
        }
        for name, (sql, params) in table.items():
            rows = await _sql_top_contents(session, sql, params, k)
            if hit_rate(rows, term):
                hits[name] += 1
    return hits


async def _probe_group_scoring(session, corpus, k) -> dict[str, int]:
    """分组 C：打分算法（固定 jieba + 前缀 AND）。

    候选集相同且规模 <= k 时，本口径（集合成员判定）对排序不敏感，故两臂零差
    表示**差异无法在此口径下测出**，不表示两个打分函数同分。
    """
    terms = derive_terms(corpus["all_contents"], MAX_TERMS)
    hits: dict[str, int] = {"ts_rank": 0, "ts_rank_cd": 0}
    for term in terms:
        kb_id = await _term_kb(session, term)
        if kb_id is None:
            continue
        plan = build_lexical_query(term)
        for name, fn in (("ts_rank", "ts_rank"), ("ts_rank_cd", "ts_rank_cd")):
            rows = await _sql_top_contents(
                session,
                f"SELECT content FROM chunks WHERE kb_id = :kb_id"
                f" AND tsv @@ to_tsquery('simple', :tsq)"
                f" ORDER BY {fn}(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k",
                {"kb_id": kb_id, "tsq": plan.tsquery},
                k,
            )
            if hit_rate(rows, term):
                hits[name] += 1
    return hits


async def _probe_explicit_cases(session, corpus, k) -> list[ExplicitCaseResult]:
    """显式失效用例：① 全单字查询 ② 词形与文档词元不一致。

    语料不含该串的用例**不得静默跳过**（`retrieval-quality` 要求两类失效用例
    显式包含且不得静默 0 命中）：转入 mode = `corpus-absent` 的行，命中列写
    「未验证（语料不含该串）」，使「某用例被跳过」成为可见事实。函数末尾按
    `EXPLICIT_CASES` 顺序核对每条 `query`，重复或遗漏即抛错（进程非零退出）。
    """
    rows: list[ExplicitCaseResult] = []
    for case in EXPLICIT_CASES:
        kb_id = await _term_kb(session, case)
        if kb_id is None:
            rows.append(
                ExplicitCaseResult(
                    query=case,
                    mode="corpus-absent",
                    verdict="未验证（语料不含该串）",
                    kb_id=None,
                )
            )
            continue
        plan = build_lexical_query(case)
        if plan.use_substring:
            sql = (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND content LIKE :pat ESCAPE '\\' ORDER BY doc_id, chunk_index LIMIT :k"
            )
            params = {"kb_id": kb_id, "pat": f"%{escape_like(case)}%"}
            mode = "substring"
        else:
            sql = (
                "SELECT content FROM chunks WHERE kb_id = :kb_id"
                " AND tsv @@ to_tsquery('simple', :tsq)"
                " ORDER BY ts_rank(tsv, to_tsquery('simple', :tsq)) DESC, id LIMIT :k"
            )
            params = {"kb_id": kb_id, "tsq": plan.tsquery}
            mode = "prefix-AND"
        contents = await _sql_top_contents(session, sql, params, k)
        if hit_rate(contents, case):
            verdict = "✅"
        else:
            verdict = "❌"
        rows.append(
            ExplicitCaseResult(query=case, mode=mode, verdict=verdict, kb_id=kb_id)
        )
    if [row.query for row in rows] != list(EXPLICIT_CASES):
        raise RuntimeError(
            f"explicit cases mismatch: {[row.query for row in rows]}"
            f" != {list(EXPLICIT_CASES)}"
        )
    return rows


async def _main(out: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT content, kb_id FROM chunks ORDER BY kb_id, doc_id, chunk_index"
            )
        )
        rows = result.all()
        contents = [r[0] for r in rows]
        by_kb: dict[str, list[str]] = {}
        for content, kb_id in rows:
            by_kb.setdefault(kb_id, []).append(content)
        corpus = {"all_contents": contents, "by_kb": by_kb}
        terms = derive_terms(contents, MAX_TERMS)
        k = min(TOP_K_RETRIEVAL, MAX_QUERY_K)
        tokenizer_group = await _probe_group_tokenizer(session, corpus, k)
        construction_group = await _probe_group_construction(session, corpus, k)
        scoring_group = await _probe_group_scoring(session, corpus, k)
        explicit = await _probe_explicit_cases(session, corpus, k)
    report = render_report(
        total=len(contents),
        kb_count=len(by_kb),
        terms=terms,
        tokenizer_counts=tokenizer_group,
        construction_counts=construction_group,
        scoring_counts=scoring_group,
        explicit=explicit,
        k=k,
        max_query_k=MAX_QUERY_K,
        ngram_min=NGRAM_MIN,
        ngram_max=NGRAM_MAX,
        df_min=DF_MIN,
        df_ratio=DF_RATIO,
    )
    Path(out).write_text(report, encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="词项命中探针")
    parser.add_argument("--out", required=True, help="报告输出路径")
    args = parser.parse_args()
    asyncio.run(run_and_dispose(_main(args.out)))


if __name__ == "__main__":
    main()
