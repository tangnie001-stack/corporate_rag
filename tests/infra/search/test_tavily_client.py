"""Tavily REST 客户端测试：search/extract 正常与熔断（mock transport，不发起真实网络）。"""

import httpx
import pytest

from src.infra.search.tavily_client import tavily_extract, tavily_search


def _mock_transport(json_body: object, status: int = 200) -> httpx.MockTransport:
    """构造返回固定 JSON 的 MockTransport，用于隔离真实网络调用。

    json_body 可为任意 JSON 可序列化对象（dict/list 等），用于模拟
    Tavily 返回非 dict 结构的异常响应。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_tavily_search_parses_results():
    """正常返回时解析出 url/title/content/score 字段。"""
    transport = _mock_transport(
        {
            "results": [
                {
                    "url": "https://a.com",
                    "title": "标题",
                    "content": "正文",
                    "score": 0.9,
                }
            ]
        }
    )
    out = await tavily_search("测试", top_k=5, timeout=5.0, transport=transport)
    assert len(out) == 1
    assert out[0]["url"] == "https://a.com"
    assert out[0]["title"] == "标题"
    assert out[0]["score"] == 0.9


@pytest.mark.asyncio
async def test_tavily_search_failure_returns_empty():
    """HTTP 5xx / 网络异常时熔断返回空列表，不抛异常。"""
    transport = _mock_transport({"error": "boom"}, status=500)
    out = await tavily_search("测试", transport=transport)
    assert out == []


@pytest.mark.asyncio
async def test_tavily_search_non_dict_json_returns_empty():
    """Tavily 返回合法 JSON 但非 dict（list）时熔断返回空列表（解析在 try 内）。"""
    transport = _mock_transport([1, 2])
    out = await tavily_search("测试", transport=transport)
    assert out == []


@pytest.mark.asyncio
async def test_tavily_extract_non_dict_json_returns_empty():
    """Tavily extract 返回非 dict JSON（list）时熔断返回空列表（解析在 try 内）。"""
    transport = _mock_transport([1, 2])
    out = await tavily_extract(["https://a.com"], timeout=5.0, transport=transport)
    assert out == []


@pytest.mark.asyncio
async def test_tavily_extract_parses_content():
    """extract 正常返回时解析出 url/content。"""
    transport = _mock_transport(
        {"results": [{"url": "https://a.com", "raw_content": "长正文"}]}
    )
    out = await tavily_extract(["https://a.com"], timeout=5.0, transport=transport)
    assert out == [{"url": "https://a.com", "content": "长正文"}]
