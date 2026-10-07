"""Token 用量数据结构 — 统一描述 LLM 调用的 token 消耗。"""

from dataclasses import dataclass

from src.infra.llm.token_count import count_messages_tokens, count_tokens


@dataclass
class TokenUsage:
    """Token 用量统一结构 —— 跨调用点共享的估算与映射结果。"""

    prompt_tokens: int = 0  # 输入 token 数（提示部分，从 LLM 原生或估算）
    completion_tokens: int = 0  # 输出 token 数（补全部分，从 LLM 原生或估算）
    total_tokens: int = 0  # 总 token 数（prompt + completion）


def estimate_usage(messages: list, output: str) -> TokenUsage:
    """估算 token 用量（分词器计数；消息与输出复用同一计数入口）。"""
    prompt_tokens = max(1, count_messages_tokens(messages))
    completion_tokens = max(1, count_tokens(output))
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
