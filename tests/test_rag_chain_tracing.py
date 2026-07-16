"""Tests for Langfuse tracing integration in RAGChain.

Tests cover:
  - Handler is None when LANGFUSE_ENABLE is False
  - CallbackHandler is created when LANGFUSE_ENABLE is True
  - Graceful degradation when CallbackHandler init fails
  - _stream_answer passes callbacks config to llm.stream()
"""

from unittest.mock import MagicMock, patch

from src.rag.chain import RAGChain


@patch("src.rag_chain.LANGFUSE_ENABLE", False)
def test_langfuse_handler_not_created_when_disabled():
    """When LANGFUSE_ENABLE is False, no handler is created."""
    chain = RAGChain()
    assert chain._langfuse_handler is None


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.Langfuse")
@patch("src.rag_chain.CallbackHandler")
def test_langfuse_handler_created_when_enabled(mock_handler_cls, mock_langfuse):
    """When LANGFUSE_ENABLE is True, CallbackHandler is initialized."""
    mock_handler = MagicMock()
    mock_handler_cls.return_value = mock_handler

    chain = RAGChain()

    mock_langfuse.assert_called_once()
    mock_handler_cls.assert_called_once()
    assert chain._langfuse_handler == mock_handler


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.Langfuse")
@patch("src.rag_chain.CallbackHandler")
def test_langfuse_init_failure_does_not_crash(mock_handler_cls, mock_langfuse):
    """When CallbackHandler init fails, chain still works without tracing."""
    mock_handler_cls.side_effect = Exception("Connection refused")

    chain = RAGChain()  # Should not raise

    assert chain._langfuse_handler is None


@patch("src.rag_chain.LANGFUSE_ENABLE", True)
@patch("src.rag_chain.Langfuse")
@patch("src.rag_chain.CallbackHandler")
def test_stream_answer_passes_callbacks(mock_handler_cls, mock_langfuse):
    """_stream_answer passes callbacks config to llm.stream()."""
    mock_handler = MagicMock()
    mock_handler_cls.return_value = mock_handler
    chain = RAGChain()
    mock_llm = MagicMock()
    mock_llm.stream.return_value = []
    chain._llm = mock_llm

    # Construct mock messages
    messages = [MagicMock()]

    list(chain._stream_answer(messages))

    mock_llm.stream.assert_called_once()
    _, kwargs = mock_llm.stream.call_args
    assert "config" in kwargs
    assert kwargs["config"]["callbacks"] == [mock_handler]
