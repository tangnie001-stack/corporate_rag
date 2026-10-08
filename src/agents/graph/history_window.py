"""历史窗口切分 —— 轮数粗筛 + 绝对 token 预算的唯一切分口径。

抽成纯函数的原因：跨轮历史摘要的**触发判据**必须与裁剪**同口径**
（判据落在"将被丢弃的那一段"），否则会把"保留尾部覆盖全量"误判为触阈。
本模块不读写任何存储、不做日志，可独立测试。
"""

from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.token_count import count_tokens


def _estimate_total(messages: list[ChatMessage]) -> int:
    """返回消息列表的总 token 数（分词器近似）。"""
    total = 0
    for message in messages:
        total += count_tokens(message.content)
    return total


def split_history_window(
    history: list[ChatMessage], max_turns: int, token_budget: int
) -> tuple[list[ChatMessage], list[ChatMessage]]:
    """按「轮数粗筛 → 绝对预算细裁」切出 (保留, 被丢弃)。

    保留段的语义与既有历史裁剪完全一致：先取最近 max_turns 轮，再按
    token 预算从最旧逐条弹出；最近 1 轮（最后两条）始终不裁。

    Args:
        history: 完整对话历史（user/assistant 交替）
        max_turns: 保留的最近轮数
        token_budget: 保留段的总 token 上限（绝对值）

    Returns:
        (kept, discarded)：kept 与旧实现的历史裁剪返回值逐一相等；
        discarded 为被丢弃的更旧部分（可能为空）
    """
    if len(history) > max_turns * 2:
        kept = history[-(max_turns * 2) :]
    else:
        kept = list(history)
    total = _estimate_total(kept)
    while total > token_budget and len(kept) > 2:
        dropped = kept.pop(0)
        total -= count_tokens(dropped.content)
    discarded = history[: len(history) - len(kept)]
    return kept, discarded


def exceeds_budget(messages: list[ChatMessage], token_budget: int) -> bool:
    """返回消息列表总 token 是否**超过**预算（等于不算超）。"""
    return _estimate_total(messages) > token_budget
