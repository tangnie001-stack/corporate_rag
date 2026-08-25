"""Agent 工具集 — retrieve_kb（知识库检索）与 ask_user（澄清追问）。

工具工厂 make_rag_tools 经闭包注入共享依赖（vector_store/bm25/reranker）；
per-request 对象（tool_contexts 收集器、ask_count 计数、clarify_channel）经
current_request_ctx 读取，不进闭包。工具不能写 state：检索上下文累积到
RequestContext.tool_contexts，由后续节点读入 state；ask_user 的挂起 Future
登记进进程级 pending_asks（POST /clarify-answer 是独立请求，contextvar 不可达）。
"""

import asyncio
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.config import TOP_K_RERANK
from src.config.const import ASK_USER_TIMEOUT, MAX_ASK_PER_TURN
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.request_context import current_request_ctx, pending_asks
from src.infra.search.bm25_index import BM25Index
from src.infra.search.query_router import SUGGESTIONS_MAP, aggregate_kb_entities
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


class AskQuestion(BaseModel):
    """ask_user 单条澄清问题（LLM 可见的入参契约）。"""

    id: str = Field(description="问题唯一 id，答案中回显")
    question: str = Field(description="问题文本")
    dimension: str = Field(
        default="free", description="缺失维度: company/period/metric/free"
    )
    multi_select: bool = Field(default=False, description="是否多选")


class AskUserArgs(BaseModel):
    """ask_user 工具参数（LLM 可见的入参契约）。"""

    questions: list[AskQuestion] = Field(description="需要用户补充的问题列表")


@tool("ask_user", args_schema=AskUserArgs)
async def ask_user(
    questions: list[AskQuestion],
    state: Annotated[AgentState | None, InjectedState()] = None,
) -> str:
    """向用户询问补充信息后继续，返回用户答案文本。

    何时调用：问题缺失关键实体（公司/期间/指标）且无法从上下文推断时调用；
    能回答就不要调用。问题选项由系统按维度从知识库注入真实候选（无候选时
    兜底静态 SUGGESTIONS_MAP），模型只负责问题措辞与询问时机，不生成候选。

    Args:
        questions: 需要用户补充的问题列表（含 id/question/dimension/multi_select）
        state: LangGraph 注入的 AgentState，读取 kb_id/_resolved_kb_ids 确定 KB 候选来源

    Returns:
        用户答案的 JSON 文本；超限/超时/取消时返回对应错误文本
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return "Error: 请求上下文不可用"
    if ctx.ask_count >= MAX_ASK_PER_TURN:  # 同步检查+自增，无 await
        return "Error: 已达本回合询问上限，请基于现有信息作答"
    ctx.ask_count += 1
    enriched = []
    for q in questions:
        options = await _load_dimension_options(q.dimension, state)
        enriched.append(
            {
                "id": q.id,
                "question": q.question,
                "options": options,
                "multi_select": q.multi_select,
            }
        )
    # 推送问题（经 channel → SSE 事件），随后登记挂起 Future 并等待答案
    await ctx.clarify_channel.put({"type": "ask_user", "questions": enriched})
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_asks[ctx.session_id] = fut
    try:
        answers = await _wait_with_abort_and_timeout(
            fut, ctx.abort_signal, ASK_USER_TIMEOUT
        )
        return str(answers)
    finally:
        pending_asks.pop(ctx.session_id, None)


async def _load_dimension_options(
    dimension: str, state: AgentState | None
) -> list[str]:
    """按维度加载问题选项：company/period 优先取 KB 聚合候选，否则兜底静态映射。

    Args:
        dimension: 缺失维度（company/period/metric/free）
        state: AgentState，提供 kb_id/_resolved_kb_ids 定位 KB 候选来源

    Returns:
        候选选项列表；KB 无候选且 dimension 不在 SUGGESTIONS_MAP 时为空列表
    """
    if dimension in ("company", "period"):
        if state is not None and state._resolved_kb_ids:
            kb_ids = state._resolved_kb_ids
        elif state is not None and state.kb_id:
            kb_ids = [state.kb_id]
        else:
            kb_ids = None
        aggregate = await aggregate_kb_entities(kb_ids)
        if dimension == "company":
            candidates = aggregate.companies
        else:
            candidates = aggregate.periods
        if candidates:
            return list(candidates)
    return SUGGESTIONS_MAP.get(dimension, [])


async def _wait_with_abort_and_timeout(
    fut: asyncio.Future, abort_signal: asyncio.Event, timeout: float
) -> Any:
    """等待答案 Future，与 abort 信号、超时三方竞争，先到者胜。

    Args:
        fut: 用户答案 Future（POST /clarify-answer 解析时 set_result）
        abort_signal: 请求取消信号（客户端断开/取消时置位）
        timeout: 等待用户回答的超时秒数（ASK_USER_TIMEOUT）

    Returns:
        答案内容（fut 先完成时）；取消/超时时返回对应错误文本
    """
    abort_task = asyncio.ensure_future(abort_signal.wait())
    try:
        done, _ = await asyncio.wait(
            {fut, abort_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if fut in done:
            if fut.cancelled():
                return "Error: 等待用户回答被取消"
            return fut.result()
        if abort_task in done:
            return "Error: 请求已取消"
        return "Error: 等待用户回答超时"
    finally:
        abort_task.cancel()
