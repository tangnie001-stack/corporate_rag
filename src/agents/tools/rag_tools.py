"""Agent 工具集 — retrieve_kb（知识库检索）与 ask_user（澄清追问）。

工具工厂 make_rag_tools 经闭包注入共享依赖（vector_store/bm25/reranker）；
per-request 对象（tool_contexts 收集器、ask_count 计数、clarify_channel）经
current_request_ctx 读取，不进闭包。工具不能写 state：检索上下文累积到
RequestContext.tool_contexts，由后续节点读入 state；ask_user 的挂起 Future
登记进进程级 pending_asks（POST /clarify-answer 是独立请求，contextvar 不可达）。
"""

import asyncio
import time
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.agents.tools.ask_tools import AskQuestion, AskUserArgs, ask_user
from src.config import TOP_K_RERANK, settings
from src.config.const import ASK_USER_TIMEOUT, RERANK_TIMEOUT
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.bm25_index import BM25Index
from src.infra.search.query_router import aggregate_kb_entities
from src.rag import retrieval
from src.rag.context import RAGContext
from src.rag.retrieval import _ALL_ENTITY_KEYS

# ASK_USER_TIMEOUT / aggregate_kb_entities 为 ask_tools 的 monkeypatch 入口（测试经
# rag_tools 模块属性替换），并作为本模块对外 re-export 的一部分
__all__ = [
    "ASK_USER_TIMEOUT",
    "AskQuestion",
    "AskUserArgs",
    "RetrieveKBArgs",
    "aggregate_kb_entities",
    "ask_user",
    "make_rag_tools",
]


class RetrieveKBArgs(BaseModel):
    """retrieve_kb 工具参数（LLM 可见的入参契约）。"""

    query: str = Field(description="检索查询文本")
    top_k: int = Field(default=TOP_K_RERANK, ge=1, le=10, description="返回条数上限")


def make_rag_tools(
    vector_store: VectorStore,
    bm25: BM25Index | None,
    reranker,
    prompt_manager,
    embed_fn,
) -> list[BaseTool]:
    """构建 agent 工具列表，共享依赖经闭包注入。

    Args:
        vector_store: 向量存储实例（闭包注入，search 使用）
        bm25: BM25 检索引擎实例（闭包注入，混合检索时使用）
        reranker: Reranker 模型实例（闭包注入，rerank_results 使用）
        prompt_manager: 提示词管理器（闭包注入，当前工具未直接使用，保留签名）
        embed_fn: 嵌入函数（闭包注入，_semantic_select_kb 语义选库使用，需实现 embed_query）

    Returns:
        工具列表：retrieve_kb（知识库检索）、ask_user（澄清追问）；开启 web 兜底时追加 search_web
    """

    @tool("retrieve_kb", args_schema=RetrieveKBArgs)
    async def retrieve_kb(
        query: str,
        top_k: int = TOP_K_RERANK,
        state: Annotated[AgentState | None, InjectedState()] = None,
    ) -> str:
        """在财务知识库检索与 query 相关的文档片段并精排，返回带引用编号的文本。

        何时调用：问题涉及公司经营数据、财务指标、报告期等事实性内容时调用；
        闲聊、一般性概念问题不需要调用。

        Args:
            query: 检索查询文本
            top_k: 返回条数上限（默认 TOP_K_RERANK，精排后按此截断）
            state: LangGraph 注入的 AgentState，读取 kb_router 已解析的 KB 列表

        Returns:
            带全局递增引用编号的精排上下文文本，如 "[1] 来源: xxx (第3页)\\n内容: ..."
        """
        if state is not None:
            kb_ids = state._resolved_kb_ids
        else:
            kb_ids = None

        start = time.monotonic()
        # kb_ids 非空 → 多 KB 并行检索后合并去重；
        # kb_ids 为空（kb_router 未解析出 KB）→ 语义选库取最相关 1 个 KB 检索，匹配失败返回空结果
        if kb_ids:
            tasks = [
                retrieval.search(query, kb_id, vector_store, bm25) for kb_id in kb_ids
            ]
            per_kb_results = await asyncio.gather(*tasks)
            results = _merge_search_results(per_kb_results)
        else:
            matched_kb_id = await _semantic_select_kb(query, embed_fn)
            if matched_kb_id:
                results = await retrieval.search(
                    query, matched_kb_id, vector_store, bm25
                )
            else:
                results = []  # 无匹配 KB → 空工具结果，模型自行决定 abstain/ask/转人工

        # rerank 为同步 HTTP 调用（无内置超时），放线程池 + 超时兜底，避免阻塞事件循环；
        # 超时后降级为检索原始顺序（distance 升序），避免空结果触发 abstain
        try:
            contexts = await asyncio.wait_for(
                asyncio.to_thread(retrieval.rerank_results, query, results, reranker),
                timeout=RERANK_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "tool=retrieve_kb rerank timeout after {}s, fallback to raw order query={}",
                RERANK_TIMEOUT,
                query,
            )
            contexts = []
            for r in results:
                pc = r.metadata.get("parent_content")
                if pc:
                    content = pc
                else:
                    content = r.content
                contexts.append(
                    RAGContext(
                        content=content,
                        source=r.metadata.get("source", ""),
                        page=r.metadata.get("page", 0),
                        doc_id=r.metadata.get("doc_id", ""),
                        chunk_id=r.id,
                        parent_content=pc,
                        score=1 - r.distance if r.distance is not None else 0.0,
                        entities={
                            k: r.metadata.get(k)
                            for k in _ALL_ENTITY_KEYS
                            if r.metadata.get(k)
                        },
                    )
                )
        contexts = contexts[:top_k]

        if state is not None:
            iteration = state._agent_iterations
        else:
            iteration = 0
        logger.info(
            "tool=retrieve_kb iteration={} query={} result_count={} latency_ms={:.0f}",
            iteration,
            query,
            len(contexts),
            (time.monotonic() - start) * 1000,
        )

        # 全局递增编号：同步块内读取偏移并追加，无 await，asyncio 单线程保证原子
        ctx = current_request_ctx.get()
        if ctx is not None:
            collector = ctx.tool_contexts
        else:
            collector = []
        offset = len(collector)
        collector.extend(contexts)
        blocks = [
            f"[{offset + i + 1}] {c.to_prompt_text()}" for i, c in enumerate(contexts)
        ]
        logger.info(
            "judge: query={} stage=retrieve iteration={} result_count={}",
            query[:40],
            iteration,
            len(blocks),
        )
        return "\n\n".join(blocks)

    tools = [retrieve_kb, ask_user]
    if settings.WEB_SEARCH_ENABLED:
        from src.agents.tools.web_tools import search_web

        tools.append(search_web)
    return tools


