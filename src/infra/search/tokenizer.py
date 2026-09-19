"""词法检索的分词入口 —— 写入侧与查询侧的唯一分词口径。

写入（`chunks.content_seg`）与查询（tsquery 词元）必须调用**同一个函数、
同一份配置**：两侧口径不一致不会报错，只会静默降召回。

分词结果会随 `chunks.tsv` 生成列**固化落库**，因此 `jieba` 的版本或词典
一旦变更，存量 `content_seg` 与新的查询侧就不再一致 —— 那是同一种静默失效的
第二种形态（进程内比较的守卫测试抓不到）。缓解有两条：依赖 pin 精确版本
（`pyproject.toml`），以及变更后必须跑 `scripts/rewrite_content_seg.py --apply`。
"""

import jieba

from src.config.const import LEXICAL_MIN_TOKEN_LEN


def tokenize(text: str) -> list[str]:
    """按 jieba 精确模式切词并过滤长度不足的词项。

    Args:
        text: 正文原文或用户查询

    Returns:
        词项列表（保留原顺序与重复项）；长度 < LEXICAL_MIN_TOKEN_LEN 的被剔除
    """
    return [t for t in jieba.lcut(text) if len(t) >= LEXICAL_MIN_TOKEN_LEN]


def to_lexical_text(text: str) -> str:
    """把正文转成写入 `chunks.content_seg` 的检索文本。

    `tsv` 是 `content_seg` 的生成列，用空格切出词边界后，`to_tsvector('simple')`
    只需按非字母数字切分即可得到词项级 lexeme。

    Args:
        text: 分块正文原文

    Returns:
        空格连接的词项串；无词项时为空串
    """
    return " ".join(tokenize(text))
