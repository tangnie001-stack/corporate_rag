"""用户实体 — 对应 users 表。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class UserEntity:
    """用户实体，对应 users 表一行记录。"""

    id: str
    """用户 UUID。"""
    account: str
    """登录账号。"""
    password: str
    """密码的 bcrypt 哈希值。"""
    token: Optional[str] = None
    """当前登录 token。"""
    created_at: Optional[datetime] = None
    """注册时间。"""
