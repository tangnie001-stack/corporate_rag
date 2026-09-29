"""Agent 工具 — ask_user（澄清追问）。

独立模块承载 ask_user 及其私有助手，避免 rag_tools.py 超过 400 行红线。
per-request 对象（clarify_channel、ask_count 计数）经 current_request_ctx 读取；
ask_user 的挂起 Future 登记进进程级 pending_asks（POST /clarify-answer 是独立
请求，contextvar 不可达）。rag_tools.make_rag_tools 经 re-export 使用本模块工具。
"""

import asyncio
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from loguru import logger
from pydantic import BaseModel, Field

from src.agents.graph.state import AgentState
from src.config import settings
from src.config.const import MAX_ASK_PER_TURN, SSEInteractionTexts
from src.infra.llm.request_context import current_request_ctx, pending_asks
from src.infra.search.query_router import SUGGESTIONS_MAP


class AskQuestion(BaseModel):
    """ask_user 单条澄清问题（LLM 可见的入参契约）。"""

    id: str = Field(description="问题唯一 id，答案中回显")
    question: str = Field(description="问题文本")
    dimension: str = Field(
        default="free", description="缺失维度: company/period/metric/free"
    )
    options: list[str] | None = Field(
        default=None,
        description="自定义候选选项：知识库问题不要填（系统按 dimension 注入真实候选防编造）；非知识库问题由你自行提供",
    )
    multi_select: bool = Field(default=False, description="是否多选")


class AskUserArgs(BaseModel):
    """ask_user 工具参数（LLM 可见的入参契约）。"""

    questions: list[AskQuestion] = Field(description="需要用户补充的问题列表")


def _ask_user_timeout() -> float:
    """读取 ASK_USER_TIMEOUT 等待超时；经 rag_tools 模块属性读取以保留测试 monkeypatch 入口。"""
    from src.agents.tools import rag_tools

    return rag_tools.ASK_USER_TIMEOUT


async def _aggregate_entities(kb_ids):
    """按 KB 聚合公司/期间候选；经 rag_tools 模块属性调用以保留测试 monkeypatch 入口。"""
    from src.agents.tools import rag_tools

    return await rag_tools.aggregate_kb_entities(kb_ids)


def _read_state_fields(state: AgentState | dict | None) -> tuple[str, int, str]:
    """从图状态读取 (query_text, iteration, kb_id)，显式形状判定两种承载。

    主循环已内化进 create_agent 装配产物，工具只会拿到映射（dict），键由装配 seed；
    外层自建图注入 AgentState 实例。AgentState 承载已无循环计数字段，迭代序号取 0
    （iteration 仅入日志，主循环计数走映射的 _turn_count）。形状无法识别或键缺失时
    走降级分支并记 warning（含工具名与缺失字段名）。

    Args:
        state: LangGraph 注入的执行状态（映射或 AgentState 实例，可为 None）

    Returns:
        (query_text, iteration, kb_id)：缺失项各取兜底值（空串 / 0）
    """
    if isinstance(state, AgentState):
        return state.query, 0, state.kb_id
    if isinstance(state, dict):
        missing = [key for key in ("query", "_turn_count", "kb_id") if key not in state]
        if missing:
            logger.warning(
                "tool=ask_user state missing fields fields={}", ",".join(missing)
            )
        return (
            str(state.get("query", "")),
            int(state.get("_turn_count", 0)),
            str(state.get("kb_id", "")),
        )
    logger.warning("tool=ask_user state missing fields fields=state")
    return "", 0, ""


@tool("ask_user", args_schema=AskUserArgs)
async def ask_user(
    questions: list[AskQuestion],
    state: Annotated[AgentState | None, InjectedState()] = None,
) -> str:
    """向用户询问补充信息后继续，返回用户答案文本。

    何时调用：问题缺失关键实体（公司/期间/指标）且无法从上下文推断时调用；
    能回答就不要调用。选项解析随 ASK_USER_MODE_DSH 分支：dash 模式（默认）直接用
    模型自带 options（可为空 = 纯文本问题）；dual 模式模型自带优先，无 options 时
    按维度从知识库注入真实候选（无候选时兜底静态 SUGGESTIONS_MAP）。

    Args:
        questions: 需要用户补充的问题列表（含 id/question/dimension/options/multi_select）
        state: LangGraph 注入的图状态（显式形状判定取 query / 迭代序号 / kb_id，
            映射承载为装配 seed 的键；AgentState 承载读实例字段）

    Returns:
        用户答案的 JSON 文本；超限/超时/取消时返回对应错误文本
    """
    ctx = current_request_ctx.get()
    if ctx is None:
        return SSEInteractionTexts.ASK_USER_CTX_UNAVAILABLE
    # 显式形状判定取 query / 迭代序号：主循环已内化进装配产物，注入的通常是映射，
    # 外层自建图给 AgentState 实例；两种承载都由 _read_state_fields 收敛
    query_text, iteration, _kb_id = _read_state_fields(state)
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
        if settings.ASK_USER_MODE_DSH:
            options = q.options or []  # dash 模式：全部模型自带，可为空 = 纯文本问题
        elif q.options:
            options = q.options  # dual 模式：模型自带优先（非 KB 问题）
        else:
            options = await _load_dimension_options(q.dimension, state)  # dual：KB 注入
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
        answers = await wait_with_abort_and_timeout(
            fut, ctx.abort_signal, _ask_user_timeout()
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
    dimension: str, state: AgentState | dict | None
) -> list[str]:
    """按维度加载问题选项：company/period 优先取 KB 聚合候选，否则兜底静态映射。

    Args:
        dimension: 缺失维度（company/period/metric/free）
        state: 图状态（显式形状判定取 kb_id，定位 KB 候选来源）

    Returns:
        候选选项列表；KB 无候选且 dimension 不在 SUGGESTIONS_MAP 时为空列表
    """
    if dimension in ("company", "period"):
        _query_text, _iteration, kb_id = _read_state_fields(state)
        if kb_id:
            kb_ids = [kb_id]
        else:
            kb_ids = None
        aggregate = await _aggregate_entities(kb_ids)
        if dimension == "company":
            candidates = aggregate.companies
        else:
            candidates = aggregate.periods
        if candidates:
            return list(candidates)
    return SUGGESTIONS_MAP.get(dimension, [])


async def wait_with_abort_and_timeout(
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
