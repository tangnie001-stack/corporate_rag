"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.config import settings
from src.config.prompts import loader, validation
from src.core import logging as core_logging
from src.core.log_events import Event
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.prompt_manager import PromptManager, _with_current_date
from src.rag.context import RAGContext

# 模板正文经唯一加载入口读取（loader.load_all 有 lru_cache，进程内零重复 I/O）。
# 段组装顺序与条件注入仍由 build_system_prompt 决定，本层只负责取正文。
_KB_BOUND_DISCIPLINE = loader.get_content("sources-kb-ladder")
_KB_UNBOUND = loader.get_content("sources-kb-unbound")
_INLINE_CITATION = loader.get_content("output-citation")
_DELEGATE_GUIDANCE = loader.get_content("tools-delegate")


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(f"[{i + 1}] {ctx.to_prompt_text()}")
    return "\n\n".join(blocks)


def _section_chars() -> dict[str, int]:
    """取各非空段的模板字符数（供组装事件与占比告警共用）。

    模板是打入镜像的静态文件，启动期已由 validation.validate_all() 校验，
    运行期不变；此处只借用非校验计数入口，不重复校验、不会在请求期失败。

    Returns:
        段名 → 字符数；空段（count=0）不出现

    说明：P0 口径是「各段模板正文字符数之和」；P1 引入段组装后应改为
    「实际拼进 system 的各段字符数」（含条件注入与领域三选一的结果）。
    """
    totals = validation.section_char_totals()
    return {name: count for name, count in totals.items() if count > 0}


def _warn_if_section_share_high(section_chars: dict[str, int]) -> None:
    """system 段估算占 context window 比例超阈值时记 warning（**不阻断**）。

    换算系数为跨模型借用的经验值、阈值为推断值（见 settings 的中文注释），
    仅作量级参考，不构成契约。

    Args:
        section_chars: 各段字符数（正整数）
    """
    # window 为环境变量可配；配成非正值时直接放弃估算，绝不让观测路径中断组装
    if settings.MODEL_CONTEXT_WINDOW_TOKENS <= 0:
        return
    total = sum(section_chars.values())
    est_tokens = int(total * settings.PROMPT_TOKENS_PER_CJK_CHAR)
    share = est_tokens / settings.MODEL_CONTEXT_WINDOW_TOKENS
    if share > settings.PROMPT_CONTEXT_SHARE_WARN:
        core_logging.log_event(
            Event.PROMPT_SECTION_SHARE_HIGH,
            est_tokens=est_tokens,
            share=round(share, 4),
            threshold=settings.PROMPT_CONTEXT_SHARE_WARN,
        )


def build_system_prompt(
    persona: str,
    kb_bound: bool,
    has_skills: bool,
    prompt_manager: PromptManager,
) -> list[SystemMessage]:
    """组装 system 消息（人设层 + 环境约束层）。

    追加顺序与既有 get_system_prompt() 一致：基础段 → 引用指令（带幂等守卫）
    → 委派引导（带守卫）→ 日期。persona 为空时逐字等同 get_system_prompt()。

    Args:
        persona: 人设层正文（会话智能体预设的 system_prompt）；空串=未选 agent，
            则用 prompt_manager.get_base_system_prompt() 作人设层
        kb_bound: 是否绑定知识库（False 时追加未绑库会话指令独立消息）
        has_skills: 本次会话是否有可用技能；仅当 persona 非空时用于决定是否追加
            委派引导段（persona 为空时恒追加，保证未选 agent 的 system 段逐字不变）
        prompt_manager: PromptManager 实例，提供基础段取值

    Returns:
        system 消息列表（未绑定 KB 时为两条：主 system + KB_UNBOUND）
    """
    if persona:
        base = persona
        persona_source = "preset"
    else:
        base = prompt_manager.get_base_system_prompt()
        persona_source = "base"
    # 环境约束层·检索纪律：仅当绑定 KB 且选定 agent（persona 非空）时注入。
    # persona 为空时人设层即 base-financial 模板，其处理流程 2–9 已含"先检索后作答"，
    # 无条件注入会破坏"默认行为逐字不变（端到端快照）"需求。
    discipline_injected = False
    if kb_bound and persona and _KB_BOUND_DISCIPLINE not in base:
        base += _KB_BOUND_DISCIPLINE
        discipline_injected = True
    if _INLINE_CITATION not in base:
        base += _INLINE_CITATION
    delegate_injected = False
    if (has_skills or not persona) and _DELEGATE_GUIDANCE not in base:
        base += _DELEGATE_GUIDANCE
        delegate_injected = True
    messages: list[SystemMessage] = [SystemMessage(content=_with_current_date(base))]
    if not kb_bound:
        messages.append(SystemMessage(content=_KB_UNBOUND))
    # system prompt 组成事实（design D11 #3/D15）：人设来源 + 条件注入命中 + system 段数
    # + 各段字符数（容器值，由日志层编码为紧凑 JSON）
    section_chars = _section_chars()
    core_logging.log_event(
        Event.PROMPT_ASSEMBLED,
        persona_source=persona_source,
        kb_bound=kb_bound,
        has_skills=has_skills,
        discipline_injected=discipline_injected,
        delegate_injected=delegate_injected,
        system_msgs=len(messages),
        section_chars=section_chars,
    )
    _warn_if_section_share_high(section_chars)
    return messages


def build_prompt(
    query: str,
    context: str,
    history: list[ChatMessage],
    prompt_manager: PromptManager,
    kb_bound: bool = True,
    persona: str = "",
    has_skills: bool = False,
) -> list:
    """构建含系统指令和对话历史的完整 prompt。

    Args:
        query: 用户查询文本
        context: 已格式化的检索上下文（可能为空字符串）
        history: 对话历史（user/assistant 交替排列）
        prompt_manager: PromptManager，提供系统指令与用户模板
        kb_bound: 是否绑定知识库（默认 True）；False 时在系统提示后追加会话指令，
            明确禁止调用知识库检索工具（KB=RAG 开关软引导）
        persona: 会话选定智能体的人设正文；空串=未选 agent，用系统默认人设（默认 ""）
        has_skills: 本次会话是否有可用技能；仅在 persona 非空时影响委派引导段（默认 False）

    Returns:
        LLM 消息列表：system（+未绑定时追加会话指令）+ 历史 + 当前 user 消息
    """
    messages: list[BaseMessage] = []
    messages.extend(build_system_prompt(persona, kb_bound, has_skills, prompt_manager))
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    user_content = prompt_manager.get_user_template(context=context, query=query)
    messages.append(HumanMessage(content=user_content))
    return messages


def build_simple_prompt(
    query: str,
    history: list[ChatMessage],
    prompt_manager: PromptManager,
) -> list:
    """构建无检索上下文的简洁 prompt。"""
    messages: list[BaseMessage] = []
    messages.extend(build_system_prompt("", True, False, prompt_manager))
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=query))
    return messages
