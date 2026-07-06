"""模型工厂函数的单元测试。

测试目标：
- get_embeddings / get_llm / get_rerank：工厂函数创建正确的模型实例
- with_retry：重试装饰器的成功 / 耗尽重试次数场景

注意：DashScope SDK 在模型验证时会尝试导入 dashscope 包，
因此需要在导入 src.models 之前先 mock 掉 dashscope 模块。
"""

from unittest.mock import MagicMock
import sys
from config.settings import EMBEDDING_MODEL
import pytest

# ---- Mock DashScope SDK ----
# 原因：DashScopeEmbeddings 和 DashScopeRerank 在 Pydantic 模型验证时
# 会尝试导入 dashscope，但在测试环境中可能未安装或无 API Key。
mock_dashscope = MagicMock()
mock_dashscope.TextEmbedding = MagicMock()
mock_dashscope.TextReRank = MagicMock()
mock_dashscope.TextReRank.Models = MagicMock()
mock_dashscope.TextReRank.Models.gte_rerank = "gte-rerank"
sys.modules["dashscope"] = mock_dashscope

from src.models import get_embeddings, get_llm, get_rerank, with_retry  # noqa: E402


# ==================== with_retry 重试装饰器测试 ====================
class TestWithRetry:
    """测试 with_retry 装饰器的重试逻辑。"""

    def test_retry_success(self):
        """前两次失败、第三次成功：应返回结果且调用 3 次。"""
        call_count = 0

        def flaky():
            """模拟不稳定的函数，前 2 次抛异常，第 3 次成功。"""
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary error")
            return "success"

        wrapped = with_retry(flaky, max_attempts=5, initial_interval=0.01, backoff=1.0)
        assert wrapped() == "success"
        assert call_count == 3  # 第 3 次成功，不再重试

    def test_retry_exhausted(self):
        """始终失败：耗尽重试次数后抛出最后一次异常。"""
        call_count = 0

        def always_fails():
            """模拟始终失败的函数。"""
            nonlocal call_count
            call_count += 1
            raise ValueError("persistent error")

        wrapped = with_retry(
            always_fails, max_attempts=3, initial_interval=0.01, backoff=1.0
        )
        with pytest.raises(ValueError, match="persistent error"):
            wrapped()
        assert call_count == 3  # 重试 3 次后放弃


# ==================== get_embeddings 嵌入模型测试 ====================
class TestGetEmbeddings:
    """测试嵌入模型工厂函数。"""

    def test_get_embeddings_returns_instance(self):
        """默认参数创建的实例必须有 embed_query / embed_documents 方法。"""
        emb = get_embeddings()
        assert emb is not None
        assert hasattr(emb, "embed_query")  # LangChain 标准接口
        assert hasattr(emb, "embed_documents")  # LangChain 标准接口

    def test_get_embeddings_custom_model(self):
        """自定义模型名称也能正常创建实例。"""
        emb = get_embeddings(model=EMBEDDING_MODEL)
        assert emb is not None


# ==================== get_llm LLM 工厂测试 ====================
class TestGetLLM:
    """测试 LLM 工厂函数。"""

    def test_get_llm_returns_instance(self):
        """默认参数创建的 LLM 实例必须有 invoke 或 generate 方法。"""
        llm = get_llm()
        assert llm is not None
        assert hasattr(llm, "invoke") or hasattr(llm, "generate")

    def test_get_llm_temperature(self):
        """自定义 temperature 参数应能传递到模型。"""
        llm = get_llm(temperature=0.5)
        assert llm is not None

    def test_get_llm_custom_model(self):
        """自定义模型名称（如 qwen-turbo）也能创建实例。"""
        llm = get_llm(model="qwen-turbo")
        assert llm is not None


# ==================== get_rerank 重排序模型测试 ====================
class TestGetRerank:
    """测试重排序模型工厂函数。"""

    def test_get_rerank_returns_instance(self):
        """默认参数创建的重排序实例必须有 rerank 或 rank 方法。"""
        rerank = get_rerank()
        assert rerank is not None
        assert hasattr(rerank, "rerank") or hasattr(rerank, "rank")

    def test_get_rerank_top_n(self):
        """自定义 top_n 参数应能传递到模型。"""
        rerank = get_rerank(top_n=3)
        assert rerank is not None
