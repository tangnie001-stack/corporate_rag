"""Token 用量数据结构 — 统一描述 LLM 调用的 token 消耗。"""

from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token 用量统一结构 — 用于 end_generation 的参数传递。"""

    prompt_tokens: int = 0  # 输入 token 数（提示部分，从 LLM 原生或估算）
    completion_tokens: int = 0  # 输出 token 数（补全部分，从 LLM 原生或估算）
    total_tokens: int = 0  # 总 token 数（prompt + completion）
