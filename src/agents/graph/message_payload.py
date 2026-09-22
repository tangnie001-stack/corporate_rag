"""消息文本提取与 Langfuse 输入载荷构造（纯函数，无业务依赖）。

`_extract_text` / `_messages_payload` 原本内联在 `agent_node`，与其图节点逻辑
无耦合；抽为独立模块以守住单文件行数红线（CLAUDE.md：单文件 ≤ 400 行）。
"""

from langchain_core.messages import BaseMessage


def _extract_text(message: BaseMessage | None) -> str:
    """从 AIMessage 提取文本 content（str 或 content blocks）。

    Args:
        message: 消息对象，None 时返回空字符串

    Returns:
        content 的纯文本形式：str 直接返回；list 拼接 dict blocks 中 type=="text" 的 text；
        其他类型 str() 兜底
    """
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _messages_payload(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """把消息列表转成 Langfuse 输入载荷 [{role, content}]。

    只取 role 与 content —— 消息对象上还挂着 id / response_metadata 等字段，
    整对象交给序列化器会把不该进 trace 的东西带进去。

    Args:
        messages: LangChain 消息列表

    Returns:
        [{"role": <消息类型>, "content": <文本>}, ...]
    """
    return [{"role": m.type, "content": _extract_text(m)} for m in messages]
