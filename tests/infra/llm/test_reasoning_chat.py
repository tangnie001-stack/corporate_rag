"""ChatQwenWithReasoning 子类：流式 chunk 提取 reasoning_content。"""

from langchain_core.messages import AIMessageChunk

from src.infra.llm.reasoning_chat import ChatQwenWithReasoning


def test_extracts_reasoning_content_to_additional_kwargs():
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15",
        api_key="sk-test",  # type: ignore[arg-type]  # 测试用假 key，真实 key 由 get_llm 注入
        base_url="http://localhost:8000",
    )
    chunk = {
        "choices": [
            {
                "delta": {"reasoning_content": "思考增量", "content": ""},
            }
        ]
    }
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs["reasoning_content"] == "思考增量"


def test_reasoning_alias_fallback():
    """OpenRouter 等用 reasoning 字段，应 fallback。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15",
        api_key="sk-test",  # type: ignore[arg-type]  # 测试用假 key，真实 key 由 get_llm 注入
        base_url="http://localhost:8000",
    )
    chunk = {"choices": [{"delta": {"reasoning": "fallback思考", "content": ""}}]}
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.additional_kwargs["reasoning_content"] == "fallback思考"


def test_no_reasoning_keeps_normal_content():
    """无 reasoning_content 时 content 正常透传，不影响既有行为。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15",
        api_key="sk-test",  # type: ignore[arg-type]  # 测试用假 key，真实 key 由 get_llm 注入
        base_url="http://localhost:8000",
    )
    chunk = {"choices": [{"delta": {"content": "正文", "role": "assistant"}}]}
    gen = llm._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert gen is not None
    assert gen.message.content == "正文"
    assert "reasoning_content" not in gen.message.additional_kwargs


def test_empty_choices_returns_chunk():
    """choices 为空时返回空内容 chunk（不崩溃）。"""
    llm = ChatQwenWithReasoning(
        model="qwen3.7-flash-2026-07-15",
        api_key="sk-test",  # type: ignore[arg-type]  # 测试用假 key，真实 key 由 get_llm 注入
        base_url="http://localhost:8000",
    )
    gen = llm._convert_chunk_to_generation_chunk({"choices": []}, AIMessageChunk, None)
    assert gen is not None
