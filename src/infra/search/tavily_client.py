"""Tavily REST 客户端 — search + extract 的 httpx 直调封装。

独立模块避免向 rag_tools.py 引入 HTTP 细节；函数接受可选 transport
便于测试注入 MockTransport，生产环境走真实网络。所有函数异常时返回
空列表（熔断语义），由调用方（search_web）决定降级路径。
"""

import httpx
from loguru import logger

from src.config import settings

_SEARCH_URL = "https://api.tavily.com/search"
_EXTRACT_URL = "https://api.tavily.com/extract"


def _client(timeout: float, transport) -> httpx.AsyncClient:
    """构造 AsyncClient，可注入 transport 用于测试。"""
    if transport is not None:
        return httpx.AsyncClient(timeout=timeout, transport=transport)
    return httpx.AsyncClient(timeout=timeout)


async def tavily_search(
    query: str,
    top_k: int = 5,
    timeout: float = 5.0,
    transport=None,
) -> list[dict]:
    """调用 Tavily search，返回归一化结果列表。

    Args:
        query: 搜索查询文本
        top_k: 返回结果条数上限
        timeout: 请求超时秒数
        transport: 测试注入的 httpx transport，None 时走真实网络

    Returns:
        [{"url", "title", "content", "score"}]；调用失败/超时返回空列表
    """
    payload = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "max_results": top_k,
        "search_depth": "basic",
    }
    try:
        async with _client(timeout, transport) as client:
            resp = await client.post(_SEARCH_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
        # 结果解析也在 try 内：data 非 dict（list/str）时 .get 抛 AttributeError
        # 同样走熔断返回空列表，确保"所有异常返回 []"契约不被绕过
        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
            }
            for r in data.get("results", [])
            if r.get("url")
        ]
    except Exception:  # noqa: BLE001
        logger.exception("tavily_search failed query={}", query[:40])
        return []


async def tavily_extract(
    urls: list[str],
    timeout: float = 5.0,
    transport=None,
) -> list[dict]:
    """调用 Tavily extract 拉取 URL 正文。

    Args:
        urls: 需要拉取正文的 URL 列表
        timeout: 请求超时秒数
        transport: 测试注入的 httpx transport，None 时走真实网络

    Returns:
        [{"url", "content"}]；调用失败/超时返回空列表
    """
    payload = {"api_key": settings.TAVILY_API_KEY, "urls": urls}
    try:
        async with _client(timeout, transport) as client:
            resp = await client.post(_EXTRACT_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
        # 结果解析在 try 内，data 非 dict 时同样熔断返回空列表（见 tavily_search）
        return [
            {"url": r.get("url", ""), "content": r.get("raw_content", "")}
            for r in data.get("results", [])
            if r.get("url")
        ]
    except Exception:  # noqa: BLE001
        logger.exception("tavily_extract failed urls={}", len(urls))
        return []
