"""Prompt 构建 — 将上下文、历史和问题组装为 LLM 消息列表。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.infra.llm.prompt_manager import PromptManager
from src.rag.context import RAGContext


def format_context(contexts: list[RAGContext]) -> str:
    """将检索上下文格式化为参考文档字符串。"""
    blocks = []
    for i, ctx in enumerate(contexts):
        blocks.append(
            f"[{i + 1}] 来源: {ctx.source} (第{ctx.page}页)\n内容: {ctx.content}"
        )
    return "\n\n".join(blocks)


def build_prompt(
    query: str,
    context: str,
    history: list[dict],
    prompt_manager: PromptManager,
) -> list:
    """构建含系统指令和对话历史的完整 prompt。"""
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    user_content = prompt_manager.get_user_template(context=context, query=query)
    messages.append(HumanMessage(content=user_content))
    return messages


def build_simple_prompt(
    query: str,
    history: list[dict],
    prompt_manager: PromptManager,
) -> list:
    """构建无检索上下文的简洁 prompt。"""
    messages = [SystemMessage(content=prompt_manager.get_system_prompt())]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=query))
    return messages
