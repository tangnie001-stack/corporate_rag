"""知识库实体 — 对应 knowledge_base 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class KbEntity:
    """知识库实体，对应 knowledge_base 表一行记录。"""

    id: str
    """知识库 UUID。"""
    user_id: str = ""
    """所属用户 ID，空字符代表无用户场景。"""
    name: str = ""
    """知识库名称，同一用户下唯一。"""
    description: Optional[str] = None
    """知识库描述。"""
    status: str = "active"
    """状态：active / deleted。"""
    created_at: Optional[datetime] = None
    """创建时间。"""
    updated_at: Optional[datetime] = None
    """最后更新时间。"""


@dataclass(slots=True)
class KbListItem:
    """知识库列表项（含文档计数）。"""

    id: str
    """知识库 UUID。"""
    user_id: str
    """所属用户 ID。"""
    name: str
    """知识库名称。"""
    doc_count: int = 0
    """该知识库下的文档数量（LEFT JOIN document 计数）。"""
