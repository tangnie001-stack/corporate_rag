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


def _tool_calls_payload(message: Any) -> list[dict[str, Any]]:
    """把消息上的 tool_calls 投影成 trace 载荷形态。

    Args:
        message: 携带 `tool_calls` 的消息 / 流聚合块

    Returns:
        `[{"id", "name", "args"}]`；`tool_calls` 缺失或为空时返回空列表。
        消息自带的 `type` 等额外键一律丢弃（LangChain 会注入 `type: "tool_call"`）。
    """
    payload: list[dict[str, Any]] = []
    for call in message.tool_calls or []:
        if not isinstance(call, dict):
            continue
        payload.append(
            {
                "id": call.get("id", ""),
                "name": call.get("name", ""),
                "args": call.get("args", {}),
            }
        )
    return payload


def _observation_output(message: Any) -> Any:
    """算出一轮模型输出的 generation `output`（文本优先）。

    参数按鸭子类型读取（`content` 走 `_extract_text`、`tool_calls` 走
    `_tool_calls_payload`），**刻意不判 `isinstance(message, AIMessage)`**：调用点传入的是
    `astream` 流聚合出来的块，测试里还会传入等价的替身对象；加类型门会让这类输入
    的 `tool_calls` 被判空、工具轮输出回落成空串 —— 正是本函数要修的问题。

    Args:
        message: 流聚合后的消息对象（AIMessageChunk / AIMessage / 等价替身）

    Returns:
        文本非空时返回文本；文本为空但有 `tool_calls` 时返回
        `{"tool_calls": [{"id", "name", "args"}]}`；两者皆空时返回空字符串
    """
    text = _extract_text(message)
    if text:
        return text
    tool_calls = _tool_calls_payload(message)
    if tool_calls:
        return {"tool_calls": tool_calls}
    return ""


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
            item["tool_calls"] = _tool_calls_payload(message)
        elif isinstance(message, ToolMessage) and message.name:
            item["name"] = message.name
        payload.append(item)
    return payload
