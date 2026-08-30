"""Agent 工具集 — retrieve_kb（知识库检索）与 ask_user（澄清追问）。

工具工厂 make_rag_tools 经闭包注入共享依赖（vector_store/bm25/reranker）；
per-request 对象（tool_contexts 收集器、ask_count 计数、clarify_channel）经
current_request_ctx 读取，不进闭包。工具不能写 state：检索上下文累积到
RequestContext.tool_contexts，由后续节点读入 state；ask_user 的挂起 Future
登记进进程级 pending_asks（POST /clarify-answer 是独立请求，contextvar 不可达）。
"""

import asyncio
import time
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.config import TOP_K_RERANK, settings
from src.config.const import (
    ASK_USER_TIMEOUT,
    MAX_ASK_PER_TURN,
    RERANK_TIMEOUT,
    SSEInteractionTexts,
)
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkResult
from src.infra.llm.request_context import current_request_ctx, pending_asks
from src.infra.search.bm25_index import BM25Index
from src.infra.search.query_router import SUGGESTIONS_MAP, aggregate_kb_entities
from src.rag import retrieval
from src.rag.context import RAGContext
from src.rag.retrieval import _ALL_ENTITY_KEYS


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
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    if state is not None:
        query_text = state.query
        iteration = state._agent_iterations
    else:
        query_text = ""
        iteration = 0
    if ctx.ask_count >= MAX_ASK_PER_TURN:  # 同步检查+自增，无 await
        logger.warning(
            "ask_user limit reached session_id={} query={}",
            ctx.session_id,
            query_text,
        )
        return SSEInteractionTexts.ASK_USER_LIMIT_REACHED
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
    logger.info(
        "tool=ask_user iteration={} questions={} session_id={}",
        iteration,
        len(questions),
        ctx.session_id,
    )
    # 单槽保护：登记前检查同一 session 是否已有挂起澄清（并发 ask_user），
    # 已存在则拒绝本次提问，避免覆盖前一个 Future（检查与登记间无 await，原子）
    if ctx.session_id in pending_asks:
        logger.warning(
            "ask_user slot occupied session_id={} query={}",
            ctx.session_id,
            query_text,
        )
        return SSEInteractionTexts.ASK_USER_LIMIT_REACHED
    # 先登记挂起 Future（进程级注册表）再推送问题事件，避免 POST /clarify-answer 在登记前到达
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_asks[ctx.session_id] = fut
    try:
        # 推送问题（经 channel → SSE 事件），随后等待答案
        await ctx.clarify_channel.put({"type": "ask_user", "questions": enriched})
        answers = await _wait_with_abort_and_timeout(
            fut, ctx.abort_signal, ASK_USER_TIMEOUT
        )
        if str(answers) in (
            SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT,
            SSEInteractionTexts.ASK_USER_REQUEST_CANCELLED,
            SSEInteractionTexts.ASK_USER_ANSWER_CANCELLED,
        ):
            logger.warning(
                "ask_user ended session_id={} query={} outcome={}",
                ctx.session_id,
                query_text,
                answers,
            )
        return str(answers)
    finally:
        pending_asks.pop(ctx.session_id, None)
        fut.cancel()


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
                return SSEInteractionTexts.ASK_USER_ANSWER_CANCELLED
            return fut.result()
        if abort_task in done:
            return SSEInteractionTexts.ASK_USER_REQUEST_CANCELLED
        return SSEInteractionTexts.ASK_USER_TIMEOUT_TEXT
    finally:
        abort_task.cancel()
