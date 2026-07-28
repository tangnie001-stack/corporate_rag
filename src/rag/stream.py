"""流式生成 — LLM 流式回答生成 + Token 估算。"""

import time
from typing import Generator, Optional

from loguru import logger

from src.config import RETRY_MAX_ATTEMPTS, RETRY_INITIAL_INTERVAL, RETRY_BACKOFF_FACTOR
from src.infra.llm.token_usage import TokenUsage
from src.infra.llm.trace_context import current_tracer


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


def stream_answer(
    messages: list,
    llm,
    trace_id: Optional[str] = None,
) -> Generator[str, None, None]:
    """流式生成 LLM 回答，支持指数退避重试。

    trace 通过 current_tracer ContextVar 自动读取（由 traced 装饰器设值）。

    Args:
        messages: 消息列表
        llm: LangChain LLM 实例
        trace_id: 追踪 ID

    Yields:
        LLM 生成的文本片段
    """
    tracer = current_tracer.get()
    gen_id = None
    if tracer:
        messages_snapshot = [
            {"role": getattr(m, "type", "unknown"), "content": m.content}
            for m in messages
            if hasattr(m, "type") or hasattr(m, "content")
        ]
        gen_id = tracer.start_generation(
            "llm_stream",
            input_data=messages_snapshot,
            model=getattr(llm, "model", None),
        )

    last_error: Optional[Exception] = None
    full_output = ""
    last_token_usage = TokenUsage()
    _stream_start = time.monotonic()
    _first_token = True

    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            stream = llm.stream(messages)
            for chunk in stream:
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    if _first_token:
                        _first_token = False
                        latency = (time.monotonic() - _stream_start) * 1000
                        logger.info("RAG first_token_latency={:.0f}ms", latency)
                    full_output += content
                    yield content
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    u = chunk.usage_metadata
                    last_token_usage = TokenUsage(
                        prompt_tokens=u.get("input_tokens", 0),
                        completion_tokens=u.get("output_tokens", 0),
                        total_tokens=u.get("total_tokens", 0),
                    )
            if (
                not last_token_usage.prompt_tokens
                and not last_token_usage.completion_tokens
            ):
                last_token_usage = estimate_usage(messages, full_output)
            if tracer:
                tracer.end_generation(
                    gen_id,
                    output=full_output,
                    usage=last_token_usage,
                )
            _gen_latency = (time.monotonic() - _stream_start) * 1000
            logger.info(
                "Generation completed: chars={} latency={:.0f}ms "
                "| tokens: prompt={} completion={} total={}",
                len(full_output),
                _gen_latency,
                last_token_usage.prompt_tokens,
                last_token_usage.completion_tokens,
                last_token_usage.total_tokens,
            )
            return
        except Exception as e:
            last_error = e
            if attempt < RETRY_MAX_ATTEMPTS:
                wait = RETRY_INITIAL_INTERVAL * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                logger.warning(
                    "LLM stream failed (attempt {}/{}): {}. Retrying in {:.1f}s...",
                    attempt,
                    RETRY_MAX_ATTEMPTS,
                    e,
                    wait,
                )
                time.sleep(wait)

    logger.error("LLM stream failed after {} attempts", RETRY_MAX_ATTEMPTS)
    error_msg = f"生成回答失败: {last_error}"
    full_output = error_msg
    if tracer:
        tracer.end_generation(gen_id, output=error_msg)
    yield error_msg
