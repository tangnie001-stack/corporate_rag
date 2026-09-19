"""词项命中探针的 markdown 报告渲染。

与探针的**执行逻辑**（`scripts/lexical_probe.py`）分离：执行侧只负责测量并交出
实测计数器，本模块负责把计数器渲染成带口径说明的表。渲染结果本身是交付物
（见 `.superpowers/sdd/2026-09-19-postgres-storage-p3-lexical-tsvector/task-6-brief.md`），
因此口径、归因与局限性必须写进**渲染出的报告**，不能只留在任务报告里 ——
否则同一批数字会被读成它们并不支持的结论。

渲染约定：
- 每张表都同时给出**命中数与词项总数**（`hits/total`），不只给比率；
- 不可归因的行、定义性的行、测不出的差异，都在表下显式标注，不得省略。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExplicitCaseResult:
    """单条显式失效用例的实测结果。"""

    query: str  # 显式用例查询串（`EXPLICIT_CASES` 成员）
    mode: str  # 实际走的检索路径：`substring` / `prefix-AND` / `corpus-absent`
    verdict: str  # 命中列展示文本：`✅` / `❌` / `未验证（语料不含该串）`
    kb_id: str | None  # 命中所在知识库 id；语料不含该串时为 None


def _count_cell(count: int, total: int) -> str:
    """渲染命中数单元格 `hits/total`。"""
    return f"{count}/{total}"


def _rate_cell(count: int, total: int) -> str:
    """渲染命中率单元格；总数为 0 时无比率可算，显式写 `n/a`。"""
    if total > 0:
        rate = count / total
        return f"{rate:.4f}"
    return "n/a"


def _pp(count_delta: int, total: int) -> float:
    """把命中数差值换算成百分点；总数为 0 时返回 0.0 而非抛错。"""
    if total > 0:
        return abs(count_delta) / total * 100
    return 0.0


def _render_group_table(
    lines: list[str],
    rows: list[tuple[str, str]],
    total: int,
    counts: dict[str, int],
) -> None:
    """渲染一张「配置 / 命中数 / 命中率」表并追加到 lines。

    Args:
        lines: 输出行缓冲（原地追加）
        rows: (键, 展示名) 序列，决定表内行序
        total: 词项总数（各臂共用同一词项集）
        counts: 各臂的命中计数
    """
    lines += [
        "| 配置 | 命中数/总数 | 词项命中率 |",
        "|---|---|---|",
    ]
    for key, label in rows:
        count = counts[key]
        lines.append(
            f"| {label} | {_count_cell(count, total)} | {_rate_cell(count, total)} |"
        )


def _render_explicit_table(
    lines: list[str], explicit: list[ExplicitCaseResult]
) -> None:
    """渲染显式失效用例表并追加到 lines。"""
    lines += [
        "| 查询 | 走的路径 | 是否命中 | kb |",
        "|---|---|---|---|",
    ]
    for item in explicit:
        if item.kb_id is None:
            kb_cell = "—"
        else:
            kb_cell = f"{item.kb_id[:8]}…"
        lines.append(f"| {item.query} | {item.mode} | {item.verdict} | {kb_cell} |")


def render_report(
    *,
    total: int,
    kb_count: int,
    terms: list[str],
    tokenizer_counts: dict[str, int],
    construction_counts: dict[str, int],
    scoring_counts: dict[str, int],
    explicit: list[ExplicitCaseResult],
    k: int,
    max_query_k: int,
    ngram_min: int,
    ngram_max: int,
    df_min: int,
    df_ratio: float,
) -> str:
    """渲染探针的 markdown 报告。

    Args:
        total: 语料分块总数
        kb_count: 知识库数
        terms: 探针词项清单（确定性顺序）
        tokenizer_counts: 分组 A 各臂命中计数
        construction_counts: 分组 B 各臂命中计数
        scoring_counts: 分组 C 各臂命中计数
        explicit: 显式失效用例实测结果（每条 `EXPLICIT_CASES` 成员一条）
        k: 检索返回条数
        max_query_k: 检索条数上限（用于说明 k 的取值来源）
        ngram_min: 词项 n-gram 下界
        ngram_max: 词项 n-gram 上界
        df_min: 词项文档频率下界
        df_ratio: 词项文档频率上界占语料的比例

    Returns:
        完整 markdown 文本
    """
    term_total = len(terms)
    subtitle = f"- 探针词项数：{term_total}（n-gram {ngram_min}..{ngram_max}，df ∈ [{df_min}, {df_ratio}×N]）"

    caveat = (
        f"> {total} 分块属**小语料**；探针的用途是在同一语料上横向比配置，"
        "**既不测排序质量也不测语义召回**。"
    )
    term_source = (
        "- **词项来源**：从**原始正文**抽 CJK 连续段的 2..4 元 n-gram，"
        "不经任何分词器 —— 避免同一分词器既造索引又造标签的自我循环。"
    )
    hit_criterion = (
        "- **命中判据**：原始正文的字符串包含（`str.__contains__`），与分词器无关。"
    )
    term_filter = (
        f"- **词项筛选**：`{df_min} <= df <= {df_ratio}×N`，剔除 df=1 与高频项；再按 "
        "`(df 升序, 词项)` 取**最稀有**的若干项。**该取舍使词项偏向最难召回的一端，"
        "各臂命中率是偏保守（偏病态）的下界，不得读作平均检索质量。**「平凡通过」只在"
        "词项 df 较大、或池规模接近 k 时才成立，与本探针取样方向相反，不适用。"
    )
    kb_pin = (
        "- **单库钉定**：词项被钉在「第一个含它的 kb」上、只在该库内检索，多库语料下"
        "进一步压低命中率 —— 同属**使结果更病态**的取舍。"
    )
    rank_inert = (
        "- **排序是否决定成员，按臂分两类**：**过滤臂**（`prefix-AND` / `prefix-OR` / "
        f"`plainto-AND` / `jieba-tsrank` / `substring`）候选集由 WHERE 谓词决定，"
        f"`_term_kb` 保证所选库含该词项、候选规模 ≤ k={k}，`LIMIT k` 返回全部匹配行"
        " ⇒ 对排序**不敏感**（分组 C 零差是**结构必然**）；**未过滤的 BM25 臂**"
        f"（`char-bm25` / `jieba-bm25`）对**整池**排序取 top-{k}（池 ≫ k：各库 "
        f"2/1/1/51/121，大库 51/121 远超 k），**排序决定成员**，两臂 top-{k} 随 "
        "tokenization 不同而不同。"
    )
    decision_scope = (
        "- **本口径能裁决什么**：**能**登记 A1 的差分（已登记）；**不能**评判其好坏"
        "（判据是「原始正文是否包含」，非排序质量）；**不能**裁决分组 C（候选集相同）；"
        "分组 B 的 `prefix-OR` / `plainto-AND` 差异是**谓词覆盖面**的差，非排序质量。"
    )

    # ---- 分组 A1 ----
    char_count = tokenizer_counts["char-bm25"]
    jieba_count = tokenizer_counts["jieba-bm25"]
    diff = char_count - jieba_count
    a1_intro = "两臂除**分词方式**外完全同口径：同 BM25Okapi、同词项集、同在每库全池上排序（不过滤候选）、同 k。"
    a1_measured = (
        f"- 实测：`char-bm25` {_count_cell(char_count, term_total)} = "
        f"{_rate_cell(char_count, term_total)}；`jieba-bm25` "
        f"{_count_cell(jieba_count, term_total)} = {_rate_cell(jieba_count, term_total)}。"
    )
    a1_delta = (
        f"- 差值 = **{diff} 个词项 = {_pp(diff, term_total):.2f} 个百分点**"
        f"（n={term_total}，每词项 1/{term_total}），方向是 **char 更高** —— "
        "与「jieba 更好」相反，故**本次替换未观察到命中收益**。"
    )
    a1_constructive = (
        "- ⚠️ `char-bm25` 的满值属**构造性必然**：字符级索引以单字为单元，"
        "而探针查询 n-gram 逐字取自源文档，命中近乎必然。**1.0 不能证明"
        "「字符级检索质量更好」**；同理，也不能据此宣称 jieba 有收益。"
    )
    a1_rank_note = (
        "- ⚠️ 该差值**确是排序差异**（两臂都在 >k 的池上排序取 top-k，tokenization "
        "不同 ⇒ top-k 成员不同，见口径说明）；但本口径**不能把它判为「质量」差** —— "
        "命中判据是原始正文包含，且 `char-bm25` 的满值属构造性必然（见上）。"
    )

    # ---- 分组 A2 ----
    tsrank_count = tokenizer_counts["jieba-tsrank"]
    a2_intro = "以下两行相对 A1 **同时改了不止一个变量**，故不能与 A1 并列解读："
    a2_var1 = (
        "- `jieba-tsrank`：同时改了**候选生成**（先 `tsv @@ tsquery` 过滤，"
        "而非在每库全池上排序）与**打分**（`ts_rank`）。"
    )
    a2_var2 = (
        "- `pg-trgm`：同时改了**打分函数**（`similarity()`）与**切分单位**（trigram）。"
    )
    a2_attribution = (
        f"- `jieba-tsrank` = {_count_cell(tsrank_count, term_total)} = "
        f"{_rate_cell(tsrank_count, term_total)}，相对 A1 的下降**主要由候选生成"
        "（tsquery 过滤）解释**：查询词元在该库的 tsv 里不存在时谓词直接为空，"
        "与分词把它切没切碎无关。**不可归因于分词质量。**"
    )
    a2_role = (
        "- `jieba-tsrank` 是生产落地形态（存储侧依赖 `scripts/rewrite_content_seg.py` "
        "的重写结果）；`pg-trgm` 是本地唯一可对照的扩展方案，两者**都不构成对"
        "分词方案的证据**。"
    )

    # ---- 分组 B ----
    and_count = construction_counts["prefix-AND"]
    or_count = construction_counts["prefix-OR"]
    plainto_count = construction_counts["plainto-AND"]
    sub_count = construction_counts["substring"]
    delta_pp = _pp(or_count - and_count, term_total)
    b_substring = (
        f"- `substring` = {_count_cell(sub_count, term_total)} = "
        f"{_rate_cell(sub_count, term_total)} 是**定义性**行"
        "（**恒为 1.0，非策略对比**）：`_term_kb` 已保证所选 kb 含该词项，且词项 df "
        f"远小于 k={k}，故 `content LIKE '%term%' ... LIMIT k` **必然**返回全部含"
        "该词项的行 —— 命中是 WHERE 子句的同义反复。该行还是一次**无索引全表扫描、"
        "且无相关性排序**（`ORDER BY doc_id, chunk_index` 只提供确定性输出顺序，"
        "并非得分排序），**不是可选策略，不得读作「LIKE 最好」**。"
    )
    b_substring_vars = (
        "- `substring` 臂相对 `prefix-AND` **改了不止一个变量**：构造（`LIKE` vs "
        "`tsquery`）之外还改了排序（`ORDER BY doc_id, chunk_index` vs `ts_rank`）。"
    )
    b_and_or = (
        f"- `prefix-OR` vs `prefix-AND`：{_count_cell(or_count, term_total)} vs "
        f"{_count_cell(and_count, term_total)}，差 **{delta_pp:.1f} 个百分点**。"
        "OR 是 AND 的**超集谓词**；在判据为「集合成员」而非「排序」、且候选集近"
        "饱和的口径下，这是**机械后果而非质量证据**。"
    )
    b_plainto = (
        f"- `plainto-AND` = {_count_cell(plainto_count, term_total)} = "
        f"{_rate_cell(plainto_count, term_total)}，低于 `prefix-AND` "
        f"{_pp(and_count - plainto_count, term_total):.1f} 个百分点，同样受"
        "「排序不敏感」影响。"
    )

    # ---- 分组 C ----
    ts_count = scoring_counts["ts_rank"]
    tscd_count = scoring_counts["ts_rank_cd"]
    c_measured = (
        f"- 实测：`ts_rank` {_count_cell(ts_count, term_total)} = "
        f"{_rate_cell(ts_count, term_total)}；`ts_rank_cd` "
        f"{_count_cell(tscd_count, term_total)} = {_rate_cell(tscd_count, term_total)}。"
    )
    c_wording = (
        "- ⚠️ 零差不表示两个打分函数「同分」，而是：**候选集相同且规模 ≤ k 时该口径对"
        "排序不敏感**，函数差异**在此口径下无法测出**（见口径说明）；比较打分质量需"
        "候选集远大于 k 并比对 top-k 排序，本探针未做。"
    )

    # ---- 选型结论 ----
    conclusion_construction = (
        f"- 查询构造：**prefix-AND**（分组 B 命中数/总数：prefix-AND = "
        f"{_count_cell(and_count, term_total)} / prefix-OR = "
        f"{_count_cell(or_count, term_total)} / plainto-AND = "
        f"{_count_cell(plainto_count, term_total)} / substring = "
        f"{_count_cell(sub_count, term_total)}（定义性））"
    )
    conclusion_ranking = (
        f"- 打分算法：**ts_rank**（分组 C 命中数/总数：ts_rank = "
        f"{_count_cell(ts_count, term_total)} / ts_rank_cd = "
        f"{_count_cell(tscd_count, term_total)}）"
    )
    conclusion_tokenizer = (
        f"- 分词方案：**jieba + `to_tsvector('simple')`**（分组 A1 命中数/总数："
        f"char-bm25 = {_count_cell(char_count, term_total)} / jieba-bm25 = "
        f"{_count_cell(jieba_count, term_total)}）"
    )
    conclusion_joiner = (
        f"- **连接符不翻转**：`prefix-OR` 高出 `prefix-AND` 恰为 {delta_pp:.1f} 个"
        "百分点，判据字面是「**超过** 10 个百分点」，未触发；且 OR 是超集谓词、"
        "集合成员口径近饱和，+10pp 是机械后果而非质量证据。故保持 "
        '`_JOINER = " & "`。'
    )
    conclusion_scoring = (
        "- **打分函数不翻转**：分组 C 零差来自「该口径对排序不敏感」，非质量证据，"
        "故保持 `func.ts_rank(`。"
    )

    lines = [
        "# P3 词项命中探针（jieba + PG 全文检索 横向比较）",
        "",
        f"- 语料：{total} 个分块 / {kb_count} 个知识库",
        f"- 检索条数 k：{k}（= TOP_K_RETRIEVAL，上限 {max_query_k}）",
        subtitle,
        "",
        "> **仅供相对比较，不得作为质量基线或发布判据。**",
        caveat,
        "",
        "## 口径说明（读表前必读）",
        "",
        term_source,
        hit_criterion,
        term_filter,
        kb_pin,
        rank_inert,
        decision_scope,
        "",
        "## 分组 A1：分词配置（唯一同口径对比：char-bm25 vs jieba-bm25）",
        "",
        a1_intro,
        "",
    ]
    _render_group_table(
        lines,
        [("char-bm25", "char-bm25"), ("jieba-bm25", "jieba-bm25")],
        term_total,
        tokenizer_counts,
    )
    lines += ["", a1_measured, a1_delta, a1_constructive, a1_rank_note]

    lines += [
        "",
        "## 分组 A2：混合变量行（仅供参考，**不可归因**）",
        "",
        a2_intro,
        "",
        a2_var1,
        a2_var2,
        "",
    ]
    _render_group_table(
        lines,
        [("jieba-tsrank", "jieba-tsrank"), ("pg-trgm", "pg-trgm")],
        term_total,
        tokenizer_counts,
    )
    lines += ["", a2_attribution, a2_role]

    lines += [
        "",
        "## 分组 B：查询构造（固定分词 = jieba，固定打分 = ts_rank）",
        "",
    ]
    _render_group_table(
        lines,
        [
            ("prefix-AND", "prefix-AND"),
            ("prefix-OR", "prefix-OR"),
            ("plainto-AND", "plainto-AND"),
            ("substring", "substring（定义性，非策略对比）"),
        ],
        term_total,
        construction_counts,
    )
    lines += ["", b_substring, b_substring_vars, b_and_or, b_plainto]

    lines += [
        "",
        "## 分组 C：打分算法（固定 jieba + 前缀 AND）",
        "",
    ]
    _render_group_table(
        lines,
        [("ts_rank", "ts_rank"), ("ts_rank_cd", "ts_rank_cd")],
        term_total,
        scoring_counts,
    )
    lines += ["", c_measured, c_wording]

    explicit_intro = (
        "每条 `EXPLICIT_CASES` 成员都**必须**出现在下表；语料不含该串的用例记为 "
        "`corpus-absent`，不得静默消失。"
    )
    lines += [
        "",
        "## 显式失效用例（探针的 df 筛选测不到，必须单列）",
        "",
        explicit_intro,
        "",
    ]
    _render_explicit_table(lines, explicit)

    lines += [
        "",
        "## 选型结论",
        "",
        conclusion_construction,
        conclusion_ranking,
        conclusion_tokenizer,
        "",
        conclusion_joiner,
        conclusion_scoring,
        "",
        "## 词项清单（完整，供复现）",
        "",
        "```",
        " ".join(terms),
        "```",
        "",
    ]
    return "\n".join(lines)
