"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.config.prompts import (
    DELEGATE_GUIDANCE_SECTION,
    INLINE_CITATION_INSTRUCTION,
    KB_UNBOUND_SYSTEM_PROMPT,
)
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.prompt_manager import PromptManager, _with_current_date
from src.rag.context import RAGContext


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(f"[{i + 1}] {ctx.to_prompt_text()}")
    return "\n\n".join(blocks)


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
        kb_bound: 是否绑定知识库（False 时追加 KB_UNBOUND_SYSTEM_PROMPT 独立消息）
        has_skills: 本次会话是否有可用技能；仅当 persona 非空时用于决定是否追加
            委派引导段（persona 为空时恒追加，保证未选 agent 的 system 段逐字不变）
        prompt_manager: PromptManager 实例，提供基础段取值

    Returns:
        system 消息列表（未绑定 KB 时为两条：主 system + KB_UNBOUND）
    """
    if persona:
        base = persona
    else:
        base = prompt_manager.get_base_system_prompt()
    if INLINE_CITATION_INSTRUCTION not in base:
        base += INLINE_CITATION_INSTRUCTION
    if (has_skills or not persona) and DELEGATE_GUIDANCE_SECTION not in base:
        base += DELEGATE_GUIDANCE_SECTION
    messages: list[SystemMessage] = [SystemMessage(content=_with_current_date(base))]
    if not kb_bound:
        messages.append(SystemMessage(content=KB_UNBOUND_SYSTEM_PROMPT))
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
