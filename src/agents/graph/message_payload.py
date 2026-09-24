"""消息文本提取与 Langfuse 输入载荷构造（纯函数，无业务依赖）。

`_extract_text` / `_messages_payload` 原本内联在 `agent_node`，与其图节点逻辑
无耦合；抽为独立模块以守住单文件行数红线（CLAUDE.md：单文件 ≤ 400 行）。

载荷刻意用 **OpenAI 形态的 role**（而不是 LangChain 的 `m.type`）：`ai` / `human`
这类类型名在 Langfuse 的对话视图里读不出来，规范化后可直接按对话渲染。
"""

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

#: LangChain 消息类型 → OpenAI 形态 role（未列出的原样透传）
_ROLE_MAP = {"ai": "assistant", "human": "user"}


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


def _messages_payload(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """把消息列表转成 Langfuse 输入载荷。

    Args:
        messages: LangChain 消息列表

    Returns:
        `[{role, content}(, tool_calls)(, name)]`；assistant 条目在发起工具调用时
        带 `tool_calls`，tool 条目在知道工具名时带 `name`。消息对象上还挂着 id /
        response_metadata 等字段，**不得整对象交给序列化器**。
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {
            "role": _ROLE_MAP.get(message.type, message.type),
            "content": _extract_text(message),
        }
        if isinstance(message, AIMessage) and message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "args": call.get("args", {}),
                }
                for call in message.tool_calls
                if isinstance(call, dict)
            ]
        elif isinstance(message, ToolMessage) and message.name:
            item["name"] = message.name
        payload.append(item)
    return payload
