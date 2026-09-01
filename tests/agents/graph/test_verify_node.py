"""测试验证循环节点 — extract_years / completeness_check / faithfulness_check。

faithfulness_check 的 judge LLM 通过 monkeypatch mock src.models.get_llm
（FakeJudgeLLM），不构造真实 RAGAS_LLM_MODEL，不发真实网络/API 调用。
"""

import pytest
from langchain_core.messages import AIMessage

from src.agents.graph.verify_node import (
    completeness_check,
    extract_years,
    faithfulness_check,
)
from src.rag.context import RAGContext


def test_extract_years():
    """应提取答案中所有 4 位年份，无年份时返回空集合。"""
    assert extract_years("2024年营收 3943 亿，2023 年 3000 亿") == {2023, 2024}
    assert extract_years("近三年持续增长") == set()


def test_completeness_check():
    """应返回要求年份中答案未覆盖的缺失年份（升序）。"""
    assert completeness_check([2023, 2024, 2025], "2024年营收3943亿") == [2023, 2025]
    assert completeness_check([2024], "2024年营收3943亿") == []


class FakeJudgeLLM:
    """极简 fake judge LLM：ainvoke 返回固定 judge 输出（AIMessage）。

    返回 AIMessage 而非 dict，因为 faithfulness_check 通过
    getattr(resp, "content", None) 读取响应文本（属性访问）。
    """

    async def ainvoke(self, messages, **kwargs):
        """返回带固定 JSON 内容的 AIMessage，等价于真实模型响应。"""
        return AIMessage(content='{"unsupported": ["句X"]}')


def _make_contexts() -> list[RAGContext]:
    """构造一条含 content 的引用上下文。"""
    return [
        RAGContext(
            content="2024年营收3943亿",
            source="a.pdf",
            page=1,
            doc_id="d1",
            chunk_id="d1:0",
        )
    ]


@pytest.mark.asyncio
async def test_faithfulness_check_returns_unsupported(monkeypatch):
    """应返回 judge 标出的无支撑句子清单。"""
    monkeypatch.setattr("src.models.get_llm", lambda *args, **kwargs: FakeJudgeLLM())
    result = await faithfulness_check("答案", _make_contexts())
    assert result == ["句X"]


@pytest.mark.asyncio
async def test_faithfulness_check_empty_contexts(monkeypatch):
    """contexts 为空时应直接返回空清单，不调用 get_llm。"""

    def fail_if_called(*args, **kwargs):
        raise AssertionError("contexts 为空时不应构造 judge LLM")

    monkeypatch.setattr("src.models.get_llm", fail_if_called)
    result = await faithfulness_check("答案", [])
    assert result == []
