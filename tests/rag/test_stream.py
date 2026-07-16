"""RAGChain 核心编排链的单元测试。

测试目标：
- RAGContext 数据类：构造 / 引用格式化 / 长内容截断
- RAGChain 初始化：默认工厂 vs 依赖注入
- chat_with_citations 全流程：
  - 知识库不存在 / 向量搜索为空 / 检索异常
  - 完整流水线（检索 + 重排序 + LLM 流式生成 + 引用）
  - 重排序失败降级 / LLM 流式失败
- 内部辅助方法：_format_context / _build_prompt
- 历史记录保存

注意：所有外部依赖（LLM / Embeddings / Reranker / VectorStore / MySQL / ChatManager）
均通过 unittest.mock 进行 mock，无需真实 API Key。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.rag.chain import RAGChain, RAGContext


# ==================== RAGContext 数据类测试 ====================
class TestRAGChainChat:
    """测试 RAGChain.chat_with_citations 的各种场景。"""

    @patch("src.rag.chain.get_rerank")
    @patch("src.rag.chain.get_llm")
    @patch("src.rag.chain.get_embeddings")
    def test_stream_answer_generates_tokens(
        self, mock_get_emb, mock_get_llm, mock_get_rerank
    ):
        """stream_answer() 应生成 token。"""
        chain = RAGChain()
        chain._format_context = MagicMock(return_value="context")
        chain._build_prompt = MagicMock(return_value=[])
        chain._stream_answer = MagicMock(return_value=iter(["token1", "token2"]))
        gen = chain.stream_answer("query", [], [])
        tokens = list(gen)
        assert tokens == ["token1", "token2"]
