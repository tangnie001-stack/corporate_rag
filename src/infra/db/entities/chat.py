"""会话/消息实体 — 对应 sessions 和 conversation_history 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class SessionEntity:
    """会话实体，对应 sessions 表一行记录。

    Attributes:
        id: 会话 UUID
        title: 会话标题（截取首条消息前 20 字）
        kb_id: 关联的知识库 ID（空字符代表所有知识库）
        user_id: 所属用户 ID
        created_at: 创建时间
        updated_at: 最后活跃时间
    """

    id: str
    """会话 UUID。"""
    title: str = ""
    """会话标题（截取首条消息前 20 字）。"""
    kb_id: str = ""
    """关联的知识库 ID（空字符代表所有知识库）。"""
    user_id: str = ""
    """所属用户 ID。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后活跃时间。"""


@dataclass(slots=True)
class SessionListItem:
    """会话列表项（含知识库名称和消息数量）。

    Attributes:
        id: 会话 UUID
        title: 会话标题
        kb_id: 关联的知识库 ID
        kb_name: 知识库名称（LEFT JOIN 结果）
        message_count: 该会话的消息数量
        created_at: 创建时间
        updated_at: 最后活跃时间
    """

    id: str
    """会话 UUID。"""
    title: str
    """会话标题。"""
    kb_id: str
    """关联的知识库 ID。"""
    kb_name: str
    """知识库名称（LEFT JOIN 结果）。"""
    message_count: int
    """该会话的消息数量。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后活跃时间。"""


@dataclass(slots=True)
class MessageEntity:
    """消息实体，对应 conversation_history 表一行记录。

    Attributes:
        session_id: 所属会话 ID
        role: 角色：user / assistant
        content: 消息内容
        kb_id: 关联的知识库 ID
        sources: 来源引用 JSON 字符串
        prompt_tokens: 提示 token 数
        completion_tokens: 补全 token 数
        total_tokens: 总 token 数
        model_name: 模型名称（如 qwen-plus）
        created_at: 创建时间
    """

    session_id: str
    """所属会话 ID。"""
    role: str
    """角色：user / assistant。"""
    content: str
    """消息内容。"""
    kb_id: str = ""
    """关联的知识库 ID。"""
    sources: Optional[str] = None
    """来源引用 JSON 字符串。"""
    prompt_tokens: int = 0
    """提示 token 数。"""
    completion_tokens: int = 0
    """补全 token 数。"""
    total_tokens: int = 0
    """总 token 数。"""
    model_name: str = ""
    """模型名称（如 qwen-plus）。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
