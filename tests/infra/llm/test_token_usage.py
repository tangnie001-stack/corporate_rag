"""TokenUsage 不变量测试 —— 不发网络。"""


def test_estimate_usage_total_is_sum_of_parts():
    """经由构造入口产出的实例，total 恒等于两项之和（spec delta 的场景）。"""
    from src.infra.llm.token_usage import estimate_usage

    est = estimate_usage([], "hello world")
    assert est.total_tokens == est.prompt_tokens + est.completion_tokens
    assert est.total_tokens > 0


def test_estimate_usage_lives_with_token_usage():
    """estimate_usage 与 TokenUsage 同模块（delta 的场景：不再是 rag/stream.py）。"""
    import src.infra.llm.token_usage as mod

    assert hasattr(mod, "TokenUsage")
    assert hasattr(mod, "estimate_usage")
