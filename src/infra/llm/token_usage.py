"""Token 用量数据结构 — 统一描述 LLM 调用的 token 消耗。"""

from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token 用量统一结构 —— 跨调用点共享的估算与映射结果。"""

    prompt_tokens: int = 0  # 输入 token 数（提示部分，从 LLM 原生或估算）
    completion_tokens: int = 0  # 输出 token 数（补全部分，从 LLM 原生或估算）
    total_tokens: int = 0  # 总 token 数（prompt + completion）


def estimate_usage(messages: list, output: str) -> TokenUsage:
    """粗略估算 token 用量。"""
    input_text = " ".join(
        getattr(m, "content", "") for m in messages if hasattr(m, "content")
    )
    prompt_tokens = max(1, len(input_text) // 2)
    completion_tokens = max(1, len(output) // 2)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
