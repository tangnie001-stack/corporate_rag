"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.config.prompts import KB_UNBOUND_SYSTEM_PROMPT
from src.infra.llm.chat_message import ChatMessage
from src.infra.llm.prompt_manager import PromptManager
from src.rag.context import RAGContext


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(f"[{i + 1}] {ctx.to_prompt_text()}")
    return "\n\n".join(blocks)


def build_prompt(
    query: str,
    context: str,
    history: list[ChatMessage],
    prompt_manager: PromptManager,
    kb_bound: bool = True,
) -> list:
    """构建含系统指令和对话历史的完整 prompt。

    Args:
        query: 用户查询文本
        context: 已格式化的检索上下文（可能为空字符串）
        history: 对话历史（user/assistant 交替排列）
        prompt_manager: PromptManager，提供系统指令与用户模板
        kb_bound: 是否绑定知识库（默认 True）；False 时在系统提示后追加会话指令，
            明确禁止调用知识库检索工具（KB=RAG 开关软引导）

    Returns:
        LLM 消息列表：system（+未绑定时追加会话指令）+ 历史 + 当前 user 消息
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=prompt_manager.get_system_prompt())
    ]
    if not kb_bound:
        messages.append(SystemMessage(content=KB_UNBOUND_SYSTEM_PROMPT))
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
    messages: list[BaseMessage] = [
        SystemMessage(content=prompt_manager.get_system_prompt())
    ]
    for msg in history:
        if msg.role == "user":
            messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            messages.append(AIMessage(content=msg.content))
    messages.append(HumanMessage(content=query))
    return messages
