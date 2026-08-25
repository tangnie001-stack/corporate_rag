"""Agent 工具集 — retrieve_kb（知识库检索）。

工具工厂 make_rag_tools 经闭包注入共享依赖（vector_store/bm25/reranker）；
per-request 对象（tool_contexts 收集器）经 current_request_ctx 读取，不进闭包。
工具不能写 state：检索上下文累积到 RequestContext.tool_contexts，由后续节点读入 state。
"""

import asyncio
from typing import Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.config import TOP_K_RERANK
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.request_context import current_request_ctx
from src.infra.search.bm25_index import BM25Index
from src.rag import retrieval


class RetrieveKBArgs(BaseModel):
    """retrieve_kb 工具参数（LLM 可见的入参契约）。"""

    query: str = Field(description="检索查询文本")
    top_k: int = Field(default=TOP_K_RERANK, ge=1, le=10, description="返回条数上限")


def make_rag_tools(
    vector_store: VectorStore, bm25: BM25Index | None, reranker, prompt_manager
) -> list[BaseTool]:
    """构建 agent 工具列表，共享依赖经闭包注入。

    Args:
        vector_store: 向量存储实例（闭包注入，search 使用）
        bm25: BM25 检索引擎实例（闭包注入，混合检索时使用）
        reranker: Reranker 模型实例（闭包注入，rerank_results 使用）
        prompt_manager: 提示词管理器（预留，ask_user 工具使用，本任务未使用）

    Returns:
        工具列表，当前仅含 retrieve_kb
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

        # 多 KB 并行检索后合并去重；None/空列表按全量检索（kb_id="" 触发 search 的 similarity_search_all）
        if kb_ids:
            tasks = [
                retrieval.search(query, kb_id, vector_store, bm25) for kb_id in kb_ids
            ]
            per_kb_results = await asyncio.gather(*tasks)
            results = _merge_search_results(per_kb_results)
        else:
            results = await retrieval.search(query, "", vector_store, bm25)

        contexts = retrieval.rerank_results(query, results, reranker)
        contexts = contexts[:top_k]

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
        return "\n\n".join(blocks)

    return [retrieve_kb]


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