async def _semantic_select_kb(query: str, embed_fn) -> str | None:
    """kb_router 未解析出 KB 时，语义匹配 query 与 KB name+description，返回最相关 1 个 KB id。

    惰性 import KbRepo/session_factory/current_user_id/KBRouter（参照 nodes.py
    make_kb_router_node 模式），避免模块导入时触发数据库引擎初始化。

    Args:
        query: 用户查询文本
        embed_fn: 嵌入函数，需实现 embed_query(text) -> list[float]

    Returns:
        最相关的知识库 id；无 KB 或相似度低于阈值（无 LLM 兜底）时返回 None
    """
    from src.infra.db.engine import session_factory
    from src.infra.db.mysql_db import KbRepo
    from src.infra.llm.trace_context import current_user_id
    from src.rag.kb_router import KBRouter

    uid = current_user_id.get()
    kbs = await KbRepo(session_factory).get_all_kb(uid)
    if not kbs:
        logger.info(
            "_semantic_select_kb: query={} no kb available, return None", query[:40]
        )
        return None

    router = KBRouter(embed_fn, None)  # 语义匹配，无 LLM 兜底
    matched = router.route(query, kbs)
    selected = matched[0] if matched else None
    logger.info(
        "_semantic_select_kb: query={} kb_count={} selected={}",
        query[:40],
        len(kbs),
        selected,
    )
    return selected


def _merge_search_results(results_list: list[list[ChunkResult]]) -> list[ChunkResult]:
    """合并多 KB 检索结果，按 chunk id 去重后保持首次出现顺序。

    Args:
        results_list: 各 KB 的检索结果（外层按 KB，内层为该 KB 的检索结果）

    Returns:
        去重后的扁平检索结果列表
    """
    merged: list[ChunkResult] = []
    seen: set[str] = set()
    for results in results_list:
        for item in results:
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append(item)
    return merged
