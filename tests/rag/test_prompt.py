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
class TestRAGChainHelpers:
    """测试 RAGChain 的内部辅助方法。"""

    def test_format_context_empty(self):
        """空上下文列表应返回空字符串。"""
        result = RAGChain._format_context([])
        assert result == ""

    def test_format_context_single(self):
        """单个上下文：必须包含引用编号 [1]、文件名、页码和内容。"""
        ctx = RAGContext(
            content="test content",
            source="test.pdf",
            page=5,
            doc_id="doc1",
            chunk_id="doc1:0",
            score=0.9,
        )
        result = RAGChain._format_context([ctx])
        assert "[1]" in result  # 引用编号
        assert "test.pdf" in result  # 文件名
        assert "第5页" in result  # 页码
        assert "test content" in result  # 内容

    def test_format_context_multiple(self):
        """多个上下文：每个都应有独立的引用编号。"""
        ctxs = [
            RAGContext(
                content="first",
                source="a.pdf",
                page=1,
                doc_id="d1",
                chunk_id="d1:0",
            ),
            RAGContext(
                content="second",
                source="b.pdf",
                page=2,
                doc_id="d2",
                chunk_id="d2:0",
            ),
        ]
        result = RAGChain._format_context(ctxs)
        assert "[1]" in result  # 第一个引用
        assert "[2]" in result  # 第二个引用
        assert "first" in result
        assert "second" in result

    def test_build_prompt_includes_system(self):
        """Prompt 构造：必须包含系统消息（角色设定）和用户消息。"""
        messages = RAGChain._build_prompt("query", "context", [])
        assert len(messages) == 2  # system + user
        assert messages[0].type == "system"
        assert "专业金融文档分析师" in messages[0].content  # 角色设定关键词

    def test_build_prompt_with_history(self):
        """带历史的 Prompt：system + 历史消息 + 当前用户消息。"""
        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]
        messages = RAGChain._build_prompt("query", "context", history)
        assert len(messages) == 4  # system + user + assistant + user
        assert messages[1].content == "previous question"
        assert messages[2].content == "previous answer"
        assert "query" in messages[3].content  # 当前查询
        assert "context" in messages[3].content  # 检索上下文
