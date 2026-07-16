"""Tests for LangfuseTracer — 官方 SDK 封装层的测试。

测试覆盖：
  - 无密钥时静默降级（_initialized == False）
  - 有密钥时正常初始化（mock Langfuse 客户端）
  - 未初始化时所有方法返回 None / 不报错
"""

from unittest.mock import MagicMock, patch

import pytest
from langfuse import Langfuse


def test_init_without_keys():
    """LANGFUSE_SECRET_KEY 为空时，tracer 不初始化。"""
    with (
        patch("src.config.LANGFUSE_SECRET_KEY", ""),
        patch("src.config.LANGFUSE_PUBLIC_KEY", ""),
    ):
        from src.infra.llm.langfuse_tracing import LangfuseTracer

        tracer = LangfuseTracer()
        assert tracer._initialized is False
        assert tracer._client is None


def test_init_without_public_key():
    """LANGFUSE_PUBLIC_KEY 为空时，tracer 不初始化。"""
    with (
        patch("src.config.LANGFUSE_SECRET_KEY", "sk-test"),
        patch("src.config.LANGFUSE_PUBLIC_KEY", ""),
    ):
        from src.infra.llm.langfuse_tracing import LangfuseTracer

        tracer = LangfuseTracer()
        assert tracer._initialized is False


def test_init_success():
    """密钥完整时，tracer 创建官方 Langfuse 客户端。"""
    with (
        patch("src.infra.llm.langfuse_tracing.Langfuse") as MockLangfuse,
        patch("src.config.LANGFUSE_SECRET_KEY", "sk-test"),
        patch("src.config.LANGFUSE_PUBLIC_KEY", "pk-test"),
        patch("src.config.LANGFUSE_HOST", "http://localhost:3000"),
    ):
        from src.infra.llm.langfuse_tracing import LangfuseTracer

        mock_client = MagicMock(spec=Langfuse)
        MockLangfuse.return_value = mock_client

        tracer = LangfuseTracer()

        assert tracer._initialized is True
        assert tracer._client is mock_client
        MockLangfuse.assert_called_once_with(
            public_key="pk-test",
            secret_key="sk-test",
            host="http://localhost:3000",
        )


def test_init_exception():
    """Langfuse 初始化抛出异常时，tracer 静默降级。"""
    with (
        patch("src.infra.llm.langfuse_tracing.Langfuse") as MockLangfuse,
        patch("src.config.LANGFUSE_SECRET_KEY", "sk-test"),
        patch("src.config.LANGFUSE_PUBLIC_KEY", "pk-test"),
        patch("src.config.LANGFUSE_HOST", "http://localhost:3000"),
    ):
        MockLangfuse.side_effect = Exception("Connection failed")

        from src.infra.llm.langfuse_tracing import LangfuseTracer

        tracer = LangfuseTracer()
        assert tracer._initialized is False


def test_start_trace_returns_none_when_not_initialized():
    """未初始化时 start_trace 返回 None。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = False
    result = tracer.start_trace("test")
    assert result is None


def test_end_trace_does_nothing_when_not_initialized():
    """未初始化时 end_trace 不报错。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = False
    tracer.end_trace("some-trace-id")  # 不应抛出异常


def test_start_generation_returns_none_when_not_initialized():
    """未初始化时 start_generation 返回 None。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = False
    result = tracer.start_generation("trace-id", "gen")
    assert result is None


def test_end_generation_does_nothing_when_not_initialized():
    """未初始化时 end_generation 不报错。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = False
    tracer.end_generation("gen-id", "trace-id")  # 不应抛出异常


def test_end_trace_does_nothing_when_trace_id_empty():
    """trace_id 为空时 end_trace 不操作。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = True
    tracer._client = MagicMock()
    tracer.end_trace("")  # 不应调用 client
    tracer._client.trace.assert_not_called()


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("start_trace", {"name": "test"}),
        ("end_trace", {"trace_id": "t1"}),
        ("start_generation", {"trace_id": "t1", "name": "gen"}),
        ("end_generation", {"gen_id": "g1", "trace_id": "t1"}),
    ],
)
def test_methods_succeed_when_initialized(method, kwargs):
    """初始化后各方法调用 langfuse 客户端对应方法。"""
    from src.infra.llm.langfuse_tracing import LangfuseTracer

    tracer = LangfuseTracer()
    tracer._initialized = True
    tracer._client = MagicMock()

    mock_result = MagicMock()
    mock_result.id = "mock-id"
    tracer._client.trace.return_value = mock_result
    tracer._client.generation.return_value = mock_result

    if method == "start_trace":
        result = tracer.start_trace(**kwargs)
        assert result == "mock-id"
    elif method == "end_trace":
        tracer.end_trace(**kwargs)
    elif method == "start_generation":
        result = tracer.start_generation(**kwargs)
        assert result == "mock-id"
    elif method == "end_generation":
        tracer.end_generation(**kwargs)

    client_method_name = method.replace("start_", "").replace("end_", "")
    getattr(tracer._client, client_method_name).assert_called()
