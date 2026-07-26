"""对话消息数据类型。"""

from dataclasses import dataclass


@dataclass
class ChatMessage:
    """单条对话消息。"""

    role: str  # "user" | "assistant"
    content: str
