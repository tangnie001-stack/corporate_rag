"""词法检索查询条件的构造与转义 —— 查询侧词元口径的唯一入口。

用户原文**不得**直接交给 PostgreSQL 的 tsquery 解析器：`&` / `|` / `!` / `:` /
空格会在解析期抛错（实测 `to_tsquery('simple','研发费用 5 月')` →
`ERROR: syntax error in tsquery`），而 `a:` 这类输入会被**静默吞字符**（得到 `'a'`）。
在并发聚合取数的路径上，抛错的影响面比静默降召回更大。

因此查询串在此由**安全词元**拼装：每个词元先按允许字符集剔除，再拼成
`词元:*`（前缀通配，词形不一致时的唯一救回手段），以 ` & ` 连接。
剔除后不剩词元时降级为**原文子串匹配** —— 写入侧已把单字从 `content_seg`
剔除，tsv 里没有单字 lexeme，"回退为不过滤"命中不了任何东西。

有空/纯空白两种边界状态：原文为空或纯空白由 `is_blank` 标记，调用方据此
直接返回空结果 —— 不得提交 tsquery，也不得退化为子串兜底（`LIKE '%%'`
会命中全库）。
"""

import re
from dataclasses import dataclass

from src.infra.search.tokenizer import tokenize

# 允许进入 tsquery 的字符：Unicode 词字符 = 中日韩字符 / 字母 / 数字 / 下划线。
# 其余（含 tsquery 操作符与空白）一律剔除 —— 剔除而非转义，因为 tsquery 没有
# 可用的通用转义形式，白名单是唯一无歧义的做法
_UNSAFE_RE = re.compile(r"\W", re.UNICODE)

# 词元之间的连接符：前缀 AND
_JOINER = " & "


@dataclass(frozen=True)
class LexicalQuery:
    """词法检索的查询条件。"""

    terms: tuple[str, ...]
    """安全化后的词元，顺序与分词结果一致。"""
    tsquery: str
    """`词元:*` 以 ` & ` 连接；词元为空时为空串（此时不得提交给数据库）。"""
    raw: str
    """用户原文，作为原文子串匹配（LIKE）的匹配串。"""
    use_substring: bool
    """True = 词元全被滤掉，改走原文子串匹配。"""
    is_blank: bool
    """True = 原文为空或纯空白，无任何可检内容。调用方 SHALL 直接返回空结果：
    不得提交 tsquery，也不得走子串兜底（`LIKE '%%'` 会命中全库）。"""


def build_lexical_query(query: str) -> LexicalQuery:
    """把用户查询转成可安全提交的检索条件。

    Args:
        query: 用户原始查询文本

    Returns:
        LexicalQuery；`use_substring` 为 True 时只有 `raw` 有意义；
        `is_blank` 为 True 时其余字段均无意义，调用方须直接返回空结果。
    """
    if not query.strip():
        return LexicalQuery(
            terms=(), tsquery="", raw="", use_substring=False, is_blank=True
        )
    terms: list[str] = []
    for token in tokenize(query):
        safe = _UNSAFE_RE.sub("", token)
        if safe:
            terms.append(safe)
    if not terms:
        return LexicalQuery(
            terms=(), tsquery="", raw=query, use_substring=True, is_blank=False
        )
    tsquery = _JOINER.join(f"{t}:*" for t in terms)
    return LexicalQuery(
        terms=tuple(terms),
        tsquery=tsquery,
        raw=query,
        use_substring=False,
        is_blank=False,
    )


def escape_like(value: str) -> str:
    """转义 LIKE 模式里的特殊字符。

    不转义时用户查询里的 `%` 会匹配任意串（查 "%" 命中全库）、`_` 会匹配单字符。

    Args:
        value: 原始子串

    Returns:
        转义后的模式片段（配合 `.like(..., escape="\\\\")` 使用）
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
